"""
Tests for V3 UniswapV3PositionManager.
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch
from web3 import Web3

from src.contracts.position_manager import (
    UniswapV3PositionManager,
    MintParams,
    MintResult,
)


# ============================================================
# MintParams Tests
# ============================================================

class TestMintParams:
    """Tests for MintParams dataclass."""

    def test_default_values(self):
        """Значения по умолчанию."""
        params = MintParams(
            token0="0xToken0",
            token1="0xToken1",
            fee=2500,
            tick_lower=-100,
            tick_upper=100,
            amount0_desired=1000,
            amount1_desired=2000,
        )
        assert params.amount0_min == 0
        assert params.amount1_min == 0
        assert params.recipient is None
        assert params.deadline is None

    def test_to_tuple_correct_order(self):
        """to_tuple возвращает корректный порядок полей."""
        params = MintParams(
            token0="0x1111111111111111111111111111111111111111",
            token1="0x2222222222222222222222222222222222222222",
            fee=3000,
            tick_lower=-60,
            tick_upper=60,
            amount0_desired=100,
            amount1_desired=200,
            amount0_min=10,
            amount1_min=20,
        )

        deadline = int(time.time()) + 3600
        result = params.to_tuple(
            recipient="0x3333333333333333333333333333333333333333",
            deadline=deadline
        )

        assert len(result) == 11
        assert result[2] == 3000  # fee
        assert result[3] == -60   # tick_lower
        assert result[4] == 60    # tick_upper
        assert result[5] == 100   # amount0_desired
        assert result[6] == 200   # amount1_desired
        assert result[7] == 10    # amount0_min
        assert result[8] == 20    # amount1_min
        assert result[10] == deadline

    def test_to_tuple_default_deadline(self):
        """to_tuple с deadline=None генерирует deadline +1 час."""
        params = MintParams(
            token0="0x1111111111111111111111111111111111111111",
            token1="0x2222222222222222222222222222222222222222",
            fee=2500,
            tick_lower=-50,
            tick_upper=50,
            amount0_desired=0,
            amount1_desired=1000,
        )

        before = int(time.time()) + 3600
        result = params.to_tuple(
            recipient="0x3333333333333333333333333333333333333333"
        )
        after = int(time.time()) + 3600

        assert before <= result[10] <= after


# ============================================================
# MintResult Tests
# ============================================================

class TestMintResult:
    """Tests for MintResult dataclass."""

    def test_fields(self):
        """MintResult содержит все поля."""
        result = MintResult(
            token_id=12345,
            liquidity=1000000,
            amount0=100,
            amount1=200,
            tx_hash="0xabcdef"
        )
        assert result.token_id == 12345
        assert result.liquidity == 1000000
        assert result.amount0 == 100
        assert result.amount1 == 200
        assert result.tx_hash == "0xabcdef"


# ============================================================
# UniswapV3PositionManager Tests
# ============================================================

class TestUniswapV3PositionManager:
    """Tests for UniswapV3PositionManager."""

    @pytest.fixture
    def mock_w3(self):
        """Mock Web3."""
        w3 = Mock(spec=Web3)
        w3.eth = Mock()
        w3.eth.gas_price = 5_000_000_000
        w3.eth.get_transaction_count = Mock(return_value=0)
        w3.eth.send_raw_transaction = Mock(return_value=b'\x12\x34' * 16)
        w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1, 'gasUsed': 300_000, 'logs': []
        })
        w3.eth.contract = Mock()
        return w3

    @pytest.fixture
    def mock_account(self):
        """Mock account."""
        account = Mock()
        account.address = "0x1234567890123456789012345678901234567890"
        account.sign_transaction = Mock(return_value=Mock(raw_transaction=b'signed'))
        return account

    @pytest.fixture
    def pm(self, mock_w3, mock_account):
        """Создание PositionManager с моками."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = mock_w3
            pm.account = mock_account
            pm.position_manager_address = "0xPositionManager"

            mock_contract = Mock()
            mock_contract.functions = Mock()
            mock_contract.encode_abi = Mock(return_value=b'encoded')

            # positions function
            mock_contract.functions.positions = Mock(return_value=Mock(
                call=Mock(return_value=(
                    0, "0x0", "0xToken0", "0xToken1",
                    2500, -100, -50, 1_000_000,
                    0, 0, 500, 300
                ))
            ))

            # decreaseLiquidity
            mock_contract.functions.decreaseLiquidity = Mock(return_value=Mock(
                _encode_transaction_data=Mock(return_value=b'decrease_data')
            ))

            # collect
            mock_contract.functions.collect = Mock(return_value=Mock(
                _encode_transaction_data=Mock(return_value=b'collect_data')
            ))

            # burn
            mock_contract.functions.burn = Mock(return_value=Mock(
                _encode_transaction_data=Mock(return_value=b'burn_data')
            ))

            pm.contract = mock_contract
            pm.nonce_manager = None
            return pm

    def test_get_position_returns_dict(self, pm):
        """get_position возвращает словарь с 12 полями."""
        result = pm.get_position(12345)

        assert isinstance(result, dict)
        assert result['liquidity'] == 1_000_000
        assert result['fee'] == 2500
        assert result['tick_lower'] == -100
        assert result['tick_upper'] == -50
        assert result['token0'] == "0xToken0"
        assert result['token1'] == "0xToken1"
        assert result['tokens_owed0'] == 500
        assert result['tokens_owed1'] == 300

    def test_get_position_all_fields_present(self, pm):
        """get_position содержит все 12 полей."""
        result = pm.get_position(1)

        expected_keys = [
            'nonce', 'operator', 'token0', 'token1', 'fee',
            'tick_lower', 'tick_upper', 'liquidity',
            'fee_growth_inside0_last_x128', 'fee_growth_inside1_last_x128',
            'tokens_owed0', 'tokens_owed1'
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_encode_mint_returns_bytes(self, pm):
        """encode_mint возвращает bytes."""
        params = MintParams(
            token0="0x1111111111111111111111111111111111111111",
            token1="0x2222222222222222222222222222222222222222",
            fee=2500,
            tick_lower=-100,
            tick_upper=-50,
            amount0_desired=0,
            amount1_desired=1000,
        )

        result = pm.encode_mint(params, recipient="0x3333333333333333333333333333333333333333")
        assert isinstance(result, bytes)

    def test_encode_decrease_liquidity_returns_bytes(self, pm):
        """encode_decrease_liquidity возвращает bytes."""
        result = pm.encode_decrease_liquidity(
            token_id=12345,
            liquidity=1_000_000,
        )
        assert isinstance(result, bytes)

    def test_encode_collect_returns_bytes(self, pm):
        """encode_collect возвращает bytes."""
        result = pm.encode_collect(
            token_id=12345,
            recipient="0x3333333333333333333333333333333333333333",
        )
        assert isinstance(result, bytes)

    def test_encode_burn_returns_bytes(self, pm):
        """encode_burn возвращает bytes."""
        result = pm.encode_burn(token_id=12345)
        assert isinstance(result, bytes)

    def test_check_and_approve_already_approved(self, pm):
        """check_and_approve пропускает если allowance достаточный."""
        mock_token = Mock()
        mock_token.functions.allowance.return_value.call.return_value = 10**30
        pm._get_token_contract = Mock(return_value=mock_token)

        result = pm.check_and_approve("0xToken", amount=1000)

        assert result is None
        mock_token.functions.approve.assert_not_called()

    def test_check_and_approve_sends_tx(self, pm):
        """check_and_approve отправляет approve если allowance недостаточный."""
        mock_token = Mock()
        mock_token.functions.allowance.return_value.call.return_value = 0
        mock_token.functions.approve.return_value.build_transaction.return_value = {}
        pm._get_token_contract = Mock(return_value=mock_token)

        result = pm.check_and_approve("0xToken", amount=1000)

        assert result is not None
        mock_token.functions.approve.assert_called_once()

    def test_build_mint_params_from_distribution(self, pm):
        """build_mint_params_from_distribution создаёт MintParams из BidAskPosition."""
        from src.math.distribution import BidAskPosition

        positions = [
            BidAskPosition(index=0, tick_lower=-100, tick_upper=-50,
                           price_lower=9.0, price_upper=10.0,
                           usd_amount=100.0, percentage=50, liquidity=1000),
            BidAskPosition(index=1, tick_lower=-150, tick_upper=-100,
                           price_lower=8.0, price_upper=9.0,
                           usd_amount=200.0, percentage=50, liquidity=2000),
        ]

        result = pm.build_mint_params_from_distribution(
            positions=positions,
            token0="0xToken0",
            token1="0xToken1",
            fee=2500,
        )

        assert len(result) == 2
        assert all(isinstance(p, MintParams) for p in result)
        assert result[0].tick_lower == -100
        assert result[0].tick_upper == -50
        assert result[0].amount0_desired == 0
        assert result[0].amount1_desired == int(100.0 * 10**18)

    def test_get_owner_of_existing(self, pm):
        """get_owner_of возвращает адрес владельца."""
        mock_erc721 = Mock()
        mock_erc721.functions.ownerOf.return_value.call.return_value = "0xOwner"
        pm.w3.eth.contract.return_value = mock_erc721

        owner = pm.get_owner_of(123)
        assert owner == "0xOwner"

    def test_get_owner_of_burned(self, pm):
        """get_owner_of возвращает None для сожжённой позиции."""
        mock_erc721 = Mock()
        mock_erc721.functions.ownerOf.return_value.call.side_effect = Exception("not found")
        pm.w3.eth.contract.return_value = mock_erc721

        owner = pm.get_owner_of(999)
        assert owner is None

    def test_is_position_owned_by_true(self, pm):
        """is_position_owned_by возвращает True для владельца."""
        pm.get_owner_of = Mock(return_value="0xMyAddress")
        assert pm.is_position_owned_by(123, "0xmyaddress") is True

    def test_is_position_owned_by_false(self, pm):
        """is_position_owned_by возвращает False для чужого адреса."""
        pm.get_owner_of = Mock(return_value="0xOtherAddress")
        assert pm.is_position_owned_by(123, "0xMyAddress") is False

    def test_is_position_owned_by_burned(self, pm):
        """is_position_owned_by возвращает False для сожжённой позиции."""
        pm.get_owner_of = Mock(return_value=None)
        assert pm.is_position_owned_by(123, "0xMyAddress") is False

    def test_get_positions_count(self, pm):
        """get_positions_count возвращает количество NFT."""
        mock_erc721 = Mock()
        mock_erc721.functions.balanceOf.return_value.call.return_value = 5
        pm.w3.eth.contract.return_value = mock_erc721

        count = pm.get_positions_count("0x7777777777777777777777777777777777777777")
        assert count == 5

    def test_get_positions_count_error(self, pm):
        """get_positions_count возвращает 0 при ошибке."""
        mock_erc721 = Mock()
        mock_erc721.functions.balanceOf.return_value.call.side_effect = Exception("error")
        pm.w3.eth.contract.return_value = mock_erc721

        count = pm.get_positions_count("0x7777777777777777777777777777777777777777")
        assert count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
