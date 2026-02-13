"""
Utility classes for transaction management and optimization.

Includes:
- NonceManager: Thread-safe nonce tracking for batch transactions
- DecimalsCache: Caching token decimals to reduce RPC calls
- GasEstimator: Smart gas estimation with fallbacks
- BatchRPC: Batch multiple RPC calls via Multicall3
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from web3 import Web3
from web3.exceptions import ContractLogicError

logger = logging.getLogger(__name__)

# Multicall3 address (same on all EVM chains)
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

# Minimal Multicall3 ABI
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"}
                ],
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"}
                ],
                "name": "returnData",
                "type": "tuple[]"
            }
        ],
        "stateMutability": "payable",
        "type": "function"
    }
]

# Minimal ERC20 ABI for utility functions
ERC20_MINIMAL_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]


class NonceManager:
    """
    Thread-safe nonce manager for batch transactions.

    Solves the race condition where multiple transactions sent quickly
    may get the same nonce from `get_transaction_count('pending')`.

    Usage:
        nonce_mgr = NonceManager(w3, account_address)

        # For sequential transactions:
        nonce1 = nonce_mgr.get_next_nonce()
        nonce2 = nonce_mgr.get_next_nonce()

        # After transaction confirmed:
        nonce_mgr.confirm_transaction(nonce1)

        # If transaction failed and needs retry:
        nonce_mgr.release_nonce(nonce1)
    """

    def __init__(self, w3: Web3, account_address: str):
        self.w3 = w3
        self.account_address = Web3.to_checksum_address(account_address)
        self._lock = threading.Lock()
        self._current_nonce: Optional[int] = None
        self._pending_nonces: set = set()
        self._last_sync_time: float = 0
        self._sync_interval: float = 30.0  # Re-sync with blockchain every 30s

    def _sync_nonce(self) -> int:
        """Sync nonce with blockchain."""
        return self.w3.eth.get_transaction_count(self.account_address, 'pending')

    def get_next_nonce(self, force_sync: bool = False) -> int:
        """
        Get the next available nonce.

        Args:
            force_sync: Force sync with blockchain even if recently synced

        Returns:
            Next nonce to use
        """
        with self._lock:
            current_time = time.time()

            # Sync with blockchain if needed
            if (self._current_nonce is None or
                force_sync or
                current_time - self._last_sync_time > self._sync_interval):

                blockchain_nonce = self._sync_nonce()

                # Clean up stale pending nonces that have already been confirmed
                # (nonces less than blockchain_nonce are already mined)
                stale_count = len(self._pending_nonces)
                self._pending_nonces = {
                    n for n in self._pending_nonces
                    if n >= blockchain_nonce
                }
                cleaned = stale_count - len(self._pending_nonces)
                if cleaned > 0:
                    logger.debug(f"Cleaned {cleaned} stale pending nonces")

                if self._current_nonce is None:
                    self._current_nonce = blockchain_nonce
                else:
                    # Take the max of our tracked nonce and blockchain nonce
                    # This handles cases where external transactions were sent
                    self._current_nonce = max(self._current_nonce, blockchain_nonce)

                self._last_sync_time = current_time
                logger.debug(f"Synced nonce with blockchain: {self._current_nonce}")

            # Get next nonce
            nonce = self._current_nonce
            self._current_nonce += 1
            self._pending_nonces.add(nonce)

            logger.debug(f"Allocated nonce: {nonce}, pending: {len(self._pending_nonces)}")
            return nonce

    def confirm_transaction(self, nonce: int):
        """Mark a nonce as confirmed (transaction included in block)."""
        with self._lock:
            self._pending_nonces.discard(nonce)
            logger.debug(f"Confirmed nonce: {nonce}")

    def release_nonce(self, nonce: int):
        """
        Release a nonce that wasn't used (transaction failed before sending).

        Decrements _current_nonce if this was the most recently allocated nonce,
        preventing nonce gaps from accumulating on rapid failures.
        """
        with self._lock:
            self._pending_nonces.discard(nonce)
            # If this was the last allocated nonce, reclaim it
            if self._current_nonce is not None and nonce == self._current_nonce - 1:
                self._current_nonce = nonce
            logger.debug(f"Released nonce: {nonce}, current: {self._current_nonce}")

    def reset(self):
        """Reset the nonce manager (force re-sync on next call)."""
        with self._lock:
            self._current_nonce = None
            self._pending_nonces.clear()
            self._last_sync_time = 0
            logger.debug("Nonce manager reset")

    def get_pending_count(self) -> int:
        """
        Get number of pending transactions.

        Note: This count may include stale nonces if sync hasn't happened recently.
        Call cleanup_stale_nonces() for accurate count.
        """
        with self._lock:
            return len(self._pending_nonces)

    def cleanup_stale_nonces(self) -> int:
        """
        Force cleanup of stale pending nonces by syncing with blockchain.

        Returns:
            Number of stale nonces that were cleaned up
        """
        with self._lock:
            blockchain_nonce = self._sync_nonce()

            stale_count = len(self._pending_nonces)
            self._pending_nonces = {
                n for n in self._pending_nonces
                if n >= blockchain_nonce
            }
            cleaned = stale_count - len(self._pending_nonces)

            # Also update current nonce
            if self._current_nonce is not None:
                self._current_nonce = max(self._current_nonce, blockchain_nonce)

            self._last_sync_time = time.time()

            if cleaned > 0:
                logger.debug(f"Cleaned {cleaned} stale pending nonces")

            return cleaned

    def get_pending_nonces(self) -> set:
        """Get a copy of the pending nonces set (for debugging)."""
        with self._lock:
            return self._pending_nonces.copy()


class DecimalsCache:
    """
    Cache for token decimals to reduce RPC calls.

    Pre-populated with known stablecoin decimals.
    Thread-safe for concurrent access.

    Usage:
        cache = DecimalsCache(w3)
        decimals = cache.get_decimals(token_address)
    """

    # Known token decimals (lowercase addresses)
    KNOWN_DECIMALS = {
        # BSC
        "0x55d398326f99059ff775485246999027b3197955": 18,  # USDT BSC
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18,  # USDC BSC
        "0xe9e7cea3dedca5984780bafc599bd69add087d56": 18,  # BUSD BSC
        "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3": 18,  # DAI BSC
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": 18,  # WBNB
        # Base
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC Base
        "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": 6,   # USDbC Base
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": 18,  # DAI Base
        "0x4200000000000000000000000000000000000006": 18,  # WETH Base
        # Ethereum
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,   # USDC ETH
        "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,   # USDT ETH
        "0x6b175474e89094c44da98b954eedeac495271d0f": 18,  # DAI ETH
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,  # WETH ETH
    }

    def __init__(self, w3: Web3):
        self.w3 = w3
        self._cache: Dict[str, int] = {}
        self._lock = threading.Lock()

        # Pre-populate with known values
        self._cache.update(self.KNOWN_DECIMALS)

    def get_decimals(self, token_address: str) -> int:
        """
        Get token decimals (cached).

        Args:
            token_address: Token contract address

        Returns:
            Number of decimals (default 18 if lookup fails)
        """
        address_lower = token_address.lower()

        # Check cache first
        with self._lock:
            if address_lower in self._cache:
                return self._cache[address_lower]

        # Fetch from blockchain
        try:
            token = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_MINIMAL_ABI
            )
            decimals = token.functions.decimals().call()

            # Cache the result
            with self._lock:
                self._cache[address_lower] = decimals

            logger.debug(f"Fetched decimals for {token_address[:10]}...: {decimals}")
            return decimals

        except Exception as e:
            logger.error(f"Failed to get decimals for {token_address[:10]}...: {e}")
            raise RuntimeError(
                f"Cannot determine decimals for token {token_address}: {e}. "
                f"Check RPC connection or add token to KNOWN_DECIMALS."
            ) from e

    def get_decimals_batch(self, token_addresses: List[str]) -> Dict[str, int]:
        """
        Get decimals for multiple tokens (uses cache + batch RPC for unknown).

        Args:
            token_addresses: List of token addresses

        Returns:
            Dict mapping address -> decimals
        """
        result = {}
        unknown_addresses = []

        # Check cache
        with self._lock:
            for addr in token_addresses:
                addr_lower = addr.lower()
                if addr_lower in self._cache:
                    result[addr] = self._cache[addr_lower]
                else:
                    unknown_addresses.append(addr)

        # Fetch unknown via batch RPC if available
        if unknown_addresses:
            for addr in unknown_addresses:
                result[addr] = self.get_decimals(addr)

        return result

    def preload(self, token_addresses: List[str]):
        """Preload decimals for a list of tokens."""
        self.get_decimals_batch(token_addresses)

    def clear(self):
        """Clear the cache (except known values)."""
        with self._lock:
            self._cache = dict(self.KNOWN_DECIMALS)


class GasEstimator:
    """
    Smart gas estimation with fallbacks.

    Tries to estimate gas, falls back to safe defaults if estimation fails.
    Applies a configurable buffer for safety.

    Usage:
        estimator = GasEstimator(w3, buffer_percent=20)
        gas_limit = estimator.estimate(contract.functions.method(), from_address)
    """

    # Default gas limits by operation type
    DEFAULTS = {
        'approve': 60000,
        'transfer': 65000,
        'mint_position': 500000,
        'multicall': 2000000,
        'swap': 300000,
        'initialize_pool': 500000,
        'modify_liquidity': 400000,
    }

    def __init__(self, w3: Web3, buffer_percent: int = 20):
        """
        Args:
            w3: Web3 instance
            buffer_percent: Percentage buffer to add to estimated gas
        """
        self.w3 = w3
        self.buffer_percent = buffer_percent

    def estimate(
        self,
        contract_function,
        from_address: str,
        value: int = 0,
        default_type: str = 'approve',
        max_gas: int = 3000000
    ) -> int:
        """
        Estimate gas for a contract function call.

        Args:
            contract_function: Web3 contract function (e.g., contract.functions.approve(...))
            from_address: Transaction sender address
            value: ETH value to send (default 0)
            default_type: Type of operation for fallback default
            max_gas: Maximum gas to return

        Returns:
            Estimated gas with buffer applied
        """
        try:
            estimated = contract_function.estimate_gas({
                'from': Web3.to_checksum_address(from_address),
                'value': value
            })

            # Apply buffer
            with_buffer = int(estimated * (1 + self.buffer_percent / 100))
            result = min(with_buffer, max_gas)

            logger.debug(f"Gas estimated: {estimated}, with buffer: {result}")
            return result

        except ContractLogicError as e:
            logger.warning(f"Gas estimation failed (contract error): {e}")
            return self.DEFAULTS.get(default_type, 200000)

        except Exception as e:
            logger.warning(f"Gas estimation failed: {e}, using default for '{default_type}'")
            return self.DEFAULTS.get(default_type, 200000)

    def estimate_batch(
        self,
        calls: List[Tuple],
        from_address: str,
        default_type: str = 'multicall'
    ) -> int:
        """
        Estimate gas for batch/multicall operations.

        Sums individual estimates or uses default if estimation fails.

        Args:
            calls: List of (contract_function, value) tuples
            from_address: Transaction sender
            default_type: Type for fallback

        Returns:
            Total estimated gas
        """
        total_gas = 0

        for contract_fn, value in calls:
            gas = self.estimate(contract_fn, from_address, value, default_type)
            total_gas += gas

        # Add overhead for batch processing
        total_with_overhead = int(total_gas * 1.1)

        return min(total_with_overhead, 8000000)  # Block gas limit safety


@dataclass
class BatchCall:
    """Single call in a batch RPC request."""
    target: str
    call_data: bytes
    allow_failure: bool = True


@dataclass
class BatchResult:
    """Result of a single call in batch."""
    success: bool
    return_data: bytes


class BatchRPC:
    """
    Batch multiple RPC calls via Multicall3.

    Reduces latency by combining multiple read calls into one RPC request.

    Usage:
        batch = BatchRPC(w3)

        # Add calls
        batch.add_balance_of(token_address, user_address)
        batch.add_allowance(token_address, user_address, spender_address)

        # Execute
        results = batch.execute()
    """

    def __init__(self, w3: Web3, multicall_address: str = MULTICALL3_ADDRESS):
        self.w3 = w3
        self.multicall = w3.eth.contract(
            address=Web3.to_checksum_address(multicall_address),
            abi=MULTICALL3_ABI
        )
        self._calls: List[BatchCall] = []
        self._decoders: List[callable] = []

    def add_call(self, target: str, call_data: bytes, decoder: callable = None, allow_failure: bool = True):
        """
        Add a raw call to the batch.

        Args:
            target: Contract address to call
            call_data: Encoded function call data
            decoder: Function to decode the result (receives bytes, returns decoded value)
            allow_failure: If True, batch continues even if this call fails
        """
        self._calls.append(BatchCall(
            target=Web3.to_checksum_address(target),
            call_data=call_data,
            allow_failure=allow_failure
        ))
        self._decoders.append(decoder)

    def add_balance_of(self, token_address: str, account_address: str):
        """Add a balanceOf call."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_MINIMAL_ABI
        )
        call_data = token.functions.balanceOf(
            Web3.to_checksum_address(account_address)
        )._encode_transaction_data()

        def decode_uint256(data: bytes) -> int:
            if len(data) >= 32:
                return int.from_bytes(data[:32], 'big')
            return 0

        self.add_call(token_address, call_data, decode_uint256)

    def add_allowance(self, token_address: str, owner_address: str, spender_address: str):
        """Add an allowance call."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_MINIMAL_ABI
        )
        call_data = token.functions.allowance(
            Web3.to_checksum_address(owner_address),
            Web3.to_checksum_address(spender_address)
        )._encode_transaction_data()

        def decode_uint256(data: bytes) -> int:
            if len(data) >= 32:
                return int.from_bytes(data[:32], 'big')
            return 0

        self.add_call(token_address, call_data, decode_uint256)

    def add_decimals(self, token_address: str):
        """Add a decimals call."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_MINIMAL_ABI
        )
        call_data = token.functions.decimals()._encode_transaction_data()

        def decode_uint8(data: bytes) -> int:
            if len(data) >= 32:
                return int.from_bytes(data[:32], 'big')
            raise RuntimeError(f"Failed to decode decimals for {token_address}: response too short ({len(data)} bytes)")

        self.add_call(token_address, call_data, decode_uint8)

    def execute(self) -> List[Any]:
        """
        Execute all batched calls.

        Returns:
            List of decoded results (or None if call failed and allow_failure=True)

        Raises:
            Exception if any call with allow_failure=False fails
        """
        if not self._calls:
            return []

        # Build aggregate3 call
        calls_data = [
            (call.target, call.allow_failure, call.call_data)
            for call in self._calls
        ]

        try:
            raw_results = self.multicall.functions.aggregate3(calls_data).call()
        except Exception as e:
            logger.error(f"Multicall failed: {e}")
            # Fallback to individual calls
            return self._fallback_execute()

        # Decode results
        results = []
        for i, (success, return_data) in enumerate(raw_results):
            if success and self._decoders[i]:
                try:
                    decoded = self._decoders[i](return_data)
                    results.append(decoded)
                except Exception as e:
                    logger.warning(f"Failed to decode result {i}: {e}")
                    results.append(None)
            else:
                if not success and not self._calls[i].allow_failure:
                    raise Exception(f"Required call {i} failed")
                results.append(None)

        return results

    def _fallback_execute(self) -> List[Any]:
        """Fallback: execute calls individually."""
        results = []
        for i, call in enumerate(self._calls):
            try:
                result = self.w3.eth.call({
                    'to': call.target,
                    'data': call.call_data
                })
                if self._decoders[i]:
                    decoded = self._decoders[i](result)
                    results.append(decoded)
                else:
                    results.append(result)
            except Exception as e:
                if not call.allow_failure:
                    raise
                logger.warning(f"Individual call {i} failed: {e}")
                results.append(None)
        return results

    # ── Position loading helpers ──────────────────────────────────────

    def add_erc721_owner_of(self, contract_address: str, token_id: int):
        """Add ownerOf(token_id) call for ERC721 (position manager)."""
        # ownerOf(uint256) selector = 0x6352211e
        call_data = bytes.fromhex('6352211e') + token_id.to_bytes(32, 'big')

        def decode_address(data: bytes):
            if len(data) >= 32:
                return Web3.to_checksum_address('0x' + data[12:32].hex())
            return None

        self.add_call(contract_address, call_data, decode_address)

    def add_v3_position(self, position_manager: str, token_id: int):
        """Add positions(token_id) call — returns V3 position data tuple."""
        # positions(uint256) selector = 0x99fbab88
        call_data = bytes.fromhex('99fbab88') + token_id.to_bytes(32, 'big')

        def decode_position(data: bytes) -> dict:
            if len(data) < 384:  # 12 fields × 32 bytes
                return None
            fields = [int.from_bytes(data[i*32:(i+1)*32], 'big') for i in range(12)]
            # Decode signed int24 for tick fields (indices 5, 6)
            for idx in (5, 6):
                if fields[idx] >= 2**255:
                    fields[idx] -= 2**256
            return {
                'nonce': fields[0],
                'operator': Web3.to_checksum_address('0x' + data[1*32+12:2*32].hex()),
                'token0': Web3.to_checksum_address('0x' + data[2*32+12:3*32].hex()),
                'token1': Web3.to_checksum_address('0x' + data[3*32+12:4*32].hex()),
                'fee': fields[4],
                'tick_lower': fields[5],
                'tick_upper': fields[6],
                'liquidity': fields[7],
                'fee_growth_inside0_last_x128': fields[8],
                'fee_growth_inside1_last_x128': fields[9],
                'tokens_owed0': fields[10],
                'tokens_owed1': fields[11],
            }

        self.add_call(position_manager, call_data, decode_position)

    def add_pool_slot0(self, pool_address: str):
        """Add slot0() call — raw selector for PancakeSwap V3 compatibility."""
        # slot0() selector = 0x3850c7bd
        call_data = bytes.fromhex('3850c7bd')

        def decode_slot0(data: bytes) -> dict:
            if len(data) < 64:
                return None
            sqrt_price_x96 = int.from_bytes(data[0:32], 'big')
            tick_raw = int.from_bytes(data[32:64], 'big')
            tick = tick_raw - 2**256 if tick_raw >= 2**255 else tick_raw
            return {'sqrtPriceX96': sqrt_price_x96, 'tick': tick}

        self.add_call(pool_address, call_data, decode_slot0)

    def add_pool_address(self, factory_address: str, token0: str, token1: str, fee: int):
        """Add getPool(token0, token1, fee) call to factory."""
        # getPool(address,address,uint24) selector = 0x1698ee82
        t0 = bytes.fromhex(Web3.to_checksum_address(token0)[2:]).rjust(32, b'\x00')
        t1 = bytes.fromhex(Web3.to_checksum_address(token1)[2:]).rjust(32, b'\x00')
        fee_bytes = fee.to_bytes(32, 'big')
        call_data = bytes.fromhex('1698ee82') + t0 + t1 + fee_bytes

        def decode_address(data: bytes):
            if len(data) >= 32:
                addr = '0x' + data[12:32].hex()
                if addr == '0x' + '00' * 20:
                    return None
                return Web3.to_checksum_address(addr)
            return None

        self.add_call(factory_address, call_data, decode_address)

    def add_erc20_symbol(self, token_address: str):
        """Add symbol() call for ERC20."""
        # symbol() selector = 0x95d89b41
        call_data = bytes.fromhex('95d89b41')

        def decode_string(data: bytes) -> str:
            if len(data) < 64:
                # Try as raw bytes (some tokens return non-standard)
                return data.rstrip(b'\x00').decode('utf-8', errors='replace') if data else '???'
            try:
                offset = int.from_bytes(data[0:32], 'big')
                length = int.from_bytes(data[offset:offset+32], 'big')
                return data[offset+32:offset+32+length].decode('utf-8', errors='replace')
            except Exception:
                return '???'

        self.add_call(token_address, call_data, decode_string)

    # ── End position loading helpers ──────────────────────────────────

    def clear(self):
        """Clear all pending calls."""
        self._calls.clear()
        self._decoders.clear()

    def __len__(self) -> int:
        return len(self._calls)


def get_token_info_batch(
    w3: Web3,
    token_addresses: List[str],
    account_address: str,
    spender_address: str = None,
    decimals_cache: DecimalsCache = None
) -> Dict[str, Dict[str, Any]]:
    """
    Get balance, decimals, and optionally allowance for multiple tokens in one batch.

    Args:
        w3: Web3 instance
        token_addresses: List of token addresses
        account_address: Address to check balance for
        spender_address: Optional spender for allowance check
        decimals_cache: Optional decimals cache to use

    Returns:
        Dict mapping token address -> {balance, decimals, allowance?}
    """
    batch = BatchRPC(w3)
    cache = decimals_cache or DecimalsCache(w3)

    # Track what we're querying
    queries = []  # [(token, 'balance'|'allowance'|'decimals'), ...]

    for token in token_addresses:
        # Always get balance
        batch.add_balance_of(token, account_address)
        queries.append((token, 'balance'))

        # Get allowance if spender provided
        if spender_address:
            batch.add_allowance(token, account_address, spender_address)
            queries.append((token, 'allowance'))

        # Get decimals if not cached
        token_lower = token.lower()
        if token_lower not in cache._cache:
            batch.add_decimals(token)
            queries.append((token, 'decimals'))

    # Execute batch
    results = batch.execute()

    # Parse results
    output = {token: {} for token in token_addresses}

    for i, (token, query_type) in enumerate(queries):
        if i < len(results):
            value = results[i]
            if query_type == 'decimals' and value is not None:
                # Also update cache
                cache._cache[token.lower()] = value
            output[token][query_type] = value

    # Fill in cached decimals
    for token in token_addresses:
        if 'decimals' not in output[token]:
            output[token]['decimals'] = cache.get_decimals(token)

    return output
