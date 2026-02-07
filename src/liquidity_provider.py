"""
Main Liquidity Provider Module

Основной модуль для создания bid-ask лесенки ликвидности.
Объединяет расчёт позиций, создание транзакций и батчинг.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple
from web3 import Web3
from eth_account import Account
from eth_account.signers.local import LocalAccount
import time

# Настройка логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Создаём handler для консоли если его нет
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
    print_distribution
)
from .math.ticks import get_tick_spacing, compute_decimal_tick_offset
from .contracts.position_manager import UniswapV3PositionManager, MintParams
from .contracts.pool_factory import PoolFactory
from .contracts.abis import ERC20_ABI
from .multicall.batcher import Multicall3Batcher
from .utils import NonceManager, DecimalsCache, GasEstimator


# Mapping Position Manager address -> Factory address (BSC chain)
POSITION_MANAGER_TO_FACTORY = {
    # Uniswap V3 on BSC
    "0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613".lower(): "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7",
    # PancakeSwap V3 on BSC
    "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364".lower(): "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
}


@dataclass
class LiquidityLadderConfig:
    """
    Конфигурация bid-ask лесенки.

    ВАЖНО: `current_price` - это верхняя граница диапазона позиций.
    Для bid-стратегии это цена рядом с текущей рыночной ценой,
    а `lower_price` - это целевая цена при падении.

    Example:
        # Создание лесенки от $400 до $600 при текущей цене ~$600:
        config = LiquidityLadderConfig(
            current_price=600.0,  # Верхняя граница (около текущей цены)
            lower_price=400.0,    # Нижняя граница (целевая при падении)
            ...
        )
    """
    current_price: float           # Верхняя граница диапазона (около текущей рыночной цены)
    lower_price: float             # Нижняя граница всего диапазона
    total_usd: float               # Общая сумма в USD
    n_positions: int               # Количество позиций
    token0: str                    # Адрес token0 (обычно volatile токен)
    token1: str                    # Адрес token1 (обычно стейблкоин)
    fee_tier: int                  # Fee tier: 100 (0.01%), 500 (0.05%), 2500 (0.25% PancakeSwap), 3000 (0.3% Uniswap), 10000 (1%)
    distribution_type: DistributionType = "linear"
    token0_decimals: int = 18
    token1_decimals: int = 18
    slippage_percent: float = 0.5  # Slippage protection

    @property
    def upper_price(self) -> float:
        """Alias for current_price - верхняя граница диапазона."""
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
        fee_tier: int,
        **kwargs
    ) -> 'LiquidityLadderConfig':
        """
        Фабричный метод с более понятными именами параметров.

        Args:
            upper_price: Верхняя граница диапазона
            lower_price: Нижняя граница диапазона
            total_usd: Общая сумма в USD
            n_positions: Количество позиций
            token0: Адрес volatile токена
            token1: Адрес стейблкоина
            fee_tier: Fee tier: 100, 500, 2500 (PancakeSwap), 3000 (Uniswap), 10000

        Returns:
            LiquidityLadderConfig instance
        """
        return cls(
            current_price=upper_price,
            lower_price=lower_price,
            total_usd=total_usd,
            n_positions=n_positions,
            token0=token0,
            token1=token1,
            fee_tier=fee_tier,
            **kwargs
        )


@dataclass
class InsufficientBalanceError(Exception):
    """Исключение при недостаточном балансе."""
    required: int
    available: int
    token_address: str

    def __str__(self):
        return f"Insufficient balance: required {self.required}, available {self.available} for token {self.token_address}"


@dataclass
class LadderResult:
    """Результат создания лесенки."""
    positions: List[BidAskPosition]
    tx_hash: Optional[str]
    gas_used: Optional[int]
    token_ids: List[int]
    success: bool
    error: Optional[str] = None


class LiquidityProvider:
    """
    Провайдер ликвидности для создания bid-ask лесенок.

    Пример использования:
    ```python
    provider = LiquidityProvider(
        rpc_url="https://bsc-dataseed.binance.org/",
        private_key="0x...",
        position_manager_address="0x46A15B0b27311cedF172AB29E4f4766fbE7F4364"
    )

    config = LiquidityLadderConfig(
        current_price=600.0,          # BNB = $600
        lower_price=400.0,            # Нижняя граница $400
        total_usd=1000,               # Всего $1000
        n_positions=7,                # 7 позиций
        token0="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        token1="0x55d398326f99059fF775485246999027B3197955",  # USDT
        fee_tier=2500,                # 0.25%
        distribution_type="linear"
    )

    # Предпросмотр
    positions = provider.preview_ladder(config)

    # Создание позиций
    result = provider.create_ladder(config)
    print(f"Token IDs: {result.token_ids}")
    print(f"Gas used: {result.gas_used}")
    ```
    """

    def __init__(
        self,
        rpc_url: str,
        private_key: str = None,
        position_manager_address: str = None,
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
        self.proxy = proxy

        if private_key:
            self.account: LocalAccount = Account.from_key(private_key)
        else:
            self.account = None

        self.position_manager_address = position_manager_address
        self.position_manager = None

        if position_manager_address:
            self.position_manager = UniswapV3PositionManager(
                self.w3,
                position_manager_address,
                self.account
            )

        self.batcher = Multicall3Batcher(self.w3, self.account)

        # Initialize utility managers
        self.decimals_cache = DecimalsCache(self.w3)
        self.gas_estimator = GasEstimator(self.w3, buffer_percent=20)
        self.nonce_manager = None
        if self.account:
            self.nonce_manager = NonceManager(self.w3, self.account.address)

    def preview_ladder(self, config: LiquidityLadderConfig) -> List[BidAskPosition]:
        """
        Предпросмотр позиций без создания транзакции.

        Args:
            config: Конфигурация лесенки

        Returns:
            Список позиций с рассчитанными параметрами
        """
        # Determine if we need to invert price based on token order
        # Pool price = token1/token0
        # If token1 is stablecoin: pool price = USD price → invert_price = False
        # If token0 is stablecoin: pool price = 1/USD price → invert_price = True
        #
        # We check by comparing sorted addresses - in Uniswap, token0 < token1 by address
        # config.token0 and config.token1 are what user entered (token0=volatile, token1=stable)
        # After sorting: if stable becomes token0 in pool → need to invert
        t0_addr = Web3.to_checksum_address(config.token0).lower()
        t1_addr = Web3.to_checksum_address(config.token1).lower()

        # config.token1 is stablecoin (user's input)
        # If stablecoin address > volatile address, stablecoin IS token1 in pool
        # Pool price = stablecoin/volatile = USD price → NO inversion
        # If stablecoin address < volatile address, stablecoin IS token0 in pool
        # Pool price = volatile/stablecoin = 1/USD price → NEED inversion
        stablecoin_is_token1_in_pool = t1_addr > t0_addr
        invert_price = not stablecoin_is_token1_in_pool

        # Compute decimal tick offset for mixed-decimal pairs (e.g. USDC 6 / token 18 on BASE)
        dec_offset = compute_decimal_tick_offset(
            token0_address=config.token0,
            token0_decimals=config.token0_decimals,
            token1_address=config.token1,
            token1_decimals=config.token1_decimals,
        )

        logger.info(f"Token order: config.token0={t0_addr[:10]}..., config.token1={t1_addr[:10]}...")
        logger.info(f"Stablecoin is token1 in pool: {stablecoin_is_token1_in_pool}")
        logger.info(f"invert_price: {invert_price}, decimal_tick_offset: {dec_offset}")

        positions = calculate_bid_ask_distribution(
            current_price=config.current_price,
            lower_price=config.lower_price,
            total_usd=config.total_usd,
            n_positions=config.n_positions,
            fee_tier=config.fee_tier,
            distribution_type=config.distribution_type,
            token0_decimals=config.token0_decimals,
            token1_decimals=config.token1_decimals,
            token1_is_stable=True,
            invert_price=invert_price,
            decimal_tick_offset=dec_offset
        )

        return positions

    def print_preview(self, config: LiquidityLadderConfig):
        """Вывод предпросмотра в консоль."""
        positions = self.preview_ladder(config)
        print_distribution(positions)

    def _ensure_token_order(self, token0: str, token1: str) -> Tuple[str, str, bool]:
        """
        Проверка порядка токенов (token0 < token1 по адресу).

        В Uniswap токены должны быть отсортированы по адресу.

        Returns:
            (sorted_token0, sorted_token1, swapped)
        """
        addr0 = Web3.to_checksum_address(token0).lower()
        addr1 = Web3.to_checksum_address(token1).lower()

        if addr0 < addr1:
            return token0, token1, False
        else:
            return token1, token0, True

    def _get_factory_address(self) -> Optional[str]:
        """Get factory address for current position manager."""
        if not self.position_manager_address:
            return None
        pm_lower = self.position_manager_address.lower()
        return POSITION_MANAGER_TO_FACTORY.get(pm_lower)

    def validate_pool_exists(
        self,
        token0: str,
        token1: str,
        fee: int
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Проверка существования и инициализации пула.

        Args:
            token0: Адрес первого токена (НЕ сортированный)
            token1: Адрес второго токена (НЕ сортированный)
            fee: Fee tier

        Returns:
            (exists, pool_address, pool_info) - pool_info содержит tick и sqrtPriceX96 если пул инициализирован
        """
        factory_address = self._get_factory_address()
        if not factory_address:
            logger.warning("Cannot validate pool: factory address unknown for this Position Manager")
            return True, None, None  # Assume it exists if we can't check

        factory = PoolFactory(self.w3, factory_address=factory_address)

        # Sort tokens for pool lookup
        sorted_token0, sorted_token1, _ = self._ensure_token_order(token0, token1)

        pool_address = factory.get_pool_address(sorted_token0, sorted_token1, fee)

        if not pool_address:
            return False, None, None

        # Check if pool is initialized
        try:
            pool_info = factory.get_pool_info(pool_address)
            if not pool_info.initialized:
                return False, pool_address, {"initialized": False}
            return True, pool_address, {
                "initialized": True,
                "tick": pool_info.tick,
                "sqrtPriceX96": pool_info.sqrt_price_x96,
                "liquidity": pool_info.liquidity
            }
        except Exception as e:
            logger.warning(f"Failed to get pool info: {e}")
            return True, pool_address, None  # Pool exists but couldn't get info

    def check_balance(
        self,
        token_address: str,
        required_amount: int,
        address: str = None
    ) -> Tuple[bool, int]:
        """
        Проверка достаточности баланса токена.

        Args:
            token_address: Адрес токена
            required_amount: Требуемая сумма в wei
            address: Адрес для проверки (по умолчанию account)

        Returns:
            (is_sufficient, current_balance)
        """
        balance = self.get_token_balance(token_address, address)
        return balance >= required_amount, balance

    def validate_balances_for_ladder(
        self,
        config: LiquidityLadderConfig
    ) -> Tuple[bool, Optional[str]]:
        """
        Валидация балансов перед созданием лесенки.

        Args:
            config: Конфигурация лесенки

        Returns:
            (is_valid, error_message)
        """
        if not self.account:
            return False, "Account not configured"

        # Для bid-ask стратегии ниже текущей цены нужен стейблкоин
        # Определяем стейблкоин динамически по адресу (не полагаемся на порядок token0/token1)
        STABLECOINS = {
            # BNB Chain
            "0x55d398326f99059ff775485246999027b3197955": 18,  # USDT (BSC)
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18,  # USDC (BSC)
            "0xe9e7cea3dedca5984780bafc599bd69add087d56": 18,  # BUSD (BSC)
            # Base
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC (Base)
            "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": 6,   # USDbC (Base)
            # Ethereum
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,   # USDC (ETH)
            "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,   # USDT (ETH)
        }

        t0_lower = config.token0.lower()
        t1_lower = config.token1.lower()

        if t0_lower in STABLECOINS:
            stablecoin_address = config.token0
            stablecoin_decimals = STABLECOINS[t0_lower]
        elif t1_lower in STABLECOINS:
            stablecoin_address = config.token1
            stablecoin_decimals = STABLECOINS[t1_lower]
        else:
            # Fallback: assume token1 is stablecoin (legacy behavior)
            stablecoin_address = config.token1
            stablecoin_decimals = config.token1_decimals

        total_amount = int(config.total_usd * (10 ** stablecoin_decimals))
        is_sufficient, balance = self.check_balance(stablecoin_address, total_amount)

        if not is_sufficient:
            formatted_required = self.format_amount(total_amount, stablecoin_decimals)
            formatted_balance = self.format_amount(balance, stablecoin_decimals)
            return False, f"Insufficient stablecoin balance: required {formatted_required}, available {formatted_balance}"

        return True, None

    def check_and_approve_tokens(
        self,
        token_address: str,
        amount: int,
        spender: str = None,
        timeout: int = 120
    ) -> Optional[str]:
        """
        Проверка и approve токенов.

        Args:
            token_address: Адрес токена
            amount: Необходимая сумма
            spender: Адрес spender (по умолчанию position_manager)
            timeout: Таймаут ожидания подтверждения в секундах

        Returns:
            tx_hash если был approve, None если уже approved
        """
        if spender is None:
            spender = self.position_manager_address

        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )

        current_allowance = token.functions.allowance(
            self.account.address,
            Web3.to_checksum_address(spender)
        ).call()

        if current_allowance >= amount:
            logger.info(f"Token {token_address[:10]}... already approved")
            return None

        logger.info(f"Approving token {token_address[:10]}...")

        max_uint256 = 2**256 - 1
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
                self.w3.eth.get_transaction_count(self.account.address)

        try:
            tx = approve_fn.build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            logger.info(f"Approved! TX: {tx_hash.hex()}")
            return tx_hash.hex()

        except Exception as e:
            if self.nonce_manager:
                self.nonce_manager.release_nonce(nonce)
            raise

    def create_ladder(
        self,
        config: LiquidityLadderConfig,
        use_multicall: bool = True,
        simulate_first: bool = True,
        timeout: int = 300,
        check_balance: bool = True,
        validated_pool_address: str = None
    ) -> LadderResult:
        """
        Создание bid-ask лесенки ликвидности.

        Args:
            config: Конфигурация лесенки
            use_multicall: Использовать Multicall3 для батчинга
            simulate_first: Симулировать перед отправкой
            timeout: Таймаут ожидания подтверждения транзакции в секундах
            validated_pool_address: Адрес пула, уже проверенного вызывающим кодом (пропускает проверку)
            check_balance: Проверять баланс перед созданием

        Returns:
            LadderResult с результатами
        """
        if not self.account:
            return LadderResult(
                positions=[],
                tx_hash=None,
                gas_used=None,
                token_ids=[],
                success=False,
                error="Account not configured"
            )

        # Расчёт позиций
        positions = self.preview_ladder(config)

        # Проверка порядка токенов для Uniswap (token0 < token1 по адресу)
        token0, token1, swapped = self._ensure_token_order(config.token0, config.token1)

        logger.debug("=== Token Order ===")
        logger.debug(f"Config token0 (volatile): {config.token0[:10]}...")
        logger.debug(f"Config token1 (stablecoin): {config.token1[:10]}...")
        logger.debug(f"Sorted token0: {token0[:10]}...")
        logger.debug(f"Sorted token1: {token1[:10]}...")
        logger.debug(f"Swapped: {swapped}")

        # Detect stablecoin dynamically (same as validate_balances_for_ladder)
        STABLECOINS_CL = {
            "0x55d398326f99059ff775485246999027b3197955": 18,  # USDT (BSC)
            "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18,  # USDC (BSC)
            "0xe9e7cea3dedca5984780bafc599bd69add087d56": 18,  # BUSD (BSC)
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC (Base)
            "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": 6,   # USDbC (Base)
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,   # USDC (ETH)
            "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,   # USDT (ETH)
        }
        t0_low = config.token0.lower()
        t1_low = config.token1.lower()
        if t0_low in STABLECOINS_CL:
            stablecoin = config.token0
            stablecoin_decimals = STABLECOINS_CL[t0_low]
        elif t1_low in STABLECOINS_CL:
            stablecoin = config.token1
            stablecoin_decimals = STABLECOINS_CL[t1_low]
        else:
            stablecoin = config.token1
            stablecoin_decimals = config.token1_decimals

        total_stablecoin_amount = int(config.total_usd * (10 ** stablecoin_decimals))

        # Проверка баланса перед созданием
        if check_balance:
            is_valid, error_msg = self.validate_balances_for_ladder(config)
            if not is_valid:
                return LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    success=False,
                    error=error_msg
                )
            logger.info("Balance check passed")

        # Проверка существования пула
        # Пропускаем если пул уже проверен вызывающим кодом
        if validated_pool_address:
            logger.info(f"Using pre-validated pool: {validated_pool_address}")
            pool_address = validated_pool_address
        else:
            logger.info(f"Checking if pool exists for {token0[:10]}.../{token1[:10]}... fee={config.fee_tier}")
            pool_exists, pool_address, pool_info = self.validate_pool_exists(
                config.token0, config.token1, config.fee_tier
            )

            if not pool_exists:
                if pool_address:
                    # Pool exists but not initialized
                    error_msg = (
                        f"Pool exists at {pool_address} but is NOT INITIALIZED. "
                        f"You need to initialize the pool with a starting price first."
                    )
                else:
                    # Pool doesn't exist at all
                    error_msg = (
                        f"Pool does NOT EXIST for tokens {config.token0[:10]}.../{config.token1[:10]}... "
                        f"with fee tier {config.fee_tier}. "
                        f"You need to CREATE the pool first before adding liquidity."
                    )
                logger.error(error_msg)
                return LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    success=False,
                    error=error_msg
                )

            if pool_info:
                logger.info(f"Pool found at {pool_address}")
                logger.info(f"Pool current tick: {pool_info.get('tick')}, liquidity: {pool_info.get('liquidity')}")

        # Approve стейблкоин (оригинальный config.token1)
        logger.info(f"Approving stablecoin {stablecoin[:15]}... amount={total_stablecoin_amount} to PM={self.position_manager_address[:15]}...")
        self.check_and_approve_tokens(stablecoin, total_stablecoin_amount, timeout=timeout)

        # Verify approval
        from .contracts.abis import ERC20_ABI
        token_contract = self.w3.eth.contract(address=Web3.to_checksum_address(stablecoin), abi=ERC20_ABI)
        current_allowance = token_contract.functions.allowance(
            self.account.address,
            self.position_manager_address
        ).call()
        logger.info(f"Current allowance: {current_allowance}")

        # Очищаем батчер
        self.batcher.clear()

        deadline = int(time.time()) + 3600

        # Добавляем mint вызовы
        for pos in positions:
            # Сумма стейблкоина для этой позиции
            stablecoin_amount = int(pos.usd_amount * (10 ** stablecoin_decimals))

            # Slippage protection
            stablecoin_amount_min = int(stablecoin_amount * (1 - config.slippage_percent / 100))

            # Определяем тики и amounts в зависимости от порядка токенов
            #
            # ВАЖНО: preview_ladder уже рассчитал тики с учётом invert_price!
            # - Когда stablecoin_addr < volatile_addr: invert_price=True
            # - Тики УЖЕ рассчитаны для pool price = volatile/stablecoin
            # - НЕ нужно негировать тики снова!
            #
            # swapped=True означает что config.token1 (стейблкоин) стал token0 после сортировки
            # Это совпадает с условием invert_price=True в preview_ladder

            # Тики всегда берём напрямую из preview_ladder (они уже правильные)
            tick_lower = pos.tick_lower
            tick_upper = pos.tick_upper

            # Determine if stablecoin is sorted token0 or token1
            stablecoin_is_sorted_token0 = stablecoin.lower() == token0.lower()

            if stablecoin_is_sorted_token0:
                # Stablecoin is pool's token0 → provide as amount0
                amount0_desired = stablecoin_amount
                amount1_desired = 0
                amount0_min = stablecoin_amount_min
                amount1_min = 0
            else:
                # Stablecoin is pool's token1 → provide as amount1
                amount0_desired = 0
                amount1_desired = stablecoin_amount
                amount0_min = 0
                amount1_min = stablecoin_amount_min

            self.batcher.add_mint_call(
                position_manager=self.position_manager_address,
                token0=token0,
                token1=token1,
                fee=config.fee_tier,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                amount0_desired=amount0_desired,
                amount1_desired=amount1_desired,
                recipient=self.account.address,
                deadline=deadline,
                amount0_min=amount0_min,
                amount1_min=amount1_min,
                allow_failure=False
            )

        logger.info(f"Prepared {len(self.batcher)} mint calls")

        # Get expected tick spacing for validation
        tick_spacing = get_tick_spacing(config.fee_tier, allow_custom=False)
        logger.info(f"Expected tick_spacing for fee {config.fee_tier}: {tick_spacing}")

        if positions:
            # Выводим INFO уровень для отладки
            logger.info("=== First position params ===")
            logger.info(f"Position Manager: {self.position_manager_address}")
            logger.info(f"Token0: {token0}")
            logger.info(f"Token1: {token1}")
            logger.info(f"Fee: {config.fee_tier}")

            # Тики напрямую из preview_ladder (уже с учётом invert_price)
            actual_tick_lower = positions[0].tick_lower
            actual_tick_upper = positions[0].tick_upper
            logger.info(f"Ticks: lower={actual_tick_lower}, upper={actual_tick_upper}")
            logger.info(f"Token order swapped: {swapped}")

            # Log first position amounts
            first_stablecoin_amount = int(positions[0].usd_amount * (10 ** stablecoin_decimals))
            stable_is_t0 = stablecoin.lower() == token0.lower()
            if stable_is_t0:
                logger.info(f"First position amounts: amount0={first_stablecoin_amount} (stablecoin), amount1=0")
            else:
                logger.info(f"First position amounts: amount0=0, amount1={first_stablecoin_amount} (stablecoin)")
            logger.info(f"First position USD: ${positions[0].usd_amount:.2f}, liquidity={positions[0].liquidity}")

            # Validate ticks
            logger.info("=== Tick Validation ===")

            # Check tickLower < tickUpper
            if actual_tick_lower >= actual_tick_upper:
                error_msg = f"tickLower ({actual_tick_lower}) >= tickUpper ({actual_tick_upper}). Invalid tick range!"
                logger.error(error_msg)
                return LadderResult(
                    positions=positions,
                    tx_hash=None, gas_used=None, token_ids=[],
                    success=False, error=error_msg
                )

            # Check tick alignment
            if actual_tick_lower % tick_spacing != 0:
                error_msg = f"tickLower ({actual_tick_lower}) not aligned to tick_spacing ({tick_spacing}). Remainder: {actual_tick_lower % tick_spacing}"
                logger.error(error_msg)
                return LadderResult(
                    positions=positions,
                    tx_hash=None, gas_used=None, token_ids=[],
                    success=False, error=error_msg
                )

            if actual_tick_upper % tick_spacing != 0:
                error_msg = f"tickUpper ({actual_tick_upper}) not aligned to tick_spacing ({tick_spacing}). Remainder: {actual_tick_upper % tick_spacing}"
                logger.error(error_msg)
                return LadderResult(
                    positions=positions,
                    tx_hash=None, gas_used=None, token_ids=[],
                    success=False, error=error_msg
                )

            logger.info(f"Tick alignment OK: tickLower % {tick_spacing} = 0, tickUpper % {tick_spacing} = 0")

        # Симуляция
        if simulate_first:
            logger.info("Simulating transaction...")

            # Выводим debug info о первом вызове на INFO уровне
            debug_info = self.batcher.debug_first_call()
            logger.info("=== Debug First Call ===")
            for key, value in debug_info.items():
                if key == "mint_params":
                    logger.info("mint_params:")
                    for pk, pv in value.items():
                        logger.info(f"  {pk}: {pv}")
                else:
                    logger.info(f"{key}: {value}")

            # Пробуем симулировать первый вызов напрямую (без Multicall)
            logger.debug("=== Testing first call directly ===")
            single_result = self.batcher.simulate_single_call(0)
            logger.debug(f"Result: {single_result}")

            if "Error" in single_result or "Revert" in single_result:
                return LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    success=False,
                    error=f"Single call test failed: {single_result}"
                )

            try:
                results = self.batcher.simulate(position_manager_address=self.position_manager_address)
                logger.info(f"Simulation successful! All {len(results)} calls passed")
            except Exception as e:
                return LadderResult(
                    positions=positions,
                    tx_hash=None,
                    gas_used=None,
                    token_ids=[],
                    success=False,
                    error=f"Simulation failed: {e}"
                )

        # Выполнение
        logger.info("Executing transaction...")
        try:
            tx_hash, results, receipt, token_ids = self.batcher.execute(
                timeout=timeout,
                position_manager_address=self.position_manager_address
            )
            logger.info(f"Transaction sent: {tx_hash}")

            # Получаем gas_used из receipt
            gas_used = receipt.get('gasUsed', None)

            if token_ids:
                logger.info(f"Created {len(token_ids)} positions with token IDs: {token_ids}")

            return LadderResult(
                positions=positions,
                tx_hash=tx_hash,
                gas_used=gas_used,
                token_ids=token_ids,
                success=True
            )

        except Exception as e:
            return LadderResult(
                positions=positions,
                tx_hash=None,
                gas_used=None,
                token_ids=[],
                success=False,
                error=str(e)
            )

    def close_positions(
        self,
        token_ids: List[int],
        recipient: str = None,
        timeout: int = 300
    ) -> Tuple[str, bool, Optional[int]]:
        """
        Закрытие нескольких позиций одной транзакцией.

        Args:
            token_ids: Список ID позиций (NFT)
            recipient: Получатель токенов
            timeout: Таймаут ожидания подтверждения в секундах

        Returns:
            (tx_hash, success, gas_used)
        """
        if recipient is None:
            recipient = self.account.address

        self.batcher.clear()

        for token_id in token_ids:
            # Получаем информацию о позиции
            position = self.position_manager.get_position(token_id)
            liquidity = position['liquidity']

            if liquidity > 0:
                self.batcher.add_close_position_calls(
                    position_manager=self.position_manager_address,
                    token_id=token_id,
                    liquidity=liquidity,
                    recipient=recipient
                )

        logger.info(f"Closing {len(token_ids)} positions...")
        tx_hash, _, receipt, _ = self.batcher.execute(
            timeout=timeout,
            position_manager_address=self.position_manager_address
        )
        gas_used = receipt.get('gasUsed', None)

        return tx_hash, True, gas_used

    def get_token_balance(self, token_address: str, address: str = None) -> int:
        """Получение баланса токена."""
        if address is None:
            address = self.account.address

        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )

        return token.functions.balanceOf(Web3.to_checksum_address(address)).call()

    def format_amount(self, amount: int, decimals: int = 18) -> str:
        """Форматирование суммы для вывода."""
        return f"{amount / (10 ** decimals):,.4f}"
