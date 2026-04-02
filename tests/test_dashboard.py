"""
Tests for dashboard and pnl_store functionality.

Covers:
- pnl_store.py open_positions CRUD (save, bulk save, get, remove)
- DashboardTab widget creation, signals, position updates, removal
- MainWindow chain switching and protocol sync
- ManageTab position persistence (LoadPositionWorker protocol stamp,
  _record_closed_trade batch close fix)
"""

import os
import sys
import time
import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock


# ============================================================
# 1. pnl_store — open_positions CRUD
# ============================================================

@pytest.fixture
def pnl_store_tmp(tmp_path, monkeypatch):
    """Redirect pnl_store DB to a temporary directory."""
    import src.storage.pnl_store as pnl_store

    monkeypatch.setattr(pnl_store, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(pnl_store, "DB_PATH", str(tmp_path / "pnl.db"))
    return pnl_store


def _make_pos(token_id, liquidity=1000, token0="0xAAAA", token1="0xBBBB",
              chain_id=56, protocol="v3", fee=500):
    """Helper to build a position dict."""
    return {
        'token_id': token_id,
        'token0': token0,
        'token1': token1,
        'token0_symbol': 'TK0',
        'token1_symbol': 'TK1',
        'token0_decimals': 18,
        'token1_decimals': 18,
        'fee': fee,
        'tick_lower': -100,
        'tick_upper': 100,
        'liquidity': liquidity,
        'chain_id': chain_id,
        'protocol': protocol,
    }


class TestPnlStoreOpenPositions:
    """Tests for open_positions CRUD in pnl_store."""

    def test_save_and_get_single_position(self, pnl_store_tmp):
        ps = pnl_store_tmp
        pos = _make_pos(token_id=42, liquidity=5000)
        ps.save_open_position("0xWALLET", pos)

        result = ps.get_open_positions()
        assert 42 in result
        assert result[42]['liquidity'] == 5000
        assert result[42]['token0'] == '0xAAAA'

    def test_save_open_position_upsert(self, pnl_store_tmp):
        """Saving the same token_id twice should update, not duplicate."""
        ps = pnl_store_tmp
        ps.save_open_position("0xWALLET", _make_pos(token_id=1, liquidity=100))
        ps.save_open_position("0xWALLET", _make_pos(token_id=1, liquidity=999))

        result = ps.get_open_positions()
        assert len(result) == 1
        assert result[1]['liquidity'] == 999

    def test_bulk_save_positions(self, pnl_store_tmp):
        ps = pnl_store_tmp
        positions = {
            10: _make_pos(10, liquidity=100),
            20: _make_pos(20, liquidity=200),
            30: _make_pos(30, liquidity=300),
        }
        ps.save_open_positions_bulk("0xWALLET", positions)

        result = ps.get_open_positions()
        assert len(result) == 3
        assert result[10]['liquidity'] == 100
        assert result[30]['liquidity'] == 300

    def test_bulk_save_skips_zero_liquidity(self, pnl_store_tmp):
        """Positions with liquidity <= 0 should be skipped in bulk save."""
        ps = pnl_store_tmp
        positions = {
            10: _make_pos(10, liquidity=100),
            20: _make_pos(20, liquidity=0),
            30: _make_pos(30, liquidity=-5),
        }
        ps.save_open_positions_bulk("0xWALLET", positions)

        result = ps.get_open_positions()
        assert len(result) == 1
        assert 10 in result
        assert 20 not in result
        assert 30 not in result

    def test_bulk_save_skips_non_dict_entries(self, pnl_store_tmp):
        """Non-dict entries should be skipped."""
        ps = pnl_store_tmp
        positions = {
            10: _make_pos(10, liquidity=100),
            20: "not a dict",
            30: None,
        }
        ps.save_open_positions_bulk("0xWALLET", positions)

        result = ps.get_open_positions()
        assert len(result) == 1
        assert 10 in result

    def test_get_open_positions_all(self, pnl_store_tmp):
        ps = pnl_store_tmp
        ps.save_open_position("0xAAA", _make_pos(1, liquidity=10))
        ps.save_open_position("0xBBB", _make_pos(2, liquidity=20))

        result = ps.get_open_positions()
        assert len(result) == 2

    def test_get_open_positions_filtered_by_wallet(self, pnl_store_tmp):
        ps = pnl_store_tmp
        ps.save_open_position("0xAAA", _make_pos(1))
        ps.save_open_position("0xBBB", _make_pos(2))
        ps.save_open_position("0xAAA", _make_pos(3))

        result = ps.get_open_positions(wallet="0xAAA")
        assert len(result) == 2
        assert 1 in result
        assert 3 in result
        assert 2 not in result

    def test_remove_open_positions(self, pnl_store_tmp):
        ps = pnl_store_tmp
        ps.save_open_position("0xWALLET", _make_pos(1))
        ps.save_open_position("0xWALLET", _make_pos(2))
        ps.save_open_position("0xWALLET", _make_pos(3))

        ps.remove_open_positions([1, 3])

        result = ps.get_open_positions()
        assert len(result) == 1
        assert 2 in result

    def test_remove_open_positions_empty_list(self, pnl_store_tmp):
        """Passing an empty list should be a no-op (no SQL error)."""
        ps = pnl_store_tmp
        ps.save_open_position("0xWALLET", _make_pos(1))

        ps.remove_open_positions([])  # should not raise

        result = ps.get_open_positions()
        assert len(result) == 1

    def test_liquidity_stored_as_text_returned_as_int(self, pnl_store_tmp):
        """Liquidity is stored as TEXT in SQLite but returned as int."""
        ps = pnl_store_tmp
        big_liq = 123456789012345678901234  # exceeds 64-bit int
        ps.save_open_position("0xWALLET", _make_pos(1, liquidity=big_liq))

        result = ps.get_open_positions()
        assert result[1]['liquidity'] == big_liq
        assert isinstance(result[1]['liquidity'], int)

        # Verify raw DB stores it as text
        import sqlite3
        conn = sqlite3.connect(ps.DB_PATH)
        raw = conn.execute(
            "SELECT typeof(liquidity), liquidity FROM open_positions WHERE token_id = 1"
        ).fetchone()
        conn.close()
        assert raw[0] == "text"
        assert raw[1] == str(big_liq)

    def test_wallet_address_lowercased_on_save(self, pnl_store_tmp):
        """Wallet addresses should be lowercased on save."""
        ps = pnl_store_tmp
        ps.save_open_position("0xAbCdEf", _make_pos(1))

        result = ps.get_open_positions()
        assert result[1]['wallet'] == "0xabcdef"

    def test_wallet_address_lowercased_on_query(self, pnl_store_tmp):
        """Filtering by wallet should be case-insensitive."""
        ps = pnl_store_tmp
        ps.save_open_position("0xAbCdEf", _make_pos(1))

        # Query with mixed case should still find it
        result = ps.get_open_positions(wallet="0xABCDEF")
        assert len(result) == 1
        assert 1 in result


# ============================================================
# 2. DashboardTab
# ============================================================

@pytest.fixture(scope="module")
def qapp():
    """QApplication fixture for Qt widget tests."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class TestDashboardTab:
    """Tests for DashboardTab widget."""

    def test_creates_without_error(self, qapp):
        """DashboardTab() should instantiate without raising."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()
            assert tab is not None
            assert hasattr(tab, '_positions_data')
            assert tab._positions_data == {}

    def test_pair_clicked_signal_signature(self, qapp):
        """pair_clicked should be pyqtSignal(list, str, int)."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()

            # Connect a mock slot to verify signal works with (list, str, int)
            received = []
            tab.pair_clicked.connect(lambda ids, proto, cid: received.append((ids, proto, cid)))
            tab.pair_clicked.emit([100, 200], "v3", 56)

            assert len(received) == 1
            assert received[0] == ([100, 200], "v3", 56)

    def test_update_positions_data_adds_active(self, qapp):
        """update_positions_data should add positions with liquidity > 0."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()
            tab._load_active_pairs = Mock()
            tab._load_open_stats = Mock()

            positions = {
                1: {'liquidity': 500, 'token0': '0xA', 'token1': '0xB'},
                2: {'liquidity': 1000, 'token0': '0xC', 'token1': '0xD'},
            }
            tab.update_positions_data(positions)

            assert 1 in tab._positions_data
            assert 2 in tab._positions_data

    def test_update_positions_data_removes_zero_liquidity(self, qapp):
        """Positions with liquidity=0 should be removed from _positions_data."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()
            tab._load_active_pairs = Mock()
            tab._load_open_stats = Mock()

            # Pre-populate
            tab._positions_data = {
                1: {'liquidity': 500, 'token0': '0xA', 'token1': '0xB'},
            }

            # Update: token_id=1 now has zero liquidity (closed)
            tab.update_positions_data({1: {'liquidity': 0}})

            assert 1 not in tab._positions_data

    def test_update_positions_data_empty_dict_is_noop(self, qapp):
        """Passing empty dict to update_positions_data should not modify data."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()
            tab._load_active_pairs = Mock()
            tab._load_open_stats = Mock()

            tab._positions_data = {1: {'liquidity': 100}}
            tab.update_positions_data({})

            assert 1 in tab._positions_data

    def test_load_saved_positions_replaces_data(self, qapp):
        """_load_saved_positions should replace _positions_data from DB, not merge."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()

            # Pre-populate with stale data
            tab._positions_data = {
                99: {'liquidity': 100, 'token0': 'old'},
            }

            db_data = {
                5: {'liquidity': 500, 'token0': '0xNew'},
            }
            with patch("ui.dashboard_tab.get_open_positions", return_value=db_data):
                tab._load_saved_positions()

            # Old data should be gone, replaced by DB content
            assert 99 not in tab._positions_data
            assert 5 in tab._positions_data
            assert tab._positions_data[5]['liquidity'] == 500

    def test_remove_pair_removes_from_memory_and_db(self, qapp):
        """_remove_pair should remove from _positions_data AND call remove_open_positions."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()
            tab._load_active_pairs = Mock()
            tab._load_open_stats = Mock()

            tab._positions_data = {
                10: {'liquidity': 100},
                20: {'liquidity': 200},
                30: {'liquidity': 300},
            }

            with patch("ui.dashboard_tab.remove_open_positions") as mock_remove:
                tab._remove_pair([10, 30])

                # Removed from memory
                assert 10 not in tab._positions_data
                assert 30 not in tab._positions_data
                assert 20 in tab._positions_data

                # Called DB removal
                mock_remove.assert_called_once_with([10, 30])

    def test_refresh_calls_load_saved_positions(self, qapp):
        """refresh() should call _load_saved_positions."""
        with patch("ui.dashboard_tab.get_dashboard_stats"), \
             patch("ui.dashboard_tab.get_cumulative_pnl", return_value=[]), \
             patch("ui.dashboard_tab.get_recent_trades", return_value=[]), \
             patch("ui.dashboard_tab.get_open_positions", return_value={}):
            from ui.dashboard_tab import DashboardTab
            tab = DashboardTab()

            with patch.object(tab, '_load_saved_positions') as mock_load, \
                 patch.object(tab, '_load_stats'), \
                 patch.object(tab, '_load_chart'), \
                 patch.object(tab, '_load_recent_trades'), \
                 patch.object(tab, '_load_active_pairs'):
                tab.refresh()
                mock_load.assert_called_once()


# ============================================================
# 3. MainWindow — chain switching and protocol sync
# ============================================================

class TestMainWindowChainMapping:
    """Tests for MainWindow._CHAIN_TO_NETWORK_IDX and _on_dashboard_pair_clicked."""

    def test_chain_to_network_idx_mapping(self):
        """_CHAIN_TO_NETWORK_IDX should map {56: 0, 1: 1, 8453: 2}."""
        # Import the class but avoid instantiation (needs full Qt app + UI)
        from ui.main_window import MainWindow
        expected = {56: 0, 1: 1, 8453: 2}
        assert MainWindow._CHAIN_TO_NETWORK_IDX == expected

    def test_on_dashboard_pair_clicked_sets_protocol(self, qapp):
        """_on_dashboard_pair_clicked should set scan_protocol_combo to the correct protocol."""
        from ui.main_window import MainWindow

        # Avoid full UI construction — patch setup_ui entirely
        with patch.object(MainWindow, "setup_ui"), \
             patch.object(MainWindow, "load_stylesheet"), \
             patch.object(MainWindow, "restore_geometry"):

            window = MainWindow()

            # Wire up the minimum attributes the handler needs
            window.create_tab = Mock()
            window.create_tab.provider = None
            window.create_tab.worker = None

            window.manage_tab = Mock()
            window.manage_tab.provider = None
            window.manage_tab.token_ids_input = Mock()
            window.manage_tab.scan_protocol_combo = Mock()
            window.manage_tab.scan_protocol_combo.findData = Mock(return_value=2)

            window.tabs = Mock()
            window.status_bar = Mock()

            # Call the handler
            window._on_dashboard_pair_clicked([100, 200], "v3_uniswap", 56)

            # Should call findData with the protocol
            window.manage_tab.scan_protocol_combo.findData.assert_called_with("v3_uniswap")
            # Should set the combo index since findData returned >= 0
            window.manage_tab.scan_protocol_combo.setCurrentIndex.assert_called_with(2)


# ============================================================
# 4. ManageTab — position persistence
# ============================================================

class TestLoadPositionWorkerProtocolStamp:
    """Test that LoadPositionWorker stamps protocol on the position dict."""

    def test_protocol_stamped_on_v3_position(self, qapp):
        """LoadPositionWorker should set position['protocol'] = self.protocol for V3."""
        from ui.manage_tab import LoadPositionWorker

        mock_provider = Mock()
        mock_provider.w3 = Mock()
        mock_provider.account = Mock()
        mock_provider.account.address = "0x1234567890123456789012345678901234567890"
        mock_provider.position_manager_address = "0xPM"

        worker = LoadPositionWorker(
            provider=mock_provider,
            token_id=42,
            pool_factory=None,
            check_ownership=False,
            protocol="v3_uniswap",
        )

        # Mock BatchRPC to return owner + position data
        mock_batch_instance = Mock()
        mock_batch_instance.execute.return_value = [
            "0x1234567890123456789012345678901234567890",  # owner
            {  # position data
                'token0': '0xAAAA',
                'token1': '0xBBBB',
                'fee': 500,
                'tick_lower': -100,
                'tick_upper': 100,
                'liquidity': 0,  # zero liq so pool_factory branch is skipped
            },
        ]

        emitted = []
        worker.position_loaded.connect(lambda tid, pos: emitted.append((tid, pos)))

        with patch("src.utils.BatchRPC", return_value=mock_batch_instance):
            worker.run()

        assert len(emitted) == 1
        tid, pos = emitted[0]
        assert tid == 42
        assert pos['protocol'] == 'v3_uniswap'


class TestRecordClosedTradeBatchFix:
    """Test _record_closed_trade extracts token_ids from pos_dicts when data['token_ids'] is empty."""

    def test_token_ids_extracted_from_pos_dicts(self, qapp):
        """When token_ids is empty (batch close), should extract from pos_dicts."""
        # We need to test the _record_closed_trade method in isolation.
        # Import ManageTab but avoid full construction.
        from ui.manage_tab import ManageTab
        from PyQt6.QtCore import QMutex
        from PyQt6.QtWidgets import QWidget

        with patch.object(ManageTab, "__init__", lambda self: QWidget.__init__(self)):
            tab = ManageTab()
            tab.provider = Mock()
            tab.provider.chain_id = 56
            tab.trade_recorded = Mock()
            tab._positions_mutex = QMutex()
            tab.positions_data = {}
            tab.initial_investment_spin = Mock()
            tab._log = Mock()

            data = {
                'positions_data': {},
                'token_ids': [],  # empty — batch close scenario
                'positions': [
                    {
                        'token_id': 10,
                        'token0_symbol': 'USDT',
                        'token1_symbol': 'WBNB',
                        'token0': '0x55d398326f99059fF775485246999027B3197955',
                        'token1': '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',
                        'protocol': 'v3',
                        'liquidity': 0,
                        'tick_lower': -100,
                        'tick_upper': 100,
                    },
                    {
                        'token_id': 20,
                        'token0_symbol': 'USDT',
                        'token1_symbol': 'WBNB',
                        'token0': '0x55d398326f99059fF775485246999027B3197955',
                        'token1': '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',
                        'protocol': 'v3',
                        'liquidity': 0,
                        'tick_lower': -200,
                        'tick_upper': 200,
                    },
                ],
                'initial_investment': 100.0,
                'tx_hash': '0xABC',
            }

            with patch("ui.manage_tab.save_trade") as mock_save_trade, \
                 patch("ui.manage_tab.remove_open_positions") as mock_remove:
                tab._record_closed_trade(data)

                # Should have called save_trade
                mock_save_trade.assert_called_once()
                record = mock_save_trade.call_args[0][0]
                assert record.n_positions == 2
                assert record.pair == "USDT/WBNB"

                # Should have extracted token_ids from pos_dicts
                mock_remove.assert_called_once_with([10, 20])
