"""
Settings Dialog

Dialog for configuring application settings.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox,
    QPushButton, QCheckBox, QTabWidget, QWidget,
    QMessageBox
)
from PyQt6.QtCore import Qt, QSettings


class SettingsDialog(QDialog):
    """
    Settings dialog for configuring application preferences.

    Settings include:
    - Default RPC URL
    - Default slippage
    - Gas settings
    - Theme preferences
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        self.settings = QSettings("BNBLiquidityLadder", "Settings")
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()

        # Network Tab
        network_tab = QWidget()
        network_layout = QVBoxLayout(network_tab)

        network_group = QGroupBox("Default Network Settings")
        network_group_layout = QVBoxLayout(network_group)

        # Default RPC URL
        rpc_row = QHBoxLayout()
        rpc_row.addWidget(QLabel("Default RPC URL:"))
        self.rpc_input = QLineEdit()
        self.rpc_input.setPlaceholderText("https://bsc-dataseed.binance.org/")
        rpc_row.addWidget(self.rpc_input)
        network_group_layout.addLayout(rpc_row)

        # Default network
        network_row = QHBoxLayout()
        network_row.addWidget(QLabel("Default Network:"))
        self.network_combo = QComboBox()
        self.network_combo.addItems(["BNB Mainnet", "Ethereum Mainnet", "Base Mainnet"])
        self.network_combo.currentIndexChanged.connect(self.on_network_changed)
        network_row.addWidget(self.network_combo)
        network_row.addWidget(QLabel(""))
        network_group_layout.addLayout(network_row)

        network_layout.addWidget(network_group)
        network_layout.addStretch()
        tabs.addTab(network_tab, "Network")

        # Transaction Tab
        tx_tab = QWidget()
        tx_layout = QVBoxLayout(tx_tab)

        tx_group = QGroupBox("Transaction Settings")
        tx_group_layout = QVBoxLayout(tx_group)

        # Default slippage
        slip_row = QHBoxLayout()
        slip_row.addWidget(QLabel("Default Slippage:"))
        self.slippage_spin = QDoubleSpinBox()
        self.slippage_spin.setRange(0.1, 5.0)
        self.slippage_spin.setValue(0.5)
        self.slippage_spin.setSuffix(" %")
        slip_row.addWidget(self.slippage_spin)
        slip_row.addStretch()
        tx_group_layout.addLayout(slip_row)

        # Gas multiplier
        gas_row = QHBoxLayout()
        gas_row.addWidget(QLabel("Gas Limit Multiplier:"))
        self.gas_multiplier_spin = QDoubleSpinBox()
        self.gas_multiplier_spin.setRange(1.0, 2.0)
        self.gas_multiplier_spin.setValue(1.2)
        self.gas_multiplier_spin.setSingleStep(0.1)
        gas_row.addWidget(self.gas_multiplier_spin)
        gas_row.addStretch()
        tx_group_layout.addLayout(gas_row)

        # Gas limit override
        gas_limit_row = QHBoxLayout()
        gas_limit_row.addWidget(QLabel("Gas Limit Override:"))
        self.gas_limit_spin = QDoubleSpinBox()
        self.gas_limit_spin.setRange(0, 10000000)
        self.gas_limit_spin.setValue(0)
        self.gas_limit_spin.setDecimals(0)
        self.gas_limit_spin.setSingleStep(100000)
        self.gas_limit_spin.setSpecialValueText("Auto")
        self.gas_limit_spin.setToolTip("0 = auto-estimate gas. Set manually for congested networks (e.g. 500000)")
        gas_limit_row.addWidget(self.gas_limit_spin)
        gas_limit_row.addStretch()
        tx_group_layout.addLayout(gas_limit_row)

        # Transaction timeout
        timeout_row = QHBoxLayout()
        timeout_row.addWidget(QLabel("Transaction Timeout:"))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(60, 600)
        self.timeout_spin.setValue(300)
        self.timeout_spin.setSuffix(" sec")
        timeout_row.addWidget(self.timeout_spin)
        timeout_row.addStretch()
        tx_group_layout.addLayout(timeout_row)

        # Max price impact
        impact_row = QHBoxLayout()
        impact_row.addWidget(QLabel("Max Price Impact:"))
        self.price_impact_spin = QDoubleSpinBox()
        self.price_impact_spin.setRange(0, 50.0)
        self.price_impact_spin.setValue(5.0)
        self.price_impact_spin.setSuffix(" %")
        self.price_impact_spin.setSpecialValueText("Disabled")
        self.price_impact_spin.setToolTip("Max allowed price impact for swaps (0 = disabled). Blocks swaps if pool price would move too much.")
        impact_row.addWidget(self.price_impact_spin)
        impact_row.addStretch()
        tx_group_layout.addLayout(impact_row)

        # Simulate first checkbox
        self.simulate_check = QCheckBox("Always simulate before executing")
        self.simulate_check.setChecked(True)
        tx_group_layout.addWidget(self.simulate_check)

        tx_layout.addWidget(tx_group)
        tx_layout.addStretch()
        tabs.addTab(tx_tab, "Transactions")

        # Calculator Tab
        calc_tab = QWidget()
        calc_layout = QVBoxLayout(calc_tab)

        calc_group = QGroupBox("Calculator Defaults")
        calc_group_layout = QVBoxLayout(calc_group)

        # Default distribution
        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel("Default Distribution:"))
        self.dist_combo = QComboBox()
        self.dist_combo.addItems(["Linear", "Quadratic", "Exponential", "Fibonacci"])
        dist_row.addWidget(self.dist_combo)
        calc_group_layout.addLayout(dist_row)

        # Default fee tier
        fee_row = QHBoxLayout()
        fee_row.addWidget(QLabel("Default Fee Tier:"))
        self.fee_combo = QComboBox()
        self.fee_combo.addItems(["0.05%", "0.25%", "0.30%", "1.00%"])
        self.fee_combo.setCurrentIndex(1)
        fee_row.addWidget(self.fee_combo)
        calc_group_layout.addLayout(fee_row)

        # Default positions
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Default Positions:"))
        self.positions_spin = QSpinBox()
        self.positions_spin.setRange(1, 20)
        self.positions_spin.setValue(7)
        pos_row.addWidget(self.positions_spin)
        pos_row.addStretch()
        calc_group_layout.addLayout(pos_row)

        calc_layout.addWidget(calc_group)
        calc_layout.addStretch()
        tabs.addTab(calc_tab, "Calculator")

        # Appearance Tab
        appearance_tab = QWidget()
        appearance_layout = QVBoxLayout(appearance_tab)

        appearance_group = QGroupBox("Appearance")
        appearance_group_layout = QVBoxLayout(appearance_group)

        # Theme selection
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark (Default)", "Light"])
        theme_row.addWidget(self.theme_combo)
        appearance_group_layout.addLayout(theme_row)

        # Font size
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Font Size:"))
        self.font_combo = QComboBox()
        self.font_combo.addItems(["Small", "Medium", "Large"])
        self.font_combo.setCurrentIndex(1)
        font_row.addWidget(self.font_combo)
        appearance_group_layout.addLayout(font_row)

        appearance_layout.addWidget(appearance_group)
        appearance_layout.addStretch()
        tabs.addTab(appearance_tab, "Appearance")

        # OKX DEX Tab
        okx_tab = QWidget()
        okx_layout = QVBoxLayout(okx_tab)

        okx_group = QGroupBox("OKX DEX API Settings")
        okx_group_layout = QVBoxLayout(okx_group)

        # API Key
        api_key_row = QHBoxLayout()
        api_key_row.addWidget(QLabel("API Key:"))
        self.okx_api_key_input = QLineEdit()
        self.okx_api_key_input.setPlaceholderText("Enter your OKX DEX API key")
        self.okx_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        api_key_row.addWidget(self.okx_api_key_input)
        okx_group_layout.addLayout(api_key_row)

        # Secret Key
        secret_row = QHBoxLayout()
        secret_row.addWidget(QLabel("Secret Key:"))
        self.okx_secret_input = QLineEdit()
        self.okx_secret_input.setPlaceholderText("Enter your OKX DEX secret key")
        self.okx_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        secret_row.addWidget(self.okx_secret_input)
        okx_group_layout.addLayout(secret_row)

        # Passphrase
        pass_row = QHBoxLayout()
        pass_row.addWidget(QLabel("Passphrase:"))
        self.okx_passphrase_input = QLineEdit()
        self.okx_passphrase_input.setPlaceholderText("Enter your OKX DEX passphrase")
        self.okx_passphrase_input.setEchoMode(QLineEdit.EchoMode.Password)
        pass_row.addWidget(self.okx_passphrase_input)
        okx_group_layout.addLayout(pass_row)

        # Project ID (optional)
        project_row = QHBoxLayout()
        project_row.addWidget(QLabel("Project ID:"))
        self.okx_project_input = QLineEdit()
        self.okx_project_input.setPlaceholderText("Optional project ID")
        project_row.addWidget(self.okx_project_input)
        okx_group_layout.addLayout(project_row)

        # Default swap slippage
        swap_slip_row = QHBoxLayout()
        swap_slip_row.addWidget(QLabel("Swap Slippage:"))
        self.okx_slippage_spin = QDoubleSpinBox()
        self.okx_slippage_spin.setRange(0.1, 50.0)
        self.okx_slippage_spin.setValue(1.0)
        self.okx_slippage_spin.setSuffix(" %")
        self.okx_slippage_spin.setToolTip("Slippage for auto-sell swaps")
        swap_slip_row.addWidget(self.okx_slippage_spin)
        swap_slip_row.addStretch()
        okx_group_layout.addLayout(swap_slip_row)

        # Info label
        info_label = QLabel(
            "Get API keys at: web3.okx.com\n"
            "Email dexapi@okx.com for API access.\n\n"
            "Used for auto-selling tokens when closing positions."
        )
        info_label.setStyleSheet("color: #888; font-size: 11px;")
        okx_group_layout.addWidget(info_label)

        okx_layout.addWidget(okx_group)
        okx_layout.addStretch()
        tabs.addTab(okx_tab, "OKX DEX")

        layout.addWidget(tabs)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self.reset_defaults)
        button_layout.addWidget(reset_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def on_network_changed(self, index):
        """Auto-update RPC URL when network changes."""
        rpc_urls = {
            0: "https://bsc-dataseed.binance.org/",              # BNB Mainnet
            1: "https://eth.llamarpc.com",                        # Ethereum Mainnet
            2: "https://rpc.ankr.com/base/1677373bc1c6f2038245f65cc3cddd165531f1e95dd90d9aad42d0c4e494d40d",  # Base Mainnet
        }
        if index in rpc_urls:
            self.rpc_input.setText(rpc_urls[index])

    def load_settings(self):
        """Load settings from QSettings."""
        self.rpc_input.setText(
            self.settings.value("network/rpc_url", "https://bsc-dataseed.binance.org/")
        )
        self.network_combo.setCurrentIndex(
            self.settings.value("network/default_network", 0, type=int)
        )
        self.slippage_spin.setValue(
            self.settings.value("tx/slippage", 0.5, type=float)
        )
        self.gas_multiplier_spin.setValue(
            self.settings.value("tx/gas_multiplier", 1.2, type=float)
        )
        self.gas_limit_spin.setValue(
            self.settings.value("tx/gas_limit_override", 0, type=float)
        )
        self.timeout_spin.setValue(
            self.settings.value("tx/timeout", 300, type=float)
        )
        self.simulate_check.setChecked(
            self.settings.value("tx/simulate_first", True, type=bool)
        )
        self.price_impact_spin.setValue(
            self.settings.value("tx/max_price_impact", 5.0, type=float)
        )
        self.dist_combo.setCurrentIndex(
            self.settings.value("calc/distribution", 0, type=int)
        )
        self.fee_combo.setCurrentIndex(
            self.settings.value("calc/fee_tier", 1, type=int)
        )
        self.positions_spin.setValue(
            self.settings.value("calc/positions", 7, type=int)
        )
        self.theme_combo.setCurrentIndex(
            self.settings.value("appearance/theme", 0, type=int)
        )
        self.font_combo.setCurrentIndex(
            self.settings.value("appearance/font_size", 1, type=int)
        )
        # OKX DEX settings
        self.okx_api_key_input.setText(
            self.settings.value("okx/api_key", "")
        )
        self.okx_secret_input.setText(
            self.settings.value("okx/secret_key", "")
        )
        self.okx_passphrase_input.setText(
            self.settings.value("okx/passphrase", "")
        )
        self.okx_project_input.setText(
            self.settings.value("okx/project_id", "")
        )
        self.okx_slippage_spin.setValue(
            self.settings.value("okx/slippage", 1.0, type=float)
        )

    def save_settings(self):
        """Save settings to QSettings."""
        self.settings.setValue("network/rpc_url", self.rpc_input.text())
        self.settings.setValue("network/default_network", self.network_combo.currentIndex())
        self.settings.setValue("tx/slippage", self.slippage_spin.value())
        self.settings.setValue("tx/gas_multiplier", self.gas_multiplier_spin.value())
        self.settings.setValue("tx/gas_limit_override", int(self.gas_limit_spin.value()))
        self.settings.setValue("tx/timeout", self.timeout_spin.value())
        self.settings.setValue("tx/simulate_first", self.simulate_check.isChecked())
        self.settings.setValue("tx/max_price_impact", self.price_impact_spin.value())
        self.settings.setValue("calc/distribution", self.dist_combo.currentIndex())
        self.settings.setValue("calc/fee_tier", self.fee_combo.currentIndex())
        self.settings.setValue("calc/positions", self.positions_spin.value())
        self.settings.setValue("appearance/theme", self.theme_combo.currentIndex())
        self.settings.setValue("appearance/font_size", self.font_combo.currentIndex())
        # OKX DEX settings
        self.settings.setValue("okx/api_key", self.okx_api_key_input.text())
        self.settings.setValue("okx/secret_key", self.okx_secret_input.text())
        self.settings.setValue("okx/passphrase", self.okx_passphrase_input.text())
        self.settings.setValue("okx/project_id", self.okx_project_input.text())
        self.settings.setValue("okx/slippage", self.okx_slippage_spin.value())

        self.accept()

    def reset_defaults(self):
        """Reset all settings to defaults."""
        reply = QMessageBox.question(
            self, "Reset Settings",
            "Reset all settings to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.settings.clear()
            self.load_settings()
