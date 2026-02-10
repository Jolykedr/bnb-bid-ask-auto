"""
Tests for V3 PoolFactory.

Тесты для фабрики пулов Uniswap V3 / PancakeSwap V3.
Все вызовы Web3 замоканы - тесты работают без подключения к блокчейну.
"""

import pytest
import math
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from web3 import Web3

from src.contracts.pool_factory import PoolFactory, PoolInfo, TokenInfo


# ============================================================
# Тестовые адреса (отсортированные по числовому значению)
# ============================================================
ADDR_LOW = "0x1111111111111111111111111111111111111111"
ADDR_HIGH = "0x9999999999999999999999999999999999999999"
ADDR_POOL = "0x5555555555555555555555555555555555555555"
ADDR_FACTORY = "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7"
ADDR_ZERO = "0x0000000000000000000000000000000000000000"

# sqrtPriceX96 для price=1 при равных decimals: sqrt(1) * 2^96
SQRT_PRICE_X96_ONE = int(math.sqrt(1) * (2 ** 96))


# ============================================================
# PoolInfo Dataclass
# ============================================================

class TestPoolInfo:
    """Тесты для датакласса PoolInfo."""

    def test_all_fields(self):
        """Все поля корректно сохраняются."""
        info = PoolInfo(
            address=ADDR_POOL,
            token0=ADDR_LOW,
            token1=ADDR_HIGH,
            fee=3000,
            tick_spacing=60,
            sqrt_price_x96=SQRT_PRICE_X96_ONE,
            tick=0,
            liquidity=1_000_000,
            initialized=True,
        )
        assert info.address == ADDR_POOL
        assert info.token0 == ADDR_LOW
        assert info.token1 == ADDR_HIGH
        assert info.fee == 3000
        assert info.tick_spacing == 60
        assert info.sqrt_price_x96 == SQRT_PRICE_X96_ONE
        assert info.tick == 0
        assert info.liquidity == 1_000_000
        assert info.initialized is True

    def test_uninitialized_pool(self):
        """Неинициализированный пул: sqrtPriceX96=0, initialized=False."""
        info = PoolInfo(
            address=ADDR_POOL,
            token0=ADDR_LOW,
            token1=ADDR_HIGH,
            fee=500,
            tick_spacing=10,
            sqrt_price_x96=0,
            tick=0,
            liquidity=0,
            initialized=False,
        )
        assert info.initialized is False
        assert info.sqrt_price_x96 == 0
        assert info.liquidity == 0

    def test_negative_tick(self):
        """Пул с отрицательным тиком."""
        info = PoolInfo(
            address=ADDR_POOL,
            token0=ADDR_LOW,
            token1=ADDR_HIGH,
            fee=10000,
            tick_spacing=200,
            sqrt_price_x96=2**96 // 2,
            tick=-6932,
            liquidity=500,
            initialized=True,
        )
        assert info.tick == -6932
        assert info.tick_spacing == 200

    def test_equality(self):
        """Датаклассы с одинаковыми полями равны."""
        kwargs = dict(
            address=ADDR_POOL, token0=ADDR_LOW, token1=ADDR_HIGH,
            fee=3000, tick_spacing=60, sqrt_price_x96=100, tick=10,
            liquidity=50, initialized=True,
        )
        assert PoolInfo(**kwargs) == PoolInfo(**kwargs)


# ============================================================
# TokenInfo Dataclass
# ============================================================

class TestTokenInfo:
    """Тесты для датакласса TokenInfo."""

    def test_all_fields(self):
        """Все поля корректно сохраняются."""
        info = TokenInfo(
            address=ADDR_LOW,
            symbol="WBNB",
            name="Wrapped BNB",
            decimals=18,
            total_supply=10_000_000 * 10**18,
        )
        assert info.address == ADDR_LOW
        assert info.symbol == "WBNB"
        assert info.name == "Wrapped BNB"
        assert info.decimals == 18
        assert info.total_supply == 10_000_000 * 10**18

    def test_six_decimals_token(self):
        """Токен с 6 decimals (USDC)."""
        info = TokenInfo(
            address=ADDR_HIGH,
            symbol="USDC",
            name="USD Coin",
            decimals=6,
            total_supply=1_000_000_000 * 10**6,
        )
        assert info.decimals == 6

    def test_equality(self):
        """Датаклассы с одинаковыми полями равны."""
        kwargs = dict(
            address=ADDR_LOW, symbol="TKN", name="Token",
            decimals=18, total_supply=0,
        )
        assert TokenInfo(**kwargs) == TokenInfo(**kwargs)


# ============================================================
# PoolFactory
# ============================================================

class TestPoolFactory:
    """Тесты для класса PoolFactory."""

    # ----------------------------------------------------------
    # Fixtures
    # ----------------------------------------------------------

    @pytest.fixture
    def mock_w3(self):
        """Мок Web3 instance для PoolFactory."""
        w3 = Mock(spec=Web3)
        w3.eth = Mock()
        w3.eth.contract = Mock()
        w3.eth.gas_price = 5_000_000_000
        w3.eth.get_transaction_count = Mock(return_value=0)
        w3.eth.send_raw_transaction = Mock(return_value=b'\x12\x34' * 16)
        w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 300_000,
            'logs': [],
            'transactionHash': b'\x12\x34' * 16,
        })
        return w3

    @pytest.fixture
    def mock_account(self):
        """Мок аккаунта для подписи транзакций."""
        account = Mock()
        account.address = "0x1234567890123456789012345678901234567890"
        account.sign_transaction = Mock(return_value=Mock(raw_transaction=b'signed'))
        return account

    @pytest.fixture
    def factory(self, mock_w3):
        """PoolFactory с замоканным __init__."""
        with patch.object(PoolFactory, '__init__', lambda self, *a, **kw: None):
            f = PoolFactory.__new__(PoolFactory)
            f.w3 = mock_w3
            f.chain_id = 56
            f.factory_address = ADDR_FACTORY
            f.account = None
            mock_contract = Mock()
            mock_contract.functions = Mock()
            mock_contract.events = Mock()
            f.factory = mock_contract
            return f

    @pytest.fixture
    def factory_with_account(self, factory, mock_account):
        """PoolFactory с подключенным аккаунтом."""
        factory.account = mock_account
        return factory

    # ----------------------------------------------------------
    # __init__ / конструктор
    # ----------------------------------------------------------

    class TestInit:
        """Тесты конструктора PoolFactory."""

        @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
        def test_default_factory_address_bsc(self, _mock_checksum):
            """По умолчанию используется Uniswap V3 Factory на BSC (chain_id=56)."""
            w3 = Mock(spec=Web3)
            w3.eth = Mock()
            w3.eth.contract = Mock(return_value=Mock())

            pf = PoolFactory(w3, chain_id=56)

            assert pf.factory_address == PoolFactory.FACTORY_ADDRESSES[56]
            assert pf.chain_id == 56
            assert pf.account is None

        @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
        def test_default_factory_address_base(self, _mock_checksum):
            """Factory на BASE (chain_id=8453)."""
            w3 = Mock(spec=Web3)
            w3.eth = Mock()
            w3.eth.contract = Mock(return_value=Mock())

            pf = PoolFactory(w3, chain_id=8453)

            assert pf.factory_address == PoolFactory.FACTORY_ADDRESSES[8453]

        @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
        def test_custom_factory_address(self, _mock_checksum):
            """Пользовательский адрес фабрики переопределяет дефолтный."""
            w3 = Mock(spec=Web3)
            w3.eth = Mock()
            w3.eth.contract = Mock(return_value=Mock())
            custom = "0xCustomFactoryAddress000000000000000000000"

            pf = PoolFactory(w3, factory_address=custom, chain_id=56)

            assert pf.factory_address == custom

        @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
        def test_unknown_chain_falls_back_to_bsc(self, _mock_checksum):
            """Неизвестный chain_id откатывается к BSC factory."""
            w3 = Mock(spec=Web3)
            w3.eth = Mock()
            w3.eth.contract = Mock(return_value=Mock())

            pf = PoolFactory(w3, chain_id=99999)

            assert pf.factory_address == PoolFactory.FACTORY_ADDRESSES[56]

        @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
        def test_with_account(self, _mock_checksum):
            """Аккаунт сохраняется для подписи транзакций."""
            w3 = Mock(spec=Web3)
            w3.eth = Mock()
            w3.eth.contract = Mock(return_value=Mock())
            account = Mock()
            account.address = "0xACCOUNT"

            pf = PoolFactory(w3, account=account, chain_id=56)

            assert pf.account is account

    # ----------------------------------------------------------
    # get_token_info
    # ----------------------------------------------------------

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_token_info_success(self, _mock_checksum, factory):
        """Успешное получение информации о токене."""
        mock_token = Mock()
        mock_token.functions.symbol.return_value.call.return_value = "WBNB"
        mock_token.functions.name.return_value.call.return_value = "Wrapped BNB"
        mock_token.functions.decimals.return_value.call.return_value = 18
        mock_token.functions.totalSupply.return_value.call.return_value = 5_000_000 * 10**18

        factory.w3.eth.contract.return_value = mock_token

        result = factory.get_token_info(ADDR_LOW)

        assert isinstance(result, TokenInfo)
        assert result.address == ADDR_LOW
        assert result.symbol == "WBNB"
        assert result.name == "Wrapped BNB"
        assert result.decimals == 18
        assert result.total_supply == 5_000_000 * 10**18

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_token_info_six_decimals(self, _mock_checksum, factory):
        """Токен с 6 decimals (USDC)."""
        mock_token = Mock()
        mock_token.functions.symbol.return_value.call.return_value = "USDC"
        mock_token.functions.name.return_value.call.return_value = "USD Coin"
        mock_token.functions.decimals.return_value.call.return_value = 6
        mock_token.functions.totalSupply.return_value.call.return_value = 10**9 * 10**6

        factory.w3.eth.contract.return_value = mock_token

        result = factory.get_token_info(ADDR_HIGH)

        assert result.decimals == 6
        assert result.symbol == "USDC"

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_token_info_symbol_fallback(self, _mock_checksum, factory):
        """Ошибка при получении symbol -> 'UNKNOWN'."""
        mock_token = Mock()
        mock_token.functions.symbol.return_value.call.side_effect = Exception("revert")
        mock_token.functions.name.return_value.call.return_value = "Some Token"
        mock_token.functions.decimals.return_value.call.return_value = 18
        mock_token.functions.totalSupply.return_value.call.return_value = 100

        factory.w3.eth.contract.return_value = mock_token

        result = factory.get_token_info(ADDR_LOW)

        assert result.symbol == "UNKNOWN"
        assert result.name == "Some Token"

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_token_info_name_fallback(self, _mock_checksum, factory):
        """Ошибка при получении name -> 'Unknown Token'."""
        mock_token = Mock()
        mock_token.functions.symbol.return_value.call.return_value = "TKN"
        mock_token.functions.name.return_value.call.side_effect = Exception("revert")
        mock_token.functions.decimals.return_value.call.return_value = 8
        mock_token.functions.totalSupply.return_value.call.return_value = 0

        factory.w3.eth.contract.return_value = mock_token

        result = factory.get_token_info(ADDR_LOW)

        assert result.name == "Unknown Token"
        assert result.symbol == "TKN"

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_token_info_decimals_fallback(self, _mock_checksum, factory):
        """Ошибка при получении decimals -> 18 по умолчанию."""
        mock_token = Mock()
        mock_token.functions.symbol.return_value.call.return_value = "X"
        mock_token.functions.name.return_value.call.return_value = "X Token"
        mock_token.functions.decimals.return_value.call.side_effect = Exception("revert")
        mock_token.functions.totalSupply.return_value.call.return_value = 0

        factory.w3.eth.contract.return_value = mock_token

        result = factory.get_token_info(ADDR_LOW)

        assert result.decimals == 18

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_token_info_total_supply_fallback(self, _mock_checksum, factory):
        """Ошибка при получении totalSupply -> 0."""
        mock_token = Mock()
        mock_token.functions.symbol.return_value.call.return_value = "Y"
        mock_token.functions.name.return_value.call.return_value = "Y Token"
        mock_token.functions.decimals.return_value.call.return_value = 18
        mock_token.functions.totalSupply.return_value.call.side_effect = Exception("revert")

        factory.w3.eth.contract.return_value = mock_token

        result = factory.get_token_info(ADDR_LOW)

        assert result.total_supply == 0

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_token_info_all_fallbacks(self, _mock_checksum, factory):
        """Все вызовы падают -> все значения по умолчанию."""
        mock_token = Mock()
        mock_token.functions.symbol.return_value.call.side_effect = Exception("fail")
        mock_token.functions.name.return_value.call.side_effect = Exception("fail")
        mock_token.functions.decimals.return_value.call.side_effect = Exception("fail")
        mock_token.functions.totalSupply.return_value.call.side_effect = Exception("fail")

        factory.w3.eth.contract.return_value = mock_token

        result = factory.get_token_info(ADDR_LOW)

        assert result.symbol == "UNKNOWN"
        assert result.name == "Unknown Token"
        assert result.decimals == 18
        assert result.total_supply == 0
        assert result.address == ADDR_LOW

    # ----------------------------------------------------------
    # get_pool_address
    # ----------------------------------------------------------

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_pool_address_exists(self, _mock_checksum, factory):
        """Пул существует - возвращается адрес."""
        factory.factory.functions.getPool.return_value.call.return_value = ADDR_POOL

        result = factory.get_pool_address(ADDR_LOW, ADDR_HIGH, 3000)

        assert result == ADDR_POOL
        factory.factory.functions.getPool.assert_called_once_with(ADDR_LOW, ADDR_HIGH, 3000)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_pool_address_not_exists(self, _mock_checksum, factory):
        """Пул не существует - возвращается None."""
        factory.factory.functions.getPool.return_value.call.return_value = ADDR_ZERO

        result = factory.get_pool_address(ADDR_LOW, ADDR_HIGH, 3000)

        assert result is None

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_pool_address_different_fees(self, _mock_checksum, factory):
        """Проверка вызова getPool с разными fee tier."""
        factory.factory.functions.getPool.return_value.call.return_value = ADDR_POOL

        for fee in [100, 500, 2500, 3000, 10000]:
            factory.get_pool_address(ADDR_LOW, ADDR_HIGH, fee)

        assert factory.factory.functions.getPool.call_count == 5

    # ----------------------------------------------------------
    # get_pool_info
    # ----------------------------------------------------------

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_pool_info_initialized(self, _mock_checksum, factory):
        """Информация об инициализированном пуле."""
        mock_pool = Mock()
        mock_pool.functions.token0.return_value.call.return_value = ADDR_LOW
        mock_pool.functions.token1.return_value.call.return_value = ADDR_HIGH
        mock_pool.functions.fee.return_value.call.return_value = 3000
        mock_pool.functions.liquidity.return_value.call.return_value = 1_000_000
        # slot0: (sqrtPriceX96, tick, observationIndex, observationCardinality,
        #         observationCardinalityNext, feeProtocol, unlocked)
        mock_pool.functions.slot0.return_value.call.return_value = [
            SQRT_PRICE_X96_ONE, 0, 0, 1, 1, 0, True
        ]

        factory.w3.eth.contract.return_value = mock_pool

        result = factory.get_pool_info(ADDR_POOL)

        assert isinstance(result, PoolInfo)
        assert result.address == ADDR_POOL
        assert result.token0 == ADDR_LOW
        assert result.token1 == ADDR_HIGH
        assert result.fee == 3000
        assert result.tick_spacing == 60
        assert result.sqrt_price_x96 == SQRT_PRICE_X96_ONE
        assert result.tick == 0
        assert result.liquidity == 1_000_000
        assert result.initialized is True

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_pool_info_with_negative_tick(self, _mock_checksum, factory):
        """Пул с отрицательным тиком."""
        mock_pool = Mock()
        mock_pool.functions.token0.return_value.call.return_value = ADDR_LOW
        mock_pool.functions.token1.return_value.call.return_value = ADDR_HIGH
        mock_pool.functions.fee.return_value.call.return_value = 500
        mock_pool.functions.liquidity.return_value.call.return_value = 5_000
        mock_pool.functions.slot0.return_value.call.return_value = [
            2**96 // 2, -6932, 0, 1, 1, 0, True
        ]

        factory.w3.eth.contract.return_value = mock_pool

        result = factory.get_pool_info(ADDR_POOL)

        assert result.tick == -6932
        assert result.tick_spacing == 10
        assert result.initialized is True

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_pool_info_slot0_fails(self, _mock_checksum, factory):
        """slot0 падает -> initialized=False, sqrtPriceX96=0, tick=0."""
        mock_pool = Mock()
        mock_pool.functions.token0.return_value.call.return_value = ADDR_LOW
        mock_pool.functions.token1.return_value.call.return_value = ADDR_HIGH
        mock_pool.functions.fee.return_value.call.return_value = 2500
        mock_pool.functions.liquidity.return_value.call.return_value = 0
        mock_pool.functions.slot0.return_value.call.side_effect = Exception("not initialized")

        factory.w3.eth.contract.return_value = mock_pool

        result = factory.get_pool_info(ADDR_POOL)

        assert result.initialized is False
        assert result.sqrt_price_x96 == 0
        assert result.tick == 0
        assert result.tick_spacing == 50  # fee=2500 -> tick_spacing=50

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_get_pool_info_zero_sqrt_price(self, _mock_checksum, factory):
        """sqrtPriceX96=0 -> initialized=False."""
        mock_pool = Mock()
        mock_pool.functions.token0.return_value.call.return_value = ADDR_LOW
        mock_pool.functions.token1.return_value.call.return_value = ADDR_HIGH
        mock_pool.functions.fee.return_value.call.return_value = 3000
        mock_pool.functions.liquidity.return_value.call.return_value = 0
        mock_pool.functions.slot0.return_value.call.return_value = [
            0, 0, 0, 0, 0, 0, False
        ]

        factory.w3.eth.contract.return_value = mock_pool

        result = factory.get_pool_info(ADDR_POOL)

        assert result.initialized is False

    # ----------------------------------------------------------
    # _get_tick_spacing
    # ----------------------------------------------------------

    def test_get_tick_spacing_known_fees(self, factory):
        """Маппинг известных fee -> tick_spacing."""
        expected = {
            100: 1,
            500: 10,
            2500: 50,
            3000: 60,
            10000: 200,
        }
        for fee, spacing in expected.items():
            assert factory._get_tick_spacing(fee) == spacing, (
                f"fee={fee} должен давать tick_spacing={spacing}"
            )

    def test_get_tick_spacing_unknown_fee(self, factory):
        """Неизвестный fee -> дефолтный tick_spacing=60."""
        assert factory._get_tick_spacing(1234) == 60
        assert factory._get_tick_spacing(0) == 60
        assert factory._get_tick_spacing(50000) == 60

    # ----------------------------------------------------------
    # create_pool
    # ----------------------------------------------------------

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_create_pool_no_account(self, _mock_checksum, factory):
        """Без аккаунта -> ValueError."""
        factory.account = None

        with pytest.raises(ValueError, match="Account not configured"):
            factory.create_pool(ADDR_LOW, ADDR_HIGH, 3000)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_create_pool_already_exists(self, _mock_checksum, factory_with_account):
        """Пул уже существует -> ValueError."""
        factory_with_account.factory.functions.getPool.return_value.call.return_value = ADDR_POOL

        with pytest.raises(ValueError, match="Pool already exists"):
            factory_with_account.create_pool(ADDR_LOW, ADDR_HIGH, 3000)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_create_pool_success(self, _mock_checksum, factory_with_account):
        """Успешное создание пула."""
        f = factory_with_account

        # getPool returns zero address (pool doesn't exist)
        f.factory.functions.getPool.return_value.call.return_value = ADDR_ZERO

        # createPool().build_transaction() returns tx dict
        f.factory.functions.createPool.return_value.build_transaction.return_value = {
            'to': ADDR_FACTORY,
            'data': '0x...',
            'gas': 5000000,
        }

        # PoolCreated event
        mock_event_processor = Mock()
        mock_event_processor.process_receipt.return_value = [
            {'args': {'pool': ADDR_POOL}}
        ]
        f.factory.events.PoolCreated.return_value = mock_event_processor

        tx_hash, pool_address = f.create_pool(ADDR_LOW, ADDR_HIGH, 3000)

        assert pool_address == ADDR_POOL
        assert isinstance(tx_hash, str)
        f.account.sign_transaction.assert_called_once()
        f.w3.eth.send_raw_transaction.assert_called_once()
        f.w3.eth.wait_for_transaction_receipt.assert_called_once()

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_create_pool_sorts_tokens(self, _mock_checksum, factory_with_account):
        """Токены сортируются: если token0 > token1, они меняются местами."""
        f = factory_with_account

        # Pool doesn't exist
        f.factory.functions.getPool.return_value.call.return_value = ADDR_ZERO

        f.factory.functions.createPool.return_value.build_transaction.return_value = {}

        mock_event_processor = Mock()
        mock_event_processor.process_receipt.return_value = [
            {'args': {'pool': ADDR_POOL}}
        ]
        f.factory.events.PoolCreated.return_value = mock_event_processor

        # Передаем HIGH, LOW (обратный порядок)
        f.create_pool(ADDR_HIGH, ADDR_LOW, 3000)

        # createPool должен быть вызван с отсортированными адресами (LOW, HIGH)
        f.factory.functions.createPool.assert_called_once_with(ADDR_LOW, ADDR_HIGH, 3000)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_create_pool_event_parse_fails_fallback(self, _mock_checksum, factory_with_account):
        """Если парсинг PoolCreated упал -> fallback на get_pool_address."""
        f = factory_with_account

        # Первый вызов getPool -> zero address (пул не существует)
        # Второй вызов getPool (fallback после ошибки) -> адрес пула
        f.factory.functions.getPool.return_value.call.side_effect = [
            ADDR_ZERO,  # check in create_pool
            ADDR_POOL,  # fallback after event parse failure
        ]

        f.factory.functions.createPool.return_value.build_transaction.return_value = {}

        # Event parsing fails
        mock_event_processor = Mock()
        mock_event_processor.process_receipt.side_effect = Exception("parse error")
        f.factory.events.PoolCreated.return_value = mock_event_processor

        tx_hash, pool_address = f.create_pool(ADDR_LOW, ADDR_HIGH, 3000)

        assert pool_address == ADDR_POOL

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_create_pool_tx_params(self, _mock_checksum, factory_with_account):
        """Проверка параметров транзакции: from, nonce, gas, gasPrice."""
        f = factory_with_account

        f.factory.functions.getPool.return_value.call.return_value = ADDR_ZERO
        f.factory.functions.createPool.return_value.build_transaction.return_value = {}

        mock_event_processor = Mock()
        mock_event_processor.process_receipt.return_value = [
            {'args': {'pool': ADDR_POOL}}
        ]
        f.factory.events.PoolCreated.return_value = mock_event_processor

        f.create_pool(ADDR_LOW, ADDR_HIGH, 3000)

        # Проверяем build_transaction вызван с правильными параметрами
        call_kwargs = f.factory.functions.createPool.return_value.build_transaction.call_args[0][0]
        assert call_kwargs['from'] == f.account.address
        assert call_kwargs['gas'] == 5000000
        assert call_kwargs['gasPrice'] == 5_000_000_000
        assert call_kwargs['nonce'] == 0

    # ----------------------------------------------------------
    # initialize_pool
    # ----------------------------------------------------------

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_initialize_pool_no_account(self, _mock_checksum, factory):
        """Без аккаунта -> ValueError."""
        factory.account = None

        with pytest.raises(ValueError, match="Account not configured"):
            factory.initialize_pool(ADDR_POOL, 1.0)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_initialize_pool_equal_decimals(self, _mock_checksum, factory_with_account):
        """price=1, decimals 18/18 -> sqrtPriceX96 = 2^96."""
        f = factory_with_account

        mock_pool = Mock()
        mock_pool.functions.initialize.return_value.build_transaction.return_value = {}
        f.w3.eth.contract.return_value = mock_pool

        f.initialize_pool(ADDR_POOL, 1.0, token0_decimals=18, token1_decimals=18)

        # Проверяем sqrtPriceX96 = sqrt(1) * 2^96 = 2^96
        expected_sqrt_price_x96 = int(math.sqrt(1.0) * (2 ** 96))
        mock_pool.functions.initialize.assert_called_once_with(expected_sqrt_price_x96)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_initialize_pool_different_decimals_18_6(self, _mock_checksum, factory_with_account):
        """price=1, decimals 18/6 -> adjusted_price = 1 * 10^12."""
        f = factory_with_account

        mock_pool = Mock()
        mock_pool.functions.initialize.return_value.build_transaction.return_value = {}
        f.w3.eth.contract.return_value = mock_pool

        f.initialize_pool(ADDR_POOL, 1.0, token0_decimals=18, token1_decimals=6)

        # adjusted_price = 1.0 * 10^(18-6) = 1e12
        expected_sqrt_price_x96 = int(math.sqrt(1e12) * (2 ** 96))
        mock_pool.functions.initialize.assert_called_once_with(expected_sqrt_price_x96)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_initialize_pool_different_decimals_6_18(self, _mock_checksum, factory_with_account):
        """price=1, decimals 6/18 -> adjusted_price = 1 * 10^(-12)."""
        f = factory_with_account

        mock_pool = Mock()
        mock_pool.functions.initialize.return_value.build_transaction.return_value = {}
        f.w3.eth.contract.return_value = mock_pool

        f.initialize_pool(ADDR_POOL, 1.0, token0_decimals=6, token1_decimals=18)

        # adjusted_price = 1.0 * 10^(6-18) = 1e-12
        expected_sqrt_price_x96 = int(math.sqrt(1e-12) * (2 ** 96))
        mock_pool.functions.initialize.assert_called_once_with(expected_sqrt_price_x96)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_initialize_pool_high_price(self, _mock_checksum, factory_with_account):
        """Высокая начальная цена."""
        f = factory_with_account

        mock_pool = Mock()
        mock_pool.functions.initialize.return_value.build_transaction.return_value = {}
        f.w3.eth.contract.return_value = mock_pool

        price = 2500.0  # например BNB/USDT
        f.initialize_pool(ADDR_POOL, price, token0_decimals=18, token1_decimals=18)

        expected = int(math.sqrt(2500.0) * (2 ** 96))
        mock_pool.functions.initialize.assert_called_once_with(expected)

    @patch('src.contracts.pool_factory.Web3.to_checksum_address', side_effect=lambda x: x)
    def test_initialize_pool_returns_tx_hash(self, _mock_checksum, factory_with_account):
        """initialize_pool возвращает hex tx_hash."""
        f = factory_with_account

        mock_pool = Mock()
        mock_pool.functions.initialize.return_value.build_transaction.return_value = {}
        f.w3.eth.contract.return_value = mock_pool

        result = f.initialize_pool(ADDR_POOL, 1.0)

        assert isinstance(result, str)
        # Возвращается hex строка
        assert len(result) > 0

    # ----------------------------------------------------------
    # create_and_initialize_pool
    # ----------------------------------------------------------

    def test_create_and_initialize_pool_success(self, factory_with_account):
        """Успешное создание и инициализация пула."""
        f = factory_with_account

        with patch.object(f, 'create_pool', return_value=("0xCREATE_TX", ADDR_POOL)) as mock_create, \
             patch.object(f, 'initialize_pool', return_value="0xINIT_TX") as mock_init:

            create_tx, init_tx, pool_address = f.create_and_initialize_pool(
                ADDR_LOW, ADDR_HIGH, 3000, 1.0,
                token0_decimals=18, token1_decimals=18
            )

        assert create_tx == "0xCREATE_TX"
        assert init_tx == "0xINIT_TX"
        assert pool_address == ADDR_POOL
        mock_create.assert_called_once_with(ADDR_LOW, ADDR_HIGH, 3000, 300)
        mock_init.assert_called_once_with(ADDR_POOL, 1.0, 18, 18, 300)

    def test_create_and_initialize_pool_no_pool_address(self, factory_with_account):
        """create_pool вернул None для адреса -> ValueError."""
        f = factory_with_account

        with patch.object(f, 'create_pool', return_value=("0xTX", None)):
            with pytest.raises(ValueError, match="Failed to get pool address"):
                f.create_and_initialize_pool(
                    ADDR_LOW, ADDR_HIGH, 3000, 1.0
                )

    def test_create_and_initialize_pool_passes_decimals(self, factory_with_account):
        """Decimals корректно передаются в initialize_pool."""
        f = factory_with_account

        with patch.object(f, 'create_pool', return_value=("0xTX", ADDR_POOL)), \
             patch.object(f, 'initialize_pool', return_value="0xINIT") as mock_init:

            f.create_and_initialize_pool(
                ADDR_LOW, ADDR_HIGH, 2500, 100.0,
                token0_decimals=6, token1_decimals=18, timeout=600
            )

        mock_init.assert_called_once_with(ADDR_POOL, 100.0, 6, 18, 600)

    def test_create_and_initialize_pool_custom_timeout(self, factory_with_account):
        """Кастомный timeout передается в оба вызова."""
        f = factory_with_account

        with patch.object(f, 'create_pool', return_value=("0xTX", ADDR_POOL)) as mock_create, \
             patch.object(f, 'initialize_pool', return_value="0xINIT") as mock_init:

            f.create_and_initialize_pool(
                ADDR_LOW, ADDR_HIGH, 3000, 1.0, timeout=120
            )

        mock_create.assert_called_once_with(ADDR_LOW, ADDR_HIGH, 3000, 120)
        mock_init.assert_called_once_with(ADDR_POOL, 1.0, 18, 18, 120)

    # ----------------------------------------------------------
    # price_to_sqrt_price_x96
    # ----------------------------------------------------------

    def test_price_to_sqrt_price_x96_equal_decimals_price_one(self, factory):
        """price=1, decimals 18/18 -> sqrtPriceX96 = 2^96."""
        result = factory.price_to_sqrt_price_x96(1.0, 18, 18)

        expected = int(math.sqrt(1.0) * (2 ** 96))
        assert result == expected
        assert result == 2 ** 96

    def test_price_to_sqrt_price_x96_equal_decimals_price_four(self, factory):
        """price=4, decimals 18/18 -> sqrtPriceX96 = 2 * 2^96."""
        result = factory.price_to_sqrt_price_x96(4.0, 18, 18)

        expected = int(math.sqrt(4.0) * (2 ** 96))
        assert result == expected
        assert result == 2 * (2 ** 96)

    def test_price_to_sqrt_price_x96_different_decimals_18_6(self, factory):
        """price=1, token0=18dec, token1=6dec -> adjusted_price=10^12."""
        result = factory.price_to_sqrt_price_x96(1.0, 18, 6)

        adjusted = 1.0 * (10 ** (18 - 6))
        expected = int(math.sqrt(adjusted) * (2 ** 96))
        assert result == expected

    def test_price_to_sqrt_price_x96_different_decimals_6_18(self, factory):
        """price=1, token0=6dec, token1=18dec -> adjusted_price=10^(-12)."""
        result = factory.price_to_sqrt_price_x96(1.0, 6, 18)

        adjusted = 1.0 * (10 ** (6 - 18))
        expected = int(math.sqrt(adjusted) * (2 ** 96))
        assert result == expected

    def test_price_to_sqrt_price_x96_high_price(self, factory):
        """Высокая цена: price=2500."""
        result = factory.price_to_sqrt_price_x96(2500.0, 18, 18)

        expected = int(math.sqrt(2500.0) * (2 ** 96))
        assert result == expected

    def test_price_to_sqrt_price_x96_small_price(self, factory):
        """Маленькая цена: price=0.0001."""
        result = factory.price_to_sqrt_price_x96(0.0001, 18, 18)

        expected = int(math.sqrt(0.0001) * (2 ** 96))
        assert result == expected

    # ----------------------------------------------------------
    # sqrt_price_x96_to_price
    # ----------------------------------------------------------

    def test_sqrt_price_x96_to_price_equal_decimals(self, factory):
        """sqrtPriceX96 = 2^96 -> price = 1.0 при равных decimals."""
        result = factory.sqrt_price_x96_to_price(2 ** 96, 18, 18)

        assert result == pytest.approx(1.0, rel=1e-10)

    def test_sqrt_price_x96_to_price_price_four(self, factory):
        """sqrtPriceX96 для price=4 -> обратная конвертация = 4.0."""
        sqrt_price_x96 = int(math.sqrt(4.0) * (2 ** 96))

        result = factory.sqrt_price_x96_to_price(sqrt_price_x96, 18, 18)

        assert result == pytest.approx(4.0, rel=1e-10)

    def test_sqrt_price_x96_to_price_different_decimals(self, factory):
        """Обратная конвертация с разными decimals (18/6)."""
        # Forward: price=1, 18/6 -> adjusted=10^12
        sqrt_price_x96 = factory.price_to_sqrt_price_x96(1.0, 18, 6)

        result = factory.sqrt_price_x96_to_price(sqrt_price_x96, 18, 6)

        assert result == pytest.approx(1.0, rel=1e-6)

    def test_sqrt_price_x96_to_price_roundtrip(self, factory):
        """Roundtrip: price -> sqrtPriceX96 -> price."""
        original_prices = [0.001, 0.5, 1.0, 4.0, 100.0, 2500.0]

        for original in original_prices:
            sqrt_x96 = factory.price_to_sqrt_price_x96(original, 18, 18)
            recovered = factory.sqrt_price_x96_to_price(sqrt_x96, 18, 18)
            assert recovered == pytest.approx(original, rel=1e-6), (
                f"Roundtrip failed для price={original}: recovered={recovered}"
            )

    def test_sqrt_price_x96_to_price_roundtrip_mixed_decimals(self, factory):
        """Roundtrip с разными decimals: 18/6 и 6/18."""
        for dec0, dec1 in [(18, 6), (6, 18), (8, 18), (18, 8)]:
            for price in [0.01, 1.0, 100.0]:
                sqrt_x96 = factory.price_to_sqrt_price_x96(price, dec0, dec1)
                recovered = factory.sqrt_price_x96_to_price(sqrt_x96, dec0, dec1)
                assert recovered == pytest.approx(price, rel=1e-5), (
                    f"Roundtrip failed: dec={dec0}/{dec1}, price={price}, got={recovered}"
                )

    def test_sqrt_price_x96_to_price_zero(self, factory):
        """sqrtPriceX96=0 -> price=0."""
        result = factory.sqrt_price_x96_to_price(0, 18, 18)

        assert result == 0.0

    # ----------------------------------------------------------
    # Class attributes
    # ----------------------------------------------------------

    def test_factory_addresses_contain_known_chains(self, factory):
        """FACTORY_ADDRESSES содержит BSC, Ethereum, Base, testnet."""
        assert 56 in PoolFactory.FACTORY_ADDRESSES     # BSC
        assert 1 in PoolFactory.FACTORY_ADDRESSES       # Ethereum
        assert 8453 in PoolFactory.FACTORY_ADDRESSES    # Base
        assert 97 in PoolFactory.FACTORY_ADDRESSES      # BSC testnet

    def test_pancakeswap_factory_addresses(self, factory):
        """PANCAKESWAP_FACTORY_ADDRESSES содержит BSC."""
        assert 56 in PoolFactory.PANCAKESWAP_FACTORY_ADDRESSES

    def test_factory_addresses_are_hex_strings(self, factory):
        """Все адреса фабрик начинаются с 0x и имеют длину 42."""
        for chain_id, addr in PoolFactory.FACTORY_ADDRESSES.items():
            assert addr.startswith("0x"), f"chain_id={chain_id}: не начинается с 0x"
            assert len(addr) == 42, f"chain_id={chain_id}: длина {len(addr)} != 42"
