"""
V4 Liquidity Provider Module

Main module for creating bid-ask ladders on Uniswap V4 / PancakeSwap V4.
Supports custom fee tiers (0-100%).
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple
from web3 import Web3
from eth_account import Account
from eth_account.signers.local import LocalAccount
import time

# Настройка логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

from .math.distribution import (
    calculate_bid_ask_distribution,
    BidAskPosition,
    DistributionType,
)
from .math.liquidity import (
    calculate_liquidity_from_usd,
    calculate_amount0_for_liquidity,
    calculate_amount1_for_liquidity
)
from .math.ticks import tick_to_price, price_to_tick, compute_decimal_tick_offset
from .contracts.v4 import V4PoolManager, V4PositionManager, V4Protocol
from .contracts.v4.pool_manager import PoolKey
from .contracts.v4.constants import (
    get_v4_addresses,
    fee_percent_to_v4,
    suggest_tick_spacing,
    MAX_V4_FEE
)
from .contracts.abis import ERC20_ABI
from .utils import NonceManager, DecimalsCache, GasEstimator, BatchRPC, get_token_info_batch

# Permit2 addresses - different for each protocol!
PERMIT2_UNISWAP = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
PERMIT2_PANCAKESWAP = "0x31c2F6fcFf4F8759b3Bd5Bf0e1084A055615c768"


def get_permit2_address(protocol: 'V4Protocol') -> str:
    """Get the correct Permit2 address for the protocol."""
    from .contracts.v4 import V4Protocol
    if protocol == V4Protocol.PANCAKESWAP:
        return PERMIT2_PANCAKESWAP
    else:
        return PERMIT2_UNISWAP

# Permit2 AllowanceTransfer ABI (minimal)
PERMIT2_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint160", "name": "amount", "type": "uint160"},
            {"internalType": "uint48", "name": "expiration", "type": "uint48"}
        ],
        "name": "approve",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"}
        ],
        "name": "allowance",
        "outputs": [
            {"internalType": "uint160", "name": "amount", "type": "uint160"},
            {"internalType": "uint48", "name": "expiration", "type": "uint48"},
            {"internalType": "uint48", "name": "nonce", "type": "uint48"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]


@dataclass
class V4LadderConfig:
    """
    Configuration for V4 bid-ask ladder.

    IMPORTANT NAMING CLARIFICATION:
    - `current_price`: This is actually the UPPER BOUND of the position range.
      Named this way for historical reasons. Use `upper_price` property for clarity.
    - `lower_price`: Lower bound of the position range.
    - `actual_current_price`: The REAL current market price (for position side detection).

    Example:
        # Create a ladder from $0.003 to $0.005 (TOKEN/USD prices):
        config = V4LadderConfig(
            current_price=0.005,  # Upper bound (confusing name, see upper_price)
            lower_price=0.003,    # Lower bound
            actual_current_price=0.004,  # Real current market price
            ...
        )
        # Or use the clearer factory method:
        config = V4LadderConfig.create(
            upper_price=0.005,
            lower_price=0.003,
            market_price=0.004,
            ...
        )
    """
    current_price: float           # UPPER BOUND of range (historical naming, use upper_price property)
    lower_price: float             # Lower bound (target price when TOKEN drops)
    total_usd: float               # Total USD to deploy
    n_positions: int               # Number of positions
    token0: str                    # Token0 address (volatile)
    token1: str                    # Token1 address (stablecoin)
    fee_percent: float             # Fee in percent (e.g., 0.3, 1.0, 3.333)
    tick_spacing: int = None       # Tick spacing (auto if None)
    distribution_type: DistributionType = "linear"
    token0_decimals: int = 18
    token1_decimals: int = 18
    slippage_percent: float = 0.5
    hooks: str = None              # Hooks address (optional)
    protocol: V4Protocol = V4Protocol.UNISWAP
    pool_id: bytes = None          # Pre-loaded pool ID (skip existence check if set)
    invert_price: bool = True      # True = prices are TOKEN/USD, need to invert for pool math
    actual_current_price: float = None  # The REAL current market price (used to determine position side)
    base_token_amount: float = None  # Amount of volatile token for positions ABOVE current price (sell orders)

    @property
    def upper_price(self) -> float:
        """Alias for current_price - the upper bound of the position range."""
        return self.current_price

    @classmethod
    def create(
        cls,
        upper_price: float,
        lower_price: float,
        total_usd: float,
        n_positions: int,
        token0: str,
        token1: str,
        fee_percent: float,
        market_price: float = None,
        **kwargs
    ) -> 'V4LadderConfig':
        """
        Factory method with clearer parameter names.

        Args:
            upper_price: Upper bound of the position range (was confusingly named current_price)
            lower_price: Lower bound of the position range
            total_usd: Total USD to deploy
            n_positions: Number of positions
            token0: Token0 address (volatile token)
            token1: Token1 address (stablecoin)
            fee_percent: Fee in percent (e.g., 0.3, 1.0, 3.333)
            market_price: The REAL current market price (optional, for position side detection)
            **kwargs: Additional optional parameters

        Returns:
            V4LadderConfig instance
        """
        return cls(
            current_price=upper_price,  # Map to legacy field name
            lower_price=lower_price,
            total_usd=total_usd,
            n_positions=n_positions,
            token0=token0,
            token1=token1,
            fee_percent=fee_percent,
            actual_current_price=market_price,
            **kwargs
        )


@dataclass
class V4LadderResult:
    """Result of creating a V4 ladder."""
    positions: List[BidAskPosition]
    tx_hash: Optional[str]
    gas_used: Optional[int]
    token_ids: List[int]
    pool_created: bool
    success: bool
    error: Optional[str] = None


class V4LiquidityProvider:
    """
    V4 Liquidity Provider for creating bid-ask ladders.

    Supports:
    - Custom fee tiers (0-100%)
    - PancakeSwap V4 (Infinity) and Uniswap V4
    - Automatic pool creation
    - Batch position creation

    Example:
    ```python
    provider = V4LiquidityProvider(
        rpc_url="https://bsc-dataseed.binance.org/",
        private_key="0x...",
        protocol=V4Protocol.PANCAKESWAP
    )

    config = V4LadderConfig(
        current_price=0.005,
        lower_price=0.003,
        total_usd=100,
        n_positions=5,
        token0="0x...",  # meme token
        token1="0x...",  # USDT
        fee_percent=3.333,  # Custom fee!
    )

    result = provider.create_ladder(config)
    ```
    """

    def __init__(
        self,
        rpc_url: str,
        private_key: str = None,
        protocol: V4Protocol = V4Protocol.PANCAKESWAP,
        chain_id: int = 56,
        proxy: dict = None  # {"http": "socks5://...", "https": "socks5://..."}
    ):
        # Create HTTPProvider with optional proxy support
        if proxy:
            provider = Web3.HTTPProvider(endpoint_uri=rpc_url, request_kwargs={"proxies": proxy})
        else:
            provider = Web3.HTTPProvider(rpc_url)

        self.w3 = Web3(provider)
        self.chain_id = chain_id
        self.protocol = protocol
        self.proxy = proxy

        if private_key:
            self.account: LocalAccount = Account.from_key(private_key)
        else:
            self.account = None

        # Initialize V4 managers
        self.pool_manager = V4PoolManager(
            self.w3,
            protocol=protocol,
            chain_id=chain_id
        )

        # Initialize utility managers
        self.decimals_cache = DecimalsCache(self.w3)
        self.gas_estimator = GasEstimator(self.w3, buffer_percent=20)
        self.nonce_manager = None  # Initialized lazily when account is set
        if self.account:
            self.nonce_manager = NonceManager(self.w3, self.account.address)

        self.position_manager = V4PositionManager(
            self.w3,
            account=self.account,
            protocol=protocol,
            chain_id=chain_id,
            nonce_manager=self.nonce_manager
        )

    def preview_ladder(self, config: V4LadderConfig) -> List[BidAskPosition]:
        """Preview positions without creating them."""
        # Convert fee percent to tick spacing
        tick_spacing = config.tick_spacing or suggest_tick_spacing(config.fee_percent)

        # Compute decimal tick offset for mixed-decimal pairs (e.g. USDC 6 dec / token 18 dec)
        # For same-decimal pairs (e.g. BNB chain 18/18) this returns 0 → no change
        dec_offset = compute_decimal_tick_offset(
            token0_address=config.token0,
            token0_decimals=config.token0_decimals,
            token1_address=config.token1,
            token1_decimals=config.token1_decimals,
        )

        positions = calculate_bid_ask_distribution(
            current_price=config.current_price,
            lower_price=config.lower_price,
            total_usd=config.total_usd,
            n_positions=config.n_positions,
            fee_tier=fee_percent_to_v4(config.fee_percent),  # Use V4 fee format internally
            distribution_type=config.distribution_type,
            token0_decimals=config.token0_decimals,
            token1_decimals=config.token1_decimals,
            token1_is_stable=True,
            allow_custom_fee=True,  # V4 supports custom fees
            tick_spacing=tick_spacing,  # Pass actual tick_spacing for proper alignment
            invert_price=config.invert_price,  # Invert price for TOKEN/USD → pool price conversion
            decimal_tick_offset=dec_offset
        )

        return positions

    def get_pool_key(self, config: V4LadderConfig) -> PoolKey:
        """Create PoolKey from config."""
        fee = fee_percent_to_v4(config.fee_percent)
        tick_spacing = config.tick_spacing or suggest_tick_spacing(config.fee_percent)

        return PoolKey.from_tokens(
            token0=config.token0,
            token1=config.token1,
            fee=fee,
            tick_spacing=tick_spacing,
            hooks=config.hooks
        )

    def check_pool_exists(self, config: V4LadderConfig) -> bool:
        """Check if pool exists for this config."""
        pool_key = self.get_pool_key(config)
        return self.pool_manager.is_pool_initialized(pool_key)

    def create_pool(
        self,
        config: V4LadderConfig,
        initial_price: float = None,
        timeout: int = 300
    ) -> Tuple[str, bool]:
        """
        Create and initialize a V4 pool.

        For V4, pool initialization goes through PositionManager.initializePool(),
        not directly through PoolManager.

        Args:
            config: Ladder configuration
            initial_price: Initial price for the pool. If not provided, uses
                          config.actual_current_price or config.current_price.
                          This should be the REAL market price in TOKEN/USD format
                          (if invert_price=True).
            timeout: Transaction timeout

        Returns:
            (tx_hash, success)
        """
        if not self.account:
            raise ValueError("Account not configured")

        pool_key = self.get_pool_key(config)

        # Check if already exists
        if self.pool_manager.is_pool_initialized(pool_key):
            logger.info("Pool already exists, skipping creation")
            return None, True  # Already exists

        # Determine the price to use for initialization
        # Priority: explicit initial_price > actual_current_price > current_price
        price_for_init = initial_price
        if price_for_init is None:
            price_for_init = config.actual_current_price or config.current_price

        logger.info(f"Creating pool with initial price: {price_for_init}")
        logger.info(f"  invert_price: {config.invert_price}")

        # Convert price to sqrtPriceX96
        # If invert_price=True, the user's price is TOKEN/USD (e.g., 0.005 USD per token)
        # But sqrtPriceX96 uses pool price format: token1/token0
        # We need to check which token is which in the pool
        if config.invert_price:
            # User price is TOKEN/USD, need to invert to USD/TOKEN for pool format
            # (assuming stablecoin is token1 or token0 based on address order)
            pool_price = 1.0 / price_for_init
            logger.info(f"  Inverted price for pool: {pool_price}")
        else:
            pool_price = price_for_init

        sqrt_price_x96 = self.pool_manager.price_to_sqrt_price_x96(
            pool_price,
            config.token0_decimals,
            config.token1_decimals
        )

        logger.info(f"Initializing pool with sqrtPriceX96: {sqrt_price_x96}")
        logger.debug(f"Pool key: {pool_key.to_tuple()}")

        # V4 uses PositionManager.initializePool() for pool creation
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        try:
            tx = self.position_manager.contract.functions.initializePool(
                pool_key.to_tuple(),
                sqrt_price_x96
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': 500000,
                'gasPrice': self.w3.eth.gas_price
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            success = receipt['status'] == 1

            if self.nonce_manager:
                if success:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)

            # Verify pool was created
            if success:
                state = self.pool_manager.get_pool_state(pool_key)
                if state.initialized:
                    logger.info(f"Pool created and verified! Tick: {state.tick}, sqrtPriceX96: {state.sqrt_price_x96}")
                else:
                    logger.warning("Transaction succeeded but pool not initialized - may need to check manually")

            return tx_hash.hex(), success

        except Exception as e:
            if self.nonce_manager:
                self.nonce_manager.release_nonce(nonce)
            raise

    def create_pool_only(
        self,
        token0: str,
        token1: str,
        fee_percent: float,
        initial_price: float,
        tick_spacing: int = None,
        token0_decimals: int = 18,
        token1_decimals: int = 18,
        hooks: str = None,
        invert_price: bool = True,
        timeout: int = 300
    ) -> Tuple[Optional[str], Optional[bytes], bool]:
        """
        Create a V4 pool without adding liquidity.

        Simplified method for creating a pool when you just need to initialize it
        without creating any positions.

        Args:
            token0: Token0 address (volatile token)
            token1: Token1 address (stablecoin)
            fee_percent: Fee in percent (e.g., 0.3, 1.0, 3.333)
            initial_price: Initial price in TOKEN/USD format (if invert_price=True)
            tick_spacing: Tick spacing (auto-calculated if None)
            token0_decimals: Decimals of token0
            token1_decimals: Decimals of token1
            hooks: Hooks address (optional)
            invert_price: If True, initial_price is TOKEN/USD and will be inverted
            timeout: Transaction timeout

        Returns:
            (tx_hash, pool_id, success)
        """
        if not self.account:
            raise ValueError("Account not configured")

        # Validate fee
        if fee_percent < 0 or fee_percent > 100:
            raise ValueError(f"Fee must be between 0% and 100%, got {fee_percent}%")

        # Calculate fee and tick_spacing
        fee = fee_percent_to_v4(fee_percent)
        if tick_spacing is None:
            tick_spacing = suggest_tick_spacing(fee_percent)

        # Create PoolKey
        pool_key = PoolKey.from_tokens(
            token0=token0,
            token1=token1,
            fee=fee,
            tick_spacing=tick_spacing,
            hooks=hooks
        )
        pool_id = self.pool_manager._compute_pool_id(pool_key)

        logger.info("=" * 50)
        logger.info("CREATE POOL ONLY (without liquidity)")
        logger.info(f"Token0: {pool_key.currency0}")
        logger.info(f"Token1: {pool_key.currency1}")
        logger.info(f"Fee: {fee_percent}% ({fee})")
        logger.info(f"Tick Spacing: {tick_spacing}")
        logger.info(f"Pool ID: 0x{pool_id.hex()}")
        logger.info(f"Initial Price: {initial_price}")
        logger.info(f"Invert Price: {invert_price}")
        logger.info("=" * 50)

        # Check if already exists
        if self.pool_manager.is_pool_initialized(pool_key):
            logger.info("Pool already exists!")
            state = self.pool_manager.get_pool_state(pool_key)
            logger.info(f"Existing pool tick: {state.tick}, liquidity: {state.liquidity}")
            return None, pool_id, True  # Already exists

        # Convert price to sqrtPriceX96
        if invert_price:
            pool_price = 1.0 / initial_price
            logger.info(f"Inverted price for pool: {pool_price}")
        else:
            pool_price = initial_price

        sqrt_price_x96 = self.pool_manager.price_to_sqrt_price_x96(
            pool_price,
            token0_decimals,
            token1_decimals
        )

        logger.info(f"sqrtPriceX96: {sqrt_price_x96}")

        # Create pool via PositionManager.initializePool()
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        try:
            tx = self.position_manager.contract.functions.initializePool(
                pool_key.to_tuple(),
                sqrt_price_x96
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': 500000,
                'gasPrice': self.w3.eth.gas_price
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"Pool creation TX sent: {tx_hash.hex()}")

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
            success = receipt['status'] == 1

            if self.nonce_manager:
                if success:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)

            if success:
                # Verify pool was created
                state = self.pool_manager.get_pool_state(pool_key)
                if state.initialized:
                    logger.info(f"Pool created successfully!")
                    logger.info(f"  Tick: {state.tick}")
                    logger.info(f"  sqrtPriceX96: {state.sqrt_price_x96}")
                    logger.info(f"  LP Fee: {state.lp_fee}")
                else:
                    logger.warning("Transaction succeeded but pool verification failed")
                    success = False
            else:
                logger.error(f"Pool creation failed! TX: {tx_hash.hex()}")

            return tx_hash.hex(), pool_id, success

        except Exception as e:
            if self.nonce_manager:
                self.nonce_manager.release_nonce(nonce)
            logger.error(f"Pool creation error: {e}")
            return None, pool_id, False

    def check_and_approve_token(
        self,
        token_address: str,
        amount: int,
        spender: str = None,
        timeout: int = 120
    ) -> Optional[str]:
        """
        Check and approve token if needed.

        Returns tx_hash if approval was sent, None if already approved.
        """
        if spender is None:
            spender = self.position_manager.position_manager_address

        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )

        current_allowance = token.functions.allowance(
            self.account.address,
            Web3.to_checksum_address(spender)
        ).call()

        logger.info(f"ERC20 allowance check: token={token_address[:10]}..., spender={spender[:10]}..., current={current_allowance}, needed={amount}")

        if current_allowance >= amount:
            logger.info("ERC20 allowance sufficient, skipping approve")
            return None

        max_uint256 = 2**256 - 1
        logger.info(f"Sending ERC20 approve: token={token_address[:10]}... -> spender={spender[:10]}...")

        approve_fn = token.functions.approve(
            Web3.to_checksum_address(spender),
            max_uint256
        )

        # Use gas estimation with fallback
        gas_limit = self.gas_estimator.estimate(
            approve_fn,
            self.account.address,
            default_type='approve'
        )

        # Use nonce manager for safe nonce handling
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        try:
            tx = approve_fn.build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            if receipt['status'] != 1:
                if self.nonce_manager:
                    self.nonce_manager.release_nonce(nonce)
                raise Exception(f"ERC20 approve failed! TX: {tx_hash.hex()}")

            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            logger.info(f"ERC20 approve confirmed: {tx_hash.hex()}")
            return tx_hash.hex()

        except Exception as e:
            if self.nonce_manager:
                self.nonce_manager.release_nonce(nonce)
            raise

    def approve_on_permit2(
        self,
        token_address: str,
        spender: str,
        amount: int,
        permit2_address: str,
        timeout: int = 120
    ) -> Optional[str]:
        """
        Grant spender allowance on Permit2.

        This is step 2 of the Permit2 flow:
        1. User approves token to Permit2 (ERC20 approve)
        2. User calls permit2.approve(token, spender, amount, expiration)
        3. Spender can then call permit2.transferFrom(user, recipient, amount)
        """
        permit2 = self.w3.eth.contract(
            address=Web3.to_checksum_address(permit2_address),
            abi=PERMIT2_ABI
        )

        # Always set Permit2 allowance (don't skip even if seems sufficient)
        current_time = int(time.time())
        max_uint160 = 2**160 - 1
        expiration = current_time + 365 * 24 * 60 * 60  # 1 year from now

        logger.info(f"Setting Permit2 allowance: token={token_address[:10]}..., spender={spender[:10]}..., expiration={expiration}")

        approve_fn = permit2.functions.approve(
            Web3.to_checksum_address(token_address),
            Web3.to_checksum_address(spender),
            max_uint160,
            expiration
        )

        # Use gas estimation with fallback
        gas_limit = self.gas_estimator.estimate(
            approve_fn,
            self.account.address,
            default_type='approve'
        )

        # Use nonce manager for safe nonce handling
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        try:
            tx = approve_fn.build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            if receipt['status'] != 1:
                if self.nonce_manager:
                    self.nonce_manager.release_nonce(nonce)
                raise Exception(f"Permit2 approval transaction failed! TX: {tx_hash.hex()}")

            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            logger.info(f"Permit2 approval tx: {tx_hash.hex()}")

            # Verify the allowance was set
            allowance_data = permit2.functions.allowance(
                self.account.address,
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(spender)
            ).call()

            set_amount = allowance_data[0]
            set_expiration = allowance_data[1]
            logger.info(f"Permit2 allowance verified: amount={set_amount}, expiration={set_expiration}")

            if set_expiration == 0:
                raise Exception(f"Permit2 allowance NOT set! Expiration is 0. Check token address and spender.")

            return tx_hash.hex()

        except Exception as e:
            if self.nonce_manager:
                self.nonce_manager.release_nonce(nonce)
            raise

    def get_token_balance(self, token_address: str) -> int:
        """Get token balance."""
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )
        return token.functions.balanceOf(self.account.address).call()

    def validate_balances(self, config: V4LadderConfig) -> Tuple[bool, Optional[str]]:
        """Validate token balances before creating ladder."""
        if not self.account:
            return False, "Account not configured"

        # Get both tokens info
        pool_key = self.get_pool_key(config)
        stablecoin_address, stablecoin_decimals = self._get_quote_token(config)
        quote_is_token0 = stablecoin_address.lower() == pool_key.currency0.lower()

        # Determine volatile token
        if quote_is_token0:
            volatile_token = pool_key.currency1
            volatile_decimals = config.token1_decimals if pool_key.currency1.lower() == config.token1.lower() else config.token0_decimals
        else:
            volatile_token = pool_key.currency0
            volatile_decimals = config.token0_decimals if pool_key.currency0.lower() == config.token0.lower() else config.token1_decimals

        # Auto-detect decimals using cache (override config if different)
        stablecoin_decimals = self.decimals_cache.get_decimals(stablecoin_address)
        volatile_decimals = self.decimals_cache.get_decimals(volatile_token)

        # Calculate how much of each token we need based on position range
        # For now, assume worst case: need all of stablecoin balance
        total_stablecoin = int(config.total_usd * (10 ** stablecoin_decimals))

        # Calculate volatile token amount using current price
        user_price = config.actual_current_price if config.actual_current_price else config.current_price
        total_volatile_usd = config.total_usd  # Worst case: same USD value in volatile
        total_volatile = int(total_volatile_usd / user_price * (10 ** volatile_decimals))

        # Use batch RPC for balance checks (more efficient)
        try:
            batch = BatchRPC(self.w3)
            batch.add_balance_of(stablecoin_address, self.account.address)
            batch.add_balance_of(volatile_token, self.account.address)
            results = batch.execute()
            stablecoin_balance = results[0] if results[0] is not None else 0
            volatile_balance = results[1] if results[1] is not None else 0
        except Exception as e:
            logger.warning(f"Batch balance check failed, falling back to individual calls: {e}")
            stablecoin_balance = self.get_token_balance(stablecoin_address)
            volatile_balance = self.get_token_balance(volatile_token)

        logger.info(f"Balance check:")
        logger.info(f"  Stablecoin ({stablecoin_address[:10]}...): need {total_stablecoin}, have {stablecoin_balance}")
        logger.info(f"  Volatile ({volatile_token[:10]}...): need {total_volatile}, have {volatile_balance}")

        # Check if we have at least ONE of the tokens in sufficient amount
        # (the actual usage depends on which side of current price the positions are)
        has_stablecoin = stablecoin_balance >= total_stablecoin
        has_volatile = volatile_balance >= total_volatile

        if not has_stablecoin and not has_volatile:
            return False, (
                f"Insufficient balance for both tokens.\n"
                f"Stablecoin: need {total_stablecoin}, have {stablecoin_balance}\n"
                f"Volatile: need {total_volatile}, have {volatile_balance}"
            )

        if not has_stablecoin:
            logger.warning(f"Low stablecoin balance (need {total_stablecoin}, have {stablecoin_balance}). OK if positions are above current price.")
        if not has_volatile:
            logger.warning(f"Low volatile balance (need {total_volatile}, have {volatile_balance}). OK if positions are below current price.")

        return True, None

    def check_approvals(self, config: V4LadderConfig) -> dict:
        """
        Check current approval state for both quote and base tokens.

        Returns dict with approval status for two-sided liquidity.
        Uses batch RPC for efficiency.
        """
        pool_key = self.get_pool_key(config)
        quote_token, quote_decimals = self._get_quote_token(config)
        quote_is_token0 = quote_token.lower() == pool_key.currency0.lower()

        if quote_is_token0:
            base_token = pool_key.currency1
            base_decimals = config.token1_decimals if pool_key.currency1.lower() == config.token1.lower() else config.token0_decimals
        else:
            base_token = pool_key.currency0
            base_decimals = config.token0_decimals if pool_key.currency0.lower() == config.token0.lower() else config.token1_decimals

        # Use decimals cache
        quote_decimals = self.decimals_cache.get_decimals(quote_token)
        base_decimals = self.decimals_cache.get_decimals(base_token)

        total_quote = int(config.total_usd * (10 ** quote_decimals))
        user_price = config.actual_current_price if config.actual_current_price else config.current_price
        total_base = int(config.total_usd / user_price * (10 ** base_decimals))

        permit2_addr = get_permit2_address(config.protocol)
        pos_manager_addr = self.position_manager.position_manager_address

        current_time = int(time.time())

        # Batch ERC20 allowance checks
        try:
            batch = BatchRPC(self.w3)
            batch.add_allowance(quote_token, self.account.address, permit2_addr)
            batch.add_allowance(base_token, self.account.address, permit2_addr)
            batch_results = batch.execute()
            quote_erc20_allowance = batch_results[0] if batch_results[0] is not None else 0
            base_erc20_allowance = batch_results[1] if batch_results[1] is not None else 0
        except Exception as e:
            logger.warning(f"Batch approval check failed: {e}, falling back to individual calls")
            # Fallback to individual calls
            quote_token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(quote_token), abi=ERC20_ABI
            )
            base_token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(base_token), abi=ERC20_ABI
            )
            quote_erc20_allowance = quote_token_contract.functions.allowance(
                self.account.address, Web3.to_checksum_address(permit2_addr)
            ).call()
            base_erc20_allowance = base_token_contract.functions.allowance(
                self.account.address, Web3.to_checksum_address(permit2_addr)
            ).call()

        # Permit2 allowances (complex return type, keep as individual calls)
        permit2 = self.w3.eth.contract(
            address=Web3.to_checksum_address(permit2_addr),
            abi=PERMIT2_ABI
        )

        quote_permit2_data = permit2.functions.allowance(
            self.account.address,
            Web3.to_checksum_address(quote_token),
            Web3.to_checksum_address(pos_manager_addr)
        ).call()

        base_permit2_data = permit2.functions.allowance(
            self.account.address,
            Web3.to_checksum_address(base_token),
            Web3.to_checksum_address(pos_manager_addr)
        ).call()

        # Build approval status dicts
        quote_approvals = {
            'erc20_to_permit2': {
                'approved': quote_erc20_allowance >= total_quote,
                'allowance': quote_erc20_allowance
            },
            'permit2_to_position_manager': {
                'approved': quote_permit2_data[0] >= total_quote and quote_permit2_data[1] > current_time,
                'amount': quote_permit2_data[0],
                'expiration': quote_permit2_data[1],
                'expired': quote_permit2_data[1] <= current_time if quote_permit2_data[1] > 0 else True
            }
        }

        base_approvals = {
            'erc20_to_permit2': {
                'approved': base_erc20_allowance >= total_base,
                'allowance': base_erc20_allowance
            },
            'permit2_to_position_manager': {
                'approved': base_permit2_data[0] >= total_base and base_permit2_data[1] > current_time,
                'amount': base_permit2_data[0],
                'expiration': base_permit2_data[1],
                'expired': base_permit2_data[1] <= current_time if base_permit2_data[1] > 0 else True
            }
        }

        return {
            'quote_token': quote_token,
            'base_token': base_token,
            'total_quote_needed': total_quote,
            'total_base_needed': total_base,
            # Keep old keys for backwards compatibility
            'erc20_to_permit2': quote_approvals['erc20_to_permit2'],
            'permit2_to_position_manager': quote_approvals['permit2_to_position_manager'],
            # New keys for base token
            'base_erc20_to_permit2': base_approvals['erc20_to_permit2'],
            'base_permit2_to_position_manager': base_approvals['permit2_to_position_manager'],
        }

    def approve_tokens_for_ladder(
        self,
        config: V4LadderConfig,
        timeout: int = 120
    ) -> dict:
        """
        Approve tokens for ladder creation - SEPARATE from position creation.

        This performs the two-step Permit2 approval flow for BOTH tokens:
        1. ERC20 approve to Permit2 contract (quote + base tokens)
        2. Permit2 approve to PositionManager (quote + base tokens)

        Call this BEFORE create_ladder() to ensure approvals are set.

        Returns:
            {
                'quote_token': str,
                'base_token': str,
                'erc20_approve_tx': str or None,
                'permit2_approve_tx': str,
                'base_erc20_approve_tx': str or None,
                'base_permit2_approve_tx': str or None,
                'success': bool
            }
        """
        if not self.account:
            raise ValueError("Account not configured")

        # Get quote (stablecoin) and base (volatile) tokens
        pool_key = self.get_pool_key(config)
        quote_token, quote_decimals = self._get_quote_token(config)
        quote_is_token0 = quote_token.lower() == pool_key.currency0.lower()

        if quote_is_token0:
            base_token = pool_key.currency1
            base_decimals = config.token1_decimals if pool_key.currency1.lower() == config.token1.lower() else config.token0_decimals
        else:
            base_token = pool_key.currency0
            base_decimals = config.token0_decimals if pool_key.currency0.lower() == config.token0.lower() else config.token1_decimals

        # Use 3x multiplier for safety (slippage + liquidity calculation variations)
        safety_multiplier = 3
        total_quote = int(config.total_usd * (10 ** quote_decimals) * safety_multiplier)

        # Calculate base token amount using current price
        user_price = config.actual_current_price if config.actual_current_price else config.current_price
        total_base = int(config.total_usd / user_price * (10 ** base_decimals) * safety_multiplier)

        permit2_addr = get_permit2_address(config.protocol)
        pos_manager_addr = self.position_manager.position_manager_address

        logger.info("=" * 50)
        logger.info("APPROVE TOKENS FOR LADDER (Two-Sided)")
        logger.info(f"Quote token (stablecoin): {quote_token}")
        logger.info(f"Quote amount: {total_quote} ({config.total_usd} USD)")
        logger.info(f"Base token (volatile): {base_token}")
        logger.info(f"Base amount: {total_base} (~{config.total_usd} USD worth)")
        logger.info(f"Permit2: {permit2_addr}")
        logger.info(f"PositionManager: {pos_manager_addr}")
        logger.info("=" * 50)

        result = {
            'quote_token': quote_token,
            'base_token': base_token,
            'erc20_approve_tx': None,
            'permit2_approve_tx': None,
            'base_erc20_approve_tx': None,
            'base_permit2_approve_tx': None,
            'success': False
        }

        try:
            # === QUOTE TOKEN (Stablecoin) ===
            logger.info("=== Quote Token (Stablecoin) ===")

            # Step 1a: ERC20 approve quote to Permit2
            logger.info("Step 1a: ERC20 approve quote to Permit2...")
            erc20_tx = self.check_and_approve_token(
                quote_token,
                total_quote,
                spender=permit2_addr,
                timeout=timeout
            )
            result['erc20_approve_tx'] = erc20_tx
            if erc20_tx:
                logger.info(f"Quote ERC20 approve tx: {erc20_tx}")
            else:
                logger.info("Quote ERC20 already approved to Permit2")

            # Note: No sleep needed - nonce manager handles sequential transactions

            # Step 2a: Permit2 approve quote to PositionManager
            logger.info("Step 2a: Permit2 approve quote to PositionManager...")
            permit2_tx = self.approve_on_permit2(
                quote_token,
                pos_manager_addr,
                total_quote,
                permit2_addr,
                timeout=timeout
            )
            result['permit2_approve_tx'] = permit2_tx
            logger.info(f"Quote Permit2 approve tx: {permit2_tx}")

            # === BASE TOKEN (Volatile) ===
            logger.info("=== Base Token (Volatile) ===")

            # Step 1b: ERC20 approve base to Permit2
            logger.info("Step 1b: ERC20 approve base to Permit2...")
            base_erc20_tx = self.check_and_approve_token(
                base_token,
                total_base,
                spender=permit2_addr,
                timeout=timeout
            )
            result['base_erc20_approve_tx'] = base_erc20_tx
            if base_erc20_tx:
                logger.info(f"Base ERC20 approve tx: {base_erc20_tx}")
            else:
                logger.info("Base ERC20 already approved to Permit2")

            # Step 2b: Permit2 approve base to PositionManager
            logger.info("Step 2b: Permit2 approve base to PositionManager...")
            base_permit2_tx = self.approve_on_permit2(
                base_token,
                pos_manager_addr,
                total_base,
                permit2_addr,
                timeout=timeout
            )
            result['base_permit2_approve_tx'] = base_permit2_tx
            logger.info(f"Base Permit2 approve tx: {base_permit2_tx}")

            result['success'] = True
            logger.info("=" * 50)
            logger.info("APPROVALS COMPLETE (Both Tokens)")
            logger.info("=" * 50)

            return result

        except Exception as e:
            logger.error(f"Approval failed: {e}")
            result['error'] = str(e)
            return result

    def check_pool_compatibility(self, config: V4LadderConfig) -> dict:
        """
        Check if the pool is compatible with USDT-only positions at LOWER USD prices.

        Key insight: "Lower USD price" means HIGHER tick (above current tick).
        Position ABOVE current tick requires token0 to be deposited.

        So for USDT-only positions at lower USD prices:
        - If USDT is token0 → compatible (USDT deposited at higher ticks)
        - If USDT is token1 → NOT compatible

        Returns:
            {
                'compatible': bool,
                'usdt_is_token0': bool,
                'currency0': str,
                'currency1': str,
                'usdt_address': str,
                'recommendation': str
            }
        """
        pool_key = self.get_pool_key(config)
        quote_token, _ = self._get_quote_token(config)
        quote_is_token0 = quote_token.lower() == pool_key.currency0.lower()

        result = {
            'compatible': quote_is_token0,
            'usdt_is_token0': quote_is_token0,
            'currency0': pool_key.currency0,
            'currency1': pool_key.currency1,
            'usdt_address': quote_token,
        }

        if quote_is_token0:
            result['recommendation'] = (
                "Pool is compatible! USDT is token0. "
                "Positions at lower USD prices (= higher ticks = above current tick) will use USDT."
            )
        else:
            result['recommendation'] = (
                f"Pool is NOT compatible for USDT-only positions at lower USD prices! "
                f"USDT ({quote_token[:10]}...) is token1, but positions above current tick need token0 ({pool_key.currency0[:10]}...). "
                f"Options: 1) Find/create a pool where USDT address < TOKEN address, "
                f"2) Provide the volatile token instead of USDT."
            )

        return result

    def _get_quote_token(self, config: V4LadderConfig) -> Tuple[str, int]:
        """
        Determine which token is the quote/stablecoin.

        For bid positions we need the stablecoin.
        Check known stablecoins first, otherwise use token1.
        """
        from config import STABLECOINS

        token0_lower = config.token0.lower()
        token1_lower = config.token1.lower()

        # Check if token0 is a stablecoin - use decimals from STABLECOINS dict
        if token0_lower in STABLECOINS:
            return config.token0, STABLECOINS[token0_lower]

        # Check if token1 is a stablecoin - use decimals from STABLECOINS dict
        if token1_lower in STABLECOINS:
            return config.token1, STABLECOINS[token1_lower]

        # Default to token1 (old behavior)
        return config.token1, config.token1_decimals

    def create_ladder(
        self,
        config: V4LadderConfig,
        auto_create_pool: bool = True,
        simulate_first: bool = True,
        skip_approvals: bool = False,
        timeout: int = 300,
        gas_limit: int = None
    ) -> V4LadderResult:
        """
        Create a V4 bid-ask ladder.

        Args:
            config: Ladder configuration
            auto_create_pool: Create pool if doesn't exist
            simulate_first: Simulate before sending
            skip_approvals: If True, assume approvals are already done (use approve_tokens_for_ladder first)
            timeout: Transaction timeout
            gas_limit: Manual gas limit (None = auto estimate)

        Returns:
            V4LadderResult with details
        """
        if not self.account:
            return V4LadderResult(
                positions=[],
                tx_hash=None,
                gas_used=None,
                token_ids=[],
                pool_created=False,
                success=False,
                error="Account not configured"
            )

        # Validate fee
        if config.fee_percent < 0 or config.fee_percent > 100:
            return V4LadderResult(
                positions=[],
                tx_hash=None,
                gas_used=None,
                token_ids=[],
                pool_created=False,
                success=False,
                error=f"Fee must be between 0% and 100%, got {config.fee_percent}%"
            )

        # Preview positions
        positions = self.preview_ladder(config)
        pool_key = self.get_pool_key(config)

        logger.info("=== V4 Ladder Creation ===")
        logger.info(f"Protocol: {config.protocol.value}")
        logger.info(f"Fee: {config.fee_percent}%")
        logger.info(f"Tick Spacing: {pool_key.tick_spacing}")
        logger.info(f"Positions: {len(positions)}")
        logger.debug(f"Position Manager: {self.position_manager.position_manager_address}")

        # Compute pool_id from pool_key (protocol-aware)
        computed_pool_id = self.pool_manager._compute_pool_id(pool_key)
        logger.info(f"Computed pool_id: 0x{computed_pool_id.hex()}")
        logger.debug(f"PoolKey: currency0={pool_key.currency0}")
        logger.debug(f"PoolKey: currency1={pool_key.currency1}")
        logger.debug(f"PoolKey: fee={pool_key.fee} ({config.fee_percent}%)")
        logger.debug(f"PoolKey: tickSpacing={pool_key.tick_spacing}")
        logger.debug(f"PoolKey: hooks={pool_key.hooks}")

        # Check if pool exists
        pool_created = False
        pool_exists = False

        # If pool_id was pre-loaded (from UI), verify it matches computed pool_id
        if config.pool_id:
            logger.info(f"Pre-loaded pool ID: 0x{config.pool_id.hex()}")

            # CRITICAL: Compare pool IDs
            if config.pool_id != computed_pool_id:
                logger.warning("=" * 50)
                logger.warning("POOL ID MISMATCH - attempting auto-correction...")
                logger.warning(f"  Loaded:   0x{config.pool_id.hex()}")
                logger.warning(f"  Computed: 0x{computed_pool_id.hex()}")
                logger.warning(f"  Your fee: {pool_key.fee} ({config.fee_percent}%)")
                logger.warning(f"  Your tick_spacing: {pool_key.tick_spacing}")

                # Try to get the actual pool's fee from getSlot0
                try:
                    loaded_pool_state = self.pool_manager.get_pool_state_by_id(config.pool_id)
                    if loaded_pool_state.initialized:
                        actual_fee = loaded_pool_state.lp_fee
                        logger.info(f"  Pool's actual fee: {actual_fee} ({actual_fee/10000:.4f}%)")

                        # Check if fee mismatch is the issue
                        if actual_fee != pool_key.fee:
                            logger.info("Fee mismatch detected! Trying with pool's actual fee...")

                            # Build comprehensive list of tick_spacings to try
                            tick_spacings_to_try = set([
                                pool_key.tick_spacing,  # Current value first
                                1, 10, 50, 60, 100, 200, 500, 780, 800, 1000, 2000, 2500, 4000,
                                2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192,
                            ])
                            # Add multiples of 10 and 100
                            tick_spacings_to_try.update(range(10, 1010, 10))
                            tick_spacings_to_try.update(range(100, 10100, 100))

                            for try_tick_spacing in sorted(tick_spacings_to_try):
                                test_pool_key = PoolKey.from_tokens(
                                    token0=config.token0,
                                    token1=config.token1,
                                    fee=actual_fee,
                                    tick_spacing=try_tick_spacing,
                                    hooks=config.hooks
                                )
                                test_pool_id = self.pool_manager._compute_pool_id(test_pool_key)
                                if test_pool_id == config.pool_id:
                                    logger.info(f"  MATCH FOUND! fee={actual_fee}, tick_spacing={try_tick_spacing}")
                                    # Update pool_key with correct values
                                    pool_key = test_pool_key
                                    computed_pool_id = test_pool_id
                                    config.fee_percent = actual_fee / 10000
                                    config.tick_spacing = try_tick_spacing
                                    break
                            else:
                                # No match found even with correct fee - probably token address issue
                                logger.error("Could not find matching parameters!")
                                logger.error(f"  Tried fee={actual_fee} with {len(tick_spacings_to_try)} tick_spacings")
                                logger.error("  Check token addresses!")
                except Exception as e:
                    logger.error(f"Could not query loaded pool: {e}")

                # Final check after auto-correction attempt
                if config.pool_id != computed_pool_id:
                    logger.error("=" * 50)
                    logger.error("POOL ID STILL MISMATCHED after auto-correction!")
                    logger.error("=" * 50)

                    return V4LadderResult(
                        positions=positions,
                        tx_hash=None,
                        gas_used=None,
                        token_ids=[],
                        pool_created=False,
                        success=False,
                        error=(
                            f"Pool ID mismatch! Could not auto-correct. "
                            f"Expected: 0x{config.pool_id.hex()[:16]}... "
                            f"Got: 0x{computed_pool_id.hex()[:16]}... "
                            f"Check token addresses carefully!"
                        )
                    )

                logger.info("Pool ID auto-corrected successfully!")

            logger.info("Pool ID verification PASSED")
            state = self.pool_manager.get_pool_state_by_id(config.pool_id)
            pool_exists = state.initialized
            if pool_exists:
                logger.info(f"Pool state: tick={state.tick}, liquidity={state.liquidity}, fee={state.lp_fee}")
            else:
                logger.warning("Pool ID found but pool is NOT initialized!")
        else:
            # Check using computed pool_key
            logger.info("No pre-loaded pool ID, checking by computed pool_key...")
            pool_exists = self.pool_manager.is_pool_initialized(pool_key)

        if not pool_exists:
            if not auto_create_pool:
                return V4LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    pool_created=False,
                    success=False,
                    error=f"Pool does not exist for fee {config.fee_percent}%. Enable auto_create_pool."
                )

            logger.info("Creating new pool...")
            try:
                tx_hash, success = self.create_pool(config, timeout)
                if not success:
                    return V4LadderResult(
                        positions=positions,
                        tx_hash=tx_hash,
                        gas_used=None,
                        token_ids=[],
                        pool_created=False,
                        success=False,
                        error="Failed to create pool"
                    )
                pool_created = True
                logger.info(f"Pool created: {tx_hash}")
            except Exception as e:
                return V4LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    pool_created=False,
                    success=False,
                    error=f"Pool creation failed: {e}"
                )

        # Validate balances
        is_valid, error = self.validate_balances(config)
        if not is_valid:
            return V4LadderResult(
                positions=positions,
                tx_hash=None,
                gas_used=None,
                token_ids=[],
                pool_created=pool_created,
                success=False,
                error=error
            )

        # Determine quote token (stablecoin) for bid positions
        quote_token, quote_decimals = self._get_quote_token(config)
        quote_is_token0 = quote_token.lower() == pool_key.currency0.lower()

        # Determine base token (volatile token) for ask positions (above current price)
        if quote_is_token0:
            base_token = pool_key.currency1
            base_decimals = config.token1_decimals if pool_key.currency1.lower() == config.token1.lower() else config.token0_decimals
        else:
            base_token = pool_key.currency0
            base_decimals = config.token0_decimals if pool_key.currency0.lower() == config.token0.lower() else config.token1_decimals

        # Auto-detect decimals for base token if still default (18)
        # This is important for non-standard tokens
        try:
            base_token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(base_token),
                abi=ERC20_ABI
            )
            actual_base_decimals = base_token_contract.functions.decimals().call()
            if actual_base_decimals != base_decimals:
                logger.info(f"Auto-correcting base_decimals: {base_decimals} -> {actual_base_decimals}")
                base_decimals = actual_base_decimals
        except Exception as e:
            logger.warning(f"Could not auto-detect base token decimals: {e}")

        logger.info(f"Quote token: {quote_token} (decimals={quote_decimals})")
        logger.info(f"Base token (volatile): {base_token}")
        logger.info(f"Pool currency0: {pool_key.currency0}")
        logger.info(f"Pool currency1: {pool_key.currency1}")
        logger.info(f"Quote is token0: {quote_is_token0}")

        # Get current tick from pool state (for reference/logging only)
        pool_state = self.pool_manager.get_pool_state_by_id(computed_pool_id)
        pool_current_tick = pool_state.tick
        logger.info(f"Pool's current tick: {pool_current_tick}")

        # Calculate expected current tick from user's ACTUAL input price

        # Use actual_current_price if provided, otherwise fall back to current_price
        # (current_price in config is actually the upper bound of the range!)
        user_price = config.actual_current_price if config.actual_current_price else config.current_price
        logger.info(f"User's actual input price: {user_price}")

        # Compute decimal tick offset (same as in preview_ladder)
        dec_offset = compute_decimal_tick_offset(
            token0_address=config.token0,
            token0_decimals=config.token0_decimals,
            token1_address=config.token1,
            token1_decimals=config.token1_decimals,
        )
        if dec_offset != 0:
            logger.info(f"Decimal tick offset: {dec_offset} (token0_dec={config.token0_decimals}, token1_dec={config.token1_decimals})")

        # User's price is TOKEN/USD, need to invert for pool price (USD/TOKEN or TOKEN/USD depending on order)
        # If invert_price=True (default), user enters TOKEN price in USD
        if config.invert_price:
            expected_current_tick = price_to_tick(user_price, invert=True) + dec_offset
        else:
            expected_current_tick = price_to_tick(user_price, invert=False) + dec_offset

        logger.info(f"Expected tick from user's price: {expected_current_tick} (includes dec_offset={dec_offset})")

        # Calculate pool's price from tick for comparison
        pool_price_from_tick = tick_to_price(pool_current_tick, invert=config.invert_price)
        logger.info(f"Pool's price from tick: {pool_price_from_tick:.8f}")

        # Compare pool tick vs expected tick
        tick_difference = abs(pool_current_tick - expected_current_tick)

        if tick_difference > 1000:  # Significant difference (roughly 10% price difference)
            logger.warning("=" * 50)
            logger.warning("PRICE MISMATCH DETECTED!")
            logger.warning(f"  Your input price: {user_price}")
            logger.warning(f"  Pool's actual price: {pool_price_from_tick:.8f}")
            logger.warning(f"  Expected tick: {expected_current_tick}")
            logger.warning(f"  Pool's tick: {pool_current_tick}")
            logger.warning(f"  Difference: {tick_difference} ticks")
            logger.warning("=" * 50)
            logger.warning("Using YOUR INPUT PRICE for position calculations.")
            logger.warning("Pool might have low liquidity or stale price.")
            logger.warning("=" * 50)

        # IMPORTANT: Use expected tick from user's price, NOT pool's tick
        # This ensures positions are created where the user intends them to be
        # Pool's actual tick is only used for information
        current_tick = expected_current_tick
        logger.info(f"Using current_tick: {current_tick} (from user's input price)")

        # Check and handle approvals
        if skip_approvals:
            logger.info("skip_approvals=True, verifying existing approvals...")
            approval_state = self.check_approvals(config)

            if not approval_state['erc20_to_permit2']['approved']:
                return V4LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    pool_created=pool_created,
                    success=False,
                    error=f"ERC20 not approved to Permit2. Run approve_tokens_for_ladder() first. Current allowance: {approval_state['erc20_to_permit2']['allowance']}"
                )

            if not approval_state['permit2_to_position_manager']['approved']:
                if approval_state['permit2_to_position_manager']['expired']:
                    return V4LadderResult(
                        positions=positions,
                        tx_hash=None,
                        gas_used=None,
                        token_ids=[],
                        pool_created=pool_created,
                        success=False,
                        error=f"Permit2 allowance EXPIRED! Expiration: {approval_state['permit2_to_position_manager']['expiration']}. Run approve_tokens_for_ladder() first."
                    )
                return V4LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    pool_created=pool_created,
                    success=False,
                    error=f"Permit2 not approved to PositionManager. Run approve_tokens_for_ladder() first."
                )

            logger.info("Approvals verified OK")
        else:
            # Do approvals inline
            logger.info("Performing token approvals...")
            approval_result = self.approve_tokens_for_ladder(config, timeout=120)

            if not approval_result['success']:
                return V4LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    pool_created=pool_created,
                    success=False,
                    error=f"Token approval failed: {approval_result.get('error', 'Unknown error')}"
                )

        # Build mint actions for all positions
        deadline = int(time.time()) + 3600

        logger.info("=" * 50)
        logger.info("Building position payloads...")
        logger.info(f"Pool tick_spacing: {pool_key.tick_spacing}")

        # IMPORTANT: For V4 batch operations, we need:
        # 1. All MINT_POSITION actions first
        # 2. ONE SETTLE_PAIR at the end
        # NOT: MINT, SETTLE, MINT, SETTLE, ...
        all_actions = []

        for i, pos in enumerate(positions):
            stablecoin_amount = int(pos.usd_amount * (10 ** quote_decimals))

            tick_lower = pos.tick_lower
            tick_upper = pos.tick_upper

            # Determine which tokens the position needs based on PROTOCOL TICK RULES:
            #
            # Uniswap V4 liquidity rules (protocol-level, not user-level):
            # - If current_tick < tick_lower: position is ABOVE current tick → needs TOKEN0
            # - If current_tick > tick_upper: position is BELOW current tick → needs TOKEN1
            # - If tick_lower <= current_tick <= tick_upper: in range → needs BOTH
            #
            # IMPORTANT: With invert_price=True, user's "lower price" = higher tick!
            # So user's "buy the dip" (lower prices) = higher ticks = ABOVE current tick
            # This means we MUST use protocol ticks for token selection, not user prices.

            if current_tick < tick_lower:
                # Position is ABOVE current tick in protocol terms → needs token0
                position_side = "above_tick"
            elif current_tick > tick_upper:
                # Position is BELOW current tick in protocol terms → needs token1
                position_side = "below_tick"
            else:
                # Position spans current tick → needs both tokens
                position_side = "in_range"

            logger.info(f"Position {i}: ticks=[{tick_lower}, {tick_upper}], current_tick={current_tick}, side={position_side}")
            logger.info(f"  User prices=[{pos.price_lower:.8f}, {pos.price_upper:.8f}], user_price={user_price:.8f}")

            # Determine amounts based on PROTOCOL position relative to current tick
            slippage_multiplier = 1 + (config.slippage_percent / 100)

            # Calculate both token amounts with 2x safety margin
            safety_margin = 2.0
            base_token_amount = int(pos.usd_amount / user_price * (10 ** base_decimals) * safety_margin)
            safe_stablecoin = int(stablecoin_amount * safety_margin)

            if position_side == "below_tick":
                if quote_is_token0:
                    amount0_max = int(safe_stablecoin * slippage_multiplier)
                    amount1_max = int(base_token_amount * slippage_multiplier)
                else:
                    amount0_max = int(base_token_amount * slippage_multiplier)
                    amount1_max = int(safe_stablecoin * slippage_multiplier)
                logger.info(f"Position {i}: BELOW_TICK, amounts: ({amount0_max}, {amount1_max})")

            elif position_side == "above_tick":
                if quote_is_token0:
                    amount0_max = int(safe_stablecoin * slippage_multiplier)
                    amount1_max = int(base_token_amount * slippage_multiplier)
                else:
                    amount0_max = int(base_token_amount * slippage_multiplier)
                    amount1_max = int(safe_stablecoin * slippage_multiplier)
                logger.info(f"Position {i}: ABOVE_TICK, amounts: ({amount0_max}, {amount1_max})")

            else:
                logger.warning(f"Position {i}: IN RANGE - using both tokens")
                if quote_is_token0:
                    amount0_max = int(safe_stablecoin * slippage_multiplier)
                    amount1_max = int(base_token_amount * slippage_multiplier)
                else:
                    amount0_max = int(base_token_amount * slippage_multiplier)
                    amount1_max = int(safe_stablecoin * slippage_multiplier)

            logger.info(f"Position {i}: amount0_max={amount0_max}, amount1_max={amount1_max}")

            # FORCE align ticks to tick_spacing (defensive fix)
            # This ensures ticks are always valid even if distribution calculation has edge cases
            spacing = pool_key.tick_spacing
            if tick_lower % spacing != 0:
                old_tick_lower = tick_lower
                # Round DOWN for lower tick (floor division works for both positive and negative)
                tick_lower = (tick_lower // spacing) * spacing
                logger.warning(f"Position {i}: Re-aligned tick_lower {old_tick_lower} → {tick_lower}")
            if tick_upper % spacing != 0:
                old_tick_upper = tick_upper
                # Round UP for upper tick: ((tick // spacing) + 1) * spacing
                # This works for both positive and negative numbers
                tick_upper = ((tick_upper // spacing) + 1) * spacing
                logger.warning(f"Position {i}: Re-aligned tick_upper {old_tick_upper} → {tick_upper}")

            # Verify tick_lower < tick_upper
            if tick_lower >= tick_upper:
                logger.error(f"Position {i}: tick_lower {tick_lower} >= tick_upper {tick_upper}!")
                return V4LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    pool_created=pool_created,
                    success=False,
                    error=f"Invalid tick range: tick_lower {tick_lower} >= tick_upper {tick_upper}"
                )

            logger.debug(f"Position {i}: ticks=[{tick_lower}, {tick_upper}], liq={pos.liquidity}, usd=${pos.usd_amount:.2f}")
            logger.debug(f"  amount0_max={amount0_max}, amount1_max={amount1_max}")

            # Add ONLY the MINT_POSITION action (not SETTLE_PAIR yet)
            mint_action = self.position_manager.build_mint_action(
                pool_key=pool_key,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                liquidity=pos.liquidity,
                amount0_max=amount0_max,
                amount1_max=amount1_max,
                recipient=self.account.address
            )
            all_actions.append(mint_action)

        # Add ONE SETTLE_PAIR at the end for all positions
        settle_action = self.position_manager.encode_settle_pair(
            pool_key.currency0,
            pool_key.currency1
        )
        all_actions.append(settle_action)

        logger.info(f"Actions: {len(positions)} MINT_POSITION + 1 SETTLE_PAIR = {len(all_actions)} total")
        logger.info("=" * 50)

        # Final verification of balances and approvals before sending (using batch RPC)
        logger.info("Final pre-flight checks (batch RPC)...")
        permit2_addr = get_permit2_address(config.protocol)
        pos_manager_addr = self.position_manager.position_manager_address

        try:
            # Use batch RPC for efficient pre-flight checks
            batch = BatchRPC(self.w3)
            tokens = [pool_key.currency0, pool_key.currency1]

            for token_addr in tokens:
                batch.add_balance_of(token_addr, self.account.address)
                batch.add_allowance(token_addr, self.account.address, permit2_addr)

            results = batch.execute()

            # Parse results: [balance0, allowance0, balance1, allowance1]
            for i, (token_addr, token_name) in enumerate([(pool_key.currency0, "currency0"), (pool_key.currency1, "currency1")]):
                balance = results[i * 2] if i * 2 < len(results) else 0
                erc20_allowance = results[i * 2 + 1] if i * 2 + 1 < len(results) else 0

                # Permit2 allowance requires separate call (complex return type)
                try:
                    permit2_contract = self.w3.eth.contract(
                        address=Web3.to_checksum_address(permit2_addr),
                        abi=PERMIT2_ABI
                    )
                    permit2_allowance = permit2_contract.functions.allowance(
                        self.account.address,
                        Web3.to_checksum_address(token_addr),
                        Web3.to_checksum_address(pos_manager_addr)
                    ).call()
                    permit2_info = f"({permit2_allowance[0]}, exp={permit2_allowance[1]})"
                except:
                    permit2_info = "(error)"

                logger.info(f"  {token_name} ({token_addr[:10]}...): balance={balance}, ERC20->Permit2={erc20_allowance}, Permit2->PosMan={permit2_info}")

        except Exception as e:
            logger.warning(f"Pre-flight batch check failed: {e}, continuing anyway...")

        logger.info(f"Creating {len(positions)} positions...")
        logger.debug(f"Quote is token0: {quote_is_token0}")

        try:
            # Encode all actions into unlockData
            unlock_data = self.position_manager._encode_actions(all_actions)
            logger.debug(f"unlockData size: {len(unlock_data)} bytes")

            # Execute directly via modifyLiquidities
            tx_hash, results = self.position_manager.execute_modify_liquidities(
                unlock_data=unlock_data,
                deadline=deadline,
                gas_limit=gas_limit,
                timeout=timeout
            )

            # Parse token IDs from receipt
            receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            gas_used = receipt.get('gasUsed', 0)

            # ВАЖНО: Проверяем статус транзакции
            if receipt['status'] != 1:
                logger.error(f"Transaction REVERTED! TX: {tx_hash}")
                logger.error(f"Check: https://bscscan.com/tx/{tx_hash}")
                return V4LadderResult(
                    positions=positions,
                    tx_hash=tx_hash,
                    gas_used=gas_used,
                    token_ids=[],
                    pool_created=pool_created,
                    success=False,
                    error=f"Transaction reverted. Check https://bscscan.com/tx/{tx_hash}"
                )

            # Parse Transfer events for token IDs
            token_ids = []
            try:
                events = self.position_manager.contract.events.Transfer().process_receipt(receipt)
                for event in events:
                    if event['args']['from'] == '0x0000000000000000000000000000000000000000':
                        token_ids.append(event['args']['tokenId'])
            except Exception as e:
                logger.warning(f"Could not parse token IDs from events: {e}")

            logger.info(f"Success! TX: {tx_hash}")
            logger.info(f"Token IDs: {token_ids}")
            logger.info(f"Gas used: {gas_used}")

            return V4LadderResult(
                positions=positions,
                tx_hash=tx_hash,
                gas_used=gas_used,
                token_ids=token_ids,
                pool_created=pool_created,
                success=True
            )

        except Exception as e:
            logger.error(f"Failed to create ladder: {e}")
            return V4LadderResult(
                positions=positions,
                tx_hash=None,
                gas_used=None,
                token_ids=[],
                pool_created=pool_created,
                success=False,
                error=str(e)
            )

    def close_positions(
        self,
        token_ids: List[int],
        currency0: str = None,
        currency1: str = None,
        recipient: str = None,
        timeout: int = 300,
        burn: bool = False
    ) -> Tuple[str, bool, Optional[int]]:
        """
        Close multiple V4 positions.

        IMPORTANT: You must provide currency0 and currency1 addresses!
        V4 positionInfo only returns truncated poolId, not the full PoolKey.

        Args:
            token_ids: List of position NFT IDs
            currency0: Token0 address (lower address) - REQUIRED
            currency1: Token1 address (higher address) - REQUIRED
            recipient: Recipient of tokens (default: account)
            timeout: Transaction timeout
            burn: If True, burn NFTs after removing liquidity (default: False)

        Returns:
            (tx_hash, success, gas_used)
        """
        if not self.account:
            raise ValueError("Account not configured")

        if not currency0 or not currency1:
            raise ValueError(
                "currency0 and currency1 are REQUIRED for closing positions! "
                "V4 positionInfo doesn't include full PoolKey, so you must provide token addresses."
            )

        # Ensure correct order (lower address first)
        addr0 = Web3.to_checksum_address(currency0)
        addr1 = Web3.to_checksum_address(currency1)
        if int(addr0, 16) > int(addr1, 16):
            addr0, addr1 = addr1, addr0

        if recipient is None:
            recipient = self.account.address

        payloads = []
        deadline = int(time.time()) + 3600

        for token_id in token_ids:
            try:
                position = self.position_manager.get_position(token_id)
                logger.info(f"Closing position {token_id}: ticks=[{position.tick_lower}, {position.tick_upper}], liquidity={position.liquidity}")

                if position.liquidity > 0:
                    payload = self.position_manager.build_close_position_payload(
                        token_id=token_id,
                        liquidity=position.liquidity,
                        recipient=recipient,
                        currency0=addr0,
                        currency1=addr1,
                        burn=burn
                    )
                    payloads.append(payload)
                else:
                    logger.warning(f"Position {token_id} has zero liquidity, skipping")
            except Exception as e:
                logger.error(f"Error processing position {token_id}: {e}")

        if not payloads:
            logger.warning("No positions to close (all have zero liquidity or errored)")
            return None, False, None

        logger.info(f"Closing {len(payloads)} positions...")

        tx_hash, _ = self.position_manager.multicall(
            payloads=payloads,
            deadline=deadline,
            timeout=timeout
        )

        receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        gas_used = receipt.get('gasUsed', 0)

        success = receipt['status'] == 1
        if success:
            logger.info(f"Positions closed successfully! TX: {tx_hash}")
        else:
            logger.error(f"Transaction failed! TX: {tx_hash}")

        return tx_hash, success, gas_used
