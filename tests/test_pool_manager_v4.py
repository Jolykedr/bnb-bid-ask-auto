"""
Tests for V4 PoolKey and V4PoolManager.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from web3 import Web3

from src.contracts.v4.pool_manager import (
    PoolKey,
    V4PoolState,
    V4PoolManager,
)
from src.contracts.v4.constants import V4Protocol


# ============================================================
# PoolKey Tests
# ============================================================

class TestPoolKey:
    """Tests for PoolKey dataclass."""

    def test_to_tuple_returns_5_elements(self):
        """to_tuple возвращает кортеж из 5 элементов."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )
        t = key.to_tuple()

        assert len(t) == 5
        assert t[2] == 3000
        assert t[3] == 60

    def test_default_hooks_is_zero_address(self):
        """hooks по умолчанию - нулевой адрес."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )
        assert key.hooks == "0x0000000000000000000000000000000000000000"

    def test_get_pool_id_returns_bytes32(self):
        """get_pool_id возвращает 32 байта."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )
        pool_id = key.get_pool_id()

        assert isinstance(pool_id, bytes)
        assert len(pool_id) == 32

    def test_get_pool_id_deterministic(self):
        """Одинаковые PoolKey дают одинаковый pool_id."""
        key1 = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )
        key2 = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        assert key1.get_pool_id() == key2.get_pool_id()

    def test_get_pool_id_different_for_different_fees(self):
        """Разные fee -> разные pool_id."""
        key1 = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )
        key2 = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=10000,
            tick_spacing=200,
        )

        assert key1.get_pool_id() != key2.get_pool_id()

    def test_from_tokens_sorts_addresses(self):
        """from_tokens автоматически сортирует адреса (меньший = currency0)."""
        higher = "0x9999999999999999999999999999999999999999"
        lower = "0x1111111111111111111111111111111111111111"

        # Передаём в обратном порядке
        key = PoolKey.from_tokens(
            token0=higher,
            token1=lower,
            fee=3000,
            tick_spacing=60,
        )

        # currency0 должен быть меньший адрес
        assert int(key.currency0, 16) < int(key.currency1, 16)

    def test_from_tokens_already_sorted(self):
        """from_tokens с уже отсортированными адресами."""
        lower = "0x1111111111111111111111111111111111111111"
        higher = "0x9999999999999999999999999999999999999999"

        key = PoolKey.from_tokens(
            token0=lower,
            token1=higher,
            fee=3000,
            tick_spacing=60,
        )

        assert int(key.currency0, 16) < int(key.currency1, 16)

    def test_from_tokens_auto_tick_spacing(self):
        """from_tokens вычисляет tick_spacing из fee если не передан."""
        key = PoolKey.from_tokens(
            token0="0x1111111111111111111111111111111111111111",
            token1="0x9999999999999999999999999999999999999999",
            fee=3000,
        )

        # 3000 / 10000 = 0.3% -> 0.3 * 200 = 60
        assert key.tick_spacing == 60

    def test_from_tokens_custom_tick_spacing(self):
        """from_tokens принимает явный tick_spacing."""
        key = PoolKey.from_tokens(
            token0="0x1111111111111111111111111111111111111111",
            token1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=100,
        )

        assert key.tick_spacing == 100

    def test_from_tokens_with_hooks(self):
        """from_tokens с кастомным hooks адресом."""
        hooks_addr = "0xAAAA000000000000000000000000000000000001"
        key = PoolKey.from_tokens(
            token0="0x1111111111111111111111111111111111111111",
            token1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
            hooks=hooks_addr,
        )

        assert hooks_addr.lower() in key.hooks.lower()


# ============================================================
# V4PoolState Tests
# ============================================================

class TestV4PoolState:
    """Tests for V4PoolState dataclass."""

    def test_fields(self):
        """V4PoolState содержит все поля."""
        state = V4PoolState(
            pool_id=b'\x01' * 32,
            sqrt_price_x96=79228162514264337593543950336,
            tick=0,
            liquidity=1000000,
            protocol_fee=0,
            lp_fee=3000,
            initialized=True,
        )
        assert state.initialized is True
        assert state.lp_fee == 3000
        assert state.tick == 0

    def test_uninitialized_pool(self):
        """Неинициализированный пул."""
        state = V4PoolState(
            pool_id=b'\x00' * 32,
            sqrt_price_x96=0,
            tick=0,
            liquidity=0,
            protocol_fee=0,
            lp_fee=0,
            initialized=False,
        )
        assert state.initialized is False
        assert state.sqrt_price_x96 == 0


# ============================================================
# V4PoolManager Tests
# ============================================================

class TestV4PoolManager:
    """Tests for V4PoolManager."""

    @pytest.fixture
    def mock_w3(self):
        """Mock Web3."""
        w3 = Mock(spec=Web3)
        w3.eth = Mock()
        w3.eth.contract = Mock()
        return w3

    @pytest.fixture
    def pool_manager(self, mock_w3):
        """Создание V4PoolManager с моками."""
        with patch.object(V4PoolManager, '__init__', lambda self, *a, **kw: None):
            pm = V4PoolManager.__new__(V4PoolManager)
            pm.w3 = mock_w3
            pm.protocol = V4Protocol.PANCAKESWAP
            pm.chain_id = 56
            pm.pool_manager_address = "0xPoolManager"
            pm.state_view_contract = None
            pm.state_view_address = None

            mock_contract = Mock()
            mock_contract.functions = Mock()

            # getSlot0
            mock_contract.functions.getSlot0 = Mock(return_value=Mock(
                call=Mock(return_value=(
                    79228162514264337593543950336,  # sqrtPriceX96
                    0,    # tick
                    0,    # protocolFee
                    3000, # lpFee
                ))
            ))

            # getLiquidity
            mock_contract.functions.getLiquidity = Mock(return_value=Mock(
                call=Mock(return_value=1_000_000)
            ))

            # initialize
            mock_contract.functions.initialize = Mock(return_value=Mock(
                _encode_transaction_data=Mock(return_value=b'init_data')
            ))

            pm.contract = mock_contract
            return pm

    def test_get_pool_state_initialized(self, pool_manager):
        """get_pool_state для инициализированного пула."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        state = pool_manager.get_pool_state(key)

        assert isinstance(state, V4PoolState)
        assert state.initialized is True
        assert state.sqrt_price_x96 == 79228162514264337593543950336
        assert state.lp_fee == 3000
        assert state.liquidity == 1_000_000

    def test_get_pool_state_uninitialized(self, pool_manager):
        """get_pool_state для несуществующего пула возвращает uninitialized."""
        pool_manager.contract.functions.getSlot0.return_value.call.side_effect = Exception("not found")

        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        state = pool_manager.get_pool_state(key)

        assert state.initialized is False
        assert state.sqrt_price_x96 == 0

    def test_is_pool_initialized_true(self, pool_manager):
        """is_pool_initialized возвращает True для инициализированного пула."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        assert pool_manager.is_pool_initialized(key) is True

    def test_is_pool_initialized_false(self, pool_manager):
        """is_pool_initialized возвращает False для несуществующего пула."""
        pool_manager.contract.functions.getSlot0.return_value.call.side_effect = Exception("not found")

        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        assert pool_manager.is_pool_initialized(key) is False

    def test_encode_initialize_returns_bytes(self, pool_manager):
        """encode_initialize возвращает bytes."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        result = pool_manager.encode_initialize(key, sqrt_price_x96=2**96)
        assert isinstance(result, bytes)

    def test_price_to_sqrt_price_x96_equal_decimals(self, pool_manager):
        """price_to_sqrt_price_x96 с равными decimals."""
        # price=1 -> sqrt(1) * 2^96 = 2^96
        result = pool_manager.price_to_sqrt_price_x96(1.0, 18, 18)
        assert result == 2**96

    def test_price_to_sqrt_price_x96_different_decimals(self, pool_manager):
        """price_to_sqrt_price_x96 с разными decimals."""
        # Adjusted: price * 10^(token0_dec - token1_dec)
        result = pool_manager.price_to_sqrt_price_x96(1.0, 18, 6)
        # 1.0 * 10^(18-6) = 10^12, sqrt(10^12) * 2^96
        import math
        expected = int(math.sqrt(10**12) * (2**96))
        assert result == expected

    def test_sqrt_price_x96_to_price_roundtrip(self, pool_manager):
        """Roundtrip: price -> sqrtPriceX96 -> price."""
        price = 1500.0
        sqrt_price_x96 = pool_manager.price_to_sqrt_price_x96(price, 18, 18)
        recovered = pool_manager.sqrt_price_x96_to_price(sqrt_price_x96, 18, 18)

        assert abs(recovered - price) / price < 0.0001  # < 0.01%

    def test_get_current_price_initialized(self, pool_manager):
        """get_current_price для инициализированного пула."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        price = pool_manager.get_current_price(key)
        assert price is not None
        assert price > 0

    def test_get_current_price_uninitialized(self, pool_manager):
        """get_current_price для неинициализированного пула возвращает None."""
        pool_manager.contract.functions.getSlot0.return_value.call.side_effect = Exception("not found")

        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        price = pool_manager.get_current_price(key)
        assert price is None

    def test_get_exact_pool_fee(self, pool_manager):
        """get_exact_pool_fee возвращает LP fee из пула."""
        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        fee = pool_manager.get_exact_pool_fee(key)
        assert fee == 3000

    def test_get_exact_pool_fee_uninitialized(self, pool_manager):
        """get_exact_pool_fee возвращает None для неинициализированного пула."""
        pool_manager.contract.functions.getSlot0.return_value.call.side_effect = Exception("not found")

        key = PoolKey(
            currency0="0x1111111111111111111111111111111111111111",
            currency1="0x9999999999999999999999999999999999999999",
            fee=3000,
            tick_spacing=60,
        )

        fee = pool_manager.get_exact_pool_fee(key)
        assert fee is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
