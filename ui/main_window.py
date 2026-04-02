"""
Main Window

Main application window with tabs for different functionality.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QMenuBar, QMenu,
    QStatusBar, QLabel, QMessageBox, QApplication
)
from PyQt6.QtCore import Qt, QSettings, QMutex, QMutexLocker, QTimer
from PyQt6.QtGui import QAction, QIcon

import os

from .dashboard_tab import DashboardTab
from .calculator_tab import CalculatorTab
from .create_tab import CreateTab
from .manage_tab import ManageTab
from .settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    """
    Main application window.

    Contains:
    - Menu bar with File, Settings, Help
    - Tab widget with Dashboard, Calculator, Create, Manage tabs
    - Status bar with connection info
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BNB Liquidity Ladder")
        self.setMinimumSize(1200, 800)

        self.settings = QSettings("BNBLiquidityLadder", "Settings")
        self._provider_mutex = QMutex()

        self.setup_ui()
        self.load_stylesheet()
        self.restore_geometry()

    def setup_ui(self):
        """Setup the main UI components."""
        # Menu bar
        self.setup_menu()

        # Central widget - Tab widget
        self.tabs = QTabWidget()

        # Dashboard tab (index 0)
        self.dashboard_tab = DashboardTab()
        self.tabs.addTab(self.dashboard_tab, "Dashboard")

        # Create tab (index 1)
        self.create_tab = CreateTab()
        self.tabs.addTab(self.create_tab, "Create Position")

        # Manage tab (index 2)
        self.manage_tab = ManageTab()
        self.tabs.addTab(self.manage_tab, "Manage Positions")

        # Calculator tab (index 3)
        self.calculator_tab = CalculatorTab()
        self.tabs.addTab(self.calculator_tab, "Calculator")

        # Connect create tab to manage tab for new positions
        self.create_tab.positions_created.connect(self._on_positions_created)

        # Connect manage tab trade_recorded signal to refresh dashboard
        self.manage_tab.trade_recorded.connect(self._on_trade_recorded)

        # Connect manage tab positions_updated to sync dashboard
        self.manage_tab.positions_updated.connect(self._sync_dashboard_positions)

        # Connect dashboard pair click to navigate to manage tab
        self.dashboard_tab.pair_clicked.connect(self._on_dashboard_pair_clicked)

        self.setCentralWidget(self.tabs)

        # Status bar
        self.setup_status_bar()

        # Connect tabs
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Initial dashboard load
        self.dashboard_tab.refresh()

    def setup_menu(self):
        """Setup the menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        new_action = QAction("New Calculator", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._on_new_calculator)
        file_menu.addAction(new_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Edit menu
        edit_menu = menubar.addMenu("Edit")

        settings_action = QAction("Settings...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings)
        edit_menu.addAction(settings_action)

        # View menu
        view_menu = menubar.addMenu("View")

        dashboard_action = QAction("Dashboard", self)
        dashboard_action.setShortcut("Ctrl+1")
        dashboard_action.triggered.connect(lambda: self.tabs.setCurrentIndex(0))
        view_menu.addAction(dashboard_action)

        create_action = QAction("Create Position", self)
        create_action.setShortcut("Ctrl+2")
        create_action.triggered.connect(lambda: self.tabs.setCurrentIndex(1))
        view_menu.addAction(create_action)

        manage_action = QAction("Manage Positions", self)
        manage_action.setShortcut("Ctrl+3")
        manage_action.triggered.connect(lambda: self.tabs.setCurrentIndex(2))
        view_menu.addAction(manage_action)

        calculator_action = QAction("Calculator", self)
        calculator_action.setShortcut("Ctrl+4")
        calculator_action.triggered.connect(lambda: self.tabs.setCurrentIndex(3))
        view_menu.addAction(calculator_action)

        # Help menu
        help_menu = menubar.addMenu("Help")

        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        docs_action = QAction("Documentation", self)
        docs_action.triggered.connect(self._show_docs)
        help_menu.addAction(docs_action)

    def setup_status_bar(self):
        """Setup the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Connection status
        self.connection_label = QLabel("Not Connected")
        self.connection_label.setStyleSheet("color: #a0a0a0;")
        self.status_bar.addPermanentWidget(self.connection_label)

        # Network indicator
        self.network_label = QLabel("BNB Mainnet")
        self.network_label.setStyleSheet("color: #fdcb6e;")
        self.status_bar.addPermanentWidget(self.network_label)

    def load_stylesheet(self):
        """Load the application stylesheet."""
        style_path = os.path.join(
            os.path.dirname(__file__),
            "styles", "dark_theme.qss"
        )

        if os.path.exists(style_path):
            with open(style_path, "r") as f:
                self.setStyleSheet(f.read())

    def restore_geometry(self):
        """Restore window geometry from settings."""
        geometry = self.settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

        state = self.settings.value("window/state")
        if state:
            self.restoreState(state)

    def save_geometry(self):
        """Save window geometry to settings."""
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())

    def closeEvent(self, event):
        """Handle window close event."""
        self._cleanup_workers()
        self.save_geometry()
        event.accept()

    def _cleanup_workers(self):
        """Stop all running workers before closing."""
        # Create tab worker
        if hasattr(self, 'create_tab') and self.create_tab.worker is not None:
            if self.create_tab.worker.isRunning():
                self.create_tab.worker.quit()
                self.create_tab.worker.wait(3000)
            self.create_tab.worker.deleteLater()
            self.create_tab.worker = None

        # Manage tab workers
        if hasattr(self, 'manage_tab'):
            # Main worker (BatchClose, ClosePositions)
            if hasattr(self.manage_tab, 'worker') and self.manage_tab.worker is not None:
                if self.manage_tab.worker.isRunning():
                    self.manage_tab.worker.quit()
                    self.manage_tab.worker.wait(3000)
                self.manage_tab.worker.deleteLater()
                self.manage_tab.worker = None

            # Load position workers (active + dying)
            for w_list in ('_active_workers', 'load_workers', '_dying_workers'):
                workers = getattr(self.manage_tab, w_list, [])
                for w in workers:
                    if w.isRunning():
                        w.quit()
                        w.wait(2000)
                    w.deleteLater()
                workers.clear()

            # Scan worker
            if hasattr(self.manage_tab, 'scan_worker') and self.manage_tab.scan_worker is not None:
                if self.manage_tab.scan_worker.isRunning():
                    self.manage_tab.scan_worker.quit()
                    self.manage_tab.scan_worker.wait(3000)
                self.manage_tab.scan_worker.deleteLater()
                self.manage_tab.scan_worker = None

        # Manage tab swap worker + balance fetch worker
        if hasattr(self, 'manage_tab'):
            for attr in ('_swap_worker', '_balance_fetch_worker'):
                w = getattr(self.manage_tab, attr, None)
                if w is not None:
                    if w.isRunning():
                        w.quit()
                        w.wait(3000)
                    w.deleteLater()
                    setattr(self.manage_tab, attr, None)

        # Create tab extra workers
        if hasattr(self, 'create_tab'):
            for attr in ('_load_pool_worker', '_balance_worker', '_pool_create_worker',
                         '_search_pool_worker', '_ref_price_worker'):
                w = getattr(self.create_tab, attr, None)
                if w is not None:
                    if w.isRunning():
                        w.quit()
                        w.wait(3000)
                    w.deleteLater()
                    setattr(self.create_tab, attr, None)
            # Clean up dying workers
            for w in getattr(self.create_tab, '_dying_workers', []):
                if w.isRunning():
                    w.quit()
                    w.wait(2000)
                w.deleteLater()
            if hasattr(self.create_tab, '_dying_workers'):
                self.create_tab._dying_workers.clear()

    def _on_tab_changed(self, index):
        """Handle tab change."""
        # Sync provider between tabs (mutex protects against worker race condition)
        locker = QMutexLocker(self._provider_mutex)
        if index == 0:  # Dashboard tab
            self._sync_dashboard_positions()
            self.dashboard_tab.refresh()
        elif index == 2:  # Manage tab
            if self.create_tab.provider and self.create_tab.worker is None:
                self.manage_tab.set_provider(self.create_tab.provider)

    def _on_trade_recorded(self):
        """Handle new trade recorded — refresh dashboard stats."""
        self.dashboard_tab.refresh()

    def _sync_dashboard_positions(self):
        """Push current manage_tab positions to dashboard."""
        if hasattr(self.manage_tab, 'positions_data'):
            # Enrich positions with ladder_group_id and invested_usd for dashboard grouping
            data = dict(self.manage_tab.positions_data)
            group_map = getattr(self.manage_tab, '_ladder_group_map', {})
            invested_map = getattr(self.manage_tab, '_invested_usd_map', {})
            for tid, pos in data.items():
                if isinstance(pos, dict):
                    if tid in group_map:
                        pos['ladder_group_id'] = group_map[tid]
                    if tid in invested_map:
                        pos['invested_usd'] = invested_map[tid]
            self.dashboard_tab.update_positions_data(data)

    def _on_positions_created(self, token_ids: list, invested_usd: float = 0, ladder_group_id: str = ""):
        """Handle new positions created in Create tab."""
        # Add positions to Manage tab
        if token_ids:
            locker = QMutexLocker(self._provider_mutex)
            # Ensure manage tab has provider (safe - worker is done at this point)
            if self.create_tab.provider:
                self.manage_tab.set_provider(self.create_tab.provider)

            # Add the new positions with invested amount and ladder group
            self.manage_tab.add_positions(token_ids, invested_usd, ladder_group_id)

            # Show notification in status bar
            self.status_bar.showMessage(
                f"Created {len(token_ids)} new position(s): {token_ids}",
                5000
            )

    # chain_id → network_combo index mapping
    _CHAIN_TO_NETWORK_IDX = {56: 0, 1: 1, 8453: 2}

    def _on_dashboard_pair_clicked(self, token_ids: list, protocol: str, chain_id: int = 56):
        """Handle pair click from dashboard — switch to Manage tab and load positions."""
        # Auto-switch network if needed
        current_chain = getattr(self.create_tab.provider, 'chain_id', None) if self.create_tab.provider else None
        needs_switch = (current_chain is not None and current_chain != chain_id)

        if needs_switch:
            target_idx = self._CHAIN_TO_NETWORK_IDX.get(chain_id)
            if target_idx is not None and self.create_tab.key_input.text().strip():
                self.create_tab.network_combo.setCurrentIndex(target_idx)
                chain_names = {56: "BNB", 1: "ETH", 8453: "Base"}
                self.status_bar.showMessage(
                    f"Switching to {chain_names.get(chain_id, chain_id)} network...", 3000
                )

                # Switch tab first, then defer connect + load so UI renders immediately
                self.tabs.setCurrentIndex(2)

                def _after_network_switch():
                    self.create_tab._connect_wallet()
                    with QMutexLocker(self._provider_mutex):
                        if self.create_tab.provider and self.create_tab.worker is None:
                            self.manage_tab.set_provider(self.create_tab.provider)
                    ids_str = ", ".join(str(tid) for tid in token_ids)
                    self.manage_tab.token_ids_input.setText(ids_str)
                    idx = self.manage_tab.scan_protocol_combo.findData(protocol)
                    if idx >= 0:
                        self.manage_tab.scan_protocol_combo.setCurrentIndex(idx)
                    if self.manage_tab.provider:
                        self.manage_tab._load_positions_by_ids(token_ids, protocol=protocol)

                QTimer.singleShot(0, _after_network_switch)
                return

        # No network switch needed — sync provider and load immediately
        with QMutexLocker(self._provider_mutex):
            if self.create_tab.provider and self.create_tab.worker is None:
                self.manage_tab.set_provider(self.create_tab.provider)

        # Set token IDs in manage tab input
        ids_str = ", ".join(str(tid) for tid in token_ids)
        self.manage_tab.token_ids_input.setText(ids_str)

        # Set protocol combo to match dashboard data (like web version sets version/dexKey)
        idx = self.manage_tab.scan_protocol_combo.findData(protocol)
        if idx >= 0:
            self.manage_tab.scan_protocol_combo.setCurrentIndex(idx)

        # Switch to Manage tab (triggers _on_tab_changed, which needs the mutex)
        self.tabs.setCurrentIndex(2)

        # Load the positions with correct protocol
        if self.manage_tab.provider:
            self.manage_tab._load_positions_by_ids(token_ids, protocol=protocol)

    def _on_new_calculator(self):
        """Reset calculator to defaults."""
        self.tabs.setCurrentIndex(3)
        self.calculator_tab.position_table.clear()
        self.calculator_tab.price_chart.clear()

    def _open_settings(self):
        """Open settings dialog."""
        dialog = SettingsDialog(self)
        if dialog.exec():
            # Settings changed - apply live without restart
            self.load_stylesheet()
            # Notify all tabs to reload settings
            for tab in [self.create_tab, self.manage_tab, self.calculator_tab]:
                if hasattr(tab, 'reload_settings'):
                    tab.reload_settings()

    def _show_about(self):
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About BNB Liquidity Ladder",
            "<h3>BNB Liquidity Ladder</h3>"
            "<p>Version 1.0.0</p>"
            "<p>A tool for creating bid-ask liquidity ladders "
            "on PancakeSwap/Uniswap V3.</p>"
            "<p>Features:</p>"
            "<ul>"
            "<li>Portfolio dashboard with PnL tracking</li>"
            "<li>Interactive position calculator</li>"
            "<li>Multiple distribution types</li>"
            "<li>Batch position creation via Multicall3</li>"
            "<li>Position management</li>"
            "</ul>"
            "<p><b>Warning:</b> Use at your own risk. "
            "Always verify transactions before signing.</p>"
        )

    def _show_docs(self):
        """Show documentation."""
        QMessageBox.information(
            self,
            "Documentation",
            "<h3>Quick Start</h3>"
            "<ol>"
            "<li><b>Dashboard:</b> Overview of your portfolio, PnL, "
            "and active positions</li>"
            "<li><b>Calculator Tab:</b> Set price, range, and distribution "
            "to preview positions</li>"
            "<li><b>Create Tab:</b> Connect wallet, configure tokens, "
            "and create real positions</li>"
            "<li><b>Manage Tab:</b> Load and manage existing positions</li>"
            "</ol>"
            "<h3>Distribution Types</h3>"
            "<ul>"
            "<li><b>Linear:</b> 1, 2, 3, 4... (equal steps)</li>"
            "<li><b>Quadratic:</b> 1, 4, 9, 16... (aggressive)</li>"
            "<li><b>Exponential:</b> Exponential growth</li>"
            "<li><b>Fibonacci:</b> 1, 1, 2, 3, 5, 8...</li>"
            "</ul>"
            "<h3>Safety Tips</h3>"
            "<ul>"
            "<li>Always preview before creating</li>"
            "<li>Start with small amounts to test</li>"
            "<li>Never share your private key</li>"
            "</ul>"
        )

    def update_connection_status(self, connected: bool, address: str = None):
        """Update connection status in status bar."""
        if connected and address:
            self.connection_label.setText(f"Connected: {address[:8]}...{address[-6:]}")
            self.connection_label.setStyleSheet("color: #00b894;")
        else:
            self.connection_label.setText("Not Connected")
            self.connection_label.setStyleSheet("color: #a0a0a0;")

    def update_network(self, network: str):
        """Update network indicator."""
        self.network_label.setText(network)
