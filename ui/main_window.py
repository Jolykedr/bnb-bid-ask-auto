"""
Main Window

Main application window with tabs for different functionality.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QMenuBar, QMenu,
    QStatusBar, QLabel, QMessageBox, QApplication
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QAction, QIcon

import os

from .calculator_tab import CalculatorTab
from .create_tab import CreateTab
from .manage_tab import ManageTab
from .advanced_tab import AdvancedTab
from .settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    """
    Main application window.

    Contains:
    - Menu bar with File, Settings, Help
    - Tab widget with Calculator, Create, Manage tabs
    - Status bar with connection info
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BNB Liquidity Ladder")
        self.setMinimumSize(1200, 800)

        self.settings = QSettings("BNBLiquidityLadder", "Settings")

        self.setup_ui()
        self.load_stylesheet()
        self.restore_geometry()

    def setup_ui(self):
        """Setup the main UI components."""
        # Menu bar
        self.setup_menu()

        # Central widget - Tab widget
        self.tabs = QTabWidget()

        # Calculator tab
        self.calculator_tab = CalculatorTab()
        self.tabs.addTab(self.calculator_tab, "Calculator")

        # Create tab
        self.create_tab = CreateTab()
        self.tabs.addTab(self.create_tab, "Create Position")

        # Manage tab
        self.manage_tab = ManageTab()
        self.tabs.addTab(self.manage_tab, "Manage Positions")

        # Advanced tab (custom tokens, pools, pool creation)
        self.advanced_tab = AdvancedTab()
        self.tabs.addTab(self.advanced_tab, "Advanced")

        # Connect advanced tab signals
        self.advanced_tab.tokens_updated.connect(self._on_tokens_updated)

        # Connect create tab to manage tab for new positions
        self.create_tab.positions_created.connect(self._on_positions_created)

        self.setCentralWidget(self.tabs)

        # Status bar
        self.setup_status_bar()

        # Connect tabs
        self.tabs.currentChanged.connect(self._on_tab_changed)

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

        calculator_action = QAction("Calculator", self)
        calculator_action.setShortcut("Ctrl+1")
        calculator_action.triggered.connect(lambda: self.tabs.setCurrentIndex(0))
        view_menu.addAction(calculator_action)

        create_action = QAction("Create Position", self)
        create_action.setShortcut("Ctrl+2")
        create_action.triggered.connect(lambda: self.tabs.setCurrentIndex(1))
        view_menu.addAction(create_action)

        manage_action = QAction("Manage Positions", self)
        manage_action.setShortcut("Ctrl+3")
        manage_action.triggered.connect(lambda: self.tabs.setCurrentIndex(2))
        view_menu.addAction(manage_action)

        advanced_action = QAction("Advanced", self)
        advanced_action.setShortcut("Ctrl+4")
        advanced_action.triggered.connect(lambda: self.tabs.setCurrentIndex(3))
        view_menu.addAction(advanced_action)

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
        self.save_geometry()
        event.accept()

    def _on_tab_changed(self, index):
        """Handle tab change."""
        # Sync provider between tabs
        if index == 2:  # Manage tab
            if self.create_tab.provider:
                self.manage_tab.set_provider(self.create_tab.provider)
        elif index == 3:  # Advanced tab
            if self.create_tab.provider:
                self.advanced_tab.set_provider(self.create_tab.provider)

    def _on_tokens_updated(self, tokens: list):
        """Handle custom tokens update from Advanced tab."""
        # Update Create tab with new custom tokens
        self.create_tab.update_custom_tokens(tokens)

    def _on_positions_created(self, token_ids: list):
        """Handle new positions created in Create tab."""
        # Add positions to Manage tab
        if token_ids:
            # Ensure manage tab has provider
            if self.create_tab.provider:
                self.manage_tab.set_provider(self.create_tab.provider)

            # Add the new positions
            self.manage_tab.add_positions(token_ids)

            # Show notification in status bar
            self.status_bar.showMessage(
                f"Created {len(token_ids)} new position(s): {token_ids}",
                5000
            )

    def _on_new_calculator(self):
        """Reset calculator to defaults."""
        self.tabs.setCurrentIndex(0)
        self.calculator_tab.position_table.clear()
        self.calculator_tab.price_chart.clear()

    def _open_settings(self):
        """Open settings dialog."""
        dialog = SettingsDialog(self)
        if dialog.exec():
            # Settings changed, could reload theme etc.
            self.load_stylesheet()

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
