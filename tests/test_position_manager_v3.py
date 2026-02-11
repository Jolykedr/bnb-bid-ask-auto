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


# ============================================================
# Additional Coverage Tests (target: 70%+)
# ============================================================

class TestInit:
    """Tests for __init__ constructor (lines 83-87)."""

    def test_init_stores_attributes(self):
        """__init__ stores w3, account, nonce_manager and creates contract."""
        mock_w3 = Mock(spec=Web3)
        mock_w3.eth = Mock()
        mock_contract = Mock()
        mock_w3.eth.contract.return_value = mock_contract

        mock_account = Mock()
        mock_account.address = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

        mock_nonce_mgr = Mock()

        addr = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        pm = UniswapV3PositionManager(
            w3=mock_w3,
            position_manager_address=addr,
            account=mock_account,
            nonce_manager=mock_nonce_mgr,
        )

        assert pm.w3 is mock_w3
        assert pm.account is mock_account
        assert pm.nonce_manager is mock_nonce_mgr
        assert pm.position_manager_address == Web3.to_checksum_address(addr)
        assert pm.contract is mock_contract
        mock_w3.eth.contract.assert_called_once()

    def test_init_no_account_no_nonce(self):
        """__init__ works with account=None and nonce_manager=None."""
        mock_w3 = Mock(spec=Web3)
        mock_w3.eth = Mock()
        mock_w3.eth.contract.return_value = Mock()

        addr = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
        pm = UniswapV3PositionManager(w3=mock_w3, position_manager_address=addr)

        assert pm.account is None
        assert pm.nonce_manager is None


class TestGetTokenContract:
    """Tests for _get_token_contract (line 94)."""

    def test_returns_erc20_contract(self):
        """_get_token_contract creates an ERC20 contract for the given address."""
        mock_w3 = Mock(spec=Web3)
        mock_w3.eth = Mock()
        mock_contract = Mock()
        mock_w3.eth.contract.return_value = mock_contract

        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = mock_w3

        token_addr = "0x1111111111111111111111111111111111111111"
        result = pm._get_token_contract(token_addr)

        assert result is mock_contract
        mock_w3.eth.contract.assert_called_once()
        call_kwargs = mock_w3.eth.contract.call_args
        assert call_kwargs[1]['address'] == Web3.to_checksum_address(token_addr)


class TestCheckAndApproveExtended:
    """Extended tests for check_and_approve nonce/receipt paths (lines 151-161)."""

    @pytest.fixture
    def pm_with_nonce(self):
        """PM with nonce_manager set."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = Mock(spec=Web3)
            pm.w3.eth = Mock()
            pm.w3.eth.gas_price = 5_000_000_000
            pm.w3.eth.send_raw_transaction = Mock(return_value=b'\xab' * 32)
            pm.w3.eth.wait_for_transaction_receipt = Mock(return_value={
                'status': 1, 'gasUsed': 50_000, 'logs': []
            })

            pm.account = Mock()
            pm.account.address = "0x1234567890123456789012345678901234567890"
            pm.account.sign_transaction = Mock(
                return_value=Mock(raw_transaction=b'signed')
            )

            pm.position_manager_address = "0xPositionManager"

            pm.nonce_manager = Mock()
            pm.nonce_manager.get_next_nonce = Mock(return_value=42)

            # Token mock: allowance=0 triggers approve
            mock_token = Mock()
            mock_token.functions.allowance.return_value.call.return_value = 0
            mock_token.functions.approve.return_value.build_transaction.return_value = {}
            pm._get_token_contract = Mock(return_value=mock_token)

            return pm

    def test_nonce_manager_confirm_on_success(self, pm_with_nonce):
        """On successful approve, nonce_manager.confirm_transaction is called."""
        pm = pm_with_nonce

        result = pm.check_and_approve("0xToken", amount=1000)

        assert result is not None
        pm.nonce_manager.get_next_nonce.assert_called_once()
        pm.nonce_manager.confirm_transaction.assert_called_once_with(42)

    def test_receipt_status_not_1_raises(self, pm_with_nonce):
        """If receipt status != 1, Exception is raised."""
        pm = pm_with_nonce
        pm.w3.eth.wait_for_transaction_receipt.return_value = {
            'status': 0, 'gasUsed': 50_000, 'logs': []
        }

        with pytest.raises(Exception, match="Approve transaction reverted"):
            pm.check_and_approve("0xToken", amount=1000)

    def test_nonce_release_on_send_failure(self, pm_with_nonce):
        """If send_raw_transaction raises, nonce_manager.release_nonce is called."""
        pm = pm_with_nonce
        pm.w3.eth.send_raw_transaction.side_effect = Exception("send failed")

        with pytest.raises(Exception, match="send failed"):
            pm.check_and_approve("0xToken", amount=1000)

        pm.nonce_manager.release_nonce.assert_called_once_with(42)
        pm.nonce_manager.confirm_transaction.assert_not_called()

    def test_nonce_release_on_receipt_reverted(self, pm_with_nonce):
        """On reverted receipt, both confirm and release are called (current behavior)."""
        pm = pm_with_nonce
        pm.w3.eth.wait_for_transaction_receipt.return_value = {
            'status': 0, 'gasUsed': 50_000, 'logs': []
        }

        with pytest.raises(Exception, match="Approve transaction reverted"):
            pm.check_and_approve("0xToken", amount=1000)

        # confirm is called first (line 151), then exception raises,
        # which triggers release in except block (line 160)
        pm.nonce_manager.confirm_transaction.assert_called_once_with(42)
        pm.nonce_manager.release_nonce.assert_called_once_with(42)

    def test_no_nonce_manager_uses_get_transaction_count(self):
        """Without nonce_manager, uses w3.eth.get_transaction_count."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = Mock(spec=Web3)
            pm.w3.eth = Mock()
            pm.w3.eth.gas_price = 5_000_000_000
            pm.w3.eth.get_transaction_count = Mock(return_value=7)
            pm.w3.eth.send_raw_transaction = Mock(return_value=b'\xab' * 32)
            pm.w3.eth.wait_for_transaction_receipt = Mock(return_value={
                'status': 1, 'gasUsed': 50_000, 'logs': []
            })

            pm.account = Mock()
            pm.account.address = "0x1234567890123456789012345678901234567890"
            pm.account.sign_transaction = Mock(
                return_value=Mock(raw_transaction=b'signed')
            )
            pm.position_manager_address = "0xPositionManager"
            pm.nonce_manager = None

            mock_token = Mock()
            mock_token.functions.allowance.return_value.call.return_value = 0
            mock_token.functions.approve.return_value.build_transaction.return_value = {}
            pm._get_token_contract = Mock(return_value=mock_token)

            result = pm.check_and_approve("0xToken", amount=1000)

            assert result is not None
            pm.w3.eth.get_transaction_count.assert_called_once_with(
                pm.account.address, 'pending'
            )


class TestParseMintEvents:
    """Tests for _parse_mint_events (lines 263-291)."""

    @pytest.fixture
    def pm(self):
        """PM fixture for event parsing tests."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.contract = Mock()
            return pm

    def test_increase_liquidity_event_found(self, pm):
        """When IncreaseLiquidity event exists, returns dict with all fields."""
        mock_event = {
            'args': {
                'tokenId': 12345,
                'liquidity': 999999,
                'amount0': 100,
                'amount1': 200,
            }
        }
        pm.contract.events.IncreaseLiquidity.return_value.process_receipt.return_value = [
            mock_event
        ]

        receipt = {'logs': []}
        result = pm._parse_mint_events(receipt)

        assert result is not None
        assert result['token_id'] == 12345
        assert result['liquidity'] == 999999
        assert result['amount0'] == 100
        assert result['amount1'] == 200

    def test_no_increase_liquidity_falls_back_to_transfer(self, pm):
        """When no IncreaseLiquidity event, falls back to Transfer from address(0)."""
        # IncreaseLiquidity returns empty list
        pm.contract.events.IncreaseLiquidity.return_value.process_receipt.return_value = []

        # Transfer event from address(0)
        mock_transfer = {
            'args': {
                'from': '0x0000000000000000000000000000000000000000',
                'to': '0x1234567890123456789012345678901234567890',
                'tokenId': 67890,
            }
        }
        pm.contract.events.Transfer.return_value.process_receipt.return_value = [
            mock_transfer
        ]

        receipt = {'logs': []}
        result = pm._parse_mint_events(receipt)

        assert result is not None
        assert result['token_id'] == 67890
        assert result['liquidity'] == 0
        assert result['amount0'] == 0
        assert result['amount1'] == 0

    def test_transfer_ignores_non_zero_from(self, pm):
        """Transfer events not from address(0) are skipped."""
        pm.contract.events.IncreaseLiquidity.return_value.process_receipt.return_value = []

        # Transfer NOT from address(0) - this is a regular transfer, not a mint
        mock_transfer = {
            'args': {
                'from': '0x9999999999999999999999999999999999999999',
                'to': '0x1234567890123456789012345678901234567890',
                'tokenId': 11111,
            }
        }
        pm.contract.events.Transfer.return_value.process_receipt.return_value = [
            mock_transfer
        ]

        receipt = {'logs': []}
        result = pm._parse_mint_events(receipt)

        assert result is None

    def test_neither_event_returns_none(self, pm):
        """When neither event is found, returns None."""
        pm.contract.events.IncreaseLiquidity.return_value.process_receipt.return_value = []
        pm.contract.events.Transfer.return_value.process_receipt.return_value = []

        receipt = {'logs': []}
        result = pm._parse_mint_events(receipt)

        assert result is None

    def test_increase_liquidity_exception_falls_back(self, pm):
        """When IncreaseLiquidity parsing raises, falls back to Transfer."""
        pm.contract.events.IncreaseLiquidity.return_value.process_receipt.side_effect = \
            Exception("ABI mismatch")

        mock_transfer = {
            'args': {
                'from': '0x0000000000000000000000000000000000000000',
                'to': '0xSomeRecipient',
                'tokenId': 55555,
            }
        }
        pm.contract.events.Transfer.return_value.process_receipt.return_value = [
            mock_transfer
        ]

        receipt = {'logs': []}
        result = pm._parse_mint_events(receipt)

        assert result is not None
        assert result['token_id'] == 55555

    def test_both_exceptions_returns_none(self, pm):
        """When both IncreaseLiquidity and Transfer raise, returns None."""
        pm.contract.events.IncreaseLiquidity.return_value.process_receipt.side_effect = \
            Exception("fail")
        pm.contract.events.Transfer.return_value.process_receipt.side_effect = \
            Exception("fail")

        receipt = {'logs': []}
        result = pm._parse_mint_events(receipt)

        assert result is None


class TestMintSingle:
    """Tests for mint_single (lines 310-361)."""

    @pytest.fixture
    def pm(self):
        """PM fixture for mint_single tests."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = Mock(spec=Web3)
            pm.w3.eth = Mock()
            pm.w3.eth.gas_price = 5_000_000_000
            pm.w3.eth.send_raw_transaction = Mock(return_value=b'\xab' * 32)
            pm.w3.eth.wait_for_transaction_receipt = Mock(return_value={
                'status': 1, 'gasUsed': 300_000, 'logs': []
            })

            pm.account = Mock()
            pm.account.address = "0x1234567890123456789012345678901234567890"
            pm.account.sign_transaction = Mock(
                return_value=Mock(raw_transaction=b'signed')
            )

            mock_contract = Mock()
            mock_contract.functions.mint.return_value.build_transaction.return_value = {}
            mock_contract.events = Mock()
            pm.contract = mock_contract

            pm.nonce_manager = Mock()
            pm.nonce_manager.get_next_nonce = Mock(return_value=10)

            return pm

    @pytest.fixture
    def mint_params(self):
        """Standard MintParams for testing."""
        return MintParams(
            token0="0x1111111111111111111111111111111111111111",
            token1="0x2222222222222222222222222222222222222222",
            fee=2500,
            tick_lower=-100,
            tick_upper=-50,
            amount0_desired=0,
            amount1_desired=1000,
        )

    def test_mint_single_success_with_events(self, pm, mint_params):
        """Successful mint returns MintResult with event data."""
        pm._parse_mint_events = Mock(return_value={
            'token_id': 777,
            'liquidity': 50000,
            'amount0': 0,
            'amount1': 1000,
        })

        result = pm.mint_single(mint_params)

        assert isinstance(result, MintResult)
        assert result.token_id == 777
        assert result.liquidity == 50000
        assert result.amount0 == 0
        assert result.amount1 == 1000
        assert result.tx_hash is not None

    def test_mint_single_nonce_confirm_on_success(self, pm, mint_params):
        """On status=1, nonce_manager.confirm_transaction is called."""
        pm._parse_mint_events = Mock(return_value=None)

        pm.mint_single(mint_params)

        pm.nonce_manager.confirm_transaction.assert_called_once_with(10)
        pm.nonce_manager.release_nonce.assert_not_called()

    def test_mint_single_nonce_release_on_revert(self, pm, mint_params):
        """On status=0, nonce_manager.release_nonce is called."""
        pm.w3.eth.wait_for_transaction_receipt.return_value = {
            'status': 0, 'gasUsed': 300_000, 'logs': []
        }
        pm._parse_mint_events = Mock(return_value=None)

        result = pm.mint_single(mint_params)

        pm.nonce_manager.release_nonce.assert_called_once_with(10)
        # confirm_transaction should NOT be called for reverted tx
        pm.nonce_manager.confirm_transaction.assert_not_called()

    def test_mint_single_fallback_result_when_no_events(self, pm, mint_params):
        """When _parse_mint_events returns None, fallback MintResult is returned."""
        pm._parse_mint_events = Mock(return_value=None)

        result = pm.mint_single(mint_params)

        assert isinstance(result, MintResult)
        assert result.token_id == 0
        assert result.liquidity == 0
        assert result.amount0 == 0
        assert result.amount1 == 0

    def test_mint_single_exception_releases_nonce(self, pm, mint_params):
        """If build_transaction raises, nonce is released and exception re-raised."""
        pm.contract.functions.mint.return_value.build_transaction.side_effect = \
            Exception("gas estimation failed")

        with pytest.raises(Exception, match="gas estimation failed"):
            pm.mint_single(mint_params)

        pm.nonce_manager.release_nonce.assert_called_once_with(10)
        pm.nonce_manager.confirm_transaction.assert_not_called()

    def test_mint_single_send_exception_releases_nonce(self, pm, mint_params):
        """If send_raw_transaction raises, nonce is released."""
        pm.w3.eth.send_raw_transaction.side_effect = Exception("nonce too low")

        with pytest.raises(Exception, match="nonce too low"):
            pm.mint_single(mint_params)

        pm.nonce_manager.release_nonce.assert_called_once_with(10)

    def test_mint_single_without_nonce_manager(self, pm, mint_params):
        """Without nonce_manager, uses get_transaction_count."""
        pm.nonce_manager = None
        pm.w3.eth.get_transaction_count = Mock(return_value=5)
        pm._parse_mint_events = Mock(return_value={
            'token_id': 888,
            'liquidity': 10000,
            'amount0': 0,
            'amount1': 500,
        })

        result = pm.mint_single(mint_params)

        pm.w3.eth.get_transaction_count.assert_called_once_with(
            pm.account.address, 'pending'
        )
        assert result.token_id == 888


class TestGetPositionTokenIds:
    """Tests for get_position_token_ids (lines 454-491)."""

    @pytest.fixture
    def pm(self):
        """PM fixture for token ID tests."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = Mock(spec=Web3)
            pm.w3.eth = Mock()
            pm.position_manager_address = "0xPositionManager"
            return pm

    def test_erc721_enumerable_works(self, pm):
        """When tokenOfOwnerByIndex works, returns token IDs directly."""
        mock_erc721 = Mock()
        mock_erc721.functions.balanceOf.return_value.call.return_value = 3
        mock_erc721.functions.tokenOfOwnerByIndex.return_value.call.side_effect = [
            100, 200, 300
        ]
        pm.w3.eth.contract.return_value = mock_erc721

        result = pm.get_position_token_ids(
            "0x1234567890123456789012345678901234567890"
        )

        assert result == [100, 200, 300]

    def test_zero_balance_returns_empty(self, pm):
        """When balance is 0, returns empty list immediately."""
        mock_erc721 = Mock()
        mock_erc721.functions.balanceOf.return_value.call.return_value = 0
        pm.w3.eth.contract.return_value = mock_erc721

        result = pm.get_position_token_ids(
            "0x1234567890123456789012345678901234567890"
        )

        assert result == []

    def test_enumerable_fails_falls_back_to_scan(self, pm):
        """When tokenOfOwnerByIndex fails, falls back to _scan_transfer_events."""
        mock_erc721 = Mock()
        mock_erc721.functions.balanceOf.return_value.call.return_value = 2
        mock_erc721.functions.tokenOfOwnerByIndex.return_value.call.side_effect = \
            Exception("not supported")
        pm.w3.eth.contract.return_value = mock_erc721

        pm._scan_transfer_events = Mock(return_value=[500, 600])

        result = pm.get_position_token_ids(
            "0x1234567890123456789012345678901234567890"
        )

        assert result == [500, 600]
        pm._scan_transfer_events.assert_called_once()

    def test_balance_of_fails_returns_empty(self, pm):
        """When balanceOf fails entirely, returns empty list."""
        mock_erc721 = Mock()
        mock_erc721.functions.balanceOf.return_value.call.side_effect = \
            Exception("contract error")
        pm.w3.eth.contract.return_value = mock_erc721

        result = pm.get_position_token_ids(
            "0x1234567890123456789012345678901234567890"
        )

        assert result == []


class TestScanTransferEvents:
    """Tests for _scan_transfer_events (lines 506-566)."""

    @pytest.fixture
    def pm(self):
        """PM fixture for Transfer event scanning tests."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = Mock(spec=Web3)
            pm.w3.eth = Mock()
            pm.w3.keccak = Mock(return_value=b'\x00' * 32)
            pm.w3.eth.block_number = 1000000
            pm.position_manager_address = "0xPositionManager"
            return pm

    def test_finds_tokens_via_topics3(self, pm):
        """Token IDs extracted from topics[3] when 4+ topics exist."""
        # Token ID = 42 in hex = 0x2a, padded to 32 bytes
        topic3 = bytes.fromhex('000000000000000000000000000000000000000000000000000000000000002a')
        mock_log = {
            'topics': [b'\x00' * 32, b'\x00' * 32, b'\x00' * 32, topic3],
            'data': b'',
        }
        pm.w3.eth.get_logs = Mock(return_value=[mock_log])

        # ownerOf confirms ownership
        mock_erc721 = Mock()
        address = "0x1234567890123456789012345678901234567890"
        mock_erc721.functions.ownerOf.return_value.call.return_value = address
        pm.w3.eth.contract.return_value = mock_erc721

        result = pm._scan_transfer_events(address, expected_count=1)

        assert 42 in result

    def test_finds_tokens_via_data_field(self, pm):
        """Token IDs extracted from data when only 3 topics exist."""
        # Token ID = 99 in hex = 0x63, padded to 32 bytes
        data = bytes.fromhex('0000000000000000000000000000000000000000000000000000000000000063')
        mock_log = {
            'topics': [b'\x00' * 32, b'\x00' * 32, b'\x00' * 32],
            'data': data,
        }
        pm.w3.eth.get_logs = Mock(return_value=[mock_log])

        # ownerOf confirms ownership
        mock_erc721 = Mock()
        address = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        mock_erc721.functions.ownerOf.return_value.call.return_value = address
        pm.w3.eth.contract.return_value = mock_erc721

        result = pm._scan_transfer_events(address, expected_count=1)

        assert 99 in result

    def test_ownership_check_filters_transferred_tokens(self, pm):
        """Tokens no longer owned are filtered out by ownerOf check."""
        topic3_a = bytes.fromhex('000000000000000000000000000000000000000000000000000000000000000a')
        topic3_b = bytes.fromhex('000000000000000000000000000000000000000000000000000000000000000b')
        logs = [
            {'topics': [b'\x00' * 32, b'\x00' * 32, b'\x00' * 32, topic3_a], 'data': b''},
            {'topics': [b'\x00' * 32, b'\x00' * 32, b'\x00' * 32, topic3_b], 'data': b''},
        ]
        pm.w3.eth.get_logs = Mock(return_value=logs)

        address = "0x1234567890123456789012345678901234567890"
        mock_erc721 = Mock()
        # Token 10 is owned by us, token 11 by someone else
        def ownerOf_side_effect(token_id):
            mock_call = Mock()
            if token_id == 10:
                mock_call.call.return_value = address
            else:
                mock_call.call.return_value = "0xSomeoneElse"
            return mock_call

        mock_erc721.functions.ownerOf = Mock(side_effect=ownerOf_side_effect)
        pm.w3.eth.contract.return_value = mock_erc721

        result = pm._scan_transfer_events(address, expected_count=1)

        assert 10 in result
        assert 11 not in result

    def test_ownerof_fails_skips_token(self, pm):
        """When ownerOf raises for a token, that token is skipped."""
        topic3 = bytes.fromhex('000000000000000000000000000000000000000000000000000000000000002a')
        mock_log = {
            'topics': [b'\x00' * 32, b'\x00' * 32, b'\x00' * 32, topic3],
            'data': b'',
        }
        pm.w3.eth.get_logs = Mock(return_value=[mock_log])

        mock_erc721 = Mock()
        mock_erc721.functions.ownerOf.return_value.call.side_effect = \
            Exception("token burned")
        pm.w3.eth.contract.return_value = mock_erc721

        address = "0x1234567890123456789012345678901234567890"
        result = pm._scan_transfer_events(address, expected_count=1)

        assert result == []

    def test_get_logs_exception_returns_empty(self, pm):
        """When get_logs raises, returns empty list."""
        pm.w3.eth.get_logs = Mock(side_effect=Exception("RPC error"))

        address = "0x1234567890123456789012345678901234567890"
        result = pm._scan_transfer_events(address, expected_count=1)

        assert result == []

    def test_no_logs_returns_empty(self, pm):
        """When no Transfer logs found, returns empty list."""
        pm.w3.eth.get_logs = Mock(return_value=[])

        address = "0x1234567890123456789012345678901234567890"
        result = pm._scan_transfer_events(address, expected_count=0)

        assert result == []


class TestScanWalletPositions:
    """Tests for scan_wallet_positions (lines 578-589)."""

    @pytest.fixture
    def pm(self):
        """PM fixture for scan_wallet_positions tests."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = Mock(spec=Web3)
            pm.w3.eth = Mock()

            # Default: contract returns position data
            mock_contract = Mock()
            mock_contract.functions.positions = Mock(return_value=Mock(
                call=Mock(return_value=(
                    0, "0x0", "0xToken0", "0xToken1",
                    2500, -100, -50, 1_000_000,
                    0, 0, 500, 300
                ))
            ))
            pm.contract = mock_contract

            return pm

    def test_scan_combines_ids_and_positions(self, pm):
        """scan_wallet_positions returns position data for each token_id."""
        pm.get_position_token_ids = Mock(return_value=[100, 200])
        pm.get_position = Mock(side_effect=[
            {'liquidity': 1000, 'fee': 2500, 'tick_lower': -100, 'tick_upper': -50,
             'token0': '0xA', 'token1': '0xB', 'nonce': 0, 'operator': '0x0',
             'fee_growth_inside0_last_x128': 0, 'fee_growth_inside1_last_x128': 0,
             'tokens_owed0': 0, 'tokens_owed1': 0},
            {'liquidity': 2000, 'fee': 3000, 'tick_lower': -200, 'tick_upper': -100,
             'token0': '0xA', 'token1': '0xB', 'nonce': 0, 'operator': '0x0',
             'fee_growth_inside0_last_x128': 0, 'fee_growth_inside1_last_x128': 0,
             'tokens_owed0': 0, 'tokens_owed1': 0},
        ])

        address = "0x1234567890123456789012345678901234567890"
        result = pm.scan_wallet_positions(address)

        assert len(result) == 2
        assert result[0]['token_id'] == 100
        assert result[0]['liquidity'] == 1000
        assert result[1]['token_id'] == 200
        assert result[1]['liquidity'] == 2000

    def test_scan_handles_individual_position_error(self, pm):
        """If get_position fails for one ID, it is skipped without crashing."""
        pm.get_position_token_ids = Mock(return_value=[100, 200, 300])
        pm.get_position = Mock(side_effect=[
            {'liquidity': 1000, 'fee': 2500, 'tick_lower': -100, 'tick_upper': -50,
             'token0': '0xA', 'token1': '0xB', 'nonce': 0, 'operator': '0x0',
             'fee_growth_inside0_last_x128': 0, 'fee_growth_inside1_last_x128': 0,
             'tokens_owed0': 0, 'tokens_owed1': 0},
            Exception("position burned"),
            {'liquidity': 3000, 'fee': 500, 'tick_lower': -300, 'tick_upper': -200,
             'token0': '0xA', 'token1': '0xB', 'nonce': 0, 'operator': '0x0',
             'fee_growth_inside0_last_x128': 0, 'fee_growth_inside1_last_x128': 0,
             'tokens_owed0': 0, 'tokens_owed1': 0},
        ])

        address = "0x1234567890123456789012345678901234567890"
        result = pm.scan_wallet_positions(address)

        # Only 2 of 3 positions should be returned
        assert len(result) == 2
        assert result[0]['token_id'] == 100
        assert result[1]['token_id'] == 300

    def test_scan_no_positions_returns_empty(self, pm):
        """When no token IDs found, returns empty list."""
        pm.get_position_token_ids = Mock(return_value=[])

        address = "0x1234567890123456789012345678901234567890"
        result = pm.scan_wallet_positions(address)

        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
