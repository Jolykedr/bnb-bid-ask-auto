"""
V4 PositionManager Wrapper

Handles position minting, burning, and management for V4.
Uses action-based encoding system.
"""

import time
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple
from web3 import Web3
from eth_abi import encode, decode
from eth_account.signers.local import LocalAccount

logger = logging.getLogger(__name__)

from .abis import V4_POSITION_MANAGER_ABI, PANCAKE_V4_POSITION_MANAGER_ABI, V4Actions, PancakeV4Actions
from .constants import V4Protocol, get_v4_addresses
from .pool_manager import PoolKey
from ...utils import NonceManager


@dataclass
class V4Position:
    """V4 Position information."""
    token_id: int
    pool_key: PoolKey
    tick_lower: int
    tick_upper: int
    liquidity: int


@dataclass
class MintResult:
    """Result of minting a position."""
    token_id: int
    liquidity: int
    amount0: int
    amount1: int
    tx_hash: str


class V4PositionManager:
    """
    V4 PositionManager wrapper.

    Supports both Uniswap V4 and PancakeSwap V4 (Infinity).
    V4 uses action-based encoding for batching operations.
    """

    def __init__(
        self,
        w3: Web3,
        account: LocalAccount = None,
        protocol: V4Protocol = V4Protocol.PANCAKESWAP,
        chain_id: int = 56,
        position_manager_address: str = None,
        nonce_manager: 'NonceManager' = None,
        proxy: dict = None
    ):
        self.w3 = w3
        self.account = account
        self.protocol = protocol
        self.chain_id = chain_id
        self.nonce_manager = nonce_manager
        self.proxy = proxy

        # Get addresses
        if position_manager_address:
            self.position_manager_address = Web3.to_checksum_address(position_manager_address)
        else:
            addresses = get_v4_addresses(chain_id, protocol)
            if not addresses:
                raise ValueError(f"No V4 addresses found for chain {chain_id} and protocol {protocol}")
            self.position_manager_address = Web3.to_checksum_address(addresses.position_manager)

        # Choose ABI based on protocol
        abi = PANCAKE_V4_POSITION_MANAGER_ABI if protocol == V4Protocol.PANCAKESWAP else V4_POSITION_MANAGER_ABI

        # Choose action codes based on protocol
        self.actions = PancakeV4Actions if protocol == V4Protocol.PANCAKESWAP else V4Actions

        self.contract = w3.eth.contract(
            address=self.position_manager_address,
            abi=abi
        )

    def get_position(self, token_id: int) -> V4Position:
        """
        Get position information.

        Uses getPoolAndPositionInfo which returns full PoolKey with token addresses.

        Args:
            token_id: NFT token ID

        Returns:
            V4Position with position details
        """
        # First try getPoolAndPositionInfo - the CORRECT V4 function!
        # Returns (PoolKey, packed PositionInfo)
        try:
            result = self.contract.functions.getPoolAndPositionInfo(token_id).call()
            # Result: (poolKey, info)
            # poolKey: (currency0, currency1, fee, tickSpacing, hooks)
            # info: can be packed uint256 OR unpacked tuple depending on ABI
            pool_key_tuple = result[0]
            info = result[1]
            tick_spacing = pool_key_tuple[3]

            logger.debug(f"V4 getPoolAndPositionInfo #{token_id}: info type={type(info).__name__}")

            # Decode PositionInfo — deterministic extraction
            tick_lower, tick_upper = self._extract_ticks(info, tick_spacing)

            logger.debug(f"V4 Position #{token_id}: ticks={tick_lower}/{tick_upper}")

            # Get liquidity separately
            try:
                liquidity = self.contract.functions.getPositionLiquidity(token_id).call()
                logger.debug(f"[V4] getPositionLiquidity returned: {liquidity}")
            except Exception as liq_e:
                logger.warning(f"[V4] getPositionLiquidity failed: {liq_e}")
                liquidity = self._get_position_liquidity(token_id)

            pool_key = PoolKey(
                currency0=pool_key_tuple[0],
                currency1=pool_key_tuple[1],
                fee=pool_key_tuple[2],
                tick_spacing=pool_key_tuple[3],
                hooks=pool_key_tuple[4]
            )

            logger.debug(f"[V4] getPoolAndPositionInfo success for {token_id}")
            logger.debug(f"[V4] Position {token_id}: {pool_key.currency0}/{pool_key.currency1}")
            logger.debug(f"[V4] Fee: {pool_key.fee}, TickSpacing: {pool_key.tick_spacing}")
            logger.debug(f"[V4] Ticks: {tick_lower}/{tick_upper}, Liquidity: {liquidity}")

            return V4Position(
                token_id=token_id,
                pool_key=pool_key,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                liquidity=liquidity
            )
        except Exception as e:
            import traceback
            logger.warning(f"[V4] getPoolAndPositionInfo failed for {token_id}: {e}")
            logger.debug(traceback.format_exc())
            logger.debug(f"[V4] Trying legacy getPositionInfo...")

        # Try PancakeSwap-specific positions() function
        try:
            result = self.contract.functions.positions(token_id).call()
            # Returns: (poolKey, tickLower, tickUpper, liquidity)
            pool_key_tuple = result[0]
            tick_lower = result[1]
            tick_upper = result[2]
            liquidity = result[3]

            pool_key = PoolKey(
                currency0=pool_key_tuple[0],
                currency1=pool_key_tuple[1],
                fee=pool_key_tuple[2],
                tick_spacing=pool_key_tuple[3],
                hooks=pool_key_tuple[4]
            )

            logger.debug(f"[V4] positions() success: {pool_key.currency0[:10]}.../{pool_key.currency1[:10]}..., liq={liquidity}")

            return V4Position(
                token_id=token_id,
                pool_key=pool_key,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                liquidity=liquidity
            )
        except Exception as e:
            logger.warning(f"[V4] positions() failed for {token_id}: {e}")

        # Try legacy getPositionInfo
        try:
            result = self.contract.functions.getPositionInfo(token_id).call()
            pool_key_tuple = result[0]
            tick_lower = result[1]
            tick_upper = result[2]
            liquidity = result[3]

            pool_key = PoolKey(
                currency0=pool_key_tuple[0],
                currency1=pool_key_tuple[1],
                fee=pool_key_tuple[2],
                tick_spacing=pool_key_tuple[3],
                hooks=pool_key_tuple[4]
            )

            logger.debug(f"[V4] getPositionInfo success: {pool_key.currency0[:10]}.../{pool_key.currency1[:10]}..., liq={liquidity}")

            return V4Position(
                token_id=token_id,
                pool_key=pool_key,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                liquidity=liquidity
            )
        except Exception as e:
            logger.warning(f"[V4] getPositionInfo also failed for {token_id}: {e}")
            logger.debug(f"[V4] Trying raw positionInfo...")

        # Fallback to positionInfo (packed data)
        try:
            # Uniswap V4 positionInfo returns PACKED data (32 bytes):
            # - bytes25 poolId (truncated)
            # - int24 tickLower
            # - int24 tickUpper
            # NOT a standard ABI-encoded tuple!

            # Get raw return data
            selector = self.w3.keccak(text='positionInfo(uint256)')[:4]
            call_data = selector + token_id.to_bytes(32, 'big')

            raw_result = self.w3.eth.call({
                'to': self.position_manager_address,
                'data': call_data
            })

            if len(raw_result) < 32:
                raise ValueError(f"Invalid positionInfo response length: {len(raw_result)}")

            # Parse packed data:
            # First 25 bytes = poolId (truncated hash)
            # Bytes 25-28 = tickUpper (int24, 3 bytes) - NOTE: Upper comes first!
            # Bytes 28-31 = tickLower (int24, 3 bytes)
            # Byte 31 = padding (usually 0)

            pool_id_truncated = raw_result[:25]
            tick_upper = int.from_bytes(raw_result[25:28], 'big', signed=True)
            tick_lower = int.from_bytes(raw_result[28:31], 'big', signed=True)

            # Get liquidity separately using getPositionLiquidity
            try:
                liquidity = self._get_position_liquidity(token_id)
            except Exception:
                liquidity = 0

            # We have truncated poolId (25 bytes), try to look up pool info via API
            # Extend to 32 bytes by padding with zeros on the right
            full_pool_id = pool_id_truncated + b'\x00' * (32 - len(pool_id_truncated))
            pool_id_hex = '0x' + full_pool_id.hex()

            logger.debug(f"[V4] Position {token_id}: truncated poolId = {pool_id_hex[:20]}...")

            # Try to get pool info from Uniswap API
            pool_key = None
            try:
                from .subgraph import query_uniswap_api
                pool_info = query_uniswap_api(pool_id_hex, chain_id=self.chain_id, proxy=self.proxy)
                if pool_info:
                    logger.debug(f"[V4] Found pool via API: {pool_info.token0_symbol}/{pool_info.token1_symbol}")
                    pool_key = PoolKey(
                        currency0=pool_info.token0_address,
                        currency1=pool_info.token1_address,
                        fee=pool_info.fee_tier,
                        tick_spacing=pool_info.tick_spacing,
                        hooks="0x0000000000000000000000000000000000000000"
                    )
            except Exception as api_error:
                logger.warning(f"[V4] API lookup failed: {api_error}")

            # Fallback to empty pool key if API lookup failed
            if pool_key is None:
                pool_key = PoolKey(
                    currency0="0x0000000000000000000000000000000000000000",
                    currency1="0x0000000000000000000000000000000000000000",
                    fee=0,
                    tick_spacing=0,
                    hooks="0x0000000000000000000000000000000000000000"
                )
                # Store truncated poolId for reference
                pool_key._truncated_pool_id = pool_id_truncated

            logger.debug(f"[V4] Position {token_id}: {pool_key.currency0[:10]}.../{pool_key.currency1[:10]}..., ticks={tick_lower}/{tick_upper}, liq={liquidity}")

            return V4Position(
                token_id=token_id,
                pool_key=pool_key,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                liquidity=liquidity
            )
        except Exception as e:
            raise ValueError(f"Failed to get position {token_id}: {e}")

    def _extract_ticks(self, info, tick_spacing: int = 0) -> Tuple[int, int]:
        """
        Извлечение tickLower и tickUpper из PositionInfo (packed int или tuple).

        Deterministic: использует tick_spacing для валидации при нескольких совпадениях.
        """
        MIN_TICK, MAX_TICK = -887272, 887272

        if isinstance(info, (list, tuple)):
            # Unpacked tuple (PancakeSwap V4 or newer ABIs)
            if len(info) >= 3:
                # hasSubscriber is always bool in web3.py ABI decoding
                if isinstance(info[0], bool):
                    # (hasSubscriber, tickLower, tickUpper)
                    return int(info[1]), int(info[2])
                elif isinstance(info[2], bool):
                    # (tickLower, tickUpper, hasSubscriber)
                    return int(info[0]), int(info[1])
                else:
                    # All ints: first two should be (tickLower, tickUpper)
                    tl, tu = int(info[0]), int(info[1])
                    if tl < tu:
                        return tl, tu
                    # Try second pair
                    tl2, tu2 = int(info[1]), int(info[2])
                    if tl2 < tu2:
                        return tl2, tu2
                    return tl, tu  # fallback
            elif len(info) == 2:
                return int(info[0]), int(info[1])
            else:
                raise ValueError(f"Unexpected PositionInfo tuple length: {len(info)}")

        elif isinstance(info, int):
            # Packed uint256 (Uniswap V4)
            # Uniswap V4 PositionInfo bytes32 layout (from LSB):
            #   bits 0-7:   hasSubscriber/flags
            #   bits 8-31:  tickLower (int24)
            #   bits 32-55: tickUpper (int24)
            #   bits 56+:   poolId (truncated)
            # This matches raw positionInfo(uint256) byte layout.
            layouts = [
                (8, 32, "standard (8/32)"),
                (24, 48, "alt (24/48)"),
                (232, 208, "top-down (232/208)"),
            ]

            best_match = None
            for tl_off, tu_off, name in layouts:
                tl = (info >> tl_off) & 0xFFFFFF
                if tl >= 0x800000:
                    tl -= 0x1000000
                tu = (info >> tu_off) & 0xFFFFFF
                if tu >= 0x800000:
                    tu -= 0x1000000

                if not (MIN_TICK <= tl <= MAX_TICK and MIN_TICK <= tu <= MAX_TICK and tl < tu):
                    continue

                # Strong match: ticks aligned to tick_spacing
                if tick_spacing > 0 and tl % tick_spacing == 0 and tu % tick_spacing == 0:
                    logger.debug(f"Tick layout: {name} (tick_spacing aligned)")
                    return tl, tu

                if best_match is None:
                    best_match = (tl, tu, name)

            if best_match:
                logger.debug(f"Tick layout: {best_match[2]}")
                return best_match[0], best_match[1]

            # No valid layout — use primary (8/32) and warn
            tl = (info >> 8) & 0xFFFFFF
            if tl >= 0x800000:
                tl -= 0x1000000
            tu = (info >> 32) & 0xFFFFFF
            if tu >= 0x800000:
                tu -= 0x1000000
            logger.warning(f"No valid tick layout for packed info 0x{info:064x}, default: tl={tl} tu={tu}")
            return tl, tu

        else:
            raise ValueError(f"Unexpected PositionInfo type: {type(info)}, value={info}")

    def _get_position_liquidity(self, token_id: int) -> int:
        """Get liquidity for a position using getPositionLiquidity."""
        try:
            # First try contract method
            liquidity = self.contract.functions.getPositionLiquidity(token_id).call()
            logger.debug(f"[V4] _get_position_liquidity via contract: {liquidity}")
            return liquidity
        except Exception as e:
            logger.warning(f"[V4] contract.getPositionLiquidity failed: {e}")

        try:
            # Fallback to raw call
            selector = self.w3.keccak(text='getPositionLiquidity(uint256)')[:4]
            call_data = selector + token_id.to_bytes(32, 'big')

            result = self.w3.eth.call({
                'to': self.position_manager_address,
                'data': call_data
            })

            logger.debug(f"[V4] Raw getPositionLiquidity result: {len(result)} bytes, hex={result.hex()[:64]}...")

            # ABI-encoded uint128 is padded to 32 bytes (uint256)
            # The value is right-aligned, so we read the full 32 bytes as int
            if len(result) >= 32:
                liquidity = int.from_bytes(result[:32], 'big')
                logger.debug(f"[V4] Parsed liquidity from 32 bytes: {liquidity}")
                return liquidity
            elif len(result) >= 16:
                # Fallback for raw uint128 (unlikely but handle it)
                liquidity = int.from_bytes(result, 'big')
                logger.debug(f"[V4] Parsed liquidity from {len(result)} bytes: {liquidity}")
                return liquidity
            return 0
        except Exception as e:
            logger.warning(f"[V4] Raw getPositionLiquidity failed: {e}")
            return 0

    def encode_mint_position(
        self,
        pool_key: PoolKey,
        tick_lower: int,
        tick_upper: int,
        liquidity: int,
        amount0_max: int,
        amount1_max: int,
        recipient: str,
        hook_data: bytes = b''
    ) -> bytes:
        """
        Encode mint position action.

        In V4, this creates an encoded action that can be used
        with modifyLiquidities.
        """
        # Action: MINT_POSITION
        action = self.actions.MINT_POSITION

        # Debug logging (pool_id NOT logged — PoolKey.get_pool_id() is Uniswap-only, wrong for PancakeSwap)
        logger.debug(f"[V4 MINT] Action code: 0x{action:02x}")
        logger.debug(f"[V4 MINT] PoolKey: {pool_key.currency0}/{pool_key.currency1} fee={pool_key.fee} ts={pool_key.tick_spacing}")
        logger.debug(f"[V4 MINT] tick_lower={tick_lower}, tick_upper={tick_upper}")
        logger.debug(f"[V4 MINT] liquidity={liquidity}")
        logger.debug(f"[V4 MINT] amount0_max={amount0_max}, amount1_max={amount1_max}")
        logger.debug(f"[V4 MINT] recipient={recipient}")

        # Encode parameters
        # V4 MINT_POSITION expects PositionConfig as first param:
        # PositionConfig = (PoolKey poolKey, int24 tickLower, int24 tickUpper)
        # PoolKey = (address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)
        position_config = (pool_key.to_tuple(), tick_lower, tick_upper)

        params = encode(
            [
                '((address,address,uint24,int24,address),int24,int24)',  # PositionConfig
                'uint256',    # liquidity
                'uint128',    # amount0Max
                'uint128',    # amount1Max
                'address',    # owner
                'bytes'       # hookData
            ],
            [
                position_config,
                liquidity,
                amount0_max,
                amount1_max,
                Web3.to_checksum_address(recipient),
                hook_data
            ]
        )

        # Return action + params
        return bytes([action]) + params

    def encode_settle_pair(self, currency0: str, currency1: str) -> bytes:
        """Encode settle pair action."""
        action = self.actions.SETTLE_PAIR
        logger.debug(f"[V4 SETTLE_PAIR] Action code: 0x{action:02x}")
        logger.debug(f"[V4 SETTLE_PAIR] currency0={currency0}, currency1={currency1}")
        params = encode(
            ['address', 'address'],
            [Web3.to_checksum_address(currency0), Web3.to_checksum_address(currency1)]
        )
        return bytes([action]) + params

    def encode_take_pair(
        self,
        currency0: str,
        currency1: str,
        recipient: str
    ) -> bytes:
        """Encode take pair action."""
        action = self.actions.TAKE_PAIR
        params = encode(
            ['address', 'address', 'address'],
            [
                Web3.to_checksum_address(currency0),
                Web3.to_checksum_address(currency1),
                Web3.to_checksum_address(recipient)
            ]
        )
        return bytes([action]) + params

    def encode_decrease_liquidity(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        hook_data: bytes = b''
    ) -> bytes:
        """Encode decrease liquidity action."""
        action = self.actions.DECREASE_LIQUIDITY
        params = encode(
            ['uint256', 'uint256', 'uint128', 'uint128', 'bytes'],
            [token_id, liquidity, amount0_min, amount1_min, hook_data]
        )
        return bytes([action]) + params

    def encode_burn_position(
        self,
        token_id: int,
        amount0_min: int,
        amount1_min: int,
        hook_data: bytes = b''
    ) -> bytes:
        """Encode burn position action."""
        action = self.actions.BURN_POSITION
        params = encode(
            ['uint256', 'uint128', 'uint128', 'bytes'],
            [token_id, amount0_min, amount1_min, hook_data]
        )
        return bytes([action]) + params

    def build_mint_action(
        self,
        pool_key: PoolKey,
        tick_lower: int,
        tick_upper: int,
        liquidity: int,
        amount0_max: int,
        amount1_max: int,
        recipient: str
    ) -> bytes:
        """
        Build ONLY the MINT_POSITION action (without SETTLE_PAIR).

        For batching multiple positions, use this method for each position,
        then add ONE SETTLE_PAIR at the end using encode_settle_pair().

        Returns:
            Single encoded MINT_POSITION action
        """
        return self.encode_mint_position(
            pool_key=pool_key,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            liquidity=liquidity,
            amount0_max=amount0_max,
            amount1_max=amount1_max,
            recipient=recipient
        )

    def build_mint_payload(
        self,
        pool_key: PoolKey,
        tick_lower: int,
        tick_upper: int,
        liquidity: int,
        amount0_max: int,
        amount1_max: int,
        recipient: str
    ) -> List[bytes]:
        """
        Build raw actions list for minting a SINGLE position.

        Returns list of raw actions (action_id + params each).
        For single position: MINT_POSITION + SETTLE_PAIR

        WARNING: For multiple positions, use build_mint_action() instead
        and add ONE SETTLE_PAIR at the end!
        """
        actions = []

        # 1. Mint position
        actions.append(self.encode_mint_position(
            pool_key=pool_key,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            liquidity=liquidity,
            amount0_max=amount0_max,
            amount1_max=amount1_max,
            recipient=recipient
        ))

        # 2. Settle tokens (pay for the position)
        actions.append(self.encode_settle_pair(
            pool_key.currency0,
            pool_key.currency1
        ))

        # Return raw actions list (not encoded yet)
        return actions

    def build_close_action(
        self,
        token_id: int,
        liquidity: int,
        burn: bool = False
    ) -> List[bytes]:
        """
        Build DECREASE_LIQUIDITY (+ optional BURN_POSITION) actions for ONE position.

        Does NOT include TAKE_PAIR - use for batching multiple closes.
        Add ONE TAKE_PAIR at the end for each unique token pair.

        Args:
            token_id: Position NFT ID
            liquidity: Amount of liquidity to remove
            burn: If True, burn the NFT after removing liquidity (default: False)

        Returns:
            List of raw action bytes
        """
        actions = []

        # 1. Remove all liquidity
        actions.append(self.encode_decrease_liquidity(
            token_id=token_id,
            liquidity=liquidity,
            amount0_min=0,
            amount1_min=0
        ))

        # 2. Optionally burn the NFT
        if burn:
            actions.append(self.encode_burn_position(
                token_id=token_id,
                amount0_min=0,
                amount1_min=0
            ))

        return actions

    def build_close_position_payload(
        self,
        token_id: int,
        liquidity: int,
        recipient: str,
        currency0: str,
        currency1: str,
        burn: bool = False
    ) -> bytes:
        """
        Build payload for closing a SINGLE position.

        Includes: DECREASE_LIQUIDITY + (optional BURN_POSITION) + TAKE_PAIR
        """
        actions = self.build_close_action(token_id, liquidity, burn=burn)

        # Add TAKE_PAIR to receive tokens
        actions.append(self.encode_take_pair(
            currency0, currency1, recipient
        ))

        return self._encode_actions(actions)

    def build_batch_close_payload(
        self,
        positions: List[dict],
        recipient: str,
        burn: bool = False
    ) -> bytes:
        """
        Build payload for closing MULTIPLE positions in ONE transaction.

        Args:
            positions: List of dicts with {token_id, liquidity, currency0, currency1}
            recipient: Address to receive all tokens
            burn: If True, burn NFTs after removing liquidity (default: False)

        Returns:
            Encoded payload for modifyLiquidities
        """
        all_actions = []
        token_pairs = set()  # Track unique token pairs for TAKE_PAIR

        # Build close actions for each position
        for pos in positions:
            close_actions = self.build_close_action(
                token_id=pos['token_id'],
                liquidity=pos['liquidity'],
                burn=burn
            )
            all_actions.extend(close_actions)

            # Track unique token pairs
            c0 = Web3.to_checksum_address(pos['currency0'])
            c1 = Web3.to_checksum_address(pos['currency1'])
            # Sort to ensure consistent ordering (currency0 < currency1)
            if c0.lower() > c1.lower():
                c0, c1 = c1, c0
            token_pairs.add((c0, c1))

        # Add ONE TAKE_PAIR for each unique token pair
        for c0, c1 in token_pairs:
            all_actions.append(self.encode_take_pair(c0, c1, recipient))

        logger.info(f"[V4] Batch close: {len(positions)} positions, {len(token_pairs)} unique pairs, {len(all_actions)} total actions")

        return self._encode_actions(all_actions)

    def _encode_actions(self, actions: List[bytes]) -> bytes:
        """
        Encode list of actions into unlockData payload.

        V4 format: abi.encode(bytes actions, bytes[] params)
        Where:
        - actions: packed bytes of action IDs (1 byte each)
        - params: array of ABI-encoded params for each action

        Each action in the input list is: action_id (1 byte) + params
        """
        # Extract action IDs and params separately
        action_ids = bytes([a[0] for a in actions])  # First byte of each is action ID
        params_list = [a[1:] for a in actions]  # Rest is params

        # Encode as: abi.encode(bytes, bytes[])
        return encode(['bytes', 'bytes[]'], [action_ids, params_list])

    def mint_position(
        self,
        pool_key: PoolKey,
        tick_lower: int,
        tick_upper: int,
        liquidity: int,
        amount0_max: int,
        amount1_max: int,
        deadline: int = None,
        gas_limit: int = 500000,
        timeout: int = 600
    ) -> MintResult:
        """
        Mint a new position.

        Args:
            pool_key: Pool key
            tick_lower: Lower tick
            tick_upper: Upper tick
            liquidity: Liquidity amount
            amount0_max: Maximum amount of token0
            amount1_max: Maximum amount of token1
            deadline: Transaction deadline
            gas_limit: Gas limit
            timeout: Transaction timeout

        Returns:
            MintResult with details
        """
        if not self.account:
            raise ValueError("Account not set")

        if deadline is None:
            deadline = int(time.time()) + 3600

        # Build actions list and encode
        actions = self.build_mint_payload(
            pool_key=pool_key,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            liquidity=liquidity,
            amount0_max=amount0_max,
            amount1_max=amount1_max,
            recipient=self.account.address
        )
        payload = self._encode_actions(actions)

        # Build transaction
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx = self.contract.functions.modifyLiquidities(
                payload,
                deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price,
                'value': 0  # For native ETH wrapping if needed
            })

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # TX mined — nonce consumed (even if reverted)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            if receipt['status'] != 1:
                raise Exception(f"Mint position reverted! TX: {tx_hash.hex()}")

            # Parse Transfer event to get tokenId
            token_id = self._parse_mint_event(receipt)

            return MintResult(
                token_id=token_id,
                liquidity=liquidity,
                amount0=0,  # Would need to decode from logs
                amount1=0,
                tx_hash=tx_hash.hex()
            )

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def _parse_mint_event(self, receipt) -> int:
        """Parse Transfer event to get minted token ID."""
        try:
            events = self.contract.events.Transfer().process_receipt(receipt)
            for event in events:
                # Transfer from zero address = mint
                if event['args']['from'] == '0x0000000000000000000000000000000000000000':
                    return event['args']['tokenId']
        except Exception:
            pass
        return 0

    def close_position(
        self,
        token_id: int,
        recipient: str = None,
        deadline: int = None,
        gas_limit: int = 500000,
        timeout: int = 600
    ) -> Tuple[str, int, int]:
        """
        Close a position (remove liquidity, collect fees).

        Args:
            token_id: Position NFT ID
            recipient: Recipient of tokens (default: account)
            deadline: Transaction deadline
            gas_limit: Gas limit
            timeout: Transaction timeout

        Returns:
            (tx_hash, amount0, amount1)
        """
        if not self.account:
            raise ValueError("Account not set")

        if recipient is None:
            recipient = self.account.address

        if deadline is None:
            deadline = int(time.time()) + 3600

        # Get position info
        position = self.get_position(token_id)

        # Validate token addresses are not null
        null_address = "0x0000000000000000000000000000000000000000"
        if position.pool_key.currency0 == null_address or position.pool_key.currency1 == null_address:
            raise ValueError(
                f"Cannot close position {token_id}: token addresses unavailable. "
                f"Pool info lookup failed. currency0={position.pool_key.currency0[:10]}..., "
                f"currency1={position.pool_key.currency1[:10]}... "
                "Try specifying the pool ID manually or contact support."
            )

        # Build close payload
        payload = self.build_close_position_payload(
            token_id=token_id,
            liquidity=position.liquidity,
            recipient=recipient,
            currency0=position.pool_key.currency0,
            currency1=position.pool_key.currency1
        )

        # Build transaction
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx = self.contract.functions.modifyLiquidities(
                payload,
                deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price
            })

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # TX mined — nonce consumed (even if reverted)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            if receipt['status'] != 1:
                raise Exception(f"Close position reverted! TX: {tx_hash.hex()}, token_id: {token_id}")

            return tx_hash.hex(), 0, 0  # Amounts would need log parsing

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def close_position_with_tokens(
        self,
        token_id: int,
        currency0: str,
        currency1: str,
        liquidity: int,
        recipient: str = None,
        deadline: int = None,
        gas_limit: int = 500000,
        timeout: int = 600
    ) -> Tuple[str, int, int]:
        """
        Close a position with explicitly provided token addresses.

        Use this when automatic pool info lookup fails but you know the token addresses.

        Args:
            token_id: Position NFT ID
            currency0: Token0 address (must be checksum address)
            currency1: Token1 address (must be checksum address)
            liquidity: Position liquidity amount
            recipient: Recipient of tokens (default: account)
            deadline: Transaction deadline
            gas_limit: Gas limit
            timeout: Transaction timeout

        Returns:
            (tx_hash, amount0, amount1)
        """
        if not self.account:
            raise ValueError("Account not set")

        if recipient is None:
            recipient = self.account.address

        if deadline is None:
            deadline = int(time.time()) + 3600

        # Validate addresses
        currency0 = Web3.to_checksum_address(currency0)
        currency1 = Web3.to_checksum_address(currency1)

        logger.info(f"[V4] Closing position {token_id} with explicit tokens:")
        logger.debug(f"[V4]   currency0: {currency0}")
        logger.debug(f"[V4]   currency1: {currency1}")
        logger.debug(f"[V4]   liquidity: {liquidity}")

        # Build close payload
        payload = self.build_close_position_payload(
            token_id=token_id,
            liquidity=liquidity,
            recipient=recipient,
            currency0=currency0,
            currency1=currency1
        )

        # Build transaction
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx = self.contract.functions.modifyLiquidities(
                payload,
                deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price
            })

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # TX mined — nonce consumed (even if reverted)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            if receipt['status'] != 1:
                raise Exception(f"Close position with tokens reverted! TX: {tx_hash.hex()}, token_id: {token_id}")

            return tx_hash.hex(), 0, 0

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def close_positions_batch(
        self,
        positions: List[dict],
        recipient: str = None,
        deadline: int = None,
        gas_limit: int = None,
        timeout: int = 600
    ) -> Tuple[str, bool, int]:
        """
        Close MULTIPLE positions in ONE transaction.

        This is more gas-efficient than closing positions one by one.

        Args:
            positions: List of dicts with {token_id, liquidity, currency0, currency1}
                       or {token_id, token0, token1, liquidity} (from positions_data)
            recipient: Recipient of all tokens (default: account)
            deadline: Transaction deadline
            gas_limit: Gas limit (auto-calculated if None: 350k per position)
            timeout: Transaction timeout

        Returns:
            (tx_hash, success, gas_used)
        """
        if not self.account:
            raise ValueError("Account not set")

        if not positions:
            raise ValueError("No positions to close")

        if recipient is None:
            recipient = self.account.address

        if deadline is None:
            deadline = int(time.time()) + 3600

        # Calculate gas limit if not provided (350k per position, min 500k)
        if gas_limit is None:
            gas_limit = max(500000, len(positions) * 350000)

        # Normalize position data (handle both currency0/currency1 and token0/token1 keys)
        normalized_positions = []
        for pos in positions:
            normalized = {
                'token_id': pos['token_id'],
                'liquidity': pos.get('liquidity', 0),
                'currency0': pos.get('currency0') or pos.get('token0'),
                'currency1': pos.get('currency1') or pos.get('token1')
            }

            # Validate addresses
            null_addr = "0x0000000000000000000000000000000000000000"
            if not normalized['currency0'] or normalized['currency0'] == null_addr:
                raise ValueError(f"Position {pos['token_id']}: missing currency0 address")
            if not normalized['currency1'] or normalized['currency1'] == null_addr:
                raise ValueError(f"Position {pos['token_id']}: missing currency1 address")

            normalized_positions.append(normalized)

        logger.info(f"[V4] Batch closing {len(normalized_positions)} positions in 1 transaction")
        for pos in normalized_positions:
            logger.debug(f"[V4]   #{pos['token_id']}: {pos['currency0'][:10]}.../{pos['currency1'][:10]}..., liq={pos['liquidity']}")

        # Build batch payload
        payload = self.build_batch_close_payload(normalized_positions, recipient)

        # Build transaction
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx = self.contract.functions.modifyLiquidities(
                payload,
                deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price
            })

            logger.debug(f"[V4] Sending batch close TX, gas limit: {gas_limit}")

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            logger.info(f"[V4] TX sent: {tx_hash.hex()}")

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            gas_used = receipt.get('gasUsed', 0)
            success = receipt['status'] == 1

            # TX mined — nonce consumed (even if reverted)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            if success:
                logger.info(f"[V4] Batch close SUCCESS, gas used: {gas_used}")
            else:
                logger.error(f"[V4] Batch close FAILED, gas used: {gas_used}")

            return tx_hash.hex(), success, gas_used

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def multicall(
        self,
        payloads: List[List[bytes]],
        deadline: int = None,
        gas_limit: int = None,
        timeout: int = 600
    ) -> Tuple[str, List[bytes]]:
        """
        Execute multiple mint operations using modifyLiquidities.

        In V4, we use modifyLiquidities with combined actions.
        Each payload is a list of raw actions from build_mint_payload.

        Args:
            payloads: List of action lists (each from build_mint_payload)
            deadline: Transaction deadline
            gas_limit: Gas limit
            timeout: Transaction timeout

        Returns:
            (tx_hash, results)
        """
        if not self.account:
            raise ValueError("Account not set")

        if deadline is None:
            deadline = int(time.time()) + 3600

        # Combine all actions from all payloads into one list
        all_actions = []
        for action_list in payloads:
            all_actions.extend(action_list)

        logger.info(f"[V4] modifyLiquidities with {len(payloads)} positions, {len(all_actions)} total actions")

        # Encode combined actions into unlockData
        unlock_data = self._encode_actions(all_actions)
        logger.debug(f"[V4] unlockData size: {len(unlock_data)} bytes")

        # Estimate gas if not provided
        if gas_limit is None:
            try:
                estimated = self.contract.functions.modifyLiquidities(
                    unlock_data,
                    deadline
                ).estimate_gas({
                    'from': self.account.address
                })
                gas_limit = int(estimated * 1.3)
                logger.debug(f"[V4] Estimated gas: {estimated}, using {gas_limit}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[V4] Gas estimation failed: {error_msg}")
                raise Exception(f"Gas estimation failed (tx would revert): {error_msg}")

        # Build transaction using modifyLiquidities
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        nonce_handled = False
        try:
            tx = self.contract.functions.modifyLiquidities(
                unlock_data,
                deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price,
                'value': 0
            })

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            logger.info(f"[V4] TX sent: {tx_hash.hex()}")

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # Nonce использован on-chain (TX замайнена, даже если revert)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)
                nonce_handled = True

            if receipt['status'] != 1:
                raise Exception(
                    f"Transaction reverted! TX: {tx_hash.hex()}. "
                    f"Check https://bscscan.com/tx/{tx_hash.hex()} for details."
                )

            logger.info(f"[V4] TX confirmed, gas used: {receipt.get('gasUsed', 'unknown')}")

            return tx_hash.hex(), []

        except Exception as e:
            if self.nonce_manager and not nonce_handled:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def execute_modify_liquidities(
        self,
        unlock_data: bytes,
        deadline: int = None,
        gas_limit: int = None,
        timeout: int = 600
    ) -> Tuple[str, List[bytes]]:
        """
        Execute modifyLiquidities with pre-encoded unlock_data.

        This is the low-level method that takes already-encoded actions.
        Use this when you need full control over action ordering
        (e.g., all MINTs first, then one SETTLE_PAIR at the end).

        Args:
            unlock_data: Pre-encoded actions via _encode_actions()
            deadline: Transaction deadline
            gas_limit: Gas limit
            timeout: Transaction timeout

        Returns:
            (tx_hash, results)
        """
        if not self.account:
            raise ValueError("Account not set")

        if deadline is None:
            deadline = int(time.time()) + 3600

        logger.debug(f"[V4] execute_modify_liquidities: unlockData size = {len(unlock_data)} bytes")

        # Estimate gas if not provided
        if gas_limit is None:
            try:
                estimated = self.contract.functions.modifyLiquidities(
                    unlock_data,
                    deadline
                ).estimate_gas({
                    'from': self.account.address
                })
                gas_limit = int(estimated * 1.3)
                logger.debug(f"[V4] Estimated gas: {estimated}, using {gas_limit}")
            except Exception as e:
                # Gas estimation failure means transaction WILL revert
                error_msg = str(e)
                logger.error(f"[V4] Gas estimation failed (tx would revert): {error_msg}")
                # Don't proceed if gas estimation fails - it means something is wrong
                raise Exception(f"Gas estimation failed (tx would revert): {error_msg}")

        # Build transaction
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        nonce_handled = False
        try:
            tx = self.contract.functions.modifyLiquidities(
                unlock_data,
                deadline
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price,
                'value': 0
            })

            # Sign and send
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            logger.info(f"[V4] TX sent: {tx_hash.hex()}")

            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # Nonce использован on-chain (TX замайнена, даже если revert)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)
                nonce_handled = True

            if receipt['status'] != 1:
                raise Exception(
                    f"Transaction reverted! TX: {tx_hash.hex()}. "
                    f"Check https://bscscan.com/tx/{tx_hash.hex()} for details."
                )

            logger.info(f"[V4] TX confirmed, gas used: {receipt.get('gasUsed', 'unknown')}")

            return tx_hash.hex(), []

        except Exception as e:
            if self.nonce_manager and not nonce_handled:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    # ============== Wallet Scanning Methods ==============

    def get_owner_of(self, token_id: int) -> Optional[str]:
        """
        Get owner of NFT position.

        Args:
            token_id: NFT token ID

        Returns:
            Owner address or None if position doesn't exist (burned)
        """
        try:
            owner = self.contract.functions.ownerOf(token_id).call()
            return owner
        except Exception:
            # ownerOf throws if token doesn't exist (burned)
            return None

    def is_position_owned_by(self, token_id: int, address: str) -> bool:
        """
        Check if position is owned by specified address.

        Args:
            token_id: NFT token ID
            address: Address to check

        Returns:
            True if position is owned by address
        """
        owner = self.get_owner_of(token_id)
        if owner is None:
            return False
        return owner.lower() == address.lower()

    def get_positions_count(self, address: str) -> int:
        """
        Get number of positions owned by address.

        Args:
            address: Wallet address

        Returns:
            Number of NFT positions
        """
        try:
            return self.contract.functions.balanceOf(
                Web3.to_checksum_address(address)
            ).call()
        except Exception:
            return 0

    def get_position_token_ids(self, address: str) -> List[int]:
        """
        Get all token IDs owned by address.

        First tries ERC721Enumerable.tokenOfOwnerByIndex for enumeration.
        Falls back to Transfer event scanning if Enumerable is not supported.

        Args:
            address: Wallet address

        Returns:
            List of token IDs owned by address
        """
        token_ids = []
        address_checksum = Web3.to_checksum_address(address)

        try:
            # Get balance first
            balance = self.contract.functions.balanceOf(address_checksum).call()
            logger.debug(f"[V4] Wallet {address_checksum[:8]}... has {balance} NFTs")

            if balance == 0:
                return []

            # Try ERC721Enumerable first (faster if supported)
            try:
                logger.debug(f"[V4] Trying tokenOfOwnerByIndex for {balance} NFTs...")
                for i in range(balance):
                    token_id = self.contract.functions.tokenOfOwnerByIndex(
                        address_checksum, i
                    ).call()
                    token_ids.append(token_id)
                    if i < 5 or i == balance - 1:  # Log first 5 and last
                        logger.debug(f"[V4]   Index {i}: token_id = {token_id}")
                logger.debug(f"[V4] Found {len(token_ids)} positions via ERC721Enumerable")
                return token_ids
            except Exception as enum_error:
                logger.warning(f"[V4] tokenOfOwnerByIndex FAILED at index {len(token_ids)}: {enum_error}")
                logger.debug(f"[V4] Falling back to Transfer event scanning...")
                token_ids = []  # Reset in case partial success

            # Fallback 1: Try BSCScan API (fast, reliable)
            token_ids = self._get_tokens_via_bscscan(address_checksum)
            if token_ids:
                logger.debug(f"[V4] Found {len(token_ids)} positions via BSCScan API")
                return token_ids

            # Fallback 2: Scan Transfer events (slow, may hit RPC limits)
            logger.debug(f"[V4] BSCScan failed, trying Transfer event scanning...")
            token_ids = self._scan_transfer_events(address_checksum, balance)

        except Exception as e:
            logger.error(f"[V4] Error scanning wallet positions: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        return token_ids

    def _get_tokens_via_bscscan(self, address: str) -> List[int]:
        """
        Get NFT token IDs via BSCScan API.

        This is faster and more reliable than scanning Transfer events.
        Works without RPC limits.
        """
        import requests

        token_ids = []

        try:
            # Select block explorer API by chain
            # Basescan V1 deprecated; use Blockscout for BASE
            EXPLORER_APIS = {
                56: "https://api.bscscan.com/api",
                1: "https://api.etherscan.io/api",
                8453: "https://base.blockscout.com/api",
            }
            api_url = EXPLORER_APIS.get(self.chain_id, "https://api.bscscan.com/api")

            params = {
                'module': 'account',
                'action': 'tokennfttx',
                'contractaddress': self.position_manager_address,
                'address': address,
                'page': 1,
                'offset': 1000,  # Max 1000 results
                'sort': 'asc'  # Oldest first — so last write per token_id = newest transfer
            }

            logger.debug(f"[V4] Querying BSCScan API for NFTs...")
            response = requests.get(api_url, params=params, timeout=10, proxies=self.proxy or {})
            data = response.json()

            if data.get('status') == '1' and data.get('result'):
                transfers = data['result']
                logger.debug(f"[V4] BSCScan returned {len(transfers)} transfer records")

                # Get unique token IDs where we are the current owner
                # (received but not sent away)
                received = {}  # tokenId -> last transfer direction

                for tx in transfers:
                    token_id = int(tx['tokenID'])
                    to_addr = tx['to'].lower()
                    from_addr = tx['from'].lower()
                    my_addr = address.lower()

                    if to_addr == my_addr:
                        received[token_id] = 'in'
                    elif from_addr == my_addr:
                        received[token_id] = 'out'

                # Keep only tokens we still own (last transfer was IN)
                for token_id, direction in received.items():
                    if direction == 'in':
                        token_ids.append(token_id)

                logger.debug(f"[V4] Found {len(token_ids)} NFTs currently owned")
            else:
                msg = data.get('message', 'Unknown error')
                logger.warning(f"[V4] BSCScan API returned no results: {msg}")

        except Exception as e:
            logger.warning(f"[V4] BSCScan API error: {e}")

        return token_ids

    def _scan_transfer_events(self, address: str, expected_count: int) -> List[int]:
        """
        Scan Transfer events to find all token IDs owned by address.

        This is a fallback when ERC721Enumerable is not supported.

        Args:
            address: Wallet address (checksum)
            expected_count: Expected number of tokens (from balanceOf)

        Returns:
            List of token IDs currently owned by address
        """
        token_ids = []

        try:
            # Get Transfer event signature
            transfer_topic = self.w3.keccak(text='Transfer(address,address,uint256)').hex()

            # Pad address to 32 bytes for topic filter
            address_padded = '0x' + address[2:].lower().zfill(64)

            # Scan for transfers TO this address (mints and incoming transfers)
            # Use very small chunks to avoid BSC public RPC limits
            current_block = self.w3.eth.block_number
            total_range = 20000  # Last ~17 hours on BSC (enough for recent positions)
            chunk_size = 1000    # BSC public RPC is very restrictive
            from_block = current_block - total_range

            logger.debug(f"[V4] Scanning Transfer events in chunks of {chunk_size} blocks...")
            logger.debug(f"[V4] Total range: {from_block} to {current_block}")

            # Get transfers TO this address in chunks
            logs = []
            for chunk_start in range(from_block, current_block, chunk_size):
                chunk_end = min(chunk_start + chunk_size - 1, current_block)
                try:
                    chunk_logs = self.w3.eth.get_logs({
                        'address': self.position_manager_address,
                        'topics': [
                            transfer_topic,
                            None,  # from (any)
                            address_padded  # to (our address)
                        ],
                        'fromBlock': chunk_start,
                        'toBlock': chunk_end
                    })
                    logs.extend(chunk_logs)
                    if chunk_logs:
                        logger.debug(f"[V4]   Blocks {chunk_start}-{chunk_end}: found {len(chunk_logs)} events")
                except Exception as chunk_err:
                    # Try even smaller chunks if limit exceeded
                    if 'limit exceeded' in str(chunk_err).lower():
                        logger.debug(f"[V4]   Blocks {chunk_start}-{chunk_end}: limit exceeded, trying smaller chunks...")
                        for mini_start in range(chunk_start, chunk_end, 200):
                            mini_end = min(mini_start + 199, chunk_end)
                            try:
                                mini_logs = self.w3.eth.get_logs({
                                    'address': self.position_manager_address,
                                    'topics': [
                                        transfer_topic,
                                        None,
                                        address_padded
                                    ],
                                    'fromBlock': mini_start,
                                    'toBlock': mini_end
                                })
                                logs.extend(mini_logs)
                                if mini_logs:
                                    logger.debug(f"[V4]     Mini {mini_start}-{mini_end}: found {len(mini_logs)} events")
                            except Exception as mini_err:
                                logger.warning(f"[V4]     Mini {mini_start}-{mini_end}: ERROR {mini_err}")
                    else:
                        logger.warning(f"[V4]   Blocks {chunk_start}-{chunk_end}: ERROR {chunk_err}")

            logger.debug(f"[V4] Found {len(logs)} Transfer events TO address")

            # Extract candidate token IDs
            candidate_ids = set()
            for idx, log in enumerate(logs):
                # tokenId is the 3rd topic (indexed) or in data if not indexed
                try:
                    if len(log['topics']) >= 4:
                        token_id = int(log['topics'][3].hex(), 16)
                        if idx < 3:
                            logger.debug(f"[V4]   Log {idx}: tokenId from topics[3] = {token_id}")
                    else:
                        # tokenId might be in data
                        data_hex = log['data'].hex() if hasattr(log['data'], 'hex') else log['data']
                        token_id = int(data_hex, 16)
                        if idx < 3:
                            logger.debug(f"[V4]   Log {idx}: tokenId from data = {token_id}")
                    candidate_ids.add(token_id)
                except Exception as parse_err:
                    logger.warning(f"[V4]   Log {idx}: PARSE ERROR: {parse_err}")
                    logger.debug(f"[V4]     topics: {[t.hex() if hasattr(t, 'hex') else t for t in log['topics']]}")
                    logger.debug(f"[V4]     data: {log['data']}")

            logger.debug(f"[V4] Found {len(candidate_ids)} candidate token IDs")

            # Verify current ownership for each candidate
            for token_id in candidate_ids:
                try:
                    owner = self.contract.functions.ownerOf(token_id).call()
                    if owner.lower() == address.lower():
                        token_ids.append(token_id)
                except Exception:
                    # Token was burned or transferred away
                    pass

            logger.debug(f"[V4] Verified {len(token_ids)} tokens still owned (expected {expected_count})")

            # If we didn't find all expected tokens, try extending the block range
            if len(token_ids) < expected_count:
                logger.warning(f"[V4] Warning: Found only {len(token_ids)} of {expected_count} expected tokens")
                logger.warning(f"[V4] Some tokens may have been minted before block {from_block}")

        except Exception as e:
            logger.error(f"[V4] Error scanning Transfer events: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        return token_ids

    def scan_wallet_positions(self, address: str) -> List[dict]:
        """
        Scan wallet and get info for all positions.

        Args:
            address: Wallet address

        Returns:
            List of dicts with position info
        """
        positions = []
        token_ids = self.get_position_token_ids(address)

        for token_id in token_ids:
            try:
                position = self.get_position(token_id)
                position_dict = {
                    'token_id': token_id,
                    'pool_key': position.pool_key,
                    'tick_lower': position.tick_lower,
                    'tick_upper': position.tick_upper,
                    'liquidity': position.liquidity,
                    # V4 doesn't have tokens_owed in position struct
                    'tokens_owed0': 0,
                    'tokens_owed1': 0,
                    # Add currency info from pool_key
                    'token0': position.pool_key.currency0,
                    'token1': position.pool_key.currency1,
                    'fee': position.pool_key.fee,
                }
                positions.append(position_dict)
            except Exception as e:
                logger.error(f"[V4] Error getting position {token_id}: {e}")

        return positions
