"""
Uniswap V3 Pool Factory Integration

Работа с фабрикой пулов для создания новых пулов и получения адресов существующих.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from web3 import Web3
from web3.contract import Contract
from eth_account.signers.local import LocalAccount
import time
import math

from .abis import ERC20_ABI
from ..utils import NonceManager

# Настройка логгера
logger = logging.getLogger(__name__)


# Uniswap V3 Factory ABI
FACTORY_ABI = [
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"}
        ],
        "name": "getPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"}
        ],
        "name": "createPool",
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "fee", "type": "uint24"}],
        "name": "feeAmountTickSpacing",
        "outputs": [{"name": "", "type": "int24"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": True, "name": "fee", "type": "uint24"},
            {"indexed": False, "name": "tickSpacing", "type": "int24"},
            {"indexed": False, "name": "pool", "type": "address"}
        ],
        "name": "PoolCreated",
        "type": "event"
    }
]

# Pool ABI for initialization
POOL_ABI = [
    {
        "inputs": [{"name": "sqrtPriceX96", "type": "uint160"}],
        "name": "initialize",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint8"},
            {"name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function"
    }
]


@dataclass
class PoolInfo:
    """Информация о пуле."""
    address: str
    token0: str
    token1: str
    fee: int
    tick_spacing: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    initialized: bool


@dataclass
class TokenInfo:
    """Информация о токене."""
    address: str
    symbol: str
    name: str
    decimals: int
    total_supply: int


class PoolFactory:
    """
    Класс для работы с Uniswap V3 Factory.

    Позволяет:
    - Получать адреса существующих пулов
    - Создавать новые пулы
    - Инициализировать пулы с начальной ценой
    - Получать информацию о токенах
    """

    # Factory addresses for different networks
    # Note: BSC has both Uniswap V3 and PancakeSwap V3 with different factories
    FACTORY_ADDRESSES = {
        56: "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7",     # Uniswap V3 on BSC (default)
        1: "0x1F98431c8aD98523631AE4a59f267346ea31F984",      # Uniswap V3 on Ethereum
        8453: "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",   # Uniswap V3 on Base
        97: "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",     # PancakeSwap V3 on BNB Testnet
    }

    # PancakeSwap V3 factory addresses (use with factory_address parameter)
    PANCAKESWAP_FACTORY_ADDRESSES = {
        56: "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",     # PancakeSwap V3 on BSC
    }

    def __init__(
        self,
        w3: Web3,
        account: LocalAccount = None,
        factory_address: str = None,
        chain_id: int = 56,
        nonce_manager: 'NonceManager' = None
    ):
        self.w3 = w3
        self.account = account
        self.chain_id = chain_id
        self.nonce_manager = nonce_manager

        # Use provided address or default for chain
        if factory_address:
            self.factory_address = Web3.to_checksum_address(factory_address)
        else:
            self.factory_address = Web3.to_checksum_address(
                self.FACTORY_ADDRESSES.get(chain_id, self.FACTORY_ADDRESSES[56])
            )

        self.factory = w3.eth.contract(
            address=self.factory_address,
            abi=FACTORY_ABI
        )

    def get_token_info(self, token_address: str) -> TokenInfo:
        """
        Получение информации о токене.

        Args:
            token_address: Адрес токена

        Returns:
            TokenInfo с данными токена
        """
        address = Web3.to_checksum_address(token_address)

        # Extended ERC20 ABI with name and totalSupply
        extended_abi = ERC20_ABI + [
            {
                "inputs": [],
                "name": "name",
                "outputs": [{"name": "", "type": "string"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "symbol",
                "outputs": [{"name": "", "type": "string"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "inputs": [],
                "name": "totalSupply",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]

        token = self.w3.eth.contract(address=address, abi=extended_abi)

        try:
            symbol = token.functions.symbol().call()
        except Exception as e:
            logger.debug(f"Failed to get symbol for {address}: {e}")
            symbol = "UNKNOWN"

        try:
            name = token.functions.name().call()
        except Exception as e:
            logger.debug(f"Failed to get name for {address}: {e}")
            name = "Unknown Token"

        try:
            decimals = token.functions.decimals().call()
        except Exception as e:
            logger.warning(f"Failed to get decimals for {address}: {e}, defaulting to 18")
            decimals = 18

        try:
            total_supply = token.functions.totalSupply().call()
        except Exception as e:
            logger.debug(f"Failed to get totalSupply for {address}: {e}")
            total_supply = 0

        return TokenInfo(
            address=address,
            symbol=symbol,
            name=name,
            decimals=decimals,
            total_supply=total_supply
        )

    def get_pool_address(
        self,
        token0: str,
        token1: str,
        fee: int
    ) -> Optional[str]:
        """
        Получение адреса существующего пула.

        Args:
            token0: Адрес первого токена
            token1: Адрес второго токена
            fee: Fee tier (например, 3000 для 0.3%)

        Returns:
            Адрес пула или None если пул не существует
        """
        token0 = Web3.to_checksum_address(token0)
        token1 = Web3.to_checksum_address(token1)

        pool_address = self.factory.functions.getPool(token0, token1, fee).call()

        if pool_address == "0x0000000000000000000000000000000000000000":
            return None

        return pool_address

    def get_pool_info(self, pool_address: str) -> PoolInfo:
        """
        Получение информации о пуле.

        Args:
            pool_address: Адрес пула

        Returns:
            PoolInfo с данными пула
        """
        address = Web3.to_checksum_address(pool_address)
        pool = self.w3.eth.contract(address=address, abi=POOL_ABI)

        token0 = pool.functions.token0().call()
        token1 = pool.functions.token1().call()
        fee = pool.functions.fee().call()
        liquidity = pool.functions.liquidity().call()

        try:
            slot0 = pool.functions.slot0().call()
            sqrt_price_x96 = slot0[0]
            tick = slot0[1]
            initialized = sqrt_price_x96 > 0
        except Exception as e:
            # ABI mismatch: PancakeSwap V3 slot0 returns 8 fields with feeProtocol:uint32,
            # but our ABI has 7 fields with feeProtocol:uint8. Fallback to raw eth_call.
            logger.debug(f"slot0 ABI decode failed, trying raw eth_call: {e}")
            try:
                raw = self.w3.eth.call({'to': address, 'data': bytes.fromhex('3850c7bd')})
                if len(raw) >= 64:
                    sqrt_price_x96 = int.from_bytes(raw[0:32], 'big')
                    tick_raw = int.from_bytes(raw[32:64], 'big')
                    tick = tick_raw - 2**256 if tick_raw >= 2**255 else tick_raw
                    initialized = sqrt_price_x96 > 0
                    logger.info(f"slot0 raw decode OK: sqrtPriceX96={sqrt_price_x96}, tick={tick}")
                else:
                    sqrt_price_x96 = 0
                    tick = 0
                    initialized = False
            except Exception as e2:
                logger.warning(f"Failed to get slot0 for pool {address}: {e2}")
                sqrt_price_x96 = 0
                tick = 0
                initialized = False

        # Calculate tick spacing from fee
        tick_spacing = self._get_tick_spacing(fee)

        return PoolInfo(
            address=address,
            token0=token0,
            token1=token1,
            fee=fee,
            tick_spacing=tick_spacing,
            sqrt_price_x96=sqrt_price_x96,
            tick=tick,
            liquidity=liquidity,
            initialized=initialized
        )

    def _get_gas_params(self) -> dict:
        """Получение параметров газа: EIP-1559 если поддерживается, иначе legacy."""
        try:
            max_priority_fee = self.w3.eth.max_priority_fee
            base_fee = self.w3.eth.get_block('latest')['baseFeePerGas']
            return {
                'maxPriorityFeePerGas': max_priority_fee,
                'maxFeePerGas': base_fee * 2 + max_priority_fee,
            }
        except Exception:
            return {'gasPrice': self.w3.eth.gas_price}

    def _get_tick_spacing(self, fee: int) -> int:
        """Получение tick spacing для fee tier."""
        # Standard tick spacings
        spacing_map = {
            100: 1,
            500: 10,
            2500: 50,   # PancakeSwap
            3000: 60,   # Uniswap
            10000: 200,
        }
        return spacing_map.get(fee, 60)

    def create_pool(
        self,
        token0: str,
        token1: str,
        fee: int,
        timeout: int = 300
    ) -> Tuple[str, str]:
        """
        Создание нового пула.

        Args:
            token0: Адрес первого токена
            token1: Адрес второго токена
            fee: Fee tier (например, 3000 для 0.3%)
            timeout: Таймаут ожидания транзакции

        Returns:
            (tx_hash, pool_address)
        """
        if not self.account:
            raise ValueError("Account not configured")

        token0 = Web3.to_checksum_address(token0)
        token1 = Web3.to_checksum_address(token1)

        # Ensure correct token order
        if int(token0, 16) > int(token1, 16):
            token0, token1 = token1, token0

        # Check if pool already exists
        existing = self.get_pool_address(token0, token1, fee)
        if existing:
            raise ValueError(f"Pool already exists at {existing}")

        # Build transaction
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx_params = {
                'from': self.account.address,
                'nonce': nonce,
                'gas': 5000000,
            }
            tx_params.update(self._get_gas_params())
            tx = self.factory.functions.createPool(
                token0, token1, fee
            ).build_transaction(tx_params)

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
                raise Exception(f"create_pool transaction reverted! TX: {tx_hash.hex()}")

            # Parse PoolCreated event to get pool address
            pool_address = None
            try:
                events = self.factory.events.PoolCreated().process_receipt(receipt)
                if events:
                    pool_address = events[0]['args']['pool']
            except Exception as e:
                logger.debug(f"Failed to parse PoolCreated event: {e}")
                # Fallback: get pool address after creation
                pool_address = self.get_pool_address(token0, token1, fee)

            return tx_hash.hex(), pool_address

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def initialize_pool(
        self,
        pool_address: str,
        initial_price: float,
        token0_decimals: int = 18,
        token1_decimals: int = 18,
        timeout: int = 300
    ) -> str:
        """
        Инициализация пула с начальной ценой.

        Args:
            pool_address: Адрес пула
            initial_price: Начальная цена (token1 за token0)
            token0_decimals: Decimals token0
            token1_decimals: Decimals token1
            timeout: Таймаут

        Returns:
            tx_hash
        """
        if not self.account:
            raise ValueError("Account not configured")

        address = Web3.to_checksum_address(pool_address)
        pool = self.w3.eth.contract(address=address, abi=POOL_ABI)

        # Calculate sqrtPriceX96 from price
        # sqrtPriceX96 = sqrt(price) * 2^96
        # price = token1/token0, adjusted for decimals
        adjusted_price = initial_price * (10 ** (token1_decimals - token0_decimals))
        sqrt_price = math.sqrt(adjusted_price)
        sqrt_price_x96 = int(sqrt_price * (2 ** 96))

        # Build transaction
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx_params = {
                'from': self.account.address,
                'nonce': nonce,
                'gas': 500000,
            }
            tx_params.update(self._get_gas_params())
            tx = pool.functions.initialize(sqrt_price_x96).build_transaction(tx_params)

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
                raise Exception(f"initialize_pool transaction reverted! TX: {tx_hash.hex()}")

            return tx_hash.hex()

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def create_and_initialize_pool(
        self,
        token0: str,
        token1: str,
        fee: int,
        initial_price: float,
        token0_decimals: int = 18,
        token1_decimals: int = 18,
        timeout: int = 300
    ) -> Tuple[str, str, str]:
        """
        Создание и инициализация пула в одном вызове.

        Args:
            token0: Адрес первого токена
            token1: Адрес второго токена
            fee: Fee tier
            initial_price: Начальная цена
            token0_decimals: Decimals token0
            token1_decimals: Decimals token1
            timeout: Таймаут

        Returns:
            (create_tx_hash, init_tx_hash, pool_address)
        """
        # Create pool
        create_tx, pool_address = self.create_pool(token0, token1, fee, timeout)

        if not pool_address:
            raise ValueError("Failed to get pool address after creation")

        # Initialize pool
        init_tx = self.initialize_pool(
            pool_address,
            initial_price,
            token0_decimals,
            token1_decimals,
            timeout
        )

        return create_tx, init_tx, pool_address

    def price_to_sqrt_price_x96(
        self,
        price: float,
        token0_decimals: int = 18,
        token1_decimals: int = 18
    ) -> int:
        """
        Конвертация цены в sqrtPriceX96.

        Args:
            price: Цена (token1 за token0)
            token0_decimals: Decimals token0
            token1_decimals: Decimals token1

        Returns:
            sqrtPriceX96
        """
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
        Конвертация sqrtPriceX96 в цену.

        Args:
            sqrt_price_x96: sqrtPriceX96 из пула
            token0_decimals: Decimals token0
            token1_decimals: Decimals token1

        Returns:
            Цена (token1 за token0)
        """
        sqrt_price = sqrt_price_x96 / (2 ** 96)
        price = sqrt_price ** 2
        return price / (10 ** (token1_decimals - token0_decimals))
