"""
V4 PoolManager Wrapper

Handles pool initialization and state queries for V4.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional, Tuple
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
            where parameters = bytes32(uint256(int256(tickSpacing)))
        """
        if self.protocol == V4Protocol.PANCAKESWAP:
            # PancakeSwap V4: PoolKey has different field order and includes poolManager + parameters
            tick_spacing = pool_key.tick_spacing
            if tick_spacing >= 0:
                params_int = tick_spacing
            else:
                params_int = (1 << 256) + tick_spacing
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
            # Use StateView if available (Uniswap V4) — PoolManager.getSlot0 doesn't exist there
            if self.state_view_contract:
                slot0 = self.state_view_contract.functions.getSlot0(pool_id).call()
                liquidity = self.state_view_contract.functions.getLiquidity(pool_id).call()
            else:
                # Direct query to PoolManager (PancakeSwap V4)
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
            logger.error(f"[V4 PoolManager] getSlot0 failed: {e}")
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
