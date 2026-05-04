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
    },
    {
        "inputs": [
            {"name": "requireSuccess", "type": "bool"},
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "callData", "type": "bytes"}
                ],
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "tryAggregate",
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
        "stateMutability": "view",
        "type": "function"
    }
]

# Minimal ERC20 ABI for utility functions
ERC20_MINIMAL_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]


# Module-level gas-price cap (gwei). 0 = disabled.
# Settings UI updates this via `set_gas_price_cap(value)`; every call to
# `eip1559_gas_fields()` enforces it before returning, so all TX-sending sites
# are automatically protected without per-site changes.
_gas_price_cap_gwei: float = 0.0


def set_gas_price_cap(cap_gwei: float) -> None:
    """Set the global gas-price cap (gwei). Pass 0 to disable."""
    global _gas_price_cap_gwei
    _gas_price_cap_gwei = max(0.0, float(cap_gwei))
    if _gas_price_cap_gwei > 0:
        logger.info(f"Gas-price cap set to {_gas_price_cap_gwei:.1f} Gwei")
    else:
        logger.info("Gas-price cap disabled")


def get_gas_price_cap() -> float:
    """Return the current global gas-price cap in gwei (0 = disabled)."""
    return _gas_price_cap_gwei


def _enforce_gas_cap(gas_price_wei: int, cap_gwei: float) -> None:
    """Internal: raise if gas_price (wei) exceeds cap (gwei). Does no RPC."""
    if cap_gwei <= 0:
        return
    gas_price_gwei = gas_price_wei / 1e9
    if gas_price_gwei > cap_gwei:
        raise RuntimeError(
            f"Gas price {gas_price_gwei:.1f} Gwei exceeds cap {cap_gwei:.1f} Gwei. "
            f"TX aborted to prevent excessive fees. "
            f"Adjust the cap in Settings or wait for lower gas."
        )


def check_gas_price(w3: Web3, cap_gwei: Optional[float] = None) -> None:
    """
    Reject TX if current gas price exceeds the configured cap.

    Safety net against gas spikes (BSC sometimes reaches 100+ gwei).
    Mirrors web's `_check_gas_price`.

    Args:
        w3: Web3 instance
        cap_gwei: Max acceptable gas price in gwei. <=0 disables the check.
                  If None, falls back to module-level `_gas_price_cap_gwei`
                  (set via `set_gas_price_cap()` from Settings).

    Raises:
        RuntimeError: If gas price exceeds cap.
    """
    cap = _gas_price_cap_gwei if cap_gwei is None else cap_gwei
    if cap <= 0:
        return
    _enforce_gas_cap(w3.eth.gas_price, cap)


def eip1559_gas_fields(w3: Web3, priority_fee_override: Optional[int] = None) -> Dict[str, Any]:
    """
    Build EIP-1559 (type 2) gas fields, with fallback to legacy gasPrice.

    Mirrors web's `_eip1559_gas_fields` formula:
        maxFeePerGas       = baseFee * 2 + priorityFee
        maxPriorityFeePerGas = priorityFee  (from node, or override)

    Also enforces the global gas-price cap (`set_gas_price_cap`) so every
    TX-sending site gets the safety net for free.

    Performance: uses `w3.batch_requests()` to fetch gas_price + get_block +
    max_priority_fee in ONE HTTP POST instead of 3 sequential. ~35% faster on
    public RPC. Falls back to sequential if the provider doesn't support batching.

    On chains without EIP-1559 (no baseFeePerGas in latest block), returns
    legacy {"gasPrice": current_gas_price}.

    Args:
        w3: Web3 instance
        priority_fee_override: If provided, use this priority fee instead of querying the node.

    Returns:
        Dict ready to be spread into build_transaction({...}):
            {"maxFeePerGas": ..., "maxPriorityFeePerGas": ..., "type": 2}
        OR  {"gasPrice": ...}

    Raises:
        RuntimeError: If gas price exceeds the configured cap (set via `set_gas_price_cap`).
    """
    cap = _gas_price_cap_gwei
    need_priority_fee = priority_fee_override is None

    gas_price: Optional[int] = None
    base_fee: Optional[int] = None
    priority_fee: Optional[int] = priority_fee_override

    # ── Single batched HTTP POST: gas_price + get_block + (optionally) max_priority_fee ──
    try:
        with w3.batch_requests() as batch:
            batch.add(w3.eth.gas_price)
            batch.add(w3.eth.get_block("latest"))
            if need_priority_fee:
                batch.add(w3.eth.max_priority_fee)
            results = batch.execute()
        # Validate batch results — protects against providers that don't truly support
        # batching but silently return junk, AND against test Mock w3 where batch is a no-op.
        if not isinstance(results, (list, tuple)) or len(results) < (3 if need_priority_fee else 2):
            raise TypeError(f"batch returned unexpected shape: {type(results).__name__}")
        if not isinstance(results[0], int):
            raise TypeError(f"batch returned non-int gas_price: {type(results[0]).__name__}")
        gas_price = results[0]
        block = results[1]
        base_fee = block.get("baseFeePerGas") if hasattr(block, 'get') else None
        if need_priority_fee:
            priority_fee = results[2]
    except Exception as e:
        logger.debug(f"Batched gas-fields fetch failed, sequential fallback: {e}")
        # Sequential fallback — same logic, separate RPCs.
        try:
            gas_price = w3.eth.gas_price
            latest = w3.eth.get_block("latest")
            base_fee = latest.get("baseFeePerGas") if hasattr(latest, 'get') else None
            if need_priority_fee:
                priority_fee = w3.eth.max_priority_fee
        except Exception as e2:
            logger.debug(f"Sequential gas-fields fetch also failed: {e2}")
            # Last-resort: legacy gasPrice only.
            gp = w3.eth.gas_price
            _enforce_gas_cap(gp, cap)
            return {"gasPrice": gp}

    # Enforce cap against the price we already have (no extra RPC).
    if isinstance(gas_price, int):
        _enforce_gas_cap(gas_price, cap)

    # Build EIP-1559 fields ONLY if both values are real ints (protects from chains
    # without baseFee, mocks returning non-int sentinels, and partially-populated blocks).
    if isinstance(base_fee, int) and isinstance(priority_fee, int):
        return {
            "maxFeePerGas": base_fee * 2 + priority_fee,
            "maxPriorityFeePerGas": priority_fee,
            "type": 2,
        }
    # Fall back to legacy gasPrice (which may itself be a mock — caller's TX building
    # will validate downstream).
    return {"gasPrice": gas_price}


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
        # Check if sync is needed (under lock), then do RPC outside lock
        need_sync = False
        with self._lock:
            current_time = time.time()
            if (self._current_nonce is None or
                force_sync or
                current_time - self._last_sync_time > self._sync_interval):
                need_sync = True

        # RPC call OUTSIDE lock to avoid blocking other threads
        blockchain_nonce = None
        if need_sync:
            blockchain_nonce = self._sync_nonce()

        # Apply sync result and allocate nonce (under lock)
        with self._lock:
            if blockchain_nonce is not None:
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

                self._last_sync_time = time.time()
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
            else:
                # Non-sequential release creates a gap; force re-sync on next allocation
                self._last_sync_time = 0
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
        # RPC call outside lock
        blockchain_nonce = self._sync_nonce()

        with self._lock:
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
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_MINIMAL_ABI
        )
        call_data = token.functions.symbol()._encode_transaction_data()

        def decode_string(data: bytes) -> str:
            if len(data) >= 64:
                try:
                    from eth_abi import decode as abi_decode
                    return abi_decode(["string"], data)[0]
                except Exception:
                    pass
            # Fallback: some tokens return bytes32 instead of string
            if len(data) >= 32:
                try:
                    return data[:32].rstrip(b'\x00').decode('utf-8').strip()
                except Exception:
                    pass
            return ""

        self.add_call(token_address, call_data, decode_string)

    # ── V4 & Permit2 helpers ──────────────────────────────────────────

    def add_pool_liquidity(self, pool_address: str):
        """Add V3 liquidity() call — returns pool's current liquidity (uint128)."""
        # liquidity() selector = 0x1a686502
        call_data = bytes.fromhex('1a686502')

        def decode_uint128(data: bytes) -> int:
            if len(data) >= 32:
                return int.from_bytes(data[:32], 'big')
            return 0

        self.add_call(pool_address, call_data, decode_uint128)

    def add_v4_slot0(self, target_address: str, pool_id: bytes):
        """Add V4 getSlot0(bytes32) call — returns (sqrtPriceX96, tick, protocolFee, lpFee)."""
        # getSlot0(bytes32) selector = 0xc815641c
        call_data = bytes.fromhex('c815641c') + pool_id.rjust(32, b'\x00')

        def decode_v4_slot0(data: bytes) -> dict:
            if len(data) < 128:
                return None
            sqrt_price_x96 = int.from_bytes(data[0:32], 'big')
            tick_raw = int.from_bytes(data[32:64], 'big')
            tick = tick_raw - 2**256 if tick_raw >= 2**255 else tick_raw
            protocol_fee = int.from_bytes(data[64:96], 'big')
            lp_fee = int.from_bytes(data[96:128], 'big')
            return {
                'sqrtPriceX96': sqrt_price_x96,
                'tick': tick,
                'protocol_fee': protocol_fee,
                'lp_fee': lp_fee,
            }

        self.add_call(target_address, call_data, decode_v4_slot0)

    def add_v4_liquidity(self, target_address: str, pool_id: bytes):
        """Add V4 getLiquidity(bytes32) call — returns pool's current liquidity (uint128)."""
        # getLiquidity(bytes32) selector = 0xfa6793d5
        call_data = bytes.fromhex('fa6793d5') + pool_id.rjust(32, b'\x00')

        def decode_uint128(data: bytes) -> int:
            if len(data) >= 32:
                return int.from_bytes(data[:32], 'big')
            return 0

        self.add_call(target_address, call_data, decode_uint128)

    def add_permit2_allowance(self, permit2_address: str, owner: str, token: str, spender: str):
        """Add Permit2 allowance(address,address,address) — returns (amount, expiration, nonce)."""
        # allowance(address,address,address) selector = 0x927da105
        owner_bytes = bytes.fromhex(Web3.to_checksum_address(owner)[2:]).rjust(32, b'\x00')
        token_bytes = bytes.fromhex(Web3.to_checksum_address(token)[2:]).rjust(32, b'\x00')
        spender_bytes = bytes.fromhex(Web3.to_checksum_address(spender)[2:]).rjust(32, b'\x00')
        call_data = bytes.fromhex('927da105') + owner_bytes + token_bytes + spender_bytes

        def decode_permit2_allowance(data: bytes) -> tuple:
            if len(data) < 96:
                return (0, 0, 0)
            amount = int.from_bytes(data[0:32], 'big')
            expiration = int.from_bytes(data[32:64], 'big')
            nonce = int.from_bytes(data[64:96], 'big')
            return (amount, expiration, nonce)

        self.add_call(permit2_address, call_data, decode_permit2_allowance)

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


def batch_read_token_info(
    w3: Web3, token0: str, token1: str
) -> tuple:
    """Batch-read decimals + symbol for both tokens in 1 Multicall3 RPC call.

    Returns (t0_decimals, t1_decimals, t0_symbol, t1_symbol).
    Falls back to sequential reads if Multicall3 fails.
    """
    batch = BatchRPC(w3)
    batch.add_decimals(token0)       # [0]
    batch.add_decimals(token1)       # [1]
    batch.add_erc20_symbol(token0)   # [2]
    batch.add_erc20_symbol(token1)   # [3]

    results = batch.execute()  # auto-fallback inside

    t0_dec = results[0]
    t1_dec = results[1]
    t0_sym = results[2] or ""
    t1_sym = results[3] or ""

    if t0_dec is None:
        raise RuntimeError(
            f"Failed to read decimals for token0 {token0} — "
            f"cannot safely proceed (wrong decimals cause catastrophic amount errors)"
        )
    if t1_dec is None:
        raise RuntimeError(
            f"Failed to read decimals for token1 {token1} — "
            f"cannot safely proceed (wrong decimals cause catastrophic amount errors)"
        )

    return t0_dec, t1_dec, t0_sym, t1_sym


# ── Pool Info Cache ─────────────────────────────────────────────────────

class PoolInfoCache:
    """
    Thread-safe cache for pool information (slot0, decimals, symbols, pool address).

    When multiple LoadPositionWorkers load positions from the same pool,
    only the first worker makes the RPC calls; others wait and get cached data.

    Key: (token0_lower, token1_lower, fee) for V3
         (currency0_lower, currency1_lower, fee, tick_spacing) for V4
    TTL: 60s default (pool tick changes often, but within a batch load it's fine).
    """

    @dataclass
    class PoolData:
        pool_address: Optional[str]  # V3 pool address (None for V4)
        token0_decimals: int
        token1_decimals: int
        token0_symbol: str
        token1_symbol: str
        current_tick: Optional[int]
        sqrt_price_x96: Optional[int]
        current_price: Optional[float]
        timestamp: float

    def __init__(self, ttl: float = 60.0):
        self._cache: Dict[tuple, 'PoolInfoCache.PoolData'] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._in_flight: Dict[tuple, threading.Event] = {}
        self._in_flight_lock = threading.Lock()

    def get(self, key: tuple) -> Optional['PoolInfoCache.PoolData']:
        """Get cached pool data. Returns None if not cached or expired."""
        with self._lock:
            data = self._cache.get(key)
            if data and (time.time() - data.timestamp) < self._ttl:
                return data
            return None

    def put(self, key: tuple, data: 'PoolInfoCache.PoolData'):
        """Store pool data in cache."""
        with self._lock:
            self._cache[key] = data

    def wait_or_claim(self, key: tuple) -> bool:
        """
        Coordinate concurrent fetches for the same pool.

        Returns True if caller should fetch (claimed the slot).
        Returns False if another thread already fetched it (data now in cache).
        """
        with self._in_flight_lock:
            if key in self._in_flight:
                event = self._in_flight[key]
            else:
                self._in_flight[key] = threading.Event()
                return True  # Caller should fetch

        # Another thread is fetching — wait up to 15s
        event.wait(timeout=15.0)
        return self.get(key) is None  # True if still no data (fetch failed)

    def release(self, key: tuple):
        """Signal that fetching is complete (success or failure)."""
        with self._in_flight_lock:
            event = self._in_flight.pop(key, None)
            if event:
                event.set()

    def clear(self):
        """Clear all cached data and in-flight trackers."""
        with self._lock:
            self._cache.clear()
        with self._in_flight_lock:
            # Wake up any waiting threads
            for event in self._in_flight.values():
                event.set()
            self._in_flight.clear()


# ── V4 Multicall3 Pre-filter ────────────────────────────────────────────

# getPositionLiquidity(uint256) selector
_GET_POS_LIQ_SELECTOR = bytes.fromhex('1efeed33')


def batch_filter_v4_active(w3: Web3, position_manager_address: str, token_ids: List[int]) -> List[int]:
    """
    Filter V4 token_ids to only those with liquidity > 0 using Multicall3.

    1 RPC per 200 positions instead of N individual calls.
    Falls back to returning all IDs if multicall fails.
    """
    if not token_ids:
        return []

    total = len(token_ids)
    try:
        multicall = w3.eth.contract(
            address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
            abi=MULTICALL3_ABI
        )
        target = Web3.to_checksum_address(position_manager_address)

        calls = []
        for tid in token_ids:
            calldata = _GET_POS_LIQ_SELECTOR + tid.to_bytes(32, 'big')
            calls.append((target, calldata))

        CHUNK = 200
        active_ids = []
        for i in range(0, total, CHUNK):
            chunk_ids = token_ids[i:i + CHUNK]
            chunk_calls = calls[i:i + CHUNK]
            results = multicall.functions.tryAggregate(False, chunk_calls).call()
            for tid, (success, data) in zip(chunk_ids, results):
                if success and len(data) >= 32:
                    liq = int.from_bytes(data[-32:], 'big')
                    if liq > 0:
                        active_ids.append(tid)
                elif not success:
                    active_ids.append(tid)  # include on failure (safe fallback)

        skipped = total - len(active_ids)
        if skipped:
            logger.info(f"[V4] Multicall pre-filter: {skipped}/{total} positions have 0 liquidity, loading {len(active_ids)} active")
        return active_ids
    except Exception as e:
        logger.warning(f"[V4] Multicall pre-filter failed ({e}), returning all IDs")
        return list(token_ids)
