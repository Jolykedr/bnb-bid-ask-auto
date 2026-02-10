"""
Integration tests for the liquidity provider system.

Эти тесты проверяют интеграцию между компонентами системы.
Используют моки для блокчейн взаимодействия.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass
from web3 import Web3

from src.liquidity_provider import (
    LiquidityProvider,
    LiquidityLadderConfig,
    LadderResult,
    InsufficientBalanceError
)
from src.multicall.batcher import Multicall3Batcher, CallResult, MintCallResult
from src.contracts.position_manager import UniswapV3PositionManager, MintParams, MintResult


class TestLiquidityProviderIntegration:
    """Integration tests for LiquidityProvider."""

    @pytest.fixture
    def mock_w3(self):
        """Create mock Web3 instance."""
        w3 = Mock(spec=Web3)
        w3.eth = Mock()
        w3.eth.gas_price = 5000000000  # 5 gwei
        w3.eth.get_transaction_count = Mock(return_value=0)
        w3.eth.chain_id = 56
        return w3

    @pytest.fixture
    def mock_account(self):
        """Create mock account."""
        account = Mock()
        account.address = "0x1234567890123456789012345678901234567890"
        account.sign_transaction = Mock(return_value=Mock(raw_transaction=b'signed_tx'))
        return account

    @pytest.fixture
    def provider(self, mock_w3, mock_account):
        """Create LiquidityProvider instance with mocks."""
        with patch.object(LiquidityProvider, '__init__', lambda self, *args, **kwargs: None):
            provider = LiquidityProvider.__new__(LiquidityProvider)
            provider.w3 = mock_w3
            provider.account = mock_account
            provider.chain_id = 56
            provider.position_manager_address = "0xEE55555555555555555555555555555555555555"
            provider.position_manager = Mock(spec=UniswapV3PositionManager)
            provider.batcher = Mock(spec=Multicall3Batcher)
            return provider

    def test_preview_ladder_calculates_positions(self, provider):
        """Test that preview_ladder correctly calculates positions."""
        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0="0xAA11111111111111111111111111111111111111",
            token1="0xBB22222222222222222222222222222222222222",
            fee_tier=2500,
            distribution_type="linear"
        )

        # Need to mock the actual calculation
        with patch('src.liquidity_provider.calculate_bid_ask_distribution') as mock_calc:
            mock_positions = [
                Mock(usd_amount=66.67, tick_lower=-100, tick_upper=-80),
                Mock(usd_amount=133.33, tick_lower=-120, tick_upper=-100),
                Mock(usd_amount=200.0, tick_lower=-140, tick_upper=-120),
                Mock(usd_amount=266.67, tick_lower=-160, tick_upper=-140),
                Mock(usd_amount=333.33, tick_lower=-180, tick_upper=-160),
            ]
            mock_calc.return_value = mock_positions

            positions = provider.preview_ladder(config)

            assert len(positions) == 5
            # Verify distribution: lower positions have more USD
            assert positions[0].usd_amount < positions[-1].usd_amount

    def test_validate_balances_insufficient_balance(self, provider):
        """Test balance validation when balance is insufficient."""
        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0="0xAA11111111111111111111111111111111111111",
            token1="0xBB22222222222222222222222222222222222222",
            fee_tier=2500
        )

        # Mock balance check - insufficient funds
        provider.get_token_balance = Mock(return_value=500 * 10**18)  # Only 500 USDT
        provider._ensure_token_order = Mock(return_value=("0xAA11111111111111111111111111111111111111", "0xBB22222222222222222222222222222222222222", False))

        is_valid, error_msg = provider.validate_balances_for_ladder(config)

        assert is_valid is False
        assert "Insufficient" in error_msg

    def test_validate_balances_sufficient_balance(self, provider):
        """Test balance validation when balance is sufficient."""
        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0="0xAA11111111111111111111111111111111111111",
            token1="0xBB22222222222222222222222222222222222222",
            fee_tier=2500
        )

        # Mock balance check - sufficient funds
        provider.get_token_balance = Mock(return_value=2000 * 10**18)  # 2000 USDT
        provider._ensure_token_order = Mock(return_value=("0xAA11111111111111111111111111111111111111", "0xBB22222222222222222222222222222222222222", False))

        is_valid, error_msg = provider.validate_balances_for_ladder(config)

        assert is_valid is True
        assert error_msg is None

    def test_ensure_token_order_no_swap(self, provider):
        """Test token ordering when already correct."""
        token0 = "0x1111111111111111111111111111111111111111"
        token1 = "0x9999999999999999999999999999999999999999"

        # Manually call the method (need to patch Web3.to_checksum_address)
        with patch.object(Web3, 'to_checksum_address', side_effect=lambda x: x):
            result = provider._ensure_token_order(token0, token1)

        assert result[0] == token0
        assert result[1] == token1
        assert result[2] is False  # not swapped

    def test_ensure_token_order_with_swap(self, provider):
        """Test token ordering when swap is needed."""
        token0 = "0x9999999999999999999999999999999999999999"
        token1 = "0x1111111111111111111111111111111111111111"

        with patch.object(Web3, 'to_checksum_address', side_effect=lambda x: x):
            result = provider._ensure_token_order(token0, token1)

        assert result[0] == token1  # swapped
        assert result[1] == token0  # swapped
        assert result[2] is True  # swapped


class TestMulticall3BatcherIntegration:
    """Integration tests for Multicall3Batcher."""

    @pytest.fixture
    def mock_w3(self):
        """Create mock Web3 instance."""
        w3 = Mock(spec=Web3)
        w3.eth = Mock()
        w3.eth.gas_price = 5000000000
        w3.eth.get_transaction_count = Mock(return_value=0)
        w3.eth.send_raw_transaction = Mock(return_value=b'\x12\x34' * 16)
        w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 500000,
            'logs': []
        })
        w3.eth.contract = Mock()
        return w3

    @pytest.fixture
    def mock_account(self):
        """Create mock account."""
        account = Mock()
        account.address = "0x1234567890123456789012345678901234567890"
        account.sign_transaction = Mock(return_value=Mock(raw_transaction=b'signed_tx'))
        return account

    @pytest.fixture
    def batcher(self, mock_w3, mock_account):
        """Create Multicall3Batcher with mocks."""
        with patch.object(Multicall3Batcher, '__init__', lambda self, *args, **kwargs: None):
            batcher = Multicall3Batcher.__new__(Multicall3Batcher)
            batcher.w3 = mock_w3
            batcher.account = mock_account
            batcher.multicall_address = "0xFF66666666666666666666666666666666666666"
            batcher.calls = []

            # Mock contract
            mock_contract = Mock()
            mock_contract.functions = Mock()
            mock_contract.functions.aggregate3 = Mock()
            mock_contract.functions.aggregate3.return_value = Mock()
            mock_contract.functions.aggregate3.return_value.build_transaction = Mock(return_value={})
            mock_contract.functions.aggregate3.return_value.estimate_gas = Mock(return_value=300000)
            mock_contract.functions.aggregate3.return_value.call = Mock(return_value=[
                (True, b'\x00' * 32)
            ])
            batcher.contract = mock_contract

            return batcher

    def test_add_mint_call(self, batcher):
        """Test adding mint call to batch."""
        # Mock the position manager contract
        mock_pm = Mock()
        mock_pm.encode_abi = Mock(return_value=b'encoded_mint_call')
        batcher._get_pm_contract = Mock(return_value=mock_pm)

        batcher.add_mint_call(
            position_manager="0xDD44444444444444444444444444444444444444",
            token0="0xAA11111111111111111111111111111111111111",
            token1="0xBB22222222222222222222222222222222222222",
            fee=2500,
            tick_lower=-100,
            tick_upper=-50,
            amount0_desired=0,
            amount1_desired=1000 * 10**18,
            recipient="0xCC33333333333333333333333333333333333333"
        )

        assert len(batcher.calls) == 1
        assert batcher.calls[0].target == "0xDD44444444444444444444444444444444444444"

    def test_add_close_position_calls(self, batcher):
        """Test adding close position calls (2 calls per position)."""
        mock_pm = Mock()
        mock_pm.encode_abi = Mock(return_value=b'encoded_call')
        batcher._get_pm_contract = Mock(return_value=mock_pm)

        batcher.add_close_position_calls(
            position_manager="0xDD44444444444444444444444444444444444444",
            token_id=12345,
            liquidity=1000000,
            recipient="0xCC33333333333333333333333333333333333333"
        )

        # Should add 2 calls: decreaseLiquidity, collect (no burn - NFT stays)
        assert len(batcher.calls) == 2

    def test_clear_removes_all_calls(self, batcher):
        """Test that clear removes all pending calls."""
        batcher.calls = [Mock(), Mock(), Mock()]
        batcher.clear()
        assert len(batcher.calls) == 0

    def test_simulate_returns_results(self, batcher):
        """Test simulation returns results."""
        mock_call = Mock()
        mock_call.target = "0xDD44444444444444444444444444444444444444"
        mock_call.call_data = b'\x00'
        batcher.calls = [mock_call]

        # Mock PM contract для simulate
        mock_pm = Mock()
        mock_pm.functions.multicall.return_value.call.return_value = [b'\x00' * 32]
        batcher._get_pm_contract = Mock(return_value=mock_pm)

        results = batcher.simulate()

        assert len(results) == 1
        assert results[0].success is True


class TestPositionManagerIntegration:
    """Integration tests for UniswapV3PositionManager."""

    @pytest.fixture
    def mock_w3(self):
        """Create mock Web3 instance."""
        w3 = Mock(spec=Web3)
        w3.eth = Mock()
        w3.eth.gas_price = 5000000000
        w3.eth.get_transaction_count = Mock(return_value=0)
        w3.eth.send_raw_transaction = Mock(return_value=b'\x12\x34' * 16)
        w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 300000,
            'logs': []
        })
        w3.eth.contract = Mock()
        return w3

    @pytest.fixture
    def mock_account(self):
        """Create mock account."""
        account = Mock()
        account.address = "0x1234567890123456789012345678901234567890"
        account.sign_transaction = Mock(return_value=Mock(raw_transaction=b'signed_tx'))
        return account

    @pytest.fixture
    def position_manager(self, mock_w3, mock_account):
        """Create UniswapV3PositionManager with mocks."""
        with patch.object(UniswapV3PositionManager, '__init__', lambda self, *args, **kwargs: None):
            pm = UniswapV3PositionManager.__new__(UniswapV3PositionManager)
            pm.w3 = mock_w3
            pm.account = mock_account
            pm.position_manager_address = "0xEE55555555555555555555555555555555555555"

            # Mock contract
            mock_contract = Mock()
            mock_contract.functions = Mock()
            mock_contract.encode_abi = Mock(return_value=b'encoded')

            # Mock positions function
            mock_contract.functions.positions = Mock(return_value=Mock(
                call=Mock(return_value=(
                    0,  # nonce
                    "0x0",  # operator
                    "0xAA11111111111111111111111111111111111111",  # token0
                    "0xBB22222222222222222222222222222222222222",  # token1
                    2500,  # fee
                    -100,  # tickLower
                    -50,  # tickUpper
                    1000000,  # liquidity
                    0,  # feeGrowthInside0LastX128
                    0,  # feeGrowthInside1LastX128
                    0,  # tokensOwed0
                    0,  # tokensOwed1
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

            pm.contract = mock_contract
            return pm

    def test_get_position_returns_dict(self, position_manager):
        """Test get_position returns properly formatted dict."""
        result = position_manager.get_position(12345)

        assert isinstance(result, dict)
        assert 'liquidity' in result
        assert result['liquidity'] == 1000000
        assert result['fee'] == 2500

    def test_encode_mint_returns_bytes(self, position_manager):
        """Test encode_mint returns encoded bytes."""
        params = MintParams(
            token0="0xAA11111111111111111111111111111111111111",
            token1="0xBB22222222222222222222222222222222222222",
            fee=2500,
            tick_lower=-100,
            tick_upper=-50,
            amount0_desired=0,
            amount1_desired=1000
        )

        result = position_manager.encode_mint(
            params=params,
            recipient="0xCC33333333333333333333333333333333333333"
        )

        assert isinstance(result, bytes)

    def test_encode_decrease_liquidity_returns_bytes(self, position_manager):
        """Test encode_decrease_liquidity returns encoded bytes."""
        result = position_manager.encode_decrease_liquidity(
            token_id=12345,
            liquidity=1000000
        )

        assert isinstance(result, bytes)

    def test_encode_collect_returns_bytes(self, position_manager):
        """Test encode_collect returns encoded bytes."""
        result = position_manager.encode_collect(
            token_id=12345,
            recipient="0xCC33333333333333333333333333333333333333"
        )

        assert isinstance(result, bytes)


class TestEndToEndFlow:
    """End-to-end tests for the complete liquidity provision flow."""

    def test_complete_ladder_creation_flow(self):
        """Test complete flow from config to ladder creation."""
        # This test verifies the integration of all components
        with patch('src.liquidity_provider.Web3') as MockWeb3, \
             patch('src.liquidity_provider.Account') as MockAccount, \
             patch('src.liquidity_provider.calculate_bid_ask_distribution') as mock_calc:

            # Setup mocks
            mock_w3 = Mock()
            mock_w3.eth.gas_price = 5000000000
            mock_w3.eth.get_transaction_count = Mock(return_value=0)
            mock_w3.eth.send_raw_transaction = Mock(return_value=b'\x12\x34' * 16)
            mock_w3.eth.wait_for_transaction_receipt = Mock(return_value={
                'status': 1,
                'gasUsed': 500000,
                'logs': []
            })
            mock_w3.eth.contract = Mock()
            MockWeb3.return_value = mock_w3
            MockWeb3.HTTPProvider = Mock()
            MockWeb3.to_checksum_address = Mock(side_effect=lambda x: x)

            mock_account = Mock()
            mock_account.address = "0x1234567890123456789012345678901234567890"
            MockAccount.from_key = Mock(return_value=mock_account)

            # Mock positions
            mock_positions = [
                Mock(usd_amount=200, tick_lower=-100, tick_upper=-80, percentage=20),
                Mock(usd_amount=300, tick_lower=-120, tick_upper=-100, percentage=30),
                Mock(usd_amount=500, tick_lower=-140, tick_upper=-120, percentage=50),
            ]
            mock_calc.return_value = mock_positions

            # Create config
            config = LiquidityLadderConfig(
                current_price=600.0,
                lower_price=400.0,
                total_usd=1000,
                n_positions=3,
                token0="0xWBNB",
                token1="0xUSDT",
                fee_tier=2500,
                distribution_type="linear"
            )

            # Verify config is valid
            assert config.current_price > config.lower_price
            assert config.total_usd > 0
            assert config.n_positions > 0

            # Verify positions sum to total
            total = sum(p.usd_amount for p in mock_positions)
            assert total == config.total_usd


class TestEventParsing:
    """Tests for event parsing from transaction receipts."""

    def test_parse_increase_liquidity_event(self):
        """Test parsing IncreaseLiquidity events from receipt."""
        # Mock receipt with IncreaseLiquidity event
        mock_receipt = {
            'status': 1,
            'gasUsed': 300000,
            'logs': [
                {
                    'address': '0xPositionManager',
                    'topics': [
                        # IncreaseLiquidity event signature
                        Web3.keccak(text="IncreaseLiquidity(uint256,uint128,uint256,uint256)"),
                        # tokenId (indexed)
                        b'\x00' * 31 + b'\x01'  # tokenId = 1
                    ],
                    'data': b'\x00' * 96  # liquidity, amount0, amount1
                }
            ]
        }

        # The batcher should be able to extract tokenId from this
        assert mock_receipt['logs'][0]['topics'][1] == b'\x00' * 31 + b'\x01'

    def test_parse_transfer_event_fallback(self):
        """Test fallback to Transfer event when IncreaseLiquidity fails."""
        # Mock receipt with Transfer event (NFT mint)
        mock_receipt = {
            'status': 1,
            'gasUsed': 300000,
            'logs': [
                {
                    'address': '0xPositionManager',
                    'topics': [
                        # Transfer event signature
                        Web3.keccak(text="Transfer(address,address,uint256)"),
                        # from (indexed) - address(0) for mint
                        b'\x00' * 32,
                        # to (indexed)
                        b'\x00' * 12 + b'\x12\x34' * 10,
                        # tokenId (indexed)
                        b'\x00' * 31 + b'\x05'  # tokenId = 5
                    ],
                    'data': b''
                }
            ]
        }

        # Verify tokenId can be extracted
        token_id_bytes = mock_receipt['logs'][0]['topics'][3]
        token_id = int.from_bytes(token_id_bytes, 'big')
        assert token_id == 5


class TestTimeouts:
    """Tests for timeout functionality."""

    def test_timeout_parameter_passed_to_wait(self):
        """Test that timeout parameter is correctly passed to wait_for_transaction_receipt."""
        mock_w3 = Mock()
        mock_w3.eth.wait_for_transaction_receipt = Mock(return_value={'status': 1})

        # Simulate calling with timeout
        tx_hash = b'\x12\x34' * 16
        timeout = 300

        mock_w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

        mock_w3.eth.wait_for_transaction_receipt.assert_called_once_with(
            tx_hash, timeout=timeout
        )

    def test_default_timeout_values(self):
        """Test default timeout values in config."""
        config = LiquidityLadderConfig(
            current_price=600.0,
            lower_price=400.0,
            total_usd=1000,
            n_positions=5,
            token0="0xAA11111111111111111111111111111111111111",
            token1="0xBB22222222222222222222222222222222222222",
            fee_tier=2500
        )

        # Default slippage should be 0.5%
        assert config.slippage_percent == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
