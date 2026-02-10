"""
Shared fixtures for all tests.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from web3 import Web3


class MockWeb3:
    """Переиспользуемый мок Web3 для тестов."""

    def __init__(self, initial_nonce: int = 100):
        self._nonce = initial_nonce
        self.eth = MagicMock()
        self.eth.get_transaction_count = MagicMock(return_value=self._nonce)
        self.eth.gas_price = 5_000_000_000  # 5 gwei
        self.eth.chain_id = 56
        self.eth.block_number = 40_000_000
        self.eth.send_raw_transaction = MagicMock(return_value=b'\x12\x34' * 16)
        self.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 1,
            'gasUsed': 300_000,
            'logs': [],
            'transactionHash': b'\x12\x34' * 16
        })
        self.eth.call = MagicMock(return_value=b'\x00' * 32)
        self.eth.contract = MagicMock()

    def set_nonce(self, nonce: int):
        self._nonce = nonce
        self.eth.get_transaction_count.return_value = nonce

    @staticmethod
    def to_checksum_address(addr: str) -> str:
        return addr

    @staticmethod
    def keccak(text: str = None, primitive: bytes = None) -> bytes:
        import hashlib
        if text:
            data = text.encode()
        elif primitive:
            data = primitive
        else:
            data = b''
        return hashlib.sha256(data).digest()


@pytest.fixture
def mock_w3():
    """Мок Web3 instance."""
    return MockWeb3()


@pytest.fixture
def mock_account():
    """Мок LocalAccount."""
    account = Mock()
    account.address = "0x1234567890123456789012345678901234567890"
    account.sign_transaction = Mock(return_value=Mock(raw_transaction=b'signed_tx'))
    return account


@pytest.fixture
def mock_erc20_contract():
    """Мок ERC20 контракта."""
    contract = Mock()
    contract.functions = Mock()

    # balanceOf
    contract.functions.balanceOf = Mock(return_value=Mock(
        call=Mock(return_value=1000 * 10**18)
    ))

    # decimals
    contract.functions.decimals = Mock(return_value=Mock(
        call=Mock(return_value=18)
    ))

    # allowance
    contract.functions.allowance = Mock(return_value=Mock(
        call=Mock(return_value=0)
    ))

    # approve
    contract.functions.approve = Mock(return_value=Mock(
        build_transaction=Mock(return_value={}),
        estimate_gas=Mock(return_value=60000)
    ))

    return contract


@pytest.fixture
def mock_receipt_success():
    """Успешный receipt транзакции."""
    return {
        'status': 1,
        'gasUsed': 300_000,
        'logs': [],
        'transactionHash': b'\x12\x34' * 16,
        'blockNumber': 40_000_000,
    }


@pytest.fixture
def mock_receipt_fail():
    """Неуспешный receipt транзакции."""
    return {
        'status': 0,
        'gasUsed': 300_000,
        'logs': [],
        'transactionHash': b'\xde\xad' * 16,
        'blockNumber': 40_000_000,
    }


# Тестовые адреса
TOKEN_A = "0x1111111111111111111111111111111111111111"
TOKEN_B = "0x9999999999999999999999999999999999999999"
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
