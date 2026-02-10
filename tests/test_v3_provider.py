"""
Tests for V3 LiquidityProvider (src/liquidity_provider.py).

Тесты для V3 провайдера ликвидности: LiquidityLadderConfig, LadderResult,
и основного класса LiquidityProvider (preview, validation, approve, create).
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, call
from web3 import Web3

from src.liquidity_provider import (
    LiquidityProvider,
    LiquidityLadderConfig,
    LadderResult,
    InsufficientBalanceError,
)
from src.math.distribution import BidAskPosition


# ---------------------------------------------------------------------------
# Тестовые адреса (BNB Chain - оба 18 dec)
# ---------------------------------------------------------------------------
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"

# Base - USDC 6 dec
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_BASE = "0x4200000000000000000000000000000000000006"

# Synthetic addresses (для тестов порядка)
ADDR_LOW = "0x1111111111111111111111111111111111111111"
ADDR_HIGH = "0x9999999999999999999999999999999999999999"


# ============================================================
# LiquidityLadderConfig
# ============================================================

class TestLiquidityLadderConfig:
    """Тесты для LiquidityLadderConfig dataclass."""

    def test_upper_price_property(self):
        """upper_price является алиасом для current_price."""
        cfg = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )
        assert cfg.upper_price == 600.0
        assert cfg.upper_price == cfg.current_price

    def test_create_factory_method(self):
        """Фабричный метод create маппит upper_price -> current_price."""
        cfg = LiquidityLadderConfig.create(
            upper_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )
        assert cfg.current_price == 600.0
        assert cfg.upper_price == 600.0
        assert cfg.lower_price == 400.0
        assert cfg.total_usd == 1000
        assert cfg.n_positions == 5
        assert cfg.token0 == WBNB
        assert cfg.token1 == USDT_BSC
        assert cfg.fee_tier == 2500

    def test_create_with_kwargs(self):
        """create() пробрасывает kwargs (distribution_type, decimals, slippage)."""
        cfg = LiquidityLadderConfig.create(
            upper_price=600.0,
            lower_price=400.0,
            total_usd=500,
            n_positions=3,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
            distribution_type="quadratic",
            token0_decimals=18,
            token1_decimals=6,
            slippage_percent=1.0,
        )
        assert cfg.distribution_type == "quadratic"
        assert cfg.token0_decimals == 18
        assert cfg.token1_decimals == 6
        assert cfg.slippage_percent == 1.0

    def test_default_values(self):
        """Дефолтные значения: slippage=0.5, distribution=linear, decimals=18/18."""
        cfg = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )
        assert cfg.slippage_percent == 0.5
        assert cfg.distribution_type == "linear"
        assert cfg.token0_decimals == 18
        assert cfg.token1_decimals == 18


# ============================================================
# LadderResult
# ============================================================

class TestLadderResult:
    """Тесты для LadderResult dataclass."""

    def _make_position(self, index: int = 0) -> BidAskPosition:
        """Вспомогательный метод: создать тестовую позицию."""
        return BidAskPosition(
            index=index,
            tick_lower=-50000,
            tick_upper=-49000,
            price_lower=400.0,
            price_upper=420.0,
            usd_amount=100.0,
            percentage=10.0,
            liquidity=123456789,
        )

    def test_fields_success(self):
        """Успешный результат содержит tx_hash, gas_used, token_ids."""
        pos = self._make_position()
        result = LadderResult(
            positions=[pos],
            tx_hash="0xabc123",
            gas_used=300000,
            token_ids=[1001, 1002],
            success=True,
        )
        assert result.success is True
        assert result.error is None
        assert result.tx_hash == "0xabc123"
        assert result.gas_used == 300000
        assert result.token_ids == [1001, 1002]
        assert len(result.positions) == 1

    def test_fields_error(self):
        """Результат с ошибкой: success=False, error заполнен."""
        result = LadderResult(
            positions=[],
            tx_hash=None,
            gas_used=None,
            token_ids=[],
            success=False,
            error="Insufficient balance",
        )
        assert result.success is False
        assert result.error == "Insufficient balance"
        assert result.tx_hash is None
        assert result.gas_used is None
        assert result.token_ids == []


# ============================================================
# InsufficientBalanceError
# ============================================================

class TestInsufficientBalanceError:
    """Тесты для InsufficientBalanceError."""

    def test_str_representation(self):
        """__str__ содержит required, available и адрес токена."""
        err = InsufficientBalanceError(
            required=1000 * 10**18,
            available=500 * 10**18,
            token_address=USDT_BSC,
        )
        s = str(err)
        assert "Insufficient balance" in s
        assert USDT_BSC in s


# ============================================================
# LiquidityProvider
# ============================================================

class TestLiquidityProvider:
    """Тесты для класса LiquidityProvider."""

    @pytest.fixture
    def provider(self):
        """
        Создаёт LiquidityProvider с полностью замоканными зависимостями,
        обходя __init__ через patch.
        """
        with patch.object(LiquidityProvider, '__init__', lambda self, *a, **kw: None):
            p = LiquidityProvider.__new__(LiquidityProvider)

            # Web3
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

            # Account
            p.account = Mock()
            p.account.address = "0x1234567890123456789012345678901234567890"
            p.account.sign_transaction = Mock(
                return_value=Mock(raw_transaction=b'signed')
            )

            # Chain / position manager
            p.chain_id = 56
            p.position_manager_address = "0xPosManager"
            p.position_manager = Mock()

            # Batcher
            p.batcher = Mock()
            p.batcher.calls = []
            p.batcher.__len__ = Mock(return_value=0)

            # Utility managers
            p.decimals_cache = Mock()
            p.gas_estimator = Mock()
            p.gas_estimator.estimate = Mock(return_value=60_000)
            p.nonce_manager = Mock()
            p.nonce_manager.get_next_nonce = Mock(return_value=1)

            return p

    # ------------------------------------------------------------------
    # preview_ladder
    # ------------------------------------------------------------------

    @patch('src.liquidity_provider.compute_decimal_tick_offset', return_value=0)
    @patch('src.liquidity_provider.calculate_bid_ask_distribution')
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_preview_ladder_returns_positions(
        self, mock_checksum, mock_calc, mock_offset, provider
    ):
        """preview_ladder возвращает список позиций из calculate_bid_ask_distribution."""
        positions = [
            BidAskPosition(
                index=0, tick_lower=-50000, tick_upper=-49500,
                price_lower=400.0, price_upper=420.0,
                usd_amount=200.0, percentage=20.0, liquidity=111,
            ),
            BidAskPosition(
                index=1, tick_lower=-49500, tick_upper=-49000,
                price_lower=420.0, price_upper=440.0,
                usd_amount=300.0, percentage=30.0, liquidity=222,
            ),
        ]
        mock_calc.return_value = positions

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=2,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        result = provider.preview_ladder(config)

        assert result == positions
        assert len(result) == 2
        mock_calc.assert_called_once()
        # Проверяем что distribution получила правильные параметры
        kwargs = mock_calc.call_args
        assert kwargs[1]['current_price'] == 600.0 or kwargs[0][0] == 600.0

    @patch('src.liquidity_provider.compute_decimal_tick_offset', return_value=0)
    @patch('src.liquidity_provider.calculate_bid_ask_distribution', return_value=[])
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_preview_ladder_invert_price_when_stablecoin_lower_address(
        self, mock_checksum, mock_calc, mock_offset, provider
    ):
        """
        Если адрес стейблкоина (token1) < адрес volatile (token0),
        стейблкоин является currency0 в пуле -> invert_price=True.
        """
        # USDT BSC: 0x55d3... < WBNB: 0xbb4C... -> stablecoin IS token0 -> invert
        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,      # volatile (address 0xbb4C... - higher)
            token1=USDT_BSC,  # stablecoin (address 0x55d3... - lower)
            fee_tier=2500,
        )

        provider.preview_ladder(config)

        # t0=WBNB (0xbb4C), t1=USDT (0x55d3): t1_addr < t0_addr -> NOT stablecoin_is_token1_in_pool
        # -> invert_price = True
        call_kwargs = mock_calc.call_args[1]
        assert call_kwargs['invert_price'] is True

    @patch('src.liquidity_provider.compute_decimal_tick_offset', return_value=0)
    @patch('src.liquidity_provider.calculate_bid_ask_distribution', return_value=[])
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_preview_ladder_no_invert_when_stablecoin_higher_address(
        self, mock_checksum, mock_calc, mock_offset, provider
    ):
        """
        Если адрес stablecoin (token1) > адрес volatile (token0),
        стейблкоин является token1 в пуле -> invert_price=False.
        """
        # Синтетические адреса: token0=ADDR_LOW (volatile), token1=ADDR_HIGH (stablecoin)
        # t1 > t0 -> stablecoin_is_token1_in_pool = True -> invert = False
        config = LiquidityLadderConfig(
            current_price=100.0,
            lower_price=50.0,
            total_usd=500,
            n_positions=3,
            token0=ADDR_LOW,
            token1=ADDR_HIGH,
            fee_tier=2500,
        )

        provider.preview_ladder(config)

        call_kwargs = mock_calc.call_args[1]
        assert call_kwargs['invert_price'] is False

    @patch('src.liquidity_provider.compute_decimal_tick_offset', return_value=276324)
    @patch('src.liquidity_provider.calculate_bid_ask_distribution', return_value=[])
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_preview_ladder_decimal_tick_offset_passed(
        self, mock_checksum, mock_calc, mock_offset, provider
    ):
        """decimal_tick_offset передаётся из compute_decimal_tick_offset в distribution."""
        config = LiquidityLadderConfig(
            current_price=3000.0,
            lower_price=2500.0,
            total_usd=1000,
            n_positions=5,
            token0=WETH_BASE,
            token1=USDC_BASE,
            fee_tier=3000,
            token0_decimals=18,
            token1_decimals=6,
        )

        provider.preview_ladder(config)

        call_kwargs = mock_calc.call_args[1]
        assert call_kwargs['decimal_tick_offset'] == 276324

    # ------------------------------------------------------------------
    # _ensure_token_order
    # ------------------------------------------------------------------

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_ensure_token_order_already_sorted(self, mock_checksum, provider):
        """Если token0 < token1 по адресу, порядок не меняется."""
        t0, t1, swapped = provider._ensure_token_order(ADDR_LOW, ADDR_HIGH)
        assert t0 == ADDR_LOW
        assert t1 == ADDR_HIGH
        assert swapped is False

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_ensure_token_order_needs_swap(self, mock_checksum, provider):
        """Если token0 > token1 по адресу, токены меняются местами."""
        t0, t1, swapped = provider._ensure_token_order(ADDR_HIGH, ADDR_LOW)
        assert t0 == ADDR_LOW
        assert t1 == ADDR_HIGH
        assert swapped is True

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_ensure_token_order_real_tokens_bnb(self, mock_checksum, provider):
        """Реальные адреса BNB: USDT (0x55d3...) < WBNB (0xbb4C...)."""
        t0, t1, swapped = provider._ensure_token_order(WBNB, USDT_BSC)
        # USDT lower address -> becomes t0
        assert t0 == USDT_BSC
        assert t1 == WBNB
        assert swapped is True

    # ------------------------------------------------------------------
    # validate_balances_for_ladder
    # ------------------------------------------------------------------

    def test_validate_balances_no_account(self, provider):
        """Без аккаунта -> (False, 'Account not configured')."""
        provider.account = None

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        is_valid, error = provider.validate_balances_for_ladder(config)
        assert is_valid is False
        assert "Account not configured" in error

    @patch('config.STABLECOINS', create=True, new={
        USDT_BSC.lower(): 18,
    })
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_validate_balances_sufficient(self, mock_checksum, provider):
        """Достаточный баланс -> (True, None)."""
        # total_usd=1000, USDT 18 dec -> need 1000 * 10^18
        required = 1000 * 10**18
        # Баланс: 2000 * 10^18
        provider.get_token_balance = Mock(return_value=2000 * 10**18)

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        is_valid, error = provider.validate_balances_for_ladder(config)
        assert is_valid is True
        assert error is None

    @patch('config.STABLECOINS', create=True, new={
        USDT_BSC.lower(): 18,
    })
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_validate_balances_insufficient(self, mock_checksum, provider):
        """Недостаточный баланс -> (False, error message)."""
        # total_usd=1000, баланс=100
        provider.get_token_balance = Mock(return_value=100 * 10**18)

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        is_valid, error = provider.validate_balances_for_ladder(config)
        assert is_valid is False
        assert "Insufficient" in error

    @patch('config.STABLECOINS', create=True, new={
        USDT_BSC.lower(): 18,
    })
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_validate_balances_stablecoin_detection_bnb(self, mock_checksum, provider):
        """USDT BSC (18 dec) детектится из STABLECOINS, баланс проверяется правильно."""
        provider.get_token_balance = Mock(return_value=500 * 10**18)

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=500,
            n_positions=3,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        is_valid, error = provider.validate_balances_for_ladder(config)
        assert is_valid is True
        # check_balance вызван с USDT адресом и правильной суммой
        provider.get_token_balance.assert_called_once_with(USDT_BSC, None)

    @patch('config.STABLECOINS', create=True, new={
        USDC_BASE.lower(): 6,
    })
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_validate_balances_stablecoin_detection_base(self, mock_checksum, provider):
        """USDC Base (6 dec) -> required = total_usd * 10^6."""
        # total_usd=1000, USDC 6 dec -> need 1000 * 10^6 = 1_000_000_000
        provider.get_token_balance = Mock(return_value=2000 * 10**6)

        config = LiquidityLadderConfig(
            current_price=3000.0,
            lower_price=2500.0,
            total_usd=1000,
            n_positions=5,
            token0=WETH_BASE,
            token1=USDC_BASE,
            fee_tier=3000,
            token0_decimals=18,
            token1_decimals=6,
        )

        is_valid, error = provider.validate_balances_for_ladder(config)
        assert is_valid is True
        # Проверяем, что get_token_balance вызван с USDC
        provider.get_token_balance.assert_called_once_with(USDC_BASE, None)

    @patch('config.STABLECOINS', create=True, new={
        USDC_BASE.lower(): 6,
    })
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_validate_balances_stablecoin_as_token0(self, mock_checksum, provider):
        """Стейблкоин может быть передан как token0 (не только token1)."""
        provider.get_token_balance = Mock(return_value=500 * 10**6)

        config = LiquidityLadderConfig(
            current_price=3000.0,
            lower_price=2500.0,
            total_usd=500,
            n_positions=3,
            token0=USDC_BASE,   # stablecoin as token0
            token1=WETH_BASE,
            fee_tier=3000,
            token0_decimals=6,
            token1_decimals=18,
        )

        is_valid, error = provider.validate_balances_for_ladder(config)
        assert is_valid is True
        provider.get_token_balance.assert_called_once_with(USDC_BASE, None)

    # ------------------------------------------------------------------
    # check_and_approve_tokens
    # ------------------------------------------------------------------

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_approve_already_sufficient(self, mock_checksum, provider):
        """Если allowance >= amount, approve не отправляется, возвращает None."""
        mock_allowance = Mock()
        mock_allowance.call = Mock(return_value=2**256 - 1)

        mock_functions = Mock()
        mock_functions.allowance = Mock(return_value=mock_allowance)

        mock_contract = Mock()
        mock_contract.functions = mock_functions

        provider.w3.eth.contract = Mock(return_value=mock_contract)

        result = provider.check_and_approve_tokens(USDT_BSC, 1000 * 10**18)
        assert result is None
        # send_raw_transaction не должен вызываться
        provider.w3.eth.send_raw_transaction.assert_not_called()

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_approve_needed(self, mock_checksum, provider):
        """Если allowance < amount, отправляется approve tx."""
        mock_allowance = Mock()
        mock_allowance.call = Mock(return_value=0)  # нет allowance

        mock_approve = Mock()
        mock_approve.build_transaction = Mock(return_value={
            'from': provider.account.address,
            'nonce': 1,
            'gas': 60000,
            'gasPrice': 5_000_000_000,
        })

        mock_functions = Mock()
        mock_functions.allowance = Mock(return_value=mock_allowance)
        mock_functions.approve = Mock(return_value=mock_approve)

        mock_contract = Mock()
        mock_contract.functions = mock_functions

        provider.w3.eth.contract = Mock(return_value=mock_contract)
        provider.w3.eth.send_raw_transaction = Mock(return_value=b'\xab\xcd' * 16)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={'status': 1})

        result = provider.check_and_approve_tokens(USDT_BSC, 1000 * 10**18)

        assert result is not None  # tx_hash вернулся
        provider.w3.eth.send_raw_transaction.assert_called_once()
        provider.account.sign_transaction.assert_called_once()

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_approve_uses_position_manager_as_default_spender(self, mock_checksum, provider):
        """По умолчанию spender = position_manager_address."""
        mock_allowance = Mock()
        mock_allowance.call = Mock(return_value=2**256 - 1)

        mock_functions = Mock()
        mock_functions.allowance = Mock(return_value=mock_allowance)

        mock_contract = Mock()
        mock_contract.functions = mock_functions

        provider.w3.eth.contract = Mock(return_value=mock_contract)

        provider.check_and_approve_tokens(USDT_BSC, 100)

        # allowance вызван с (account.address, position_manager_address)
        mock_functions.allowance.assert_called_once_with(
            provider.account.address,
            provider.position_manager_address,
        )

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_approve_nonce_released_on_error(self, mock_checksum, provider):
        """При ошибке отправки nonce должен быть освобождён."""
        mock_allowance = Mock()
        mock_allowance.call = Mock(return_value=0)

        mock_approve = Mock()
        mock_approve.build_transaction = Mock(side_effect=Exception("gas estimation failed"))

        mock_functions = Mock()
        mock_functions.allowance = Mock(return_value=mock_allowance)
        mock_functions.approve = Mock(return_value=mock_approve)

        mock_contract = Mock()
        mock_contract.functions = mock_functions
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        with pytest.raises(Exception, match="gas estimation failed"):
            provider.check_and_approve_tokens(USDT_BSC, 1000)

        provider.nonce_manager.release_nonce.assert_called_once()

    # ------------------------------------------------------------------
    # create_ladder
    # ------------------------------------------------------------------

    def test_create_ladder_no_account(self, provider):
        """Без аккаунта -> LadderResult(success=False, error='Account not configured')."""
        provider.account = None

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        result = provider.create_ladder(config)

        assert result.success is False
        assert result.error == "Account not configured"
        assert result.positions == []
        assert result.token_ids == []

    @patch('config.STABLECOINS', create=True, new={USDT_BSC.lower(): 18})
    @patch('src.liquidity_provider.compute_decimal_tick_offset', return_value=0)
    @patch('src.liquidity_provider.calculate_bid_ask_distribution', return_value=[])
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_create_ladder_insufficient_balance(
        self, mock_checksum, mock_calc, mock_offset, provider
    ):
        """Недостаточный баланс -> LadderResult(success=False)."""
        provider.get_token_balance = Mock(return_value=10 * 10**18)  # только 10

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        result = provider.create_ladder(config, check_balance=True)

        assert result.success is False
        assert "Insufficient" in result.error

    @patch('config.STABLECOINS', create=True, new={USDT_BSC.lower(): 18})
    @patch('src.liquidity_provider.compute_decimal_tick_offset', return_value=0)
    @patch('src.liquidity_provider.calculate_bid_ask_distribution')
    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_create_ladder_skip_balance_check(
        self, mock_checksum, mock_calc, mock_offset, provider
    ):
        """check_balance=False пропускает проверку баланса."""
        positions = [
            BidAskPosition(
                index=0, tick_lower=-51000, tick_upper=-50950,
                price_lower=400.0, price_upper=420.0,
                usd_amount=1000.0, percentage=100.0, liquidity=999,
            ),
        ]
        mock_calc.return_value = positions

        # Pool validation - use pre-validated
        # Batcher execute
        provider.batcher.execute = Mock(return_value=(
            "0xtxhash",
            [True],
            {'gasUsed': 250000},
            [5001],
        ))
        provider.batcher.simulate = Mock(return_value=[True])
        provider.batcher.simulate_single_call = Mock(return_value="Success")
        provider.batcher.debug_first_call = Mock(return_value={})
        provider.batcher.clear = Mock()

        # Approve
        provider.check_and_approve_tokens = Mock(return_value=None)

        # Mock contract for allowance check inside create_ladder
        mock_allowance = Mock()
        mock_allowance.call = Mock(return_value=2**256 - 1)
        mock_functions = Mock()
        mock_functions.allowance = Mock(return_value=mock_allowance)
        mock_contract = Mock()
        mock_contract.functions = mock_functions
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=1,
            token0=WBNB,
            token1=USDT_BSC,
            fee_tier=2500,
        )

        result = provider.create_ladder(
            config,
            check_balance=False,
            validated_pool_address="0xPoolAddr",
        )

        # validate_balances_for_ladder не должен вызываться
        # Если бы вызвался - get_token_balance не определён и упал бы
        assert result.success is True
        assert result.token_ids == [5001]

    # ------------------------------------------------------------------
    # get_token_balance
    # ------------------------------------------------------------------

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_get_token_balance(self, mock_checksum, provider):
        """Возвращает balanceOf из контракта."""
        mock_balance = Mock()
        mock_balance.call = Mock(return_value=555 * 10**18)

        mock_functions = Mock()
        mock_functions.balanceOf = Mock(return_value=mock_balance)

        mock_contract = Mock()
        mock_contract.functions = mock_functions

        provider.w3.eth.contract = Mock(return_value=mock_contract)

        balance = provider.get_token_balance(USDT_BSC)
        assert balance == 555 * 10**18

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_get_token_balance_custom_address(self, mock_checksum, provider):
        """Можно указать произвольный адрес для проверки баланса."""
        custom_addr = "0xCustomAddr"
        mock_balance = Mock()
        mock_balance.call = Mock(return_value=100 * 10**6)

        mock_functions = Mock()
        mock_functions.balanceOf = Mock(return_value=mock_balance)

        mock_contract = Mock()
        mock_contract.functions = mock_functions

        provider.w3.eth.contract = Mock(return_value=mock_contract)

        balance = provider.get_token_balance(USDC_BASE, address=custom_addr)
        assert balance == 100 * 10**6

        # balanceOf вызван с custom_addr
        mock_functions.balanceOf.assert_called_once_with(custom_addr)

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_get_token_balance_default_address(self, mock_checksum, provider):
        """Без address -> используется account.address."""
        mock_balance = Mock()
        mock_balance.call = Mock(return_value=42)

        mock_functions = Mock()
        mock_functions.balanceOf = Mock(return_value=mock_balance)

        mock_contract = Mock()
        mock_contract.functions = mock_functions
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        provider.get_token_balance(WBNB)

        mock_functions.balanceOf.assert_called_once_with(provider.account.address)

    # ------------------------------------------------------------------
    # check_balance
    # ------------------------------------------------------------------

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_check_balance_sufficient(self, mock_checksum, provider):
        """Баланс >= required -> (True, balance)."""
        provider.get_token_balance = Mock(return_value=500 * 10**18)

        is_ok, balance = provider.check_balance(USDT_BSC, 400 * 10**18)
        assert is_ok is True
        assert balance == 500 * 10**18

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_check_balance_insufficient(self, mock_checksum, provider):
        """Баланс < required -> (False, balance)."""
        provider.get_token_balance = Mock(return_value=100 * 10**18)

        is_ok, balance = provider.check_balance(USDT_BSC, 400 * 10**18)
        assert is_ok is False
        assert balance == 100 * 10**18

    @patch.object(Web3, 'to_checksum_address', side_effect=lambda addr: addr)
    def test_check_balance_exact(self, mock_checksum, provider):
        """Баланс == required -> (True, balance)."""
        amount = 1000 * 10**18
        provider.get_token_balance = Mock(return_value=amount)

        is_ok, balance = provider.check_balance(USDT_BSC, amount)
        assert is_ok is True

    # ------------------------------------------------------------------
    # format_amount
    # ------------------------------------------------------------------

    def test_format_amount_18_decimals(self, provider):
        """Форматирование wei -> human для 18-decimal токена."""
        # 1234.5678 * 10^18
        amount = 1234567800000000000000
        result = provider.format_amount(amount, decimals=18)
        assert "1,234.5678" in result

    def test_format_amount_6_decimals(self, provider):
        """Форматирование для USDC (6 decimals)."""
        amount = 1_000_000_000  # 1000 * 10^6
        result = provider.format_amount(amount, decimals=6)
        assert "1,000.0000" in result

    def test_format_amount_zero(self, provider):
        """Нулевая сумма."""
        result = provider.format_amount(0, decimals=18)
        assert "0.0000" in result

    def test_format_amount_small(self, provider):
        """Маленькая сумма (< 1 токена)."""
        # 0.001 * 10^18 = 10^15
        amount = 10**15
        result = provider.format_amount(amount, decimals=18)
        assert "0.0010" in result

    def test_format_amount_default_decimals(self, provider):
        """По умолчанию decimals=18."""
        amount = 10**18
        result_default = provider.format_amount(amount)
        result_explicit = provider.format_amount(amount, decimals=18)
        assert result_default == result_explicit

    # ------------------------------------------------------------------
    # _get_factory_address
    # ------------------------------------------------------------------

    def test_get_factory_address_known(self, provider):
        """Для известного Position Manager возвращает factory."""
        provider.position_manager_address = "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364"
        result = provider._get_factory_address()
        assert result == "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"

    def test_get_factory_address_unknown(self, provider):
        """Для неизвестного Position Manager -> None."""
        provider.position_manager_address = "0x0000000000000000000000000000000000000000"
        result = provider._get_factory_address()
        assert result is None

    def test_get_factory_address_no_pm(self, provider):
        """Без position_manager_address -> None."""
        provider.position_manager_address = None
        result = provider._get_factory_address()
        assert result is None
