"""
Extended tests for V4 LiquidityProvider — targeting coverage increase from 30% to 60%+.

Covers uncovered lines:
- __init__ (proxy / no proxy / with-without private_key)
- create_pool (TX build, sign, send, receipt check, nonce management)
- create_pool_only (fee validation, pool exists, TX success/fail)
- validate_balances edge cases (only stablecoin low, only volatile low)
- check_approvals (batch RPC, permit2 checks, expiration)
- approve_tokens_for_ladder (full ERC20 + Permit2 flow)
- check_pool_compatibility (tick spacing mismatch)
- create_ladder extended (pool ID mismatch, approval flow, TX execution, close_positions)
- close_positions (success, reverted TX, empty token_ids, missing currencies)
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch, PropertyMock, call
from dataclasses import dataclass
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
from src.contracts.v4.pool_manager import PoolKey, V4PoolState
from src.math.distribution import BidAskPosition


# ============================================================
# Test constants
# ============================================================

TOKEN_VOLATILE = "0x1111111111111111111111111111111111111111"
TOKEN_STABLE = "0x9999999999999999999999999999999999999999"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ACCOUNT_ADDRESS = "0x1234567890123456789012345678901234567890"
POS_MANAGER_ADDRESS = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PERMIT2_ADDRESS = PERMIT2_PANCAKESWAP
FAKE_TX_HASH = b'\xab\xcd' * 16


def _make_config(**overrides) -> V4LadderConfig:
    """Create config with reasonable defaults."""
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
    """Create mock position."""
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


def _make_pool_state(initialized=True, tick=-100, liquidity=1000000,
                     sqrt_price_x96=79228162514264337593543950336,
                     lp_fee=3000):
    """Create a V4PoolState instance."""
    return V4PoolState(
        pool_id=b'\x00' * 32,
        sqrt_price_x96=sqrt_price_x96,
        tick=tick,
        liquidity=liquidity,
        protocol_fee=0,
        lp_fee=lp_fee,
        initialized=initialized,
    )


def _build_provider():
    """Build a V4LiquidityProvider with mocked internals (bypass __init__)."""
    with patch.object(V4LiquidityProvider, '__init__', lambda self, *a, **kw: None):
        p = V4LiquidityProvider.__new__(V4LiquidityProvider)
        p.w3 = Mock(spec=Web3)
        p.w3.eth = Mock()
        p.w3.eth.gas_price = 5_000_000_000
        p.w3.eth.get_transaction_count = Mock(return_value=0)
        p.w3.eth.send_raw_transaction = Mock(return_value=FAKE_TX_HASH)
        p.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 300_000,
            'logs': [],
        })
        p.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 300_000,
            'logs': [],
        })
        p.w3.eth.contract = Mock()
        p.w3.to_checksum_address = Web3.to_checksum_address

        p.account = Mock()
        p.account.address = ACCOUNT_ADDRESS
        p.account.sign_transaction = Mock(
            return_value=Mock(raw_transaction=b'signed')
        )

        p.chain_id = 56
        p.protocol = V4Protocol.PANCAKESWAP
        p.proxy = None

        p.pool_manager = Mock()
        p.pool_manager.is_pool_initialized = Mock(return_value=True)
        p.pool_manager.get_pool_state = Mock(return_value=_make_pool_state())
        p.pool_manager.get_pool_state_by_id = Mock(return_value=_make_pool_state())
        p.pool_manager.price_to_sqrt_price_x96 = Mock(return_value=79228162514264337593543950336)
        p.pool_manager._compute_pool_id = Mock(return_value=b'\x01' * 32)

        p.position_manager = Mock()
        p.position_manager.position_manager_address = POS_MANAGER_ADDRESS
        p.position_manager.contract = Mock()
        p.position_manager.build_mint_action = Mock(return_value=b'\x00' * 64)
        p.position_manager.encode_settle_pair = Mock(return_value=b'\x01' * 32)
        p.position_manager._encode_actions = Mock(return_value=b'\x02' * 128)
        p.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
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


# ============================================================
# TestInit — __init__ with proxy, without proxy, with/without key
# ============================================================

class TestInit:
    """Tests for V4LiquidityProvider.__init__ (lines 245-274)."""

    @patch('src.v4_liquidity_provider.V4PositionManager')
    @patch('src.v4_liquidity_provider.V4PoolManager')
    @patch('src.v4_liquidity_provider.GasEstimator')
    @patch('src.v4_liquidity_provider.DecimalsCache')
    @patch('src.v4_liquidity_provider.NonceManager')
    @patch('src.v4_liquidity_provider.Account.from_key')
    @patch('src.v4_liquidity_provider.Web3')
    def test_init_without_proxy(self, MockWeb3, mock_from_key, MockNonce,
                                MockDecimals, MockGas, MockPoolMgr, MockPosMgr):
        """__init__ without proxy uses plain HTTPProvider."""
        mock_w3_instance = MockWeb3.return_value
        mock_account = Mock()
        mock_account.address = ACCOUNT_ADDRESS
        mock_from_key.return_value = mock_account

        provider = V4LiquidityProvider(
            rpc_url="https://bsc-dataseed.binance.org/",
            private_key="0x" + "ab" * 32,
            protocol=V4Protocol.PANCAKESWAP,
            chain_id=56,
        )

        MockWeb3.HTTPProvider.assert_called_once_with("https://bsc-dataseed.binance.org/")
        assert provider.chain_id == 56
        assert provider.protocol == V4Protocol.PANCAKESWAP
        assert provider.proxy is None
        assert provider.account == mock_account
        MockNonce.assert_called_once()
        MockPoolMgr.assert_called_once()
        MockPosMgr.assert_called_once()

    @patch('src.v4_liquidity_provider.V4PositionManager')
    @patch('src.v4_liquidity_provider.V4PoolManager')
    @patch('src.v4_liquidity_provider.GasEstimator')
    @patch('src.v4_liquidity_provider.DecimalsCache')
    @patch('src.v4_liquidity_provider.NonceManager')
    @patch('src.v4_liquidity_provider.Account.from_key')
    @patch('src.v4_liquidity_provider.Web3')
    def test_init_with_proxy(self, MockWeb3, mock_from_key, MockNonce,
                             MockDecimals, MockGas, MockPoolMgr, MockPosMgr):
        """__init__ with proxy passes proxies to HTTPProvider."""
        mock_w3_instance = MockWeb3.return_value
        mock_account = Mock()
        mock_account.address = ACCOUNT_ADDRESS
        mock_from_key.return_value = mock_account

        proxy = {"http": "socks5://127.0.0.1:1080", "https": "socks5://127.0.0.1:1080"}

        provider = V4LiquidityProvider(
            rpc_url="https://bsc-dataseed.binance.org/",
            private_key="0x" + "ab" * 32,
            protocol=V4Protocol.PANCAKESWAP,
            chain_id=56,
            proxy=proxy,
        )

        MockWeb3.HTTPProvider.assert_called_once_with(
            endpoint_uri="https://bsc-dataseed.binance.org/",
            request_kwargs={"proxies": proxy}
        )
        assert provider.proxy == proxy

    @patch('src.v4_liquidity_provider.V4PositionManager')
    @patch('src.v4_liquidity_provider.V4PoolManager')
    @patch('src.v4_liquidity_provider.GasEstimator')
    @patch('src.v4_liquidity_provider.DecimalsCache')
    @patch('src.v4_liquidity_provider.Web3')
    def test_init_without_private_key(self, MockWeb3, MockDecimals, MockGas,
                                      MockPoolMgr, MockPosMgr):
        """__init__ without private_key leaves account=None and no NonceManager."""
        mock_w3_instance = MockWeb3.return_value

        provider = V4LiquidityProvider(
            rpc_url="https://bsc-dataseed.binance.org/",
            protocol=V4Protocol.UNISWAP,
            chain_id=1,
        )

        assert provider.account is None
        assert provider.nonce_manager is None
        assert provider.chain_id == 1
        assert provider.protocol == V4Protocol.UNISWAP


# ============================================================
# TestCreatePool — TX success, revert, nonce management
# ============================================================

class TestCreatePool:
    """Tests for create_pool (lines 368-436)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    @pytest.fixture
    def config(self):
        return _make_config()

    def test_create_pool_tx_success(self, provider, config):
        """create_pool builds TX, signs, sends, and returns (hash, True) on success."""
        provider.pool_manager.is_pool_initialized.return_value = False

        # Mock initializePool
        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={
            'from': ACCOUNT_ADDRESS,
            'nonce': 1,
            'gas': 500000,
            'gasPrice': 5_000_000_000,
        })
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            tx_hash, success = provider.create_pool(config)

        assert success is True
        assert tx_hash is not None
        provider.nonce_manager.confirm_transaction.assert_called_once_with(1)
        provider.nonce_manager.release_nonce.assert_not_called()

    def test_create_pool_tx_reverted(self, provider, config):
        """create_pool with reverted TX returns (hash, False) and confirms nonce (TX was mined)."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        # Receipt with status=0 (reverted)
        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 0,
            'gasUsed': 21_000,
        })

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            tx_hash, success = provider.create_pool(config)

        assert success is False
        assert tx_hash is not None
        # Reverted TX was mined — nonce consumed on-chain, so confirm (not release)
        provider.nonce_manager.confirm_transaction.assert_called_once_with(1)
        provider.nonce_manager.release_nonce.assert_not_called()

    def test_create_pool_exception_releases_nonce(self, provider, config):
        """create_pool exception before send_raw_transaction releases nonce."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(side_effect=Exception("gas estimation failed"))
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            with pytest.raises(Exception, match="gas estimation failed"):
                provider.create_pool(config)

        provider.nonce_manager.release_nonce.assert_called_once_with(1)

    def test_create_pool_without_nonce_manager(self, provider, config):
        """create_pool without nonce_manager uses w3.eth.get_transaction_count."""
        provider.nonce_manager = None
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            tx_hash, success = provider.create_pool(config)

        assert success is True
        provider.w3.eth.get_transaction_count.assert_called()

    def test_create_pool_uses_initial_price(self, provider, config):
        """create_pool uses explicit initial_price when provided."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            provider.create_pool(config, initial_price=0.01)

        # Should have inverted the price (1/0.01 = 100) since config.invert_price=True
        provider.pool_manager.price_to_sqrt_price_x96.assert_called_once_with(
            100.0, config.token0_decimals, config.token1_decimals
        )

    def test_create_pool_uses_actual_current_price_fallback(self, provider, config):
        """create_pool uses actual_current_price when initial_price not given."""
        config.actual_current_price = 0.004
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            provider.create_pool(config)

        # Should use actual_current_price=0.004, inverted → 250.0
        provider.pool_manager.price_to_sqrt_price_x96.assert_called_once_with(
            250.0, config.token0_decimals, config.token1_decimals
        )

    def test_create_pool_no_invert(self, provider, config):
        """create_pool with invert_price=False passes price directly."""
        config.invert_price = False
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            provider.create_pool(config, initial_price=200.0)

        # No inversion: passes 200.0 directly
        provider.pool_manager.price_to_sqrt_price_x96.assert_called_once_with(
            200.0, config.token0_decimals, config.token1_decimals
        )

    def test_create_pool_verifies_state_after_success(self, provider, config):
        """create_pool verifies pool state after successful TX."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.to_tuple = Mock(return_value=('0x1111', '0x9999', 3000, 60, ZERO_ADDRESS))

        with patch.object(provider, 'get_pool_key', return_value=mock_pool_key):
            provider.create_pool(config)

        # get_pool_state should be called to verify creation
        provider.pool_manager.get_pool_state.assert_called_once_with(mock_pool_key)


# ============================================================
# TestCreatePoolOnly — fee validation, pool exists, TX success/fail
# ============================================================

class TestCreatePoolOnly:
    """Tests for create_pool_only (lines 472-575)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    def test_create_pool_only_no_account(self, provider):
        """create_pool_only without account raises ValueError."""
        provider.account = None

        with pytest.raises(ValueError, match="Account not configured"):
            provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=0.005,
            )

    def test_create_pool_only_invalid_fee_negative(self, provider):
        """Negative fee raises ValueError."""
        with pytest.raises(ValueError, match="Fee must be between 0% and 100%"):
            provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=-1.0, initial_price=0.005,
            )

    def test_create_pool_only_invalid_fee_over_100(self, provider):
        """Fee > 100% raises ValueError."""
        with pytest.raises(ValueError, match="Fee must be between 0% and 100%"):
            provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=101.0, initial_price=0.005,
            )

    def _make_mock_pool_key(self):
        """Create a mock PoolKey with proper attributes for create_pool_only."""
        mock_pk = Mock()
        mock_pk.currency0 = Web3.to_checksum_address(TOKEN_VOLATILE)
        mock_pk.currency1 = Web3.to_checksum_address(TOKEN_STABLE)
        mock_pk.fee = 3000
        mock_pk.tick_spacing = 60
        mock_pk.hooks = ZERO_ADDRESS
        mock_pk.to_tuple = Mock(return_value=(
            Web3.to_checksum_address(TOKEN_VOLATILE),
            Web3.to_checksum_address(TOKEN_STABLE),
            3000, 60, ZERO_ADDRESS,
        ))
        return mock_pk

    def test_create_pool_only_already_exists(self, provider):
        """If pool exists, returns (None, pool_id, True)."""
        provider.pool_manager.is_pool_initialized.return_value = True

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()):
            tx_hash, pool_id, success = provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=0.005,
            )

        assert tx_hash is None
        assert pool_id is not None
        assert success is True

    def test_create_pool_only_tx_success(self, provider):
        """Successful TX returns (hash, pool_id, True)."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()):
            tx_hash, pool_id, success = provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=0.005,
            )

        assert tx_hash is not None
        assert pool_id is not None
        assert success is True
        provider.nonce_manager.confirm_transaction.assert_called_once_with(1)

    def test_create_pool_only_tx_reverted(self, provider):
        """Reverted TX returns (hash, pool_id, False) and confirms nonce (TX was mined)."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        provider.w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 0,
            'gasUsed': 21_000,
        })

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()):
            tx_hash, pool_id, success = provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=0.005,
            )

        assert success is False
        # Reverted TX was mined — nonce consumed on-chain, so confirm (not release)
        provider.nonce_manager.confirm_transaction.assert_called_once_with(1)
        provider.nonce_manager.release_nonce.assert_not_called()

    def test_create_pool_only_exception_returns_false(self, provider):
        """Exception during TX returns (None, pool_id, False)."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(side_effect=Exception("network error"))
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()):
            tx_hash, pool_id, success = provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=0.005,
            )

        assert tx_hash is None
        assert success is False
        provider.nonce_manager.release_nonce.assert_called_once_with(1)

    def test_create_pool_only_invert_price(self, provider):
        """invert_price=True inverts the initial price."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()):
            provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=0.005,
                invert_price=True,
            )

        # 1/0.005 = 200.0
        provider.pool_manager.price_to_sqrt_price_x96.assert_called_once_with(200.0, 18, 18)

    def test_create_pool_only_no_invert_price(self, provider):
        """invert_price=False passes price directly."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()):
            provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=200.0,
                invert_price=False,
            )

        provider.pool_manager.price_to_sqrt_price_x96.assert_called_once_with(200.0, 18, 18)

    def test_create_pool_only_verification_fails(self, provider):
        """TX succeeds but pool verification fails → success=False."""
        provider.pool_manager.is_pool_initialized.return_value = False

        mock_init_fn = Mock()
        mock_init_fn.build_transaction = Mock(return_value={})
        provider.position_manager.contract.functions.initializePool = Mock(
            return_value=mock_init_fn
        )

        # Pool state says NOT initialized even after successful TX
        provider.pool_manager.get_pool_state.return_value = _make_pool_state(initialized=False)

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()):
            tx_hash, pool_id, success = provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=0.3, initial_price=0.005,
            )

        assert success is False

    def test_create_pool_only_auto_tick_spacing(self, provider):
        """When tick_spacing=None, auto-calculated from fee."""
        provider.pool_manager.is_pool_initialized.return_value = True

        with patch.object(PoolKey, 'from_tokens', return_value=self._make_mock_pool_key()) as mock_from:
            provider.create_pool_only(
                token0=TOKEN_VOLATILE, token1=TOKEN_STABLE,
                fee_percent=1.0, initial_price=0.005,
                tick_spacing=None,
            )

        # suggest_tick_spacing(1.0) = round(1.0 * 200) = 200
        call_kwargs = mock_from.call_args
        assert call_kwargs.kwargs.get('tick_spacing') == 200 or call_kwargs[1].get('tick_spacing') == 200


# ============================================================
# TestValidateBalancesEdgeCases — only volatile sufficient, low stablecoin
# ============================================================

class TestValidateBalancesEdgeCases:
    """Tests for validate_balances edge cases (lines 745, 768-769, 816-817)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    @pytest.fixture
    def config(self):
        return _make_config()

    def _setup_balances(self, provider, config, stablecoin_balance, volatile_balance):
        """Common setup for balance tests."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.decimals_cache.get_decimals = Mock(return_value=18)

        mock_batch = Mock()
        mock_batch.execute = Mock(return_value=[stablecoin_balance, volatile_balance])
        return mock_batch

    def test_validate_balances_only_volatile_sufficient(self, provider, config):
        """Only volatile token sufficient returns True (positions below current price)."""
        # total_volatile = total_usd / user_price * 10^18 = 100 / 0.005 * 10^18 = 20000 * 10^18
        mock_batch = self._setup_balances(
            provider, config,
            stablecoin_balance=0,  # Insufficient stablecoin
            volatile_balance=20001 * 10**18,  # Sufficient volatile (need 20000)
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is True
        assert error is None

    def test_validate_balances_both_insufficient(self, provider, config):
        """Both tokens insufficient returns False with error."""
        mock_batch = self._setup_balances(
            provider, config,
            stablecoin_balance=0,
            volatile_balance=0,
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is False
        assert "Insufficient" in error

    def test_validate_balances_quote_is_token0(self, provider, config):
        """When stablecoin is currency0, volatile is currency1."""
        provider._get_quote_token = Mock(return_value=(TOKEN_VOLATILE, 18))

        mock_pool_key = Mock()
        # Stablecoin address < volatile address, so stablecoin is currency0
        mock_pool_key.currency0 = TOKEN_VOLATILE  # quote == currency0
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.decimals_cache.get_decimals = Mock(return_value=18)

        mock_batch = Mock()
        mock_batch.execute = Mock(return_value=[200 * 10**18, 200 * 10**18])

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is True
        assert error is None

    def test_validate_balances_only_stablecoin_insufficient(self, provider, config):
        """Only stablecoin insufficient but volatile sufficient → True with warning."""
        # volatile needs 20000 * 10^18 (100 USD / 0.005 price)
        mock_batch = self._setup_balances(
            provider, config,
            stablecoin_balance=1,  # Insufficient
            volatile_balance=20001 * 10**18,  # Sufficient volatile
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is True
        assert error is None

    def test_validate_balances_uses_actual_current_price(self, provider, config):
        """validate_balances uses actual_current_price for volatile amount calculation."""
        config.actual_current_price = 0.004

        mock_batch = self._setup_balances(
            provider, config,
            stablecoin_balance=200 * 10**18,
            volatile_balance=200 * 10**18,
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            is_valid, error = provider.validate_balances(config)

        assert is_valid is True


# ============================================================
# TestCheckApprovals — batch RPC, permit2 checks, expiration
# ============================================================

class TestCheckApprovals:
    """Tests for check_approvals (lines 817, 830-923)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    @pytest.fixture
    def config(self):
        return _make_config()

    def _setup_approvals(self, provider, config, quote_erc20=10**30, base_erc20=10**30,
                         quote_permit2=(10**30, int(time.time()) + 86400, 0),
                         base_permit2=(10**30, int(time.time()) + 86400, 0)):
        """Common approval setup."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)
        provider.decimals_cache.get_decimals = Mock(return_value=18)

        # Mock batch RPC for ERC20 allowances
        mock_batch = Mock()
        mock_batch.execute = Mock(return_value=[quote_erc20, base_erc20])

        # Mock Permit2 contract calls
        mock_permit2_contract = Mock()
        mock_permit2_contract.functions.allowance = Mock(
            side_effect=[
                Mock(call=Mock(return_value=quote_permit2)),
                Mock(call=Mock(return_value=base_permit2)),
            ]
        )
        provider.w3.eth.contract = Mock(return_value=mock_permit2_contract)

        return mock_batch

    def test_check_approvals_all_approved(self, provider, config):
        """All approvals in place returns approved=True for all."""
        mock_batch = self._setup_approvals(provider, config)

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            result = provider.check_approvals(config)

        assert result['erc20_to_permit2']['approved'] is True
        assert result['permit2_to_position_manager']['approved'] is True
        assert result['base_erc20_to_permit2']['approved'] is True
        assert result['base_permit2_to_position_manager']['approved'] is True
        assert result['quote_token'] == TOKEN_STABLE
        assert result['base_token'] == TOKEN_VOLATILE

    def test_check_approvals_none_approved(self, provider, config):
        """No approvals returns approved=False for all."""
        mock_batch = self._setup_approvals(
            provider, config,
            quote_erc20=0, base_erc20=0,
            quote_permit2=(0, 0, 0),
            base_permit2=(0, 0, 0),
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            result = provider.check_approvals(config)

        assert result['erc20_to_permit2']['approved'] is False
        assert result['permit2_to_position_manager']['approved'] is False
        assert result['base_erc20_to_permit2']['approved'] is False
        assert result['base_permit2_to_position_manager']['approved'] is False

    def test_check_approvals_expired_permit2(self, provider, config):
        """Expired Permit2 allowance returns approved=False, expired=True."""
        expired_time = int(time.time()) - 3600  # 1 hour ago

        mock_batch = self._setup_approvals(
            provider, config,
            quote_erc20=10**30, base_erc20=10**30,
            quote_permit2=(10**30, expired_time, 0),
            base_permit2=(10**30, expired_time, 0),
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            result = provider.check_approvals(config)

        assert result['permit2_to_position_manager']['approved'] is False
        assert result['permit2_to_position_manager']['expired'] is True
        assert result['base_permit2_to_position_manager']['approved'] is False
        assert result['base_permit2_to_position_manager']['expired'] is True

    def test_check_approvals_batch_rpc_fallback(self, provider, config):
        """When BatchRPC fails, fallback to individual contract calls."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)
        provider.decimals_cache.get_decimals = Mock(return_value=18)

        # BatchRPC throws exception
        mock_batch = Mock()
        mock_batch.execute = Mock(side_effect=Exception("RPC error"))

        # Fallback contract returns
        mock_contract = Mock()
        mock_contract.functions.allowance = Mock(
            side_effect=[
                # ERC20 quote allowance
                Mock(call=Mock(return_value=10**30)),
                # ERC20 base allowance
                Mock(call=Mock(return_value=10**30)),
                # Permit2 quote allowance
                Mock(call=Mock(return_value=(10**30, int(time.time()) + 86400, 0))),
                # Permit2 base allowance
                Mock(call=Mock(return_value=(10**30, int(time.time()) + 86400, 0))),
            ]
        )
        provider.w3.eth.contract = Mock(return_value=mock_contract)

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            result = provider.check_approvals(config)

        assert result['erc20_to_permit2']['approved'] is True
        assert result['permit2_to_position_manager']['approved'] is True

    def test_check_approvals_partial_approved(self, provider, config):
        """ERC20 approved but Permit2 not → mixed results."""
        mock_batch = self._setup_approvals(
            provider, config,
            quote_erc20=10**30, base_erc20=10**30,
            quote_permit2=(0, 0, 0),
            base_permit2=(10**30, int(time.time()) + 86400, 0),
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            result = provider.check_approvals(config)

        assert result['erc20_to_permit2']['approved'] is True
        assert result['permit2_to_position_manager']['approved'] is False
        assert result['base_erc20_to_permit2']['approved'] is True
        assert result['base_permit2_to_position_manager']['approved'] is True


# ============================================================
# TestApproveTokensForLadder — full ERC20 + Permit2 flow
# ============================================================

class TestApproveTokensForLadder:
    """Tests for approve_tokens_for_ladder (lines 961-1078)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    @pytest.fixture
    def config(self):
        return _make_config()

    def test_approve_no_account_raises(self, provider, config):
        """Without account raises ValueError."""
        provider.account = None

        with pytest.raises(ValueError, match="Account not configured"):
            provider.approve_tokens_for_ladder(config)

    def test_approve_full_flow_success(self, provider, config):
        """Full approval flow succeeds for both tokens."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        # Mock check_and_approve_token to return TX hashes
        provider.check_and_approve_token = Mock(
            side_effect=["0xerc20_quote_tx", "0xerc20_base_tx"]
        )
        # Mock approve_on_permit2
        provider.approve_on_permit2 = Mock(
            side_effect=["0xpermit2_quote_tx", "0xpermit2_base_tx"]
        )

        result = provider.approve_tokens_for_ladder(config)

        assert result['success'] is True
        assert result['erc20_approve_tx'] == "0xerc20_quote_tx"
        assert result['permit2_approve_tx'] == "0xpermit2_quote_tx"
        assert result['base_erc20_approve_tx'] == "0xerc20_base_tx"
        assert result['base_permit2_approve_tx'] == "0xpermit2_base_tx"
        assert result['quote_token'] == TOKEN_STABLE
        assert result['base_token'] == TOKEN_VOLATILE

    def test_approve_erc20_already_approved(self, provider, config):
        """ERC20 already approved returns None for erc20_approve_tx."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        # check_and_approve_token returns None (already approved)
        provider.check_and_approve_token = Mock(return_value=None)
        provider.approve_on_permit2 = Mock(return_value="0xpermit2_tx")

        result = provider.approve_tokens_for_ladder(config)

        assert result['success'] is True
        assert result['erc20_approve_tx'] is None
        assert result['base_erc20_approve_tx'] is None

    def test_approve_exception_returns_error(self, provider, config):
        """Exception during approval returns success=False with error."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.check_and_approve_token = Mock(side_effect=Exception("Approval failed"))

        result = provider.approve_tokens_for_ladder(config)

        assert result['success'] is False
        assert 'error' in result
        assert "Approval failed" in result['error']

    def test_approve_uses_safety_multiplier(self, provider, config):
        """Approval amounts use 3x safety multiplier."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.check_and_approve_token = Mock(return_value=None)
        provider.approve_on_permit2 = Mock(return_value="0xtx")

        result = provider.approve_tokens_for_ladder(config)

        # check_and_approve_token called with amount = total_usd * 10^18 * 3
        expected_quote_amount = int(100.0 * (10 ** 18) * 3)
        first_call_amount = provider.check_and_approve_token.call_args_list[0][0][1]
        assert first_call_amount == expected_quote_amount

    def test_approve_quote_is_token0(self, provider, config):
        """When quote is token0, base token is currency1."""
        provider._get_quote_token = Mock(return_value=(TOKEN_VOLATILE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE  # quote is currency0
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.check_and_approve_token = Mock(return_value=None)
        provider.approve_on_permit2 = Mock(return_value="0xtx")

        result = provider.approve_tokens_for_ladder(config)

        assert result['success'] is True
        assert result['quote_token'] == TOKEN_VOLATILE
        assert result['base_token'] == TOKEN_STABLE


# ============================================================
# TestCheckPoolCompatibility — tick spacing mismatch
# ============================================================

class TestCheckPoolCompatibility:
    """Tests for check_pool_compatibility (lines 1101-1126)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    @pytest.fixture
    def config(self):
        return _make_config()

    def test_compatible_quote_is_token0(self, provider, config):
        """When quote/stablecoin is token0, pool is compatible."""
        provider._get_quote_token = Mock(return_value=(TOKEN_VOLATILE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE  # quote is token0
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        result = provider.check_pool_compatibility(config)

        assert result['compatible'] is True
        assert result['usdt_is_token0'] is True
        assert "compatible" in result['recommendation'].lower()

    def test_not_compatible_quote_is_token1(self, provider, config):
        """When quote/stablecoin is token1, pool is NOT compatible for USDT-only."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE  # quote is NOT token0
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        result = provider.check_pool_compatibility(config)

        assert result['compatible'] is False
        assert result['usdt_is_token0'] is False
        assert "NOT compatible" in result['recommendation']

    def test_compatibility_returns_addresses(self, provider, config):
        """Result includes currency0, currency1, and usdt_address."""
        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock()
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        result = provider.check_pool_compatibility(config)

        assert result['currency0'] == TOKEN_VOLATILE
        assert result['currency1'] == TOKEN_STABLE
        assert result['usdt_address'] == TOKEN_STABLE


# ============================================================
# TestCreateLadderExtended — pool ID mismatch, approval checks, TX execution
# ============================================================

class TestCreateLadderExtended:
    """Tests for create_ladder extended scenarios (lines 1223-1776)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    @pytest.fixture
    def config(self):
        return _make_config()

    def _setup_create_ladder(self, provider, config, positions=None, pool_exists=True):
        """Common setup for create_ladder tests."""
        if positions is None:
            positions = [_make_position(i, tick_lower=-200, tick_upper=-100) for i in range(2)]

        provider._get_quote_token = Mock(return_value=(TOKEN_STABLE, 18))

        mock_pool_key = Mock(spec=PoolKey)
        mock_pool_key.currency0 = TOKEN_VOLATILE
        mock_pool_key.currency1 = TOKEN_STABLE
        mock_pool_key.tick_spacing = 60
        mock_pool_key.fee = 3000
        mock_pool_key.hooks = ZERO_ADDRESS
        provider.get_pool_key = Mock(return_value=mock_pool_key)

        provider.pool_manager.is_pool_initialized.return_value = pool_exists
        provider.pool_manager._compute_pool_id.return_value = b'\x01' * 32

        provider.preview_ladder = Mock(return_value=positions)
        provider.validate_balances = Mock(return_value=(True, None))
        provider.approve_tokens_for_ladder = Mock(return_value={'success': True})

        # Mock ERC20 contract for base decimals check
        mock_erc20 = Mock()
        mock_erc20.functions.decimals = Mock(return_value=Mock(call=Mock(return_value=18)))
        provider.w3.eth.contract = Mock(return_value=mock_erc20)

        # Mock batch RPC for pre-flight checks
        mock_batch = Mock()
        mock_batch.execute = Mock(return_value=[200 * 10**18, 10**30, 200 * 10**18, 10**30])

        return positions, mock_pool_key, mock_batch

    def test_create_ladder_pool_not_exists_auto_create_success(self, provider, config):
        """Pool doesn't exist, auto_create creates it."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(
            provider, config, pool_exists=False
        )
        config.pool_id = None

        provider.create_pool = Mock(return_value=("0xpool_tx", True))
        # After creation, pool should exist on second check
        provider.pool_manager.is_pool_initialized.return_value = False

        # Mock receipt for the ladder TX
        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 500_000,
            'logs': [],
        })

        # Mock Transfer event parsing
        provider.position_manager.contract.events.Transfer = Mock(
            return_value=Mock(
                process_receipt=Mock(return_value=[
                    {'args': {'from': ZERO_ADDRESS, 'tokenId': 101}},
                    {'args': {'from': ZERO_ADDRESS, 'tokenId': 102}},
                ])
            )
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, auto_create_pool=True, skip_approvals=False)

        assert result.pool_created is True

    def test_create_ladder_pool_not_exists_auto_create_fails(self, provider, config):
        """Pool creation fails → return error result."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(
            provider, config, pool_exists=False
        )
        config.pool_id = None

        provider.create_pool = Mock(return_value=("0xpool_tx", False))

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            result = provider.create_ladder(config, auto_create_pool=True, skip_approvals=False)

        assert result.success is False
        assert "Failed to create pool" in result.error

    def test_create_ladder_pool_create_exception(self, provider, config):
        """Pool creation exception → return error result."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(
            provider, config, pool_exists=False
        )
        config.pool_id = None

        provider.create_pool = Mock(side_effect=Exception("Pool creation error"))

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch):
            result = provider.create_ladder(config, auto_create_pool=True, skip_approvals=False)

        assert result.success is False
        assert "Pool creation failed" in result.error

    def test_create_ladder_skip_approvals_checks_state(self, provider, config):
        """skip_approvals=True checks approval state and fails if not approved."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.check_approvals = Mock(return_value={
            'erc20_to_permit2': {'approved': False, 'allowance': 0},
            'permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
            'base_erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'base_permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=True)

        assert result.success is False
        assert "Quote ERC20 not approved" in result.error

    def test_create_ladder_skip_approvals_expired_permit2(self, provider, config):
        """skip_approvals=True with expired Permit2 returns error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.check_approvals = Mock(return_value={
            'erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'permit2_to_position_manager': {'approved': False, 'amount': 10**30, 'expiration': int(time.time()) - 3600, 'expired': True},
            'base_erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'base_permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=True)

        assert result.success is False
        assert "EXPIRED" in result.error

    def test_create_ladder_skip_approvals_base_erc20_not_approved(self, provider, config):
        """skip_approvals=True with base ERC20 not approved returns error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.check_approvals = Mock(return_value={
            'erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
            'base_erc20_to_permit2': {'approved': False, 'allowance': 0},
            'base_permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=True)

        assert result.success is False
        assert "Base ERC20 not approved" in result.error

    def test_create_ladder_skip_approvals_base_permit2_not_approved(self, provider, config):
        """skip_approvals=True with base Permit2 not approved returns error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.check_approvals = Mock(return_value={
            'erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
            'base_erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'base_permit2_to_position_manager': {'approved': False, 'amount': 0, 'expiration': 0, 'expired': True},
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=True)

        assert result.success is False
        assert "Base Permit2" in result.error

    def test_create_ladder_skip_approvals_base_permit2_expired(self, provider, config):
        """skip_approvals=True with base Permit2 expired returns EXPIRED error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.check_approvals = Mock(return_value={
            'erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
            'base_erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'base_permit2_to_position_manager': {'approved': False, 'amount': 10**30, 'expiration': int(time.time()) - 3600, 'expired': True},
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=True)

        assert result.success is False
        assert "Base Permit2 allowance EXPIRED" in result.error

    def test_create_ladder_inline_approvals_fail(self, provider, config):
        """Inline approval failure returns error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.approve_tokens_for_ladder = Mock(return_value={
            'success': False,
            'error': 'ERC20 approve reverted',
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is False
        assert "Token approval failed" in result.error

    def test_create_ladder_tx_success(self, provider, config):
        """Full create_ladder TX success parses token IDs and returns result."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        # Mock receipt with Transfer events
        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 500_000,
            'logs': [],
        })

        provider.position_manager.contract.events.Transfer = Mock(
            return_value=Mock(
                process_receipt=Mock(return_value=[
                    {'args': {'from': ZERO_ADDRESS, 'tokenId': 201}},
                    {'args': {'from': ZERO_ADDRESS, 'tokenId': 202}},
                ])
            )
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is True
        assert result.token_ids == [201, 202]
        assert result.gas_used == 500_000

    def test_create_ladder_tx_reverted(self, provider, config):
        """Transaction reverted → success=False with error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 0,
            'gasUsed': 500_000,
            'logs': [],
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is False
        assert "reverted" in result.error.lower()

    def test_create_ladder_execute_exception(self, provider, config):
        """Exception during execute_modify_liquidities → error result."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.position_manager.execute_modify_liquidities = Mock(
            side_effect=Exception("execution failed")
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is False
        assert "execution failed" in result.error

    def test_create_ladder_balance_validation_fails(self, provider, config):
        """Balance validation failure → error result."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.validate_balances = Mock(return_value=(False, "Insufficient balance"))

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is False
        assert "Insufficient balance" in result.error

    def test_create_ladder_with_pool_id_matching(self, provider, config):
        """Pre-loaded pool_id matches computed → proceeds normally."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = b'\x01' * 32  # Same as computed

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 500_000,
            'logs': [],
        })
        provider.position_manager.contract.events.Transfer = Mock(
            return_value=Mock(process_receipt=Mock(return_value=[]))
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is True

    def test_create_ladder_with_pool_id_mismatch_uncorrectable(self, provider, config):
        """Pre-loaded pool_id doesn't match and can't auto-correct → error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = b'\xff' * 32  # Different from computed (b'\x01' * 32)

        # Auto-correction lookup: pool state returns fee that won't match
        provider.pool_manager.get_pool_state_by_id = Mock(
            return_value=_make_pool_state(initialized=True, lp_fee=5000)
        )

        # _compute_pool_id never returns the target pool_id
        provider.pool_manager._compute_pool_id = Mock(return_value=b'\x01' * 32)

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005), \
             patch.object(PoolKey, 'from_tokens', return_value=mock_pool_key):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is False
        assert "Pool ID mismatch" in result.error

    def test_create_ladder_tick_alignment(self, provider, config):
        """Ticks not aligned to tick_spacing get re-aligned."""
        # Positions with misaligned ticks
        positions = [
            _make_position(0, tick_lower=-201, tick_upper=-99),  # Not aligned to 60
        ]
        _, mock_pool_key, mock_batch = self._setup_create_ladder(
            provider, config, positions=positions
        )
        config.pool_id = None

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 400_000,
            'logs': [],
        })
        provider.position_manager.contract.events.Transfer = Mock(
            return_value=Mock(process_receipt=Mock(return_value=[]))
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is True
        # build_mint_action should have been called with aligned ticks
        mint_call = provider.position_manager.build_mint_action.call_args
        aligned_lower = mint_call.kwargs.get('tick_lower', mint_call[1].get('tick_lower'))
        aligned_upper = mint_call.kwargs.get('tick_upper', mint_call[1].get('tick_upper'))
        assert aligned_lower % 60 == 0
        assert aligned_upper % 60 == 0

    def test_create_ladder_tick_lower_ge_tick_upper_error(self, provider, config):
        """tick_lower >= tick_upper after alignment → error result."""
        # Position where after alignment tick_lower >= tick_upper
        positions = [
            _make_position(0, tick_lower=60, tick_upper=60),  # Same tick
        ]
        _, mock_pool_key, mock_batch = self._setup_create_ladder(
            provider, config, positions=positions
        )
        config.pool_id = None

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is False
        assert "Invalid tick range" in result.error

    def test_create_ladder_position_above_tick(self, provider, config):
        """Position above current tick needs token0."""
        # current_tick=-150, position ticks [-100, -50] → above current tick
        positions = [
            _make_position(0, tick_lower=-120, tick_upper=-60),
        ]
        _, mock_pool_key, mock_batch = self._setup_create_ladder(
            provider, config, positions=positions
        )
        config.pool_id = None

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 400_000,
            'logs': [],
        })
        provider.position_manager.contract.events.Transfer = Mock(
            return_value=Mock(process_receipt=Mock(return_value=[]))
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is True

    def test_create_ladder_position_in_range(self, provider, config):
        """Position spanning current tick uses both tokens."""
        # current_tick=-150, position ticks [-180, -120] → in range
        positions = [
            _make_position(0, tick_lower=-180, tick_upper=-120),
        ]
        _, mock_pool_key, mock_batch = self._setup_create_ladder(
            provider, config, positions=positions
        )
        config.pool_id = None

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 400_000,
            'logs': [],
        })
        provider.position_manager.contract.events.Transfer = Mock(
            return_value=Mock(process_receipt=Mock(return_value=[]))
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is True

    def test_create_ladder_token_id_parse_error(self, provider, config):
        """Transfer event parsing error doesn't cause failure."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 500_000,
            'logs': [],
        })

        provider.position_manager.contract.events.Transfer = Mock(
            return_value=Mock(
                process_receipt=Mock(side_effect=Exception("parse error"))
            )
        )

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=False)

        assert result.success is True
        assert result.token_ids == []  # Could not parse, but TX succeeded

    def test_create_ladder_quote_permit2_not_approved(self, provider, config):
        """skip_approvals=True with quote Permit2 not approved (not expired) returns error."""
        positions, mock_pool_key, mock_batch = self._setup_create_ladder(provider, config)
        config.pool_id = None

        provider.check_approvals = Mock(return_value={
            'erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'permit2_to_position_manager': {'approved': False, 'amount': 0, 'expiration': 0, 'expired': False},
            'base_erc20_to_permit2': {'approved': True, 'allowance': 10**30},
            'base_permit2_to_position_manager': {'approved': True, 'amount': 10**30, 'expiration': int(time.time()) + 86400, 'expired': False},
        })

        with patch('src.v4_liquidity_provider.BatchRPC', return_value=mock_batch), \
             patch('src.v4_liquidity_provider.compute_decimal_tick_offset', return_value=0), \
             patch('src.v4_liquidity_provider.price_to_tick', return_value=-150), \
             patch('src.v4_liquidity_provider.tick_to_price', return_value=0.005):
            result = provider.create_ladder(config, skip_approvals=True)

        assert result.success is False
        assert "Quote Permit2 not approved" in result.error


# ============================================================
# TestClosePositions — success, reverted TX, edge cases
# ============================================================

class TestClosePositions:
    """Tests for close_positions (lines 1812-1879)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    def test_close_positions_no_account(self, provider):
        """close_positions without account raises ValueError."""
        provider.account = None

        with pytest.raises(ValueError, match="Account not configured"):
            provider.close_positions(
                token_ids=[1, 2],
                currency0=TOKEN_VOLATILE,
                currency1=TOKEN_STABLE,
            )

    def test_close_positions_no_currencies(self, provider):
        """close_positions without currency0/currency1 raises ValueError."""
        with pytest.raises(ValueError, match="currency0 and currency1 are REQUIRED"):
            provider.close_positions(token_ids=[1], currency0=None, currency1=None)

    def test_close_positions_no_currency0(self, provider):
        """close_positions without currency0 raises ValueError."""
        with pytest.raises(ValueError, match="currency0 and currency1 are REQUIRED"):
            provider.close_positions(token_ids=[1], currency0=None, currency1=TOKEN_STABLE)

    def test_close_positions_success(self, provider):
        """Successful close_positions returns (hash, True, gas_used)."""
        mock_position = Mock()
        mock_position.liquidity = 1000000
        mock_position.tick_lower = -100
        mock_position.tick_upper = 100
        provider.position_manager.get_position = Mock(return_value=mock_position)

        provider.position_manager.build_batch_close_payload = Mock(return_value=b'\x00' * 64)
        provider.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
        )

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 200_000,
        })

        tx_hash, success, gas_used = provider.close_positions(
            token_ids=[101, 102],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
        )

        assert success is True
        assert gas_used == 200_000
        assert tx_hash is not None

    def test_close_positions_reverted(self, provider):
        """Reverted TX returns (hash, False, gas_used)."""
        mock_position = Mock()
        mock_position.liquidity = 1000000
        mock_position.tick_lower = -100
        mock_position.tick_upper = 100
        provider.position_manager.get_position = Mock(return_value=mock_position)

        provider.position_manager.build_batch_close_payload = Mock(return_value=b'\x00' * 64)
        provider.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
        )

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 0,
            'gasUsed': 21_000,
        })

        tx_hash, success, gas_used = provider.close_positions(
            token_ids=[101],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
        )

        assert success is False
        assert gas_used == 21_000

    def test_close_positions_zero_liquidity_skipped(self, provider):
        """Positions with zero liquidity are skipped."""
        mock_position = Mock()
        mock_position.liquidity = 0
        mock_position.tick_lower = -100
        mock_position.tick_upper = 100
        provider.position_manager.get_position = Mock(return_value=mock_position)

        tx_hash, success, gas_used = provider.close_positions(
            token_ids=[101, 102],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
        )

        assert tx_hash is None
        assert success is True  # nothing to close is not an error
        assert gas_used is None

    def test_close_positions_position_error_skipped(self, provider):
        """Positions that raise errors are skipped; empty result is success."""
        provider.position_manager.get_position = Mock(
            side_effect=Exception("Position not found")
        )

        tx_hash, success, gas_used = provider.close_positions(
            token_ids=[101],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
        )

        assert tx_hash is None
        assert success is True  # nothing to close is not an error
        assert gas_used is None

    def test_close_positions_address_sorting(self, provider):
        """Addresses are sorted so lower address is currency0."""
        mock_position = Mock()
        mock_position.liquidity = 1000000
        mock_position.tick_lower = -100
        mock_position.tick_upper = 100
        provider.position_manager.get_position = Mock(return_value=mock_position)

        provider.position_manager.build_batch_close_payload = Mock(return_value=b'\x00' * 64)
        provider.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
        )

        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 200_000,
        })

        # Pass addresses in wrong order (higher first)
        provider.close_positions(
            token_ids=[101],
            currency0=TOKEN_STABLE,   # 0x9999... (higher)
            currency1=TOKEN_VOLATILE,  # 0x1111... (lower)
        )

        # build_batch_close_payload should receive sorted addresses
        call_args = provider.position_manager.build_batch_close_payload.call_args
        positions_arg = call_args.kwargs.get('positions', call_args[1].get('positions'))

        # The lower address should be currency0 in the position dict
        addr0 = positions_arg[0]['currency0']
        addr1 = positions_arg[0]['currency1']
        assert int(addr0, 16) < int(addr1, 16)

    def test_close_positions_default_recipient(self, provider):
        """Default recipient is account address."""
        mock_position = Mock()
        mock_position.liquidity = 1000000
        mock_position.tick_lower = -100
        mock_position.tick_upper = 100
        provider.position_manager.get_position = Mock(return_value=mock_position)

        provider.position_manager.build_batch_close_payload = Mock(return_value=b'\x00' * 64)
        provider.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
        )
        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1, 'gasUsed': 200_000
        })

        provider.close_positions(
            token_ids=[101],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
        )

        call_args = provider.position_manager.build_batch_close_payload.call_args
        recipient_arg = call_args.kwargs.get('recipient', call_args[1].get('recipient'))
        assert recipient_arg == ACCOUNT_ADDRESS

    def test_close_positions_custom_recipient(self, provider):
        """Custom recipient is passed through."""
        custom_recipient = "0xDEADBEEFDEADBEEFDEADBEEFDEADBEEFDEADBEEF"

        mock_position = Mock()
        mock_position.liquidity = 1000000
        mock_position.tick_lower = -100
        mock_position.tick_upper = 100
        provider.position_manager.get_position = Mock(return_value=mock_position)

        provider.position_manager.build_batch_close_payload = Mock(return_value=b'\x00' * 64)
        provider.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
        )
        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1, 'gasUsed': 200_000
        })

        provider.close_positions(
            token_ids=[101],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
            recipient=custom_recipient,
        )

        call_args = provider.position_manager.build_batch_close_payload.call_args
        recipient_arg = call_args.kwargs.get('recipient', call_args[1].get('recipient'))
        assert recipient_arg == custom_recipient

    def test_close_positions_mixed_liquidity(self, provider):
        """Mix of positions with and without liquidity."""
        mock_pos_with_liq = Mock()
        mock_pos_with_liq.liquidity = 1000000
        mock_pos_with_liq.tick_lower = -100
        mock_pos_with_liq.tick_upper = 100

        mock_pos_zero_liq = Mock()
        mock_pos_zero_liq.liquidity = 0
        mock_pos_zero_liq.tick_lower = -200
        mock_pos_zero_liq.tick_upper = -100

        provider.position_manager.get_position = Mock(
            side_effect=[mock_pos_with_liq, mock_pos_zero_liq, mock_pos_with_liq]
        )

        provider.position_manager.build_batch_close_payload = Mock(return_value=b'\x00' * 64)
        provider.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
        )
        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1, 'gasUsed': 200_000
        })

        tx_hash, success, gas_used = provider.close_positions(
            token_ids=[101, 102, 103],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
        )

        assert success is True
        # Only 2 positions should be in the batch (101 and 103, 102 has zero liquidity)
        call_args = provider.position_manager.build_batch_close_payload.call_args
        positions_arg = call_args.kwargs.get('positions', call_args[1].get('positions'))
        assert len(positions_arg) == 2

    def test_close_positions_burn_flag(self, provider):
        """burn=True is passed through to build_batch_close_payload."""
        mock_position = Mock()
        mock_position.liquidity = 1000000
        mock_position.tick_lower = -100
        mock_position.tick_upper = 100
        provider.position_manager.get_position = Mock(return_value=mock_position)

        provider.position_manager.build_batch_close_payload = Mock(return_value=b'\x00' * 64)
        provider.position_manager.execute_modify_liquidities = Mock(
            return_value=(FAKE_TX_HASH.hex(), [])
        )
        provider.w3.eth.get_transaction_receipt = Mock(return_value={
            'status': 1, 'gasUsed': 200_000
        })

        provider.close_positions(
            token_ids=[101],
            currency0=TOKEN_VOLATILE,
            currency1=TOKEN_STABLE,
            burn=True,
        )

        call_args = provider.position_manager.build_batch_close_payload.call_args
        burn_arg = call_args.kwargs.get('burn', call_args[1].get('burn'))
        assert burn_arg is True


# ============================================================
# TestGetQuoteToken — _get_quote_token internal method
# ============================================================

class TestGetQuoteToken:
    """Tests for _get_quote_token internal method (lines 1128-1149)."""

    @pytest.fixture
    def provider(self):
        return _build_provider()

    def test_token0_is_stablecoin(self, provider):
        """When token0 is a known stablecoin, returns it with correct decimals."""
        usdt_bsc = "0x55d398326f99059fF775485246999027B3197955"
        config = _make_config(token0=usdt_bsc, token1=TOKEN_VOLATILE)

        with patch('config.STABLECOINS', {usdt_bsc.lower(): 18}):
            token, decimals = provider._get_quote_token(config)

        assert token == usdt_bsc
        assert decimals == 18

    def test_token1_is_stablecoin(self, provider):
        """When token1 is a known stablecoin, returns it with correct decimals."""
        usdc_base = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        config = _make_config(token0=TOKEN_VOLATILE, token1=usdc_base)

        with patch('config.STABLECOINS', {usdc_base.lower(): 6}):
            token, decimals = provider._get_quote_token(config)

        assert token == usdc_base
        assert decimals == 6

    def test_no_stablecoin_defaults_to_token1(self, provider):
        """When neither token is a known stablecoin, defaults to token1."""
        config = _make_config(
            token0="0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            token1="0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            token1_decimals=8,
        )

        with patch('config.STABLECOINS', {}):
            token, decimals = provider._get_quote_token(config)

        assert token == config.token1
        assert decimals == 8
