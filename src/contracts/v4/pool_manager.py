"""
V4 PoolManager Wrapper

Handles pool initialization and state queries for V4.
Includes off-chain fee calculation (ported from web backend).
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from web3 import Web3
from eth_abi import encode, decode

from .abis import V4_POOL_MANAGER_ABI, V4_STATE_VIEW_ABI
from .constants import V4Protocol, get_v4_addresses, suggest_tick_spacing

logger = logging.getLogger(__name__)


@dataclass
class PoolKey:
    """V4 Pool Key - uniquely identifies a pool."""
    currency0: str  # Token address (lower address)
    currency1: str  # Token address (higher address)
    fee: int        # Fee in hundredths of a bip (0-1,000,000)
    tick_spacing: int  # Tick spacing
    hooks: str = "0x0000000000000000000000000000000000000000"  # Hooks address
    _truncated_pool_id: Optional[bytes] = field(default=None, repr=False)  # Set when reconstructed from truncated ID

    def to_tuple(self) -> tuple:
        """Convert to tuple for contract calls."""
        return (
            Web3.to_checksum_address(self.currency0),
            Web3.to_checksum_address(self.currency1),
            self.fee,
            self.tick_spacing,
            Web3.to_checksum_address(self.hooks)
        )

    def to_pancake_tuple(self, pool_manager_address: str) -> tuple:
        """Convert to PancakeSwap V4 PoolKey tuple.

        PancakeSwap V4 PoolKey: (currency0, currency1, hooks, poolManager, fee, parameters)
        where parameters = bytes32(tickSpacing << 16)
        """
        ts = self.tick_spacing
        if ts >= 0:
            params_int = ts << 16
        else:
            params_int = ((1 << 256) + ts) << 16
        parameters = params_int.to_bytes(32, 'big')
        return (
            Web3.to_checksum_address(self.currency0),
            Web3.to_checksum_address(self.currency1),
            Web3.to_checksum_address(self.hooks),
            Web3.to_checksum_address(pool_manager_address),
            self.fee,
            parameters
        )

    def get_pool_id(self) -> bytes:
        """Calculate pool ID for Uniswap V4 (keccak256 of encoded pool key).
        NOTE: For PancakeSwap V4, use V4PoolManager._compute_pool_id() instead —
        PancakeSwap uses a different PoolKey encoding format.
        """
        encoded = encode(
            ['address', 'address', 'uint24', 'int24', 'address'],
            list(self.to_tuple())
        )
        return Web3.keccak(encoded)

    @classmethod
    def from_tokens(
        cls,
        token0: str,
        token1: str,
        fee: int,
        tick_spacing: int = None,
        hooks: str = None
    ) -> 'PoolKey':
        """
        Create PoolKey from token addresses.

        Automatically sorts tokens by address (required for V4).
        """
        addr0 = Web3.to_checksum_address(token0)
        addr1 = Web3.to_checksum_address(token1)

        # Ensure correct order (lower address first)
        if int(addr0, 16) > int(addr1, 16):
            addr0, addr1 = addr1, addr0

        if tick_spacing is None:
            tick_spacing = suggest_tick_spacing(fee / 10000)

        return cls(
            currency0=addr0,
            currency1=addr1,
            fee=fee,
            tick_spacing=tick_spacing,
            hooks=hooks or "0x0000000000000000000000000000000000000000"
        )


@dataclass
class V4PoolState:
    """V4 Pool state information."""
    pool_id: bytes
    sqrt_price_x96: int
    tick: int
    liquidity: int
    protocol_fee: int
    lp_fee: int
    initialized: bool


class V4PoolManager:
    """
    V4 PoolManager wrapper.

    Supports both Uniswap V4 and PancakeSwap V4 (Infinity).
    """

    def __init__(
        self,
        w3: Web3,
        protocol: V4Protocol = V4Protocol.PANCAKESWAP,
        chain_id: int = 56,
        pool_manager_address: str = None
    ):
        self.w3 = w3
        self.protocol = protocol
        self.chain_id = chain_id
        self.state_view_contract = None
        self.state_view_address = None

        # Get addresses from constants or use provided
        addresses = get_v4_addresses(chain_id, protocol)

        if pool_manager_address:
            self.pool_manager_address = Web3.to_checksum_address(pool_manager_address)
        else:
            if not addresses:
                raise ValueError(f"No V4 addresses found for chain {chain_id} and protocol {protocol}")
            self.pool_manager_address = Web3.to_checksum_address(addresses.pool_manager)

        self.contract = w3.eth.contract(
            address=self.pool_manager_address,
            abi=V4_POOL_MANAGER_ABI
        )

        # Initialize StateView contract if available (Uniswap V4)
        if addresses and addresses.state_view:
            self.state_view_address = Web3.to_checksum_address(addresses.state_view)
            self.state_view_contract = w3.eth.contract(
                address=self.state_view_address,
                abi=V4_STATE_VIEW_ABI
            )

    def _compute_pool_id(self, pool_key: PoolKey) -> bytes:
        """
        Compute pool ID respecting protocol differences.

        Uniswap V4 PoolKey:     (currency0, currency1, fee, tickSpacing, hooks)
        PancakeSwap V4 PoolKey:  (currency0, currency1, hooks, poolManager, fee, parameters)
            where parameters = bytes32(uint256(int256(tickSpacing)) << 16)
        """
        if self.protocol == V4Protocol.PANCAKESWAP:
            # PancakeSwap V4: PoolKey has different field order and includes poolManager + parameters
            # IMPORTANT: parameters = bytes32(tickSpacing << 16), NOT just tickSpacing!
            # Must match to_pancake_tuple() encoding for consistent pool ID computation.
            tick_spacing = pool_key.tick_spacing
            if tick_spacing >= 0:
                params_int = tick_spacing << 16
            else:
                params_int = ((1 << 256) + tick_spacing) << 16
            parameters = params_int.to_bytes(32, 'big')

            encoded = encode(
                ['address', 'address', 'address', 'address', 'uint24', 'bytes32'],
                [
                    Web3.to_checksum_address(pool_key.currency0),
                    Web3.to_checksum_address(pool_key.currency1),
                    Web3.to_checksum_address(pool_key.hooks),
                    self.pool_manager_address,
                    pool_key.fee,
                    parameters
                ]
            )
            return Web3.keccak(encoded)
        else:
            # Uniswap V4: standard PoolKey encoding
            return pool_key.get_pool_id()

    def get_pool_state(self, pool_key: PoolKey) -> V4PoolState:
        """
        Get pool state information.

        Args:
            pool_key: The pool key

        Returns:
            V4PoolState with current pool state
        """
        pool_id = self._compute_pool_id(pool_key)

        try:
            target = self.state_view_contract or self.contract
            target_addr = target.address

            # Batch: getSlot0 + getLiquidity in 1 Multicall3 call (2 RPC → 1)
            from src.utils import BatchRPC
            batch = BatchRPC(self.w3)
            batch.add_v4_slot0(target_addr, pool_id)
            batch.add_v4_liquidity(target_addr, pool_id)
            results = batch.execute()

            slot0_data = results[0]
            liquidity = results[1] if results[1] is not None else 0

            if slot0_data:
                sqrt_price_x96 = slot0_data['sqrtPriceX96']
                tick = slot0_data['tick']
                protocol_fee = slot0_data['protocol_fee']
                lp_fee = slot0_data['lp_fee']
            else:
                # Multicall decoded nothing — fallback to sequential
                raise ValueError("Batch slot0 decode returned None")

            return V4PoolState(
                pool_id=pool_id,
                sqrt_price_x96=sqrt_price_x96,
                tick=tick,
                liquidity=liquidity,
                protocol_fee=protocol_fee,
                lp_fee=lp_fee,
                initialized=sqrt_price_x96 > 0
            )
        except Exception as e:
            # Fallback: sequential calls (e.g. Multicall3 not available)
            try:
                target = self.state_view_contract or self.contract
                slot0 = target.functions.getSlot0(pool_id).call()
                liq = target.functions.getLiquidity(pool_id).call()
                return V4PoolState(
                    pool_id=pool_id,
                    sqrt_price_x96=slot0[0],
                    tick=slot0[1],
                    liquidity=liq,
                    protocol_fee=slot0[2],
                    lp_fee=slot0[3],
                    initialized=slot0[0] > 0
                )
            except Exception as e2:
                logger.error(f"[V4 PoolManager] getSlot0 failed: {e2}")
                return V4PoolState(
                    pool_id=pool_id,
                    sqrt_price_x96=0,
                    tick=0,
                    liquidity=0,
                    protocol_fee=0,
                    lp_fee=0,
                    initialized=False
                )

    def is_pool_initialized(self, pool_key: PoolKey) -> bool:
        """Check if a pool is initialized."""
        state = self.get_pool_state(pool_key)
        return state.initialized

    def encode_initialize(
        self,
        pool_key: PoolKey,
        sqrt_price_x96: int
    ) -> bytes:
        """
        Encode pool initialization call.

        Args:
            pool_key: The pool key
            sqrt_price_x96: Initial sqrt price

        Returns:
            Encoded call data
        """
        return self.contract.functions.initialize(
            pool_key.to_tuple(),
            sqrt_price_x96
        )._encode_transaction_data()

    def price_to_sqrt_price_x96(
        self,
        price: float,
        token0_decimals: int = 18,
        token1_decimals: int = 18
    ) -> int:
        """
        Convert human-readable price to sqrtPriceX96.

        Args:
            price: Price (token1 per token0)
            token0_decimals: Decimals of token0
            token1_decimals: Decimals of token1

        Returns:
            sqrtPriceX96
        """
        # Adjust for decimals
        adjusted_price = price * (10 ** (token1_decimals - token0_decimals))
        sqrt_price = math.sqrt(adjusted_price)
        return int(sqrt_price * (2 ** 96))

    def sqrt_price_x96_to_price(
        self,
        sqrt_price_x96: int,
        token0_decimals: int = 18,
        token1_decimals: int = 18
    ) -> float:
        """
        Convert sqrtPriceX96 to human-readable price.

        Args:
            sqrt_price_x96: The sqrt price
            token0_decimals: Decimals of token0
            token1_decimals: Decimals of token1

        Returns:
            Price (token1 per token0)
        """
        sqrt_price = sqrt_price_x96 / (2 ** 96)
        price = sqrt_price ** 2
        return price / (10 ** (token1_decimals - token0_decimals))

    def get_current_price(
        self,
        pool_key: PoolKey,
        token0_decimals: int = 18,
        token1_decimals: int = 18
    ) -> Optional[float]:
        """
        Get current pool price in human-readable format.

        Returns:
            Price or None if pool not initialized
        """
        state = self.get_pool_state(pool_key)
        if not state.initialized:
            return None

        return self.sqrt_price_x96_to_price(
            state.sqrt_price_x96,
            token0_decimals,
            token1_decimals
        )

    def get_exact_pool_fee(self, pool_key: PoolKey) -> Optional[int]:
        """
        Get the exact LP fee from an existing pool.

        This is useful when the user enters a fee percentage that might
        have precision issues (e.g., 3.8998% vs 3.9%).

        Args:
            pool_key: The pool key (fee field might be approximate)

        Returns:
            Exact LP fee from the pool, or None if pool doesn't exist
        """
        state = self.get_pool_state(pool_key)
        if not state.initialized:
            return None
        return state.lp_fee

    def get_pool_state_by_id(self, pool_id: bytes) -> V4PoolState:
        """
        Get pool state by pool ID directly.

        For Uniswap V4, uses StateView contract (which already knows PoolManager).
        For PancakeSwap V4, queries PoolManager directly.

        Args:
            pool_id: The pool ID (bytes32)

        Returns:
            V4PoolState with current pool state
        """
        try:
            # Use StateView if available (Uniswap V4)
            if self.state_view_contract:
                logger.debug(f"[V4] Using StateView at {self.state_view_address}")
                logger.debug(f"[V4] Pool ID: 0x{pool_id.hex()}")
                # StateView already knows PoolManager address (via ImmutableState)
                # so we only pass poolId
                slot0 = self.state_view_contract.functions.getSlot0(pool_id).call()
                logger.debug(f"[V4] slot0 result: {slot0}")
                liquidity = self.state_view_contract.functions.getLiquidity(pool_id).call()
                logger.debug(f"[V4] liquidity: {liquidity}")
            else:
                # Direct query to PoolManager (PancakeSwap V4)
                logger.debug(f"[V4] Direct query to PoolManager: {self.pool_manager_address}")
                slot0 = self.contract.functions.getSlot0(pool_id).call()
                liquidity = self.contract.functions.getLiquidity(pool_id).call()

            sqrt_price_x96 = slot0[0]
            tick = slot0[1]
            protocol_fee = slot0[2]
            lp_fee = slot0[3]

            return V4PoolState(
                pool_id=pool_id,
                sqrt_price_x96=sqrt_price_x96,
                tick=tick,
                liquidity=liquidity,
                protocol_fee=protocol_fee,
                lp_fee=lp_fee,
                initialized=sqrt_price_x96 > 0
            )
        except Exception as e:
            logger.error(f"[V4] ERROR querying pool state: {e}", exc_info=True)
            return V4PoolState(
                pool_id=pool_id,
                sqrt_price_x96=0,
                tick=0,
                liquidity=0,
                protocol_fee=0,
                lp_fee=0,
                initialized=False
            )


# ── V4 Fee Calculation (off-chain) ────────────────────────────────────


def compute_position_id(
    owner: str, tick_lower: int, tick_upper: int, salt: int
) -> bytes:
    """
    Compute V4 positionId = keccak256(abi.encodePacked(owner, tickLower, tickUpper, salt)).

    Args:
        owner: PositionManager contract address (NOT the wallet)
        tick_lower: Lower tick (signed int24)
        tick_upper: Upper tick (signed int24)
        salt: NFT token ID (used as bytes32 salt)

    Returns:
        bytes32 position ID
    """
    owner_bytes = bytes.fromhex(owner[2:] if owner.startswith('0x') else owner)  # 20 bytes

    def int24_to_bytes(val: int) -> bytes:
        if val < 0:
            val = (1 << 24) + val  # two's complement
        return val.to_bytes(3, 'big')

    packed = (
        owner_bytes                          # 20 bytes
        + int24_to_bytes(tick_lower)         # 3 bytes
        + int24_to_bytes(tick_upper)         # 3 bytes
        + salt.to_bytes(32, 'big')           # 32 bytes
    )  # total: 58 bytes
    return Web3.keccak(packed)


def calculate_unclaimed_fees(
    fee_growth_global0: int, fee_growth_global1: int,
    fg_outside0_lower: int, fg_outside1_lower: int,
    fg_outside0_upper: int, fg_outside1_upper: int,
    fg_inside0_last: int, fg_inside1_last: int,
    liquidity: int, current_tick: int,
    tick_lower: int, tick_upper: int,
) -> Tuple[int, int]:
    """
    Calculate unclaimed fees for a V4 position (pure math, no RPC).

    Mirrors Uniswap V4 fee accounting exactly:
    feeGrowthInside = global - below - above
    fees = (feeGrowthInside - feeGrowthInsideLast) * liquidity / Q128

    Returns:
        (fees0_raw, fees1_raw) in wei
    """
    MOD = 1 << 256
    Q128 = 1 << 128

    # feeGrowthBelow
    if current_tick >= tick_lower:
        fg_below0, fg_below1 = fg_outside0_lower, fg_outside1_lower
    else:
        fg_below0 = (fee_growth_global0 - fg_outside0_lower) % MOD
        fg_below1 = (fee_growth_global1 - fg_outside1_lower) % MOD

    # feeGrowthAbove
    if current_tick < tick_upper:
        fg_above0, fg_above1 = fg_outside0_upper, fg_outside1_upper
    else:
        fg_above0 = (fee_growth_global0 - fg_outside0_upper) % MOD
        fg_above1 = (fee_growth_global1 - fg_outside1_upper) % MOD

    # feeGrowthInside
    fg_inside0 = (fee_growth_global0 - fg_below0 - fg_above0) % MOD
    fg_inside1 = (fee_growth_global1 - fg_below1 - fg_above1) % MOD

    # Unclaimed fees (raw wei)
    fees0 = ((fg_inside0 - fg_inside0_last) % MOD) * liquidity // Q128
    fees1 = ((fg_inside1 - fg_inside1_last) % MOD) * liquidity // Q128
    return fees0, fees1


def get_v4_unclaimed_fees(
    w3: Web3,
    positions: List[dict],
    chain_id: int,
    protocol: V4Protocol,
) -> Dict[int, Tuple[int, int]]:
    """
    Batch-read fee accumulators and compute unclaimed fees for V4 positions.

    Args:
        w3: Web3 instance
        positions: List of position dicts with keys:
            token_id, pool_key (PoolKey), tick_lower, tick_upper, liquidity
        chain_id: Chain ID
        protocol: V4Protocol (UNISWAP or PANCAKESWAP)

    Returns:
        {token_id: (fees0_raw, fees1_raw)} — raw wei amounts
    """
    if not positions:
        return {}

    from ...utils import BatchRPC

    addresses = get_v4_addresses(chain_id, protocol)
    if not addresses:
        logger.warning(f"[V4 Fees] No addresses for chain={chain_id}, protocol={protocol}")
        return {}

    # Pick target contract: StateView (Uniswap) or PoolManager (PancakeSwap)
    if protocol == V4Protocol.UNISWAP and addresses.state_view:
        fee_target = Web3.to_checksum_address(addresses.state_view)
        tick_fn_selector = bytes.fromhex('7c40f1fe')   # getTickInfo(bytes32, int24)
    else:
        fee_target = Web3.to_checksum_address(addresses.pool_manager)
        tick_fn_selector = bytes.fromhex('5aa208a4')   # getPoolTickInfo(bytes32, int24)

    fee_globals_selector = bytes.fromhex('9ec538c8')    # getFeeGrowthGlobals(bytes32)
    slot0_selector = bytes.fromhex('c815641c')          # getSlot0(bytes32)
    pos_info_selector = bytes.fromhex('97fd7b42')       # getPositionInfo(bytes32, bytes32)

    pm_address = Web3.to_checksum_address(addresses.position_manager)

    # Build a PoolManager instance to compute pool IDs
    pool_mgr = V4PoolManager(w3, protocol=protocol, chain_id=chain_id)

    # Group positions by pool_key tuple for deduplication
    pool_groups: Dict[tuple, List[dict]] = {}
    for pos in positions:
        pk = pos['pool_key']
        key = (pk.currency0.lower(), pk.currency1.lower(), pk.fee, pk.tick_spacing, pk.hooks.lower())
        pool_groups.setdefault(key, []).append(pos)

    # ── Build batch calls ──────────────────────────────────────────
    batch = BatchRPC(w3)
    call_map = []  # tracks what each call index means

    for pool_key_tuple, group in pool_groups.items():
        pk = group[0]['pool_key']
        pool_id = pool_mgr._compute_pool_id(pk)
        pool_id_padded = pool_id if len(pool_id) == 32 else pool_id.rjust(32, b'\x00')

        # 1) getFeeGrowthGlobals(poolId)
        batch.add_call(
            fee_target,
            fee_globals_selector + pool_id_padded,
            _decode_fee_growth_globals,
        )
        call_map.append(('fee_globals', pool_key_tuple))

        # 2) getSlot0(poolId) — need current tick
        batch.add_call(
            fee_target,
            slot0_selector + pool_id_padded,
            _decode_slot0_tick,
        )
        call_map.append(('slot0', pool_key_tuple))

        # 3) getTickInfo for unique ticks in this pool group
        unique_ticks = set()
        for p in group:
            unique_ticks.add(p['tick_lower'])
            unique_ticks.add(p['tick_upper'])

        for tick in sorted(unique_ticks):
            tick_encoded = _encode_int24(tick)
            batch.add_call(
                fee_target,
                tick_fn_selector + pool_id_padded + tick_encoded,
                _decode_tick_info,
            )
            call_map.append(('tick', pool_key_tuple, tick))

        # 4) getPositionInfo(poolId, positionId) for each position
        for p in group:
            position_id = compute_position_id(
                pm_address, p['tick_lower'], p['tick_upper'], p['token_id']
            )
            batch.add_call(
                fee_target,
                pos_info_selector + pool_id_padded + position_id,
                _decode_position_info,
            )
            call_map.append(('pos', pool_key_tuple, p['token_id']))

    # ── Execute batch ──────────────────────────────────────────────
    try:
        results = batch.execute()
    except Exception as e:
        logger.error(f"[V4 Fees] Batch RPC failed: {e}")
        return {}

    # ── Parse results into lookup dicts ────────────────────────────
    pool_fee_globals: Dict[tuple, Tuple[int, int]] = {}
    pool_current_tick: Dict[tuple, int] = {}
    tick_info: Dict[tuple, Dict[int, Tuple[int, int]]] = {}   # pool_key -> {tick: (fg0, fg1)}
    pos_fee_inside: Dict[int, Tuple[int, int, int]] = {}      # token_id -> (liq, fg0last, fg1last)

    for i, entry in enumerate(call_map):
        val = results[i] if i < len(results) else None
        if val is None:
            continue

        if entry[0] == 'fee_globals':
            pool_fee_globals[entry[1]] = val
        elif entry[0] == 'slot0':
            pool_current_tick[entry[1]] = val
        elif entry[0] == 'tick':
            pkt = entry[1]
            tick_info.setdefault(pkt, {})[entry[2]] = val
        elif entry[0] == 'pos':
            pos_fee_inside[entry[2]] = val

    # ── Compute fees per position ──────────────────────────────────
    fees_result: Dict[int, Tuple[int, int]] = {}

    for pool_key_tuple, group in pool_groups.items():
        fg = pool_fee_globals.get(pool_key_tuple)
        ct = pool_current_tick.get(pool_key_tuple)
        tinfo = tick_info.get(pool_key_tuple, {})

        if fg is None or ct is None:
            logger.warning(f"[V4 Fees] Missing globals/slot0 for pool {pool_key_tuple[0][:10]}.../{pool_key_tuple[1][:10]}...")
            for p in group:
                fees_result[p['token_id']] = (0, 0)
            continue

        fg0_global, fg1_global = fg

        for p in group:
            tid = p['token_id']
            pfi = pos_fee_inside.get(tid)
            lower_info = tinfo.get(p['tick_lower'])
            upper_info = tinfo.get(p['tick_upper'])

            if pfi is None or lower_info is None or upper_info is None:
                logger.warning(f"[V4 Fees] Missing data for token {tid}")
                fees_result[tid] = (0, 0)
                continue

            liq_from_pool, fg_inside0_last, fg_inside1_last = pfi
            pos_liquidity = p.get('liquidity', 0) or liq_from_pool

            if pos_liquidity == 0:
                fees_result[tid] = (0, 0)
                continue

            fees_result[tid] = calculate_unclaimed_fees(
                fg0_global, fg1_global,
                lower_info[0], lower_info[1],  # fg_outside0/1 lower
                upper_info[0], upper_info[1],  # fg_outside0/1 upper
                fg_inside0_last, fg_inside1_last,
                pos_liquidity, ct,
                p['tick_lower'], p['tick_upper'],
            )

    return fees_result


# ── Batch RPC decoders (private) ──────────────────────────────────────


def _encode_int24(val: int) -> bytes:
    """Encode int24 as ABI-padded bytes32."""
    if val < 0:
        val = (1 << 256) + val
    return val.to_bytes(32, 'big')


def _decode_fee_growth_globals(data: bytes) -> Tuple[int, int]:
    """Decode (uint256, uint256) → (feeGrowthGlobal0, feeGrowthGlobal1)."""
    if len(data) < 64:
        return None
    fg0 = int.from_bytes(data[0:32], 'big')
    fg1 = int.from_bytes(data[32:64], 'big')
    return (fg0, fg1)


def _decode_slot0_tick(data: bytes) -> int:
    """Decode getSlot0 → extract current tick (int24 at offset 32)."""
    if len(data) < 64:
        return None
    tick_raw = int.from_bytes(data[32:64], 'big')
    return tick_raw - (1 << 256) if tick_raw >= (1 << 255) else tick_raw


def _decode_tick_info(data: bytes) -> Tuple[int, int]:
    """Decode getTickInfo → (feeGrowthOutside0X128, feeGrowthOutside1X128) at offsets +64, +96."""
    if len(data) < 128:
        return None
    fg0 = int.from_bytes(data[64:96], 'big')
    fg1 = int.from_bytes(data[96:128], 'big')
    return (fg0, fg1)


def _decode_position_info(data: bytes) -> Tuple[int, int, int]:
    """Decode getPositionInfo(poolId, positionId) → (liquidity, fgInside0Last, fgInside1Last)."""
    if len(data) < 96:
        return None
    liquidity = int.from_bytes(data[0:32], 'big')
    fg0 = int.from_bytes(data[32:64], 'big')
    fg1 = int.from_bytes(data[64:96], 'big')
    return (liquidity, fg0, fg1)
