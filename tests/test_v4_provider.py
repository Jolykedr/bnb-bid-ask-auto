"""
Tests for V4 LiquidityProvider.

Тесты покрывают:
- get_permit2_address: возвращает правильный адрес Permit2 для каждого протокола
- V4LadderConfig: дата-класс конфигурации лесенки, свойства, фабричный метод
- V4LadderResult: дата-класс результата создания лесенки
- V4LiquidityProvider: основной класс провайдера ликвидности V4
  - preview_ladder: предпросмотр позиций
  - get_pool_key: создание PoolKey из конфига
  - check_pool_exists: проверка существования пула
  - validate_balances: проверка балансов токенов
  - check_and_approve_token: проверка и одобрение ERC20 токенов
  - create_ladder: создание лесенки позиций
  - get_token_balance: получение баланса токена
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from dataclasses import FrozenInstanceError
from web3 import Web3

from src.v4_liquidity_provider import (
    V4LiquidityProvider,
    V4LadderConfig,
    V4LadderResult,
    get_permit2_address,
    PERMIT2_UNISWAP,
    PERMIT2_PANCAKESWAP,
)
from src.contracts.v4.constants import V4Protocol
from src.contracts.v4.pool_manager import PoolKey
from src.math.distribution import BidAskPosition


# ============================================================
# Тестовые константы
# ============================================================

TOKEN_VOLATILE = "0x1111111111111111111111111111111111111111"
TOKEN_STABLE = "0x9999999999999999999999999999999999999999"
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _make_config(**overrides) -> V4LadderConfig:
    """Утилита для создания конфига с разумными значениями по умолчанию."""
    defaults = dict(
        current_price=0.005,
        lower_price=0.003,
        total_usd=100.0,
        n_positions=5,
        token0=TOKEN_VOLATILE,
        token1=TOKEN_STABLE,
        fee_percent=0.3,
        protocol=V4Protocol.PANCAKESWAP,
    )
    defaults.update(overrides)
    return V4LadderConfig(**defaults)


def _make_position(index=0, tick_lower=-100, tick_upper=100,
                   price_lower=0.003, price_upper=0.005,
                   usd_amount=20.0, percentage=20.0,
                   liquidity=1000000) -> BidAskPosition:
    """Утилита для создания мок-позиции."""
    return BidAskPosition(
        index=index,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
        price_lower=price_lower,
        price_upper=price_upper,
        usd_amount=usd_amount,
        percentage=percentage,
        liquidity=liquidity,
    )


# ============================================================
# TestGetPermit2Address
# ============================================================

class TestGetPermit2Address:
    """Тесты для get_permit2_address."""

    def test_pancakeswap_permit2(self):
        """PancakeSwap возвращает свой адрес Permit2."""
        result = get_permit2_address(V4Protocol.PANCAKESWAP)
        assert result == PERMIT2_PANCAKESWAP
        assert result == "0x31c2F6fcFf4F8759b3Bd5Bf0e1084A055615c768"

    def test_uniswap_permit2(self):
        """Uniswap возвращает универсальный адрес Permit2."""
        result = get_permit2_address(V4Protocol.UNISWAP)
        assert result == PERMIT2_UNISWAP
        assert result == "0x000000000022D473030F116dDEE9F6B43aC78BA3"

    def test_addresses_are_different(self):
        """Адреса Permit2 для PancakeSwap и Uniswap различаются."""
        assert PERMIT2_UNISWAP != PERMIT2_PANCAKESWAP

    def test_unknown_protocol_defaults_to_uniswap(self):
        """Неизвестный протокол возвращает адрес Uniswap (ветка else)."""
        # Создаём мок перечисления, который не равен PANCAKESWAP
        mock_protocol = Mock()
        mock_protocol.__eq__ = Mock(return_value=False)
        result = get_permit2_address(mock_protocol)
        assert result == PERMIT2_UNISWAP


# ============================================================
# TestV4LadderConfig
# ============================================================

class TestV4LadderConfig:
    """Тесты для V4LadderConfig."""

    def test_upper_price_property(self):
        """upper_price — это алиас для current_price."""
        config = _make_config(current_price=0.005)
        assert config.upper_price == 0.005
        assert config.upper_price == config.current_price

    def test_create_factory_method(self):
        """Фабричный метод create() корректно маппит параметры."""
        config = V4LadderConfig.create(
            upper_price=0.005,
            lower_price=0.003,
            total_usd=100.0,
            n_positions=5,
            token0=TOKEN_VOLATILE,
            token1=TOKEN_STABLE,
            fee_percent=0.3,
            market_price=0.004,
        )
        # upper_price маппится в current_price
        assert config.current_price == 0.005
        assert config.upper_price == 0.005
        assert config.lower_price == 0.003
        assert config.total_usd == 100.0
        assert config.n_positions == 5
        assert config.token0 == TOKEN_VOLATILE
        assert config.token1 == TOKEN_STABLE
        assert config.fee_percent == 0.3
        # market_price маппится в actual_current_price
        assert config.actual_current_price == 0.004

    def test_create_factory_without_market_price(self):
        """Фабричный метод без market_price оставляет actual_current_price=None."""
        config = V4LadderConfig.create(
            upper_price=0.01,
            lower_price=0.005,
            total_usd=50.0,
            n_positions=3,
            token0=TOKEN_VOLATILE,
            token1=TOKEN_STABLE,
            fee_percent=1.0,
        )
        assert config.actual_current_price is None

    def test_create_factory_with_kwargs(self):
        """Фабричный метод принимает дополнительные kwargs."""
        config = V4LadderConfig.create(
            upper_price=0.01,
            lower_price=0.005,
            total_usd=50.0,
            n_positions=3,
            token0=TOKEN_VOLATILE,
            token1=TOKEN_STABLE,
            fee_percent=1.0,
            tick_spacing=200,
            slippage_percent=1.0,
            hooks="0xABCDABCDABCDABCDABCDABCDABCDABCDABCDABCD",
            invert_price=False,
        )
        assert config.tick_spacing == 200
        assert config.slippage_percent == 1.0
        assert config.hooks == "0xABCDABCDABCDABCDABCDABCDABCDABCDABCDABCD"
        assert config.invert_price is False

    def test_default_values(self):
        """Значения по умолчанию корректны."""
        config = _make_config()
        assert config.tick_spacing is None
        assert config.distribution_type == "linear"
        assert config.token0_decimals == 18
        assert config.token1_decimals == 18
        assert config.slippage_percent == 0.5
        assert config.hooks is None
        assert config.protocol == V4Protocol.PANCAKESWAP
        assert config.pool_id is None
        assert config.invert_price is True
        assert config.actual_current_price is None
        assert config.base_token_amount is None

    def test_custom_decimals(self):
        """Поддержка пользовательских decimals (например, USDC 6 на BASE)."""
        config = _make_config(
            token0_decimals=18,
            token1_decimals=6,
        )
        assert config.token0_decimals == 18
        assert config.token1_decimals == 6


# ============================================================
# TestV4LadderResult
# ============================================================

class TestV4LadderResult:
    """Тесты для V4LadderResult."""

    def test_success_result(self):
        """Успешный результат содержит tx_hash и token_ids."""
        positions = [_make_position(i) for i in range(3)]
        result = V4LadderResult(
            positions=positions,
            tx_hash="0xabcdef1234567890",
            gas_used=500_000,
            token_ids=[101, 102, 103],
            pool_created=False,
            success=True,
        )
        assert result.success is True
        assert result.error is None
        assert result.tx_hash == "0xabcdef1234567890"
        assert result.gas_used == 500_000
        assert len(result.positions) == 3
        assert result.token_ids == [101, 102, 103]
        assert result.pool_created is False

    def test_error_result(self):
        """Результат с ошибкой содержит сообщение и success=False."""
        result = V4LadderResult(
            positions=[],
            tx_hash=None,
            gas_used=None,
            token_ids=[],
            pool_created=False,
            success=False,
            error="Account not configured",
        )
        assert result.success is False
        assert result.error == "Account not configured"
        assert result.tx_hash is None
        assert result.gas_used is None
        assert result.token_ids == []

    def test_pool_created_flag(self):
        """Флаг pool_created отражает создание нового пула."""
        result = V4LadderResult(
            positions=[],
            tx_hash="0x1234",
            gas_used=100_000,
            token_ids=[1],
            pool_created=True,
            success=True,
        )
        assert result.pool_created is True

    def test_default_error_is_none(self):
        """Поле error по умолчанию None."""
        result = V4LadderResult(
            positions=[],
            tx_hash=None,
            gas_used=None,
            token_ids=[],
            pool_created=False,
            success=True,
        )
        assert result.error is None


# ============================================================
# TestV4LiquidityProvider
# ============================================================

class TestV4LiquidityProvider:
    """Тесты для V4LiquidityProvider."""

    @pytest.fixture
    def provider(self):
        """
        Создание провайдера с мок-объектами, минуя __init__.

        Используем patch.object чтобы не создавать реальное
        подключение к блокчейну.
        """
        with patch.object(V4LiquidityProvider, '__init__', lambda self, *a, **kw: None):
            p = V4LiquidityProvider.__new__(V4LiquidityProvider)
            p.w3 = Mock(spec=Web3)
            p.w3.eth = Mock()
            p.w3.eth.gas_price = 5_000_000_000
            p.w3.eth.get_transaction_count = Mock(return_value=0)
            p.w3.eth.send_raw_transaction = Mock(return_value=b'\x12\x34' * 16)
            p.w3.eth.wait_for_transaction_receipt = Mock(return_value={
                'status': 1,
                'gasUsed': 300_000,
                'logs': [],
            })
            p.w3.eth.contract = Mock()
            p.w3.to_checksum_address = Web3.to_checksum_address

            p.account = Mock()
            p.account.address = "0x1234567890123456789012345678901234567890"
            p.account.sign_transaction = Mock(
                return_value=Mock(raw_transaction=b'signed')
            )

            p.chain_id = 56
            p.protocol = V4Protocol.PANCAKESWAP
            p.proxy = None

            p.pool_manager = Mock()
            p.pool_manager.is_pool_initialized = Mock(return_value=True)

            p.position_manager = Mock()
            p.position_manager.position_manager_address = (
                "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            )

            p.decimals_cache = Mock()
            p.decimals_cache.get_decimals = Mock(return_value=18)

            p.gas_estimator = Mock()
            p.gas_estimator.estimate = Mock(return_value=60_000)

            p.nonce_manager = Mock()
            p.nonce_manager.get_next_nonce = Mock(return_value=1)
            p.nonce_manager.confirm_transaction = Mock()
            p.nonce_manager.release_nonce = Mock()

            return p

    @pytest.fixture
    def config(self):
        """Стандартный конфиг для тестов провайдера."""
        return _make_config()

    # ----------------------------------------------------------
    # preview_ladder
    # ----------------------------------------------------------

    def test_preview_ladder_returns_positions(self, provider, config):
        """preview_ladder возвращает список BidAskPosition."""
        mock_positions = [_make_position(i) for i in range(5)]
        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist:
            result = provider.preview_ladder(config)

        assert result == mock_positions
        assert len(result) == 5
        mock_dist.assert_called_once()

    def test_preview_ladder_uses_tick_spacing(self, provider, config):
        """preview_ladder использует tick_spacing из конфига, если задан."""
        config.tick_spacing = 200
        mock_positions = [_make_position()]

        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist:
            provider.preview_ladder(config)

        call_kwargs = mock_dist.call_args
        assert call_kwargs[1].get('tick_spacing') == 200 or \
               call_kwargs.kwargs.get('tick_spacing') == 200

    def test_preview_ladder_auto_tick_spacing(self, provider):
        """Если tick_spacing не задан, вычисляется через suggest_tick_spacing."""
        config = _make_config(fee_percent=1.0, tick_spacing=None)
        mock_positions = [_make_position()]

        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist:
            provider.preview_ladder(config)

        call_kwargs = mock_dist.call_args
        # suggest_tick_spacing(1.0) = round(1.0 * 200) = 200
        assert call_kwargs[1].get('tick_spacing') == 200 or \
               call_kwargs.kwargs.get('tick_spacing') == 200

    def test_preview_ladder_decimal_offset(self, provider):
        """preview_ladder учитывает decimal_tick_offset для пар с разными decimals."""
        config = _make_config(
            token0_decimals=18,
            token1_decimals=6,
        )
        mock_positions = [_make_position()]

        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist, patch(
            'src.v4_liquidity_provider.compute_decimal_tick_offset',
            return_value=276324,
        ) as mock_offset:
            provider.preview_ladder(config)

        # Проверяем, что compute_decimal_tick_offset был вызван
        mock_offset.assert_called_once_with(
            token0_address=config.token0,
            token0_decimals=18,
            token1_address=config.token1,
            token1_decimals=6,
        )

        # Проверяем, что offset передан в distribution
        call_kwargs = mock_dist.call_args
        assert call_kwargs[1].get('decimal_tick_offset') == 276324 or \
               call_kwargs.kwargs.get('decimal_tick_offset') == 276324

    def test_preview_ladder_zero_offset_same_decimals(self, provider, config):
        """Для пар с одинаковыми decimals (18/18) offset=0."""
        mock_positions = [_make_position()]

        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist, patch(
            'src.v4_liquidity_provider.compute_decimal_tick_offset',
            return_value=0,
        ):
            provider.preview_ladder(config)

        call_kwargs = mock_dist.call_args
        assert call_kwargs[1].get('decimal_tick_offset') == 0 or \
               call_kwargs.kwargs.get('decimal_tick_offset') == 0

    def test_preview_ladder_passes_invert_price(self, provider):
        """preview_ladder передаёт invert_price в distribution."""
        config = _make_config(invert_price=False)
        mock_positions = [_make_position()]

        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist:
            provider.preview_ladder(config)

        call_kwargs = mock_dist.call_args
        assert call_kwargs[1].get('invert_price') is False or \
               call_kwargs.kwargs.get('invert_price') is False

    def test_preview_ladder_passes_distribution_type(self, provider):
        """preview_ladder передаёт distribution_type из конфига."""
        config = _make_config()
        config.distribution_type = "quadratic"
        mock_positions = [_make_position()]

        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist:
            provider.preview_ladder(config)

        call_kwargs = mock_dist.call_args
        assert call_kwargs[1].get('distribution_type') == "quadratic" or \
               call_kwargs.kwargs.get('distribution_type') == "quadratic"

    # ----------------------------------------------------------
    # get_pool_key
    # ----------------------------------------------------------

    def test_get_pool_key_creates_pool_key(self, provider, config):
        """get_pool_key создаёт PoolKey с правильными параметрами."""
        with patch.object(PoolKey, 'from_tokens', return_value=Mock(spec=PoolKey)) as mock_from:
            result = provider.get_pool_key(config)

        mock_from.assert_called_once()
        call_kwargs = mock_from.call_args
        assert call_kwargs[1]['token0'] == config.token0 or call_kwargs.kwargs.get('token0') == config.token0
        assert call_kwargs[1]['token1'] == config.token1 or call_kwargs.kwargs.get('token1') == config.token1

    def test_get_pool_key_uses_custom_tick_spacing(self, provider):
        """get_pool_key использует tick_spacing из конфига, если задан."""
        config = _make_config(tick_spacing=500)

        with patch.object(PoolKey, 'from_tokens', return_value=Mock(spec=PoolKey)) as mock_from:
            provider.get_pool_key(config)

        call_kwargs = mock_from.call_args
        assert call_kwargs[1].get('tick_spacing') == 500 or \
               call_kwargs.kwargs.get('tick_spacing') == 500

    def test_get_pool_key_auto_tick_spacing(self, provider):
        """Если tick_spacing=None, вычисляется из fee_percent."""
        config = _make_config(fee_percent=3.333, tick_spacing=None)

        with patch.object(PoolKey, 'from_tokens', return_value=Mock(spec=PoolKey)) as mock_from:
            provider.get_pool_key(config)

        call_kwargs = mock_from.call_args
        # suggest_tick_spacing(3.333) = round(3.333 * 200) = 667
        expected_spacing = round(3.333 * 200)
        assert call_kwargs[1].get('tick_spacing') == expected_spacing or \
               call_kwargs.kwargs.get('tick_spacing') == expected_spacing

    def test_get_pool_key_passes_hooks(self, provider):
        """get_pool_key передаёт адрес hooks."""
        hooks_addr = "0xDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF"
        config = _make_config(hooks=hooks_addr)

        with patch.object(PoolKey, 'from_tokens', return_value=Mock(spec=PoolKey)) as mock_from:
            provider.get_pool_key(config)

        call_kwargs = mock_from.call_args
        assert call_kwargs[1].get('hooks') == hooks_addr or \
               call_kwargs.kwargs.get('hooks') == hooks_addr

    # ----------------------------------------------------------
    # check_pool_exists
    # ----------------------------------------------------------

    def test_check_pool_exists_true(self, provider, config):
        """check_pool_exists возвращает True для инициализированного пула."""
        mock_pool_key = Mock(spec=PoolKey)
        provider.pool_manager.is_pool_initialized.return_value = True

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            result = provider.check_pool_exists(config)

        assert result is True
        provider.pool_manager.is_pool_initialized.assert_called_once_with(mock_pool_key)

    def test_check_pool_exists_false(self, provider, config):
        """check_pool_exists возвращает False для несуществующего пула."""
        mock_pool_key = Mock(spec=PoolKey)
        provider.pool_manager.is_pool_initialized.return_value = False

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            result = provider.check_pool_exists(config)

        assert result is False

    # ----------------------------------------------------------
    # validate_balances
    # ----------------------------------------------------------

    def test_validate_balances_no_account(self, provider, config):
        """Без аккаунта validate_balances возвращает (False, error)."""
        provider.account = None

        is_valid, error = provider.validate_balances(config)

        assert is_valid is False
        assert error == "Account not configured"

    def test_validate_balances_sufficient(self, provider, config):
        """При достаточных балансах возвращает (True, None)."""
        # Мокаем _get_quote_token
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        # Мокаем get_pool_key
        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        # decimals_cache возвращает 18
        provider.decimals_cache.get_decimals = Mock(return_value=18)

        # BatchRPC возвращает достаточные балансы
        mock_batch = Mock()
        # balance_stablecoin=200*10^18, balance_volatile=200*10^18
        mock_batch.execute = Mock(return_value=[
            200 * 10**18,  # stablecoin balance
            200 * 10**18,  # volatile balance
        ])
        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is True
        assert error is None

    def test_validate_balances_insufficient(self, provider, config):
        """При недостаточных балансах обоих токенов возвращает ошибку."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.decimals_cache.get_decimals = Mock(return_value=18)

        # BatchRPC возвращает нулевые балансы
        mock_batch = Mock()
        mock_batch.execute = Mock(return_value=[0, 0])
        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is False
        assert error is not None
        assert "Insufficient" in error

    def test_validate_balances_only_stablecoin(self, provider, config):
        """Достаточно одного токена — validate_balances возвращает True."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.decimals_cache.get_decimals = Mock(return_value=18)

        # Только stablecoin достаточно, volatile = 0
        mock_batch = Mock()
        mock_batch.execute = Mock(return_value=[
            200 * 10**18,  # stablecoin balance - достаточно
            0,             # volatile balance - ноль
        ])
        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is True
        assert error is None

    def test_validate_balances_batch_rpc_fallback(self, provider, config):
        """При ошибке BatchRPC используются отдельные вызовы get_token_balance."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.decimals_cache.get_decimals = Mock(return_value=18)

        # BatchRPC бросает исключение
        mock_batch = Mock()
        mock_batch.execute = Mock(side_effect=Exception("RPC error"))

        # Fallback через get_token_balance
        provider.get_token_balance = Mock(return_value=500 * 10**18)

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is True
        assert provider.get_token_balance.call_count == 2

    # ----------------------------------------------------------
    # check_and_approve_token
    # ----------------------------------------------------------

    def test_check_and_approve_already_approved(self, provider):
        """Если allowance достаточен, approve не отправляется."""
        # Мокаем ERC20 контракт с достаточным allowance
        mock_contract = Mock()
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=10**30))
        )
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        result = provider.check_and_approve_token(
            token_address=TOKEN_VOLATILE,
            amount=10**18,
            spender="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        )

        assert result is None
        # approve не должен быть вызван
        mock_contract.functions.approve.assert_not_called()

    def test_check_and_approve_sends_tx(self, provider):
        """Если allowance недостаточен, отправляется approve транзакция."""
        mock_contract = Mock()
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=0))  # Нулевой allowance
        )
        mock_approve_fn = Mock()
        mock_approve_fn.build_transaction = Mock(return_value={
            'from': provider.account.address,
            'nonce': 1,
            'gas': 60_000,
            'gasPrice': 5_000_000_000,
        })
        mock_contract.functions.approve = Mock(return_value=mock_approve_fn)
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        # gas_estimator
        provider.gas_estimator.estimate = Mock(return_value=60_000)

        # Мок транзакции
        tx_hash_bytes = b'\xab\xcd' * 16
        provider.w3.eth.send_raw_transaction = Mock(return_value=tx_hash_bytes)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 50_000,
        })

        result = provider.check_and_approve_token(
            token_address=TOKEN_VOLATILE,
            amount=10**18,
            spender="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        )

        assert result is not None
        assert isinstance(result, str)
        provider.nonce_manager.confirm_transaction.assert_called_once()

    def test_check_and_approve_default_spender(self, provider):
        """Без явного spender используется адрес PositionManager."""
        mock_contract = Mock()
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=10**30))
        )
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        provider.check_and_approve_token(
            token_address=TOKEN_VOLATILE,
            amount=10**18,
        )

        # Проверяем, что allowance проверялся с адресом PositionManager
        # Web3.to_checksum_address преобразует адрес, поэтому сравниваем через .lower()
        allowance_call_args = mock_contract.functions.allowance.call_args
        spender_used = allowance_call_args[0][1]  # Второй позиционный аргумент
        assert spender_used.lower() == provider.position_manager.position_manager_address.lower()

    def test_check_and_approve_failed_tx_releases_nonce(self, provider):
        """При неуспешной транзакции nonce освобождается.

        Примечание: release_nonce вызывается дважды — сначала в блоке
        'if receipt.status != 1', затем в 'except' (т.к. raise re-enters except).
        Важно, что nonce гарантированно освобождён.
        """
        mock_contract = Mock()
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=0))
        )
        mock_approve_fn = Mock()
        mock_approve_fn.build_transaction = Mock(return_value={})
        mock_contract.functions.approve = Mock(return_value=mock_approve_fn)
        provider.w3.eth.contract = Mock(return_value=mock_contract)
        provider.gas_estimator.estimate = Mock(return_value=60_000)

        # Транзакция не проходит
        provider.w3.eth.send_raw_transaction = Mock(return_value=b'\xaa' * 32)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 0,  # FAILED
            'gasUsed': 21_000,
        })

        with pytest.raises(Exception, match="ERC20 approve failed"):
            provider.check_and_approve_token(
                token_address=TOKEN_VOLATILE,
                amount=10**18,
                spender="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            )

        # release_nonce вызывается 2 раза: в блоке status!=1 и в except (re-raise)
        assert provider.nonce_manager.release_nonce.call_count >= 1
        provider.nonce_manager.release_nonce.assert_called_with(1)

    def test_check_and_approve_exception_releases_nonce(self, provider):
        """При исключении во время approve nonce освобождается."""
        mock_contract = Mock()
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=0))
        )
        mock_approve_fn = Mock()
        mock_approve_fn.build_transaction = Mock(side_effect=Exception("gas estimation failed"))
        mock_contract.functions.approve = Mock(return_value=mock_approve_fn)
        provider.w3.eth.contract = Mock(return_value=mock_contract)
        provider.gas_estimator.estimate = Mock(return_value=60_000)

        with pytest.raises(Exception, match="gas estimation failed"):
            provider.check_and_approve_token(
                token_address=TOKEN_VOLATILE,
                amount=10**18,
                spender="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            )

        provider.nonce_manager.release_nonce.assert_called_once()

    def test_check_and_approve_no_nonce_manager(self, provider):
        """Без nonce_manager используется w3.eth.get_transaction_count."""
        provider.nonce_manager = None

        mock_contract = Mock()
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=0))
        )
        mock_approve_fn = Mock()
        mock_approve_fn.build_transaction = Mock(return_value={})
        mock_contract.functions.approve = Mock(return_value=mock_approve_fn)
        provider.w3.eth.contract = Mock(return_value=mock_contract)
        provider.gas_estimator.estimate = Mock(return_value=60_000)

        tx_hash_bytes = b'\xab\xcd' * 16
        provider.w3.eth.send_raw_transaction = Mock(return_value=tx_hash_bytes)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 50_000,
        })

        result = provider.check_and_approve_token(
            token_address=TOKEN_VOLATILE,
            amount=10**18,
            spender="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        )

        assert result is not None
        provider.w3.eth.get_transaction_count.assert_called()

    # ----------------------------------------------------------
    # create_ladder
    # ----------------------------------------------------------

    def test_create_ladder_no_account(self, provider, config):
        """Без аккаунта create_ladder возвращает ошибку."""
        provider.account = None

        result = provider.create_ladder(config)

        assert result.success is False
        assert result.error == "Account not configured"
        assert result.positions == []
        assert result.token_ids == []

    def test_create_ladder_invalid_fee_negative(self, provider, config):
        """Отрицательная комиссия вызывает ошибку."""
        config.fee_percent = -1.0

        result = provider.create_ladder(config)

        assert result.success is False
        assert "Fee must be between 0% and 100%" in result.error

    def test_create_ladder_invalid_fee_over_100(self, provider, config):
        """Комиссия больше 100% вызывает ошибку."""
        config.fee_percent = 101.0

        result = provider.create_ladder(config)

        assert result.success is False
        assert "Fee must be between 0% and 100%" in result.error

    def test_create_ladder_valid_fee_boundary_zero(self, provider, config):
        """Комиссия 0% допустима (граничное значение)."""
        config.fee_percent = 0.0
        # Чтобы не запускать весь пайплайн, проверяем что fee-валидация проходит
        # и падаем на следующем шаге (preview_ladder)
        with patch.object(provider, 'preview_ladder', side_effect=Exception("stopped")):
            with pytest.raises(Exception, match="stopped"):
                provider.create_ladder(config)

    def test_create_ladder_valid_fee_boundary_100(self, provider, config):
        """Комиссия 100% допустима (граничное значение)."""
        config.fee_percent = 100.0
        with patch.object(provider, 'preview_ladder', side_effect=Exception("stopped")):
            with pytest.raises(Exception, match="stopped"):
                provider.create_ladder(config)

    def test_create_ladder_pool_not_exists_no_auto_create(self, provider, config):
        """Без auto_create_pool и при отсутствии пула — ошибка."""
        config.pool_id = None

        mock_positions = [_make_position()]
        mock_pool_key = Mock()
        mock_pool_key.tick_spacing = 60
        mock_pool_key.fee = 3000

        with patch.object(provider, 'preview_ladder', return_value=mock_positions), \
             patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            provider.pool_manager.is_pool_initialized.return_value = False

            result = provider.create_ladder(
                config,
                auto_create_pool=False,
            )

        assert result.success is False
        assert "Pool does not exist" in result.error

    # ----------------------------------------------------------
    # get_token_balance
    # ----------------------------------------------------------

    def test_get_token_balance(self, provider):
        """get_token_balance возвращает баланс токена."""
        expected_balance = 500 * 10**18

        mock_contract = Mock()
        mock_contract.functions.balanceOf = Mock(
            return_value=Mock(call=Mock(return_value=expected_balance))
        )
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        result = provider.get_token_balance(TOKEN_VOLATILE)

        assert result == expected_balance

    def test_get_token_balance_zero(self, provider):
        """get_token_balance для пустого баланса возвращает 0."""
        mock_contract = Mock()
        mock_contract.functions.balanceOf = Mock(
            return_value=Mock(call=Mock(return_value=0))
        )
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        result = provider.get_token_balance(TOKEN_VOLATILE)

        assert result == 0

    def test_get_token_balance_uses_account_address(self, provider):
        """get_token_balance вызывает balanceOf с адресом аккаунта."""
        mock_contract = Mock()
        mock_balance_of = Mock(return_value=Mock(call=Mock(return_value=100)))
        mock_contract.functions.balanceOf = mock_balance_of
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        provider.get_token_balance(TOKEN_VOLATILE)

        mock_balance_of.assert_called_once_with(provider.account.address)

    # ----------------------------------------------------------
    # approve_on_permit2
    # ----------------------------------------------------------

    def test_approve_on_permit2_sends_tx(self, provider):
        """approve_on_permit2 отправляет транзакцию Permit2 approve."""
        mock_contract = Mock()
        mock_approve_fn = Mock()
        mock_approve_fn.build_transaction = Mock(return_value={})
        mock_contract.functions.approve = Mock(return_value=mock_approve_fn)
        # Мок allowance (для верификации после approve)
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=(10**160, 9999999999, 0)))
        )
        provider.w3.eth.contract = Mock(return_value=mock_contract)
        provider.gas_estimator.estimate = Mock(return_value=60_000)

        tx_hash_bytes = b'\xaa\xbb' * 16
        provider.w3.eth.send_raw_transaction = Mock(return_value=tx_hash_bytes)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 55_000,
        })

        result = provider.approve_on_permit2(
            token_address=TOKEN_VOLATILE,
            spender="0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
            amount=10**18,
            permit2_address=PERMIT2_PANCAKESWAP,
        )

        assert result is not None
        assert isinstance(result, str)
        provider.nonce_manager.confirm_transaction.assert_called_once()

    def test_approve_on_permit2_failed_tx_raises(self, provider):
        """При неуспешной транзакции Permit2 approve выбрасывает исключение."""
        mock_contract = Mock()
        mock_approve_fn = Mock()
        mock_approve_fn.build_transaction = Mock(return_value={})
        mock_contract.functions.approve = Mock(return_value=mock_approve_fn)
        provider.w3.eth.contract = Mock(return_value=mock_contract)
        provider.gas_estimator.estimate = Mock(return_value=60_000)

        provider.w3.eth.send_raw_transaction = Mock(return_value=b'\xdd' * 32)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 0,
            'gasUsed': 21_000,
        })

        with pytest.raises(Exception, match="Permit2 approval transaction failed"):
            provider.approve_on_permit2(
                token_address=TOKEN_VOLATILE,
                spender="0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
                amount=10**18,
                permit2_address=PERMIT2_PANCAKESWAP,
            )

        provider.nonce_manager.release_nonce.assert_called()

    def test_approve_on_permit2_zero_expiration_raises(self, provider):
        """Если Permit2 allowance не установился (expiration=0), выбрасывает ошибку."""
        mock_contract = Mock()
        mock_approve_fn = Mock()
        mock_approve_fn.build_transaction = Mock(return_value={})
        mock_contract.functions.approve = Mock(return_value=mock_approve_fn)
        # Мок allowance возвращает expiration=0
        mock_contract.functions.allowance = Mock(
            return_value=Mock(call=Mock(return_value=(0, 0, 0)))
        )
        provider.w3.eth.contract = Mock(return_value=mock_contract)
        provider.gas_estimator.estimate = Mock(return_value=60_000)

        provider.w3.eth.send_raw_transaction = Mock(return_value=b'\xee' * 32)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 50_000,
        })

        with pytest.raises(Exception, match="Permit2 allowance NOT set"):
            provider.approve_on_permit2(
                token_address=TOKEN_VOLATILE,
                spender="0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
                amount=10**18,
                permit2_address=PERMIT2_PANCAKESWAP,
            )

    # ----------------------------------------------------------
    # create_pool (unit-level)
    # ----------------------------------------------------------

    def test_create_pool_no_account_raises(self, provider, config):
        """create_pool без аккаунта выбрасывает ValueError."""
        provider.account = None

        with pytest.raises(ValueError, match="Account not configured"):
            provider.create_pool(config)

    def test_create_pool_already_exists(self, provider, config):
        """Если пул уже существует, create_pool возвращает (None, True)."""
        mock_pool_key = Mock()
        provider.pool_manager.is_pool_initialized = Mock(return_value=True)

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            tx_hash, success = provider.create_pool(config)

        assert tx_hash is None
        assert success is True

    # ----------------------------------------------------------
    # Интеграционные тесты config + provider
    # ----------------------------------------------------------

    def test_config_protocol_propagation(self, provider):
        """Протокол из конфига используется корректно."""
        config_uni = _make_config(protocol=V4Protocol.UNISWAP)
        config_pcs = _make_config(protocol=V4Protocol.PANCAKESWAP)

        assert config_uni.protocol == V4Protocol.UNISWAP
        assert config_pcs.protocol == V4Protocol.PANCAKESWAP

    def test_preview_and_pool_key_consistency(self, provider):
        """preview_ladder и get_pool_key используют одинаковый tick_spacing."""
        config = _make_config(fee_percent=1.0, tick_spacing=None)
        expected_tick_spacing = 200  # suggest_tick_spacing(1.0) = 200

        mock_positions = [_make_position()]

        with patch(
            'src.v4_liquidity_provider.calculate_bid_ask_distribution',
            return_value=mock_positions,
        ) as mock_dist, \
             patch.object(PoolKey, 'from_tokens', return_value=Mock()) as mock_from:

            provider.preview_ladder(config)
            provider.get_pool_key(config)

        # Оба метода должны использовать tick_spacing=200
        dist_kwargs = mock_dist.call_args
        from_tokens_kwargs = mock_from.call_args

        dist_tick_spacing = dist_kwargs[1].get('tick_spacing') or \
                           dist_kwargs.kwargs.get('tick_spacing')
        pool_key_tick_spacing = from_tokens_kwargs[1].get('tick_spacing') or \
                               from_tokens_kwargs.kwargs.get('tick_spacing')

        assert dist_tick_spacing == expected_tick_spacing
        assert pool_key_tick_spacing == expected_tick_spacing
