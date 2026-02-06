"""
Tests for utility classes.
"""

import pytest
from unittest.mock import MagicMock, patch
import threading
import time

from src.utils import NonceManager, DecimalsCache


class MockWeb3:
    """Mock Web3 instance for testing."""

    def __init__(self, initial_nonce: int = 100):
        self._nonce = initial_nonce
        self.eth = MagicMock()
        self.eth.get_transaction_count = MagicMock(return_value=self._nonce)

    def set_nonce(self, nonce: int):
        """Set the nonce that will be returned by get_transaction_count."""
        self._nonce = nonce
        self.eth.get_transaction_count.return_value = nonce

    @staticmethod
    def to_checksum_address(addr: str) -> str:
        return addr


class TestNonceManager:
    """Tests for NonceManager."""

    def test_initial_sync(self):
        """Test that first get_next_nonce syncs with blockchain."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce = manager.get_next_nonce()

        assert nonce == 100
        assert manager.get_pending_count() == 1
        w3.eth.get_transaction_count.assert_called_once()

    def test_sequential_nonces(self):
        """Test that sequential calls return incrementing nonces."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce1 = manager.get_next_nonce()
        nonce2 = manager.get_next_nonce()
        nonce3 = manager.get_next_nonce()

        assert nonce1 == 100
        assert nonce2 == 101
        assert nonce3 == 102
        assert manager.get_pending_count() == 3

    def test_confirm_transaction(self):
        """Test that confirming a transaction removes it from pending."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce1 = manager.get_next_nonce()
        nonce2 = manager.get_next_nonce()

        assert manager.get_pending_count() == 2

        manager.confirm_transaction(nonce1)

        assert manager.get_pending_count() == 1
        assert nonce1 not in manager.get_pending_nonces()
        assert nonce2 in manager.get_pending_nonces()

    def test_release_nonce(self):
        """Test that releasing a nonce removes it from pending."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce = manager.get_next_nonce()
        assert manager.get_pending_count() == 1

        manager.release_nonce(nonce)

        assert manager.get_pending_count() == 0

    def test_cleanup_stale_nonces_on_sync(self):
        """Test that stale nonces are cleaned up when syncing with blockchain."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")
        manager._sync_interval = 0  # Force sync on every call

        # Get some nonces
        nonce1 = manager.get_next_nonce()  # 100
        nonce2 = manager.get_next_nonce()  # 101
        nonce3 = manager.get_next_nonce()  # 102

        assert manager.get_pending_count() == 3

        # Simulate blockchain advancing (external transactions or confirmations)
        w3.set_nonce(102)

        # Force sync
        nonce4 = manager.get_next_nonce(force_sync=True)

        # nonce1 (100) and nonce2 (101) should be cleaned up
        # nonce3 (102) should remain, nonce4 (102) is new
        pending = manager.get_pending_nonces()
        assert 100 not in pending, "Stale nonce 100 should be cleaned"
        assert 101 not in pending, "Stale nonce 101 should be cleaned"
        assert 102 in pending, "Nonce 102 should remain"

    def test_cleanup_stale_nonces_manual(self):
        """Test manual cleanup of stale nonces."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        # Get some nonces
        manager.get_next_nonce()  # 100
        manager.get_next_nonce()  # 101
        manager.get_next_nonce()  # 102

        assert manager.get_pending_count() == 3

        # Simulate blockchain advancing
        w3.set_nonce(102)

        # Manual cleanup
        cleaned = manager.cleanup_stale_nonces()

        assert cleaned == 2  # 100 and 101 cleaned
        assert manager.get_pending_count() == 1
        assert 102 in manager.get_pending_nonces()

    def test_reset(self):
        """Test that reset clears all state."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        manager.get_next_nonce()
        manager.get_next_nonce()

        assert manager.get_pending_count() == 2

        manager.reset()

        assert manager.get_pending_count() == 0
        assert manager._current_nonce is None

    def test_external_transaction_handling(self):
        """Test handling of external transactions that increment nonce."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")
        manager._sync_interval = 0  # Force sync on every call

        # Get a nonce
        nonce1 = manager.get_next_nonce()  # 100

        # External transaction happened (e.g., MetaMask)
        w3.set_nonce(105)

        # Next call should sync and use blockchain nonce
        nonce2 = manager.get_next_nonce(force_sync=True)

        assert nonce2 == 105  # Should use blockchain nonce

        # Stale nonce should be cleaned
        assert 100 not in manager.get_pending_nonces()


class TestDecimalsCache:
    """Tests for DecimalsCache."""

    def test_known_decimals(self):
        """Test that known decimals are returned from cache."""
        w3 = MagicMock()
        cache = DecimalsCache(w3)

        # Known USDT on BSC
        decimals = cache.get_decimals("0x55d398326f99059ff775485246999027b3197955")

        assert decimals == 18
        # Should not call blockchain
        w3.eth.contract.assert_not_called()

    def test_unknown_decimals_fetched(self):
        """Test that unknown decimals are fetched from blockchain."""
        w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 8
        w3.eth.contract.return_value = mock_contract

        cache = DecimalsCache(w3)

        # Unknown token
        decimals = cache.get_decimals("0x0000000000000000000000000000000000000001")

        assert decimals == 8
        w3.eth.contract.assert_called_once()

    def test_cached_after_fetch(self):
        """Test that fetched decimals are cached."""
        w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 6
        w3.eth.contract.return_value = mock_contract

        cache = DecimalsCache(w3)

        # First call fetches
        decimals1 = cache.get_decimals("0x0000000000000000000000000000000000000002")
        # Second call uses cache
        decimals2 = cache.get_decimals("0x0000000000000000000000000000000000000002")

        assert decimals1 == 6
        assert decimals2 == 6
        # Should only call blockchain once
        assert w3.eth.contract.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
