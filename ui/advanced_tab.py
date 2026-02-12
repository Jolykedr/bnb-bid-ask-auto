"""
Advanced Tab

Tab for advanced features: custom tokens, custom pools, and pool creation.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QTextEdit, QProgressBar, QDoubleSpinBox,
    QSpinBox, QTabWidget, QFrame, QFormLayout, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QFont

import logging

from src.contracts.pool_factory import PoolFactory, TokenInfo, PoolInfo
from src.contracts.v4 import V4Protocol
from config import get_tokens_for_chain, is_stablecoin, get_chain_config

logger = logging.getLogger(__name__)


class LoadTokenWorker(QThread):
    """Worker thread for loading token info."""
    finished = pyqtSignal(bool, str, object)

    def __init__(self, factory, address):
        super().__init__()
        self.factory = factory
        self.address = address

    def run(self):
        try:
            info = self.factory.get_token_info(self.address)
            self.finished.emit(True, "Success", info)
        except Exception as e:
            self.finished.emit(False, str(e), None)


class CreatePoolWorker(QThread):
    """Worker thread for creating pools."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str, dict)

    def __init__(self, factory, token0, token1, fee, initial_price=None,
                 token0_decimals=18, token1_decimals=18):
        super().__init__()
        self.factory = factory
        self.token0 = token0
        self.token1 = token1
        self.fee = fee
        self.initial_price = initial_price
        self.token0_decimals = token0_decimals
        self.token1_decimals = token1_decimals

    def run(self):
        try:
            if self.initial_price:
                self.progress.emit("Creating and initializing pool...")
                create_tx, init_tx, pool_address = self.factory.create_and_initialize_pool(
                    self.token0, self.token1, self.fee, self.initial_price,
                    self.token0_decimals, self.token1_decimals
                )
                self.finished.emit(True, "Pool created and initialized!", {
                    'create_tx': create_tx,
                    'init_tx': init_tx,
                    'pool_address': pool_address
                })
            else:
                self.progress.emit("Creating pool...")
                tx_hash, pool_address = self.factory.create_pool(
                    self.token0, self.token1, self.fee
                )
                self.finished.emit(True, "Pool created!", {
                    'create_tx': tx_hash,
                    'pool_address': pool_address
                })
        except Exception as e:
            self.finished.emit(False, str(e), {})


class CreateV4PoolWorker(QThread):
    """Worker thread for creating Uniswap V4 pools."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str, dict)

    def __init__(self, provider, token0, token1, fee_percent, initial_price,
                 tick_spacing=None, token0_decimals=18, token1_decimals=18,
                 invert_price=True, hooks=None, protocol=V4Protocol.UNISWAP):
        super().__init__()
        self.provider = provider
        self.token0 = token0
        self.token1 = token1
        self.fee_percent = fee_percent
        self.initial_price = initial_price
        self.tick_spacing = tick_spacing
        self.token0_decimals = token0_decimals
        self.token1_decimals = token1_decimals
        self.invert_price = invert_price
        self.hooks = hooks
        self.protocol = protocol

    def run(self):
        try:
            self.progress.emit(f"Creating V4 pool ({self.protocol.value})...")
            self.progress.emit(f"Token0: {self.token0}")
            self.progress.emit(f"Token1: {self.token1}")
            self.progress.emit(f"Fee: {self.fee_percent}%")
            self.progress.emit(f"Initial Price: {self.initial_price}")

            tx_hash, pool_id, success = self.provider.create_pool_only(
                token0=self.token0,
                token1=self.token1,
                fee_percent=self.fee_percent,
                initial_price=self.initial_price,
                tick_spacing=self.tick_spacing,
                token0_decimals=self.token0_decimals,
                token1_decimals=self.token1_decimals,
                hooks=self.hooks,
                invert_price=self.invert_price
            )

            if success:
                pool_id_hex = f"0x{pool_id.hex()}" if pool_id else "N/A"
                self.finished.emit(True, "V4 Pool created successfully!", {
                    'tx_hash': tx_hash,
                    'pool_id': pool_id_hex,
                    'protocol': self.protocol.value
                })
            else:
                self.finished.emit(False, "Pool creation failed (transaction reverted)", {
                    'tx_hash': tx_hash,
                    'pool_id': f"0x{pool_id.hex()}" if pool_id else "N/A"
                })

        except Exception as e:
            logger.exception(f"Error in LoadPoolWorker: {e}")
            self.finished.emit(False, str(e), {})


class AdvancedTab(QWidget):
    """
    Advanced tab for custom tokens, pools, and pool creation.

    Features:
    - Add custom tokens by address
    - Check pool existence
    - Create new pools
    - Initialize pools with starting price
    """

    # Signal to share custom tokens with other tabs
    # Emits list of dicts: [{'symbol': ..., 'address': ..., 'decimals': ...}, ...]
    tokens_updated = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.factory = None
        self.v4_provider = None  # V4LiquidityProvider for V4 pool operations
        self.custom_tokens = {}  # address -> TokenInfo
        self.worker = None
        self.chain_id = 56  # Default BNB, updated by set_provider()
        self.settings = QSettings("BNBLiquidityLadder", "CustomTokens")
        self.setup_ui()
        self.load_saved_tokens()

    def reload_settings(self):
        """Reload settings from QSettings (called when settings dialog closes)."""
        pass  # advanced_tab reads QSettings at operation time

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Sub-tabs
        sub_tabs = QTabWidget()

        # Custom Tokens Tab
        tokens_tab = QWidget()
        self._setup_tokens_tab(tokens_tab)
        sub_tabs.addTab(tokens_tab, "Custom Tokens")

        # Pool Lookup Tab
        lookup_tab = QWidget()
        self._setup_lookup_tab(lookup_tab)
        sub_tabs.addTab(lookup_tab, "Pool Lookup")

        # Create Pool Tab (V3)
        create_tab = QWidget()
        self._setup_create_tab(create_tab)
        sub_tabs.addTab(create_tab, "Create V3 Pool")

        # Create V4 Pool Tab
        create_v4_tab = QWidget()
        self._setup_create_v4_tab(create_v4_tab)
        sub_tabs.addTab(create_v4_tab, "Create V4 Pool")

        layout.addWidget(sub_tabs)

        # Log
        log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group)

    def _setup_tokens_tab(self, tab):
        """Setup custom tokens management tab."""
        layout = QVBoxLayout(tab)

        # Add token section
        add_group = QGroupBox("Add Custom Token")
        add_layout = QVBoxLayout(add_group)

        # Address input
        addr_layout = QHBoxLayout()
        addr_layout.addWidget(QLabel("Token Address:"))
        self.token_address_input = QLineEdit()
        self.token_address_input.setPlaceholderText("0x...")
        addr_layout.addWidget(self.token_address_input)

        self.load_token_btn = QPushButton("Load Info")
        self.load_token_btn.clicked.connect(self._load_token_info)
        addr_layout.addWidget(self.load_token_btn)
        add_layout.addLayout(addr_layout)

        # Token info display
        info_frame = QFrame()
        info_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        info_layout = QFormLayout(info_frame)

        self.token_symbol_label = QLabel("-")
        self.token_name_label = QLabel("-")
        self.token_decimals_label = QLabel("-")
        self.token_supply_label = QLabel("-")

        info_layout.addRow("Symbol:", self.token_symbol_label)
        info_layout.addRow("Name:", self.token_name_label)
        info_layout.addRow("Decimals:", self.token_decimals_label)
        info_layout.addRow("Total Supply:", self.token_supply_label)

        add_layout.addWidget(info_frame)

        # Custom symbol override
        custom_layout = QHBoxLayout()
        custom_layout.addWidget(QLabel("Custom Symbol (optional):"))
        self.custom_symbol_input = QLineEdit()
        self.custom_symbol_input.setPlaceholderText("Leave empty to use detected")
        self.custom_symbol_input.setMaximumWidth(150)
        custom_layout.addWidget(self.custom_symbol_input)
        custom_layout.addStretch()

        self.add_token_btn = QPushButton("Add to List")
        self.add_token_btn.setObjectName("primaryButton")
        self.add_token_btn.clicked.connect(self._add_token)
        self.add_token_btn.setEnabled(False)
        custom_layout.addWidget(self.add_token_btn)
        add_layout.addLayout(custom_layout)

        layout.addWidget(add_group)

        # Saved tokens list
        list_group = QGroupBox("Custom Tokens List")
        list_layout = QVBoxLayout(list_group)

        self.tokens_table = QTableWidget()
        self.tokens_table.setColumnCount(5)
        self.tokens_table.setHorizontalHeaderLabels([
            "Symbol", "Name", "Address", "Decimals", "Actions"
        ])
        header = self.tokens_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.tokens_table.setColumnWidth(0, 80)
        self.tokens_table.setColumnWidth(3, 70)
        self.tokens_table.setColumnWidth(4, 80)
        list_layout.addWidget(self.tokens_table)

        # Actions
        actions_layout = QHBoxLayout()
        self.clear_tokens_btn = QPushButton("Clear All")
        self.clear_tokens_btn.clicked.connect(self._clear_tokens)
        actions_layout.addWidget(self.clear_tokens_btn)
        actions_layout.addStretch()
        list_layout.addLayout(actions_layout)

        layout.addWidget(list_group)

        # Store the loaded token info temporarily
        self._loaded_token_info = None

    def _setup_lookup_tab(self, tab):
        """Setup pool lookup tab."""
        layout = QVBoxLayout(tab)

        # Lookup section
        lookup_group = QGroupBox("Find Pool")
        lookup_layout = QVBoxLayout(lookup_group)

        # Token 0
        t0_layout = QHBoxLayout()
        t0_layout.addWidget(QLabel("Token 0:"))
        self.lookup_token0_input = QLineEdit()
        self.lookup_token0_input.setPlaceholderText("0x... or select from list")
        t0_layout.addWidget(self.lookup_token0_input)
        self.lookup_token0_combo = QComboBox()
        self.lookup_token0_combo.addItem("Custom address")
        self.lookup_token0_combo.currentIndexChanged.connect(
            lambda: self._on_token_combo_changed(0)
        )
        t0_layout.addWidget(self.lookup_token0_combo)
        lookup_layout.addLayout(t0_layout)

        # Token 1
        t1_layout = QHBoxLayout()
        t1_layout.addWidget(QLabel("Token 1:"))
        self.lookup_token1_input = QLineEdit()
        self.lookup_token1_input.setPlaceholderText("0x... or select from list")
        t1_layout.addWidget(self.lookup_token1_input)
        self.lookup_token1_combo = QComboBox()
        self.lookup_token1_combo.addItem("Custom address")
        self.lookup_token1_combo.currentIndexChanged.connect(
            lambda: self._on_token_combo_changed(1)
        )
        t1_layout.addWidget(self.lookup_token1_combo)
        lookup_layout.addLayout(t1_layout)

        # Fee tier
        fee_layout = QHBoxLayout()
        fee_layout.addWidget(QLabel("Fee Tier:"))
        self.lookup_fee_input = QDoubleSpinBox()
        self.lookup_fee_input.setRange(0.001, 10.0)
        self.lookup_fee_input.setValue(0.3)
        self.lookup_fee_input.setDecimals(4)  # 4 decimals to support fees like 3.8998%
        self.lookup_fee_input.setSuffix(" %")
        fee_layout.addWidget(self.lookup_fee_input)

        self.lookup_btn = QPushButton("Find Pool")
        self.lookup_btn.clicked.connect(self._lookup_pool)
        fee_layout.addWidget(self.lookup_btn)
        fee_layout.addStretch()
        lookup_layout.addLayout(fee_layout)

        layout.addWidget(lookup_group)

        # Pool info display
        info_group = QGroupBox("Pool Information")
        info_layout = QFormLayout(info_group)

        self.pool_address_label = QLabel("-")
        self.pool_address_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.pool_token0_label = QLabel("-")
        self.pool_token1_label = QLabel("-")
        self.pool_fee_label = QLabel("-")
        self.pool_price_label = QLabel("-")
        self.pool_liquidity_label = QLabel("-")
        self.pool_status_label = QLabel("-")

        info_layout.addRow("Pool Address:", self.pool_address_label)
        info_layout.addRow("Token 0:", self.pool_token0_label)
        info_layout.addRow("Token 1:", self.pool_token1_label)
        info_layout.addRow("Fee:", self.pool_fee_label)
        info_layout.addRow("Current Price:", self.pool_price_label)
        info_layout.addRow("Liquidity:", self.pool_liquidity_label)
        info_layout.addRow("Status:", self.pool_status_label)

        layout.addWidget(info_group)
        layout.addStretch()

    def _setup_create_tab(self, tab):
        """Setup pool creation tab."""
        layout = QVBoxLayout(tab)

        # Warning
        warning_label = QLabel(
            "Warning: Creating a pool requires gas. "
            "Make sure tokens exist and addresses are correct."
        )
        warning_label.setObjectName("warningLabel")
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)

        # Create section
        create_group = QGroupBox("Create New Pool")
        create_layout = QVBoxLayout(create_group)

        # Token 0
        t0_layout = QHBoxLayout()
        t0_layout.addWidget(QLabel("Token 0:"))
        self.create_token0_input = QLineEdit()
        self.create_token0_input.setPlaceholderText("0x...")
        t0_layout.addWidget(self.create_token0_input)
        self.create_token0_combo = QComboBox()
        self.create_token0_combo.addItem("Custom address")
        self.create_token0_combo.currentIndexChanged.connect(
            lambda: self._on_create_combo_changed(0)
        )
        t0_layout.addWidget(self.create_token0_combo)
        create_layout.addLayout(t0_layout)

        # Token 0 decimals
        t0_dec_layout = QHBoxLayout()
        t0_dec_layout.addWidget(QLabel("Token 0 Decimals:"))
        self.create_token0_decimals = QSpinBox()
        self.create_token0_decimals.setRange(0, 24)
        self.create_token0_decimals.setValue(18)
        t0_dec_layout.addWidget(self.create_token0_decimals)
        t0_dec_layout.addStretch()
        create_layout.addLayout(t0_dec_layout)

        # Token 1
        t1_layout = QHBoxLayout()
        t1_layout.addWidget(QLabel("Token 1:"))
        self.create_token1_input = QLineEdit()
        self.create_token1_input.setPlaceholderText("0x...")
        t1_layout.addWidget(self.create_token1_input)
        self.create_token1_combo = QComboBox()
        self.create_token1_combo.addItem("Custom address")
        self.create_token1_combo.currentIndexChanged.connect(
            lambda: self._on_create_combo_changed(1)
        )
        t1_layout.addWidget(self.create_token1_combo)
        create_layout.addLayout(t1_layout)

        # Token 1 decimals
        t1_dec_layout = QHBoxLayout()
        t1_dec_layout.addWidget(QLabel("Token 1 Decimals:"))
        self.create_token1_decimals = QSpinBox()
        self.create_token1_decimals.setRange(0, 24)
        self.create_token1_decimals.setValue(18)
        t1_dec_layout.addWidget(self.create_token1_decimals)
        t1_dec_layout.addStretch()
        create_layout.addLayout(t1_dec_layout)

        # Fee tier (custom)
        fee_layout = QHBoxLayout()
        fee_layout.addWidget(QLabel("Fee Tier (%):"))
        self.create_fee_input = QDoubleSpinBox()
        self.create_fee_input.setRange(0.001, 10.0)
        self.create_fee_input.setValue(0.3)
        self.create_fee_input.setDecimals(4)  # 4 decimals to support fees like 3.8998%
        self.create_fee_input.setSuffix(" %")
        fee_layout.addWidget(self.create_fee_input)

        fee_layout.addWidget(QLabel("(e.g., 0.05, 0.25, 0.3, 1.0, or custom like 3.943)"))
        fee_layout.addStretch()
        create_layout.addLayout(fee_layout)

        # Initial price (optional)
        price_layout = QHBoxLayout()
        price_layout.addWidget(QLabel("Initial Price (Token1/Token0):"))
        self.create_price_input = QDoubleSpinBox()
        self.create_price_input.setRange(0, 999999999)
        self.create_price_input.setValue(0)
        self.create_price_input.setDecimals(8)
        self.create_price_input.setSpecialValueText("Skip initialization")
        price_layout.addWidget(self.create_price_input)
        price_layout.addStretch()
        create_layout.addLayout(price_layout)

        price_hint = QLabel(
            "If price > 0, pool will be initialized with this price. "
            "Leave at 0 to create pool without initialization."
        )
        price_hint.setObjectName("subtitleLabel")
        price_hint.setWordWrap(True)
        create_layout.addWidget(price_hint)

        layout.addWidget(create_group)

        # Progress
        self.create_progress = QProgressBar()
        self.create_progress.setRange(0, 0)
        self.create_progress.hide()
        layout.addWidget(self.create_progress)

        # Create button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.create_pool_btn = QPushButton("Create Pool")
        self.create_pool_btn.setObjectName("primaryButton")
        self.create_pool_btn.clicked.connect(self._create_pool)
        btn_layout.addWidget(self.create_pool_btn)
        layout.addLayout(btn_layout)

        layout.addStretch()

    def _setup_create_v4_tab(self, tab):
        """Setup V4 pool creation tab (Uniswap V4 / PancakeSwap V4)."""
        layout = QVBoxLayout(tab)

        # Info
        info_label = QLabel(
            "<b>Uniswap V4 / PancakeSwap V4 Pool Creation</b><br>"
            "Creates and initializes a V4 pool in a single transaction.<br>"
            "V4 uses PoolManager singleton - pools don't have separate addresses."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Protocol selection
        protocol_group = QGroupBox("Protocol")
        protocol_layout = QHBoxLayout(protocol_group)

        protocol_layout.addWidget(QLabel("Select Protocol:"))
        self.v4_protocol_combo = QComboBox()
        self.v4_protocol_combo.addItem("Uniswap V4", V4Protocol.UNISWAP)
        self.v4_protocol_combo.addItem("PancakeSwap V4 (Infinity)", V4Protocol.PANCAKESWAP)
        protocol_layout.addWidget(self.v4_protocol_combo)
        protocol_layout.addStretch()

        layout.addWidget(protocol_group)

        # Token settings
        tokens_group = QGroupBox("Token Settings")
        tokens_layout = QVBoxLayout(tokens_group)

        # Token 0 (volatile)
        t0_layout = QHBoxLayout()
        t0_layout.addWidget(QLabel("Token 0 (Volatile):"))
        self.v4_token0_input = QLineEdit()
        self.v4_token0_input.setPlaceholderText("0x... (meme/volatile token)")
        t0_layout.addWidget(self.v4_token0_input)
        self.v4_token0_combo = QComboBox()
        self.v4_token0_combo.addItem("Custom address")
        self.v4_token0_combo.currentIndexChanged.connect(
            lambda: self._on_v4_token_combo_changed(0)
        )
        t0_layout.addWidget(self.v4_token0_combo)
        tokens_layout.addLayout(t0_layout)

        # Token 0 decimals
        t0_dec_layout = QHBoxLayout()
        t0_dec_layout.addWidget(QLabel("Token 0 Decimals:"))
        self.v4_token0_decimals = QSpinBox()
        self.v4_token0_decimals.setRange(0, 24)
        self.v4_token0_decimals.setValue(18)
        t0_dec_layout.addWidget(self.v4_token0_decimals)
        t0_dec_layout.addStretch()
        tokens_layout.addLayout(t0_dec_layout)

        # Token 1 (stablecoin)
        t1_layout = QHBoxLayout()
        t1_layout.addWidget(QLabel("Token 1 (Stablecoin):"))
        self.v4_token1_input = QLineEdit()
        self.v4_token1_input.setPlaceholderText("0x... (USDT/USDC)")
        t1_layout.addWidget(self.v4_token1_input)
        self.v4_token1_combo = QComboBox()
        self.v4_token1_combo.addItem("Custom address")
        self.v4_token1_combo.currentIndexChanged.connect(
            lambda: self._on_v4_token_combo_changed(1)
        )
        t1_layout.addWidget(self.v4_token1_combo)
        tokens_layout.addLayout(t1_layout)

        # Token 1 decimals
        t1_dec_layout = QHBoxLayout()
        t1_dec_layout.addWidget(QLabel("Token 1 Decimals:"))
        self.v4_token1_decimals = QSpinBox()
        self.v4_token1_decimals.setRange(0, 24)
        self.v4_token1_decimals.setValue(18)
        t1_dec_layout.addWidget(self.v4_token1_decimals)
        t1_dec_layout.addStretch()
        tokens_layout.addLayout(t1_dec_layout)

        layout.addWidget(tokens_group)

        # Pool parameters
        params_group = QGroupBox("Pool Parameters")
        params_layout = QVBoxLayout(params_group)

        # Fee percent (V4 supports custom fees 0-100%)
        fee_layout = QHBoxLayout()
        fee_layout.addWidget(QLabel("Fee (%):"))
        self.v4_fee_input = QDoubleSpinBox()
        self.v4_fee_input.setRange(0.001, 100.0)
        self.v4_fee_input.setValue(0.3)
        self.v4_fee_input.setDecimals(4)
        self.v4_fee_input.setSuffix(" %")
        self.v4_fee_input.valueChanged.connect(self._on_v4_fee_changed)  # Auto-update tick spacing
        fee_layout.addWidget(self.v4_fee_input)
        fee_layout.addWidget(QLabel("(V4 supports any fee 0-100%)"))
        fee_layout.addStretch()
        params_layout.addLayout(fee_layout)

        # Tick spacing (auto-calculated from fee)
        tick_layout = QHBoxLayout()
        tick_layout.addWidget(QLabel("Tick Spacing:"))
        self.v4_tick_spacing_input = QSpinBox()
        self.v4_tick_spacing_input.setRange(1, 8388607)  # int24 max value, min 1
        self.v4_tick_spacing_input.setValue(60)  # Default for 0.3% fee
        tick_layout.addWidget(self.v4_tick_spacing_input)
        tick_layout.addWidget(QLabel("(fee × 200)"))
        tick_layout.addStretch()
        params_layout.addLayout(tick_layout)

        # Initial price
        price_layout = QHBoxLayout()
        price_layout.addWidget(QLabel("Initial Price:"))
        self.v4_price_input = QDoubleSpinBox()
        self.v4_price_input.setRange(0.0000000001, 999999999)
        self.v4_price_input.setValue(1.0)
        self.v4_price_input.setDecimals(10)
        price_layout.addWidget(self.v4_price_input)
        params_layout.addLayout(price_layout)

        # Invert price checkbox
        invert_layout = QHBoxLayout()
        self.v4_invert_price_cb = QCheckBox("Invert Price (price is TOKEN/USD)")
        self.v4_invert_price_cb.setChecked(True)
        self.v4_invert_price_cb.setToolTip(
            "Check this if you enter price as TOKEN price in USD (e.g., 0.005 USD per token).\n"
            "Uncheck if you enter price as USD/TOKEN ratio."
        )
        invert_layout.addWidget(self.v4_invert_price_cb)
        invert_layout.addStretch()
        params_layout.addLayout(invert_layout)

        # Price hint
        price_hint = QLabel(
            "Example: If token costs 0.005 USD, enter 0.005 with 'Invert Price' checked.\n"
            "The pool will be initialized at this price."
        )
        price_hint.setObjectName("subtitleLabel")
        price_hint.setWordWrap(True)
        params_layout.addWidget(price_hint)

        # Hooks address (optional)
        hooks_layout = QHBoxLayout()
        hooks_layout.addWidget(QLabel("Hooks Address:"))
        self.v4_hooks_input = QLineEdit()
        self.v4_hooks_input.setPlaceholderText("0x0000... (optional, leave empty for no hooks)")
        hooks_layout.addWidget(self.v4_hooks_input)
        params_layout.addLayout(hooks_layout)

        layout.addWidget(params_group)

        # Progress
        self.v4_create_progress = QProgressBar()
        self.v4_create_progress.setRange(0, 0)
        self.v4_create_progress.hide()
        layout.addWidget(self.v4_create_progress)

        # Create button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.v4_create_pool_btn = QPushButton("Create V4 Pool")
        self.v4_create_pool_btn.setObjectName("primaryButton")
        self.v4_create_pool_btn.clicked.connect(self._create_v4_pool)
        btn_layout.addWidget(self.v4_create_pool_btn)
        layout.addLayout(btn_layout)

        layout.addStretch()

    def _should_invert_price(self, token0: str, token1: str) -> bool:
        """
        Determine if price inversion is needed based on stablecoin position IN THE POOL.

        IMPORTANT: In Uniswap V3/V4, pool tokens are sorted by address (lower = currency0).
        The config's token0/token1 order may NOT match the pool's currency0/currency1 order.
        We must check the stablecoin's position AFTER address sorting.

        Pool price = currency1/currency0 (sorted by address, NOT by config order).

        If stablecoin is pool's currency1 (higher address):
            - Pool price = stablecoin/token = "price of token in USD"
            - invert_price = False

        If stablecoin is pool's currency0 (lower address):
            - Pool price = token/stablecoin = "how many tokens per USD"
            - invert_price = True

        Returns:
            True if inversion is needed, False otherwise
        """
        token0_lower = token0.lower()
        token1_lower = token1.lower()

        token0_is_stable = is_stablecoin(token0)
        token1_is_stable = is_stablecoin(token1)

        if not token0_is_stable and not token1_is_stable:
            return True  # Neither is stablecoin - default

        if token0_is_stable and not token1_is_stable:
            stablecoin_addr = token0_lower
        elif token1_is_stable and not token0_is_stable:
            stablecoin_addr = token1_lower
        else:
            return True  # Both are stablecoins - default

        # Sort by address to find pool ordering (lower = currency0)
        addr0_int = int(token0_lower, 16)
        addr1_int = int(token1_lower, 16)
        pool_currency0 = token0_lower if addr0_int < addr1_int else token1_lower

        # If stablecoin is pool's currency0 (lower address) → need inversion
        return stablecoin_addr == pool_currency0

    def _update_invert_price_auto(self):
        """Auto-update invert_price checkbox based on token positions."""
        token0 = self.v4_token0_input.text().strip()
        token1 = self.v4_token1_input.text().strip()

        if token0 and token1 and token0.startswith("0x") and token1.startswith("0x"):
            invert_price = self._should_invert_price(token0, token1)
            self.v4_invert_price_cb.setChecked(invert_price)
            self._log(f"Auto-detected invert_price: {invert_price}")

    def _on_v4_fee_changed(self, fee_value: float):
        """Handle V4 fee value change - auto-update tick spacing."""
        # Formula: tick_spacing = fee_percent × 200
        tick_spacing = max(1, round(fee_value * 200))
        self.v4_tick_spacing_input.setValue(tick_spacing)

    def _on_v4_token_combo_changed(self, token_index: int):
        """Handle V4 token combo change."""
        if token_index == 0:
            combo = self.v4_token0_combo
            input_field = self.v4_token0_input
            decimals_spin = self.v4_token0_decimals
        else:
            combo = self.v4_token1_combo
            input_field = self.v4_token1_input
            decimals_spin = self.v4_token1_decimals

        if combo.currentIndex() > 0:
            address = combo.currentData()
            input_field.setText(address)

            # Try to set decimals
            if address.lower() in self.custom_tokens:
                decimals_spin.setValue(self.custom_tokens[address.lower()].decimals)
            else:
                for symbol, token in get_tokens_for_chain(self.chain_id).items():
                    if token.address.lower() == address.lower():
                        decimals_spin.setValue(token.decimals)
                        break

        # Auto-update invert_price based on tokens
        self._update_invert_price_auto()

    def _create_v4_pool(self):
        """Create a new V4 pool."""
        if not self.v4_provider:
            QMessageBox.warning(
                self, "Error",
                "V4 Provider not configured. Connect wallet first in Create tab."
            )
            return

        token0 = self.v4_token0_input.text().strip()
        token1 = self.v4_token1_input.text().strip()
        fee_percent = self.v4_fee_input.value()
        initial_price = self.v4_price_input.value()
        tick_spacing = self.v4_tick_spacing_input.value()
        token0_decimals = self.v4_token0_decimals.value()
        token1_decimals = self.v4_token1_decimals.value()
        invert_price = self.v4_invert_price_cb.isChecked()
        hooks = self.v4_hooks_input.text().strip() or None
        protocol = self.v4_protocol_combo.currentData()

        if not token0 or not token1:
            QMessageBox.warning(self, "Error", "Enter both token addresses.")
            return

        if not token0.startswith("0x") or not token1.startswith("0x"):
            QMessageBox.warning(self, "Error", "Token addresses must start with 0x")
            return

        if initial_price <= 0:
            QMessageBox.warning(self, "Error", "Initial price must be greater than 0")
            return

        # Confirm
        msg = (
            f"Create V4 Pool:\n\n"
            f"Protocol: {protocol.value}\n"
            f"Token 0: {token0[:20]}...\n"
            f"Token 1: {token1[:20]}...\n"
            f"Fee: {fee_percent}%\n"
            f"Tick Spacing: {'Auto' if tick_spacing == 0 else tick_spacing}\n"
            f"Initial Price: {initial_price}\n"
            f"Invert Price: {invert_price}"
        )

        reply = QMessageBox.question(
            self, "Confirm V4 Pool Creation", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Start worker
        self.v4_create_progress.show()
        self.v4_create_pool_btn.setEnabled(False)

        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

        self.worker = CreateV4PoolWorker(
            provider=self.v4_provider,
            token0=token0,
            token1=token1,
            fee_percent=fee_percent,
            initial_price=initial_price,
            tick_spacing=tick_spacing if tick_spacing > 0 else None,
            token0_decimals=token0_decimals,
            token1_decimals=token1_decimals,
            invert_price=invert_price,
            hooks=hooks,
            protocol=protocol
        )
        self.worker.progress.connect(self._log)
        self.worker.finished.connect(self._on_v4_pool_created)
        self.worker.start()

    def _on_v4_pool_created(self, success: bool, message: str, data: dict):
        """Handle V4 pool creation result."""
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self.v4_create_progress.hide()
        self.v4_create_pool_btn.setEnabled(True)

        if success:
            self._log(f"SUCCESS: {message}")
            self._log(f"Pool ID: {data.get('pool_id', 'N/A')}")
            self._log(f"TX: {data.get('tx_hash', 'N/A')}")
            self._log(f"Protocol: {data.get('protocol', 'N/A')}")

            QMessageBox.information(
                self, "Success",
                f"V4 Pool created successfully!\n\n"
                f"Pool ID: {data.get('pool_id', 'N/A')}\n"
                f"TX: {data.get('tx_hash', 'N/A')}"
            )
        else:
            self._log(f"FAILED: {message}")
            if data.get('tx_hash'):
                self._log(f"TX: {data.get('tx_hash')}")
            QMessageBox.critical(self, "Error", f"Failed to create V4 pool:\n{message}")

    def set_v4_provider(self, provider):
        """Set the V4 liquidity provider instance."""
        self.v4_provider = provider
        self._log("V4 Provider connected")
        self._update_v4_token_combos()

    def _update_v4_token_combos(self):
        """Update V4 token combo boxes."""
        combos = [self.v4_token0_combo, self.v4_token1_combo]

        for combo in combos:
            current = combo.currentText()
            combo.clear()
            combo.addItem("Custom address")

            # Add standard tokens
            for symbol, token in get_tokens_for_chain(self.chain_id).items():
                combo.addItem(f"{symbol} ({token.address[:8]}...)", token.address)

            # Add custom tokens
            for addr, info in self.custom_tokens.items():
                combo.addItem(f"{info.symbol} [Custom]", addr)

            # Restore selection
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def set_factory(self, factory: PoolFactory):
        """Set the pool factory instance."""
        self.factory = factory
        self._log("Factory connected")
        self._update_token_combos()

    def set_provider(self, provider):
        """Set provider and create factory from it."""
        chain_id = getattr(provider, 'chain_id', 56)
        self.chain_id = chain_id
        chain_config = get_chain_config(chain_id)
        self.factory = PoolFactory(
            provider.w3,
            provider.account,  # Pass LocalAccount, not key
            chain_config.pool_factory,
            chain_config.chain_id
        )
        self._log(f"Provider connected, factory created for chain {chain_id}")
        self._update_token_combos()

        # Also create V4 provider if this is a V4LiquidityProvider
        if hasattr(provider, 'create_pool_only'):
            self.v4_provider = provider
            self._log("V4 Provider also connected")

    def _log(self, message: str):
        """Add message to log."""
        self.log_text.append(message)

    def _load_token_info(self):
        """Load token information from address."""
        if not self.factory:
            QMessageBox.warning(self, "Error", "Connect wallet first in Create tab.")
            return

        address = self.token_address_input.text().strip()
        if not address or not address.startswith("0x"):
            QMessageBox.warning(self, "Error", "Enter a valid token address.")
            return

        self.load_token_btn.setEnabled(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self.worker = LoadTokenWorker(self.factory, address)
        self.worker.finished.connect(self._on_token_loaded)
        self.worker.start()

    def _on_token_loaded(self, success: bool, message: str, info: TokenInfo):
        """Handle token info loaded."""
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self.load_token_btn.setEnabled(True)

        if success and info:
            self._loaded_token_info = info
            self.token_symbol_label.setText(info.symbol)
            self.token_name_label.setText(info.name)
            self.token_decimals_label.setText(str(info.decimals))
            self.token_supply_label.setText(f"{info.total_supply / (10 ** info.decimals):,.4f}")
            self.add_token_btn.setEnabled(True)
            self._log(f"Loaded token: {info.symbol} ({info.name})")
        else:
            self._loaded_token_info = None
            self.add_token_btn.setEnabled(False)
            QMessageBox.warning(self, "Error", f"Failed to load token: {message}")
            self._log(f"Failed to load token: {message}")

    def _add_token(self):
        """Add token to custom list."""
        if not self._loaded_token_info:
            return

        info = self._loaded_token_info

        # Use custom symbol if provided
        custom_symbol = self.custom_symbol_input.text().strip()
        if custom_symbol:
            info = TokenInfo(
                address=info.address,
                symbol=custom_symbol,
                name=info.name,
                decimals=info.decimals,
                total_supply=info.total_supply
            )

        # Add to dict
        self.custom_tokens[info.address.lower()] = info

        # Update table
        self._refresh_tokens_table()

        # Save to settings
        self._save_tokens()

        # Emit signal (convert to list format for CreateTab)
        self.tokens_updated.emit(self._get_tokens_list())

        # Clear inputs
        self.token_address_input.clear()
        self.custom_symbol_input.clear()
        self._loaded_token_info = None
        self.add_token_btn.setEnabled(False)

        # Update combos
        self._update_token_combos()

        self._log(f"Added custom token: {info.symbol}")

    def _refresh_tokens_table(self):
        """Refresh the tokens table."""
        self.tokens_table.setRowCount(0)

        for address, info in self.custom_tokens.items():
            row = self.tokens_table.rowCount()
            self.tokens_table.insertRow(row)

            self.tokens_table.setItem(row, 0, QTableWidgetItem(info.symbol))
            self.tokens_table.setItem(row, 1, QTableWidgetItem(info.name))
            self.tokens_table.setItem(row, 2, QTableWidgetItem(f"{address[:10]}...{address[-8:]}"))
            self.tokens_table.setItem(row, 3, QTableWidgetItem(str(info.decimals)))

            # Remove button
            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda checked, addr=address: self._remove_token(addr))
            self.tokens_table.setCellWidget(row, 4, remove_btn)

    def _remove_token(self, address: str):
        """Remove token from list."""
        if address in self.custom_tokens:
            symbol = self.custom_tokens[address].symbol
            del self.custom_tokens[address]
            self._refresh_tokens_table()
            self._save_tokens()
            self._update_token_combos()
            self.tokens_updated.emit(self._get_tokens_list())
            self._log(f"Removed token: {symbol}")

    def _clear_tokens(self):
        """Clear all custom tokens."""
        reply = QMessageBox.question(
            self, "Confirm", "Remove all custom tokens?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.custom_tokens.clear()
            self._refresh_tokens_table()
            self._save_tokens()
            self._update_token_combos()
            self.tokens_updated.emit(self._get_tokens_list())
            self._log("Cleared all custom tokens")

    def _save_tokens(self):
        """Save custom tokens to settings."""
        tokens_data = {}
        for addr, info in self.custom_tokens.items():
            tokens_data[addr] = {
                'symbol': info.symbol,
                'name': info.name,
                'decimals': info.decimals,
                'total_supply': info.total_supply
            }
        self.settings.setValue("custom_tokens", tokens_data)

    def load_saved_tokens(self):
        """Load saved custom tokens."""
        tokens_data = self.settings.value("custom_tokens", {})
        if tokens_data:
            for addr, data in tokens_data.items():
                self.custom_tokens[addr] = TokenInfo(
                    address=addr,
                    symbol=data['symbol'],
                    name=data['name'],
                    decimals=data['decimals'],
                    total_supply=data['total_supply']
                )
            self._refresh_tokens_table()
            self._update_token_combos()

    def _update_token_combos(self):
        """Update all token combo boxes."""
        combos = [
            self.lookup_token0_combo, self.lookup_token1_combo,
            self.create_token0_combo, self.create_token1_combo,
            self.v4_token0_combo, self.v4_token1_combo
        ]

        for combo in combos:
            current = combo.currentText()
            combo.clear()
            combo.addItem("Custom address")

            # Add standard tokens
            for symbol, token in get_tokens_for_chain(self.chain_id).items():
                combo.addItem(f"{symbol} ({token.address[:8]}...)", token.address)

            # Add custom tokens
            for addr, info in self.custom_tokens.items():
                combo.addItem(f"{info.symbol} [Custom]", addr)

            # Restore selection
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _on_token_combo_changed(self, token_index: int):
        """Handle lookup token combo change."""
        if token_index == 0:
            combo = self.lookup_token0_combo
            input_field = self.lookup_token0_input
        else:
            combo = self.lookup_token1_combo
            input_field = self.lookup_token1_input

        if combo.currentIndex() > 0:
            address = combo.currentData()
            input_field.setText(address)

    def _on_create_combo_changed(self, token_index: int):
        """Handle create token combo change."""
        if token_index == 0:
            combo = self.create_token0_combo
            input_field = self.create_token0_input
            decimals_spin = self.create_token0_decimals
        else:
            combo = self.create_token1_combo
            input_field = self.create_token1_input
            decimals_spin = self.create_token1_decimals

        if combo.currentIndex() > 0:
            address = combo.currentData()
            input_field.setText(address)

            # Try to set decimals
            if address.lower() in self.custom_tokens:
                decimals_spin.setValue(self.custom_tokens[address.lower()].decimals)
            else:
                for symbol, token in get_tokens_for_chain(self.chain_id).items():
                    if token.address.lower() == address.lower():
                        decimals_spin.setValue(token.decimals)
                        break

    def _lookup_pool(self):
        """Look up pool information."""
        if not self.factory:
            QMessageBox.warning(self, "Error", "Connect wallet first in Create tab.")
            return

        token0 = self.lookup_token0_input.text().strip()
        token1 = self.lookup_token1_input.text().strip()
        fee_percent = self.lookup_fee_input.value()

        if not token0 or not token1:
            QMessageBox.warning(self, "Error", "Enter both token addresses.")
            return

        # Convert fee percent to basis points (use round() to avoid float precision issues)
        fee = round(fee_percent * 10000)

        try:
            pool_address = self.factory.get_pool_address(token0, token1, fee)

            if not pool_address:
                self.pool_address_label.setText("Pool not found")
                self.pool_status_label.setText("Does not exist")
                self.pool_status_label.setStyleSheet("color: #d63031;")
                self._log(f"Pool not found for fee {fee_percent}%")
                return

            # Get pool info
            info = self.factory.get_pool_info(pool_address)

            self.pool_address_label.setText(pool_address)
            self.pool_token0_label.setText(f"{info.token0[:10]}...{info.token0[-8:]}")
            self.pool_token1_label.setText(f"{info.token1[:10]}...{info.token1[-8:]}")
            self.pool_fee_label.setText(f"{info.fee / 10000}%")
            self.pool_liquidity_label.setText(f"{info.liquidity:,}")

            if info.initialized:
                price = self.factory.sqrt_price_x96_to_price(info.sqrt_price_x96)
                self.pool_price_label.setText(f"{price:.8f}")
                self.pool_status_label.setText("Initialized")
                self.pool_status_label.setStyleSheet("color: #00b894;")
            else:
                self.pool_price_label.setText("-")
                self.pool_status_label.setText("Not initialized")
                self.pool_status_label.setStyleSheet("color: #fdcb6e;")

            self._log(f"Found pool: {pool_address}")

        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            self._log(f"Lookup failed: {e}")

    def _create_pool(self):
        """Create a new pool."""
        if not self.factory:
            QMessageBox.warning(self, "Error", "Connect wallet first in Create tab.")
            return

        token0 = self.create_token0_input.text().strip()
        token1 = self.create_token1_input.text().strip()
        fee_percent = self.create_fee_input.value()
        initial_price = self.create_price_input.value()
        token0_decimals = self.create_token0_decimals.value()
        token1_decimals = self.create_token1_decimals.value()

        if not token0 or not token1:
            QMessageBox.warning(self, "Error", "Enter both token addresses.")
            return

        # Convert fee percent to basis points (use round() to avoid float precision issues)
        fee = round(fee_percent * 10000)

        # Confirm
        msg = f"Create pool:\n\nToken 0: {token0}\nToken 1: {token1}\nFee: {fee_percent}%"
        if initial_price > 0:
            msg += f"\nInitial Price: {initial_price}"

        reply = QMessageBox.question(
            self, "Confirm Pool Creation", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Start worker
        self.create_progress.show()
        self.create_pool_btn.setEnabled(False)

        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

        self.worker = CreatePoolWorker(
            self.factory, token0, token1, fee,
            initial_price if initial_price > 0 else None,
            token0_decimals, token1_decimals
        )
        self.worker.progress.connect(self._log)
        self.worker.finished.connect(self._on_pool_created)
        self.worker.start()

    def _on_pool_created(self, success: bool, message: str, data: dict):
        """Handle pool creation result."""
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self.create_progress.hide()
        self.create_pool_btn.setEnabled(True)

        if success:
            self._log(f"SUCCESS: {message}")
            self._log(f"Pool Address: {data.get('pool_address', 'N/A')}")
            self._log(f"Create TX: {data.get('create_tx', 'N/A')}")
            if 'init_tx' in data:
                self._log(f"Init TX: {data['init_tx']}")

            QMessageBox.information(
                self, "Success",
                f"Pool created successfully!\n\n"
                f"Address: {data.get('pool_address', 'N/A')}\n"
                f"TX: {data.get('create_tx', 'N/A')}"
            )
        else:
            self._log(f"FAILED: {message}")
            QMessageBox.critical(self, "Error", f"Failed to create pool:\n{message}")

    def get_custom_tokens(self) -> dict:
        """Return custom tokens dict."""
        return self.custom_tokens

    def _get_tokens_list(self) -> list:
        """Convert custom tokens to list format for CreateTab."""
        result = []
        for addr, info in self.custom_tokens.items():
            result.append({
                'symbol': info.symbol,
                'address': addr,
                'decimals': info.decimals,
                'name': info.name
            })
        return result
