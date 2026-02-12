"""
Calculator Tab

Interactive calculator for previewing bid-ask ladder positions.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QSpinBox, QComboBox,
    QPushButton, QRadioButton, QButtonGroup, QSplitter,
    QMessageBox, QFrame
)
from PyQt6.QtCore import Qt

from .widgets.position_table import PositionTableWidget
from .widgets.price_chart import PriceChartWidget

from src.math.distribution import (
    calculate_bid_ask_from_percent,
    calculate_bid_ask_distribution
)


class CalculatorTab(QWidget):
    """
    Calculator tab for previewing liquidity ladder positions.

    Allows users to:
    - Set current price
    - Define range (percent or absolute)
    - Configure distribution parameters
    - Preview positions in table and chart
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.positions = []
        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout(self)

        # Left side - Parameters
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_widget.setMaximumWidth(400)

        # Price Group
        price_group = QGroupBox("Price Settings")
        price_layout = QVBoxLayout(price_group)

        # Current price
        price_row = QHBoxLayout()
        price_row.addWidget(QLabel("Current Price ($):"))
        self.current_price_spin = QDoubleSpinBox()
        self.current_price_spin.setRange(0.0001, 1000000)
        self.current_price_spin.setValue(600.0)
        self.current_price_spin.setDecimals(4)
        self.current_price_spin.setPrefix("$ ")
        price_row.addWidget(self.current_price_spin)
        price_layout.addLayout(price_row)

        # Range type selection
        range_type_layout = QHBoxLayout()
        self.range_type_group = QButtonGroup(self)

        self.percent_radio = QRadioButton("Percent Range")
        self.percent_radio.setChecked(True)
        self.range_type_group.addButton(self.percent_radio)
        range_type_layout.addWidget(self.percent_radio)

        self.absolute_radio = QRadioButton("Absolute Range")
        self.range_type_group.addButton(self.absolute_radio)
        range_type_layout.addWidget(self.absolute_radio)

        price_layout.addLayout(range_type_layout)

        # Range inputs - Percent
        self.percent_frame = QFrame()
        percent_layout = QVBoxLayout(self.percent_frame)
        percent_layout.setContentsMargins(0, 0, 0, 0)

        pct_from_row = QHBoxLayout()
        pct_from_row.addWidget(QLabel("From (%):"))
        self.percent_from_spin = QDoubleSpinBox()
        self.percent_from_spin.setRange(-99, 0)
        self.percent_from_spin.setValue(-5)
        self.percent_from_spin.setSuffix(" %")
        pct_from_row.addWidget(self.percent_from_spin)
        percent_layout.addLayout(pct_from_row)

        pct_to_row = QHBoxLayout()
        pct_to_row.addWidget(QLabel("To (%):"))
        self.percent_to_spin = QDoubleSpinBox()
        self.percent_to_spin.setRange(-99, 0)
        self.percent_to_spin.setValue(-50)
        self.percent_to_spin.setSuffix(" %")
        pct_to_row.addWidget(self.percent_to_spin)
        percent_layout.addLayout(pct_to_row)

        price_layout.addWidget(self.percent_frame)

        # Range inputs - Absolute
        self.absolute_frame = QFrame()
        absolute_layout = QVBoxLayout(self.absolute_frame)
        absolute_layout.setContentsMargins(0, 0, 0, 0)

        upper_row = QHBoxLayout()
        upper_row.addWidget(QLabel("Upper Price ($):"))
        self.upper_price_spin = QDoubleSpinBox()
        self.upper_price_spin.setRange(0.0001, 1000000)
        self.upper_price_spin.setValue(570)
        self.upper_price_spin.setDecimals(2)
        self.upper_price_spin.setPrefix("$ ")
        upper_row.addWidget(self.upper_price_spin)
        absolute_layout.addLayout(upper_row)

        lower_row = QHBoxLayout()
        lower_row.addWidget(QLabel("Lower Price ($):"))
        self.lower_price_spin = QDoubleSpinBox()
        self.lower_price_spin.setRange(0.0001, 1000000)
        self.lower_price_spin.setValue(300)
        self.lower_price_spin.setDecimals(2)
        self.lower_price_spin.setPrefix("$ ")
        lower_row.addWidget(self.lower_price_spin)
        absolute_layout.addLayout(lower_row)

        price_layout.addWidget(self.absolute_frame)
        self.absolute_frame.hide()

        left_layout.addWidget(price_group)

        # Distribution Group
        dist_group = QGroupBox("Distribution Settings")
        dist_layout = QVBoxLayout(dist_group)

        # Number of positions
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Positions:"))
        self.positions_spin = QSpinBox()
        self.positions_spin.setRange(1, 20)
        self.positions_spin.setValue(7)
        pos_row.addWidget(self.positions_spin)
        dist_layout.addLayout(pos_row)

        # Total USD
        usd_row = QHBoxLayout()
        usd_row.addWidget(QLabel("Total USD:"))
        self.total_usd_spin = QDoubleSpinBox()
        self.total_usd_spin.setRange(1, 10000000)
        self.total_usd_spin.setValue(1000)
        self.total_usd_spin.setDecimals(2)
        self.total_usd_spin.setPrefix("$ ")
        usd_row.addWidget(self.total_usd_spin)
        dist_layout.addLayout(usd_row)

        # Distribution type
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Distribution:"))
        self.dist_type_combo = QComboBox()
        self.dist_type_combo.addItems([
            "Linear (1,2,3,4...)",
            "Quadratic (1,4,9,16...)",
            "Exponential",
            "Fibonacci (1,1,2,3,5...)"
        ])
        type_row.addWidget(self.dist_type_combo)
        dist_layout.addLayout(type_row)

        # Fee tier
        fee_row = QHBoxLayout()
        fee_row.addWidget(QLabel("Fee Tier:"))
        self.fee_tier_combo = QComboBox()
        self.fee_tier_combo.addItems([
            "0.05% (500) - Stable pairs",
            "0.25% (2500) - PancakeSwap",
            "0.30% (3000) - Uniswap",
            "1.00% (10000) - Exotic pairs"
        ])
        self.fee_tier_combo.setCurrentIndex(1)  # Default to 0.25%
        fee_row.addWidget(self.fee_tier_combo)
        dist_layout.addLayout(fee_row)

        left_layout.addWidget(dist_group)

        # Calculate button
        self.calculate_btn = QPushButton("Calculate Preview")
        self.calculate_btn.setObjectName("primaryButton")
        self.calculate_btn.clicked.connect(self.calculate_positions)
        left_layout.addWidget(self.calculate_btn)

        # Stretch at bottom
        left_layout.addStretch()

        # Right side - Results
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Splitter for table and chart
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Position table
        self.position_table = PositionTableWidget()
        splitter.addWidget(self.position_table)

        # Price chart
        self.price_chart = PriceChartWidget()
        splitter.addWidget(self.price_chart)

        splitter.setSizes([300, 300])
        right_layout.addWidget(splitter)

        # Add to main layout
        main_layout.addWidget(left_widget)
        main_layout.addWidget(right_widget, 1)

        # Connect signals
        self.percent_radio.toggled.connect(self._on_range_type_changed)
        self.absolute_radio.toggled.connect(self._on_range_type_changed)

    def _on_range_type_changed(self):
        """Toggle between percent and absolute range inputs."""
        if self.percent_radio.isChecked():
            self.percent_frame.show()
            self.absolute_frame.hide()
        else:
            self.percent_frame.hide()
            self.absolute_frame.show()

    def _get_distribution_type(self) -> str:
        """Get distribution type from combo box."""
        index = self.dist_type_combo.currentIndex()
        types = ["linear", "quadratic", "exponential", "fibonacci"]
        return types[index]

    def _get_fee_tier(self) -> int:
        """Get fee tier from combo box."""
        index = self.fee_tier_combo.currentIndex()
        tiers = [500, 2500, 3000, 10000]
        return tiers[index]

    def calculate_positions(self):
        """Calculate and display positions."""
        try:
            current_price = self.current_price_spin.value()
            total_usd = self.total_usd_spin.value()
            n_positions = self.positions_spin.value()
            distribution_type = self._get_distribution_type()
            fee_tier = self._get_fee_tier()

            if self.percent_radio.isChecked():
                # Percent-based range
                percent_from = self.percent_from_spin.value()
                percent_to = self.percent_to_spin.value()

                if percent_from >= percent_to:
                    percent_from, percent_to = percent_to, percent_from

                self.positions = calculate_bid_ask_from_percent(
                    current_price=current_price,
                    percent_from=percent_from,
                    percent_to=percent_to,
                    total_usd=total_usd,
                    n_positions=n_positions,
                    fee_tier=fee_tier,
                    distribution_type=distribution_type
                )
            else:
                # Absolute range
                upper_price = self.upper_price_spin.value()
                lower_price = self.lower_price_spin.value()

                if lower_price >= upper_price:
                    QMessageBox.warning(
                        self, "Invalid Range",
                        "Lower price must be less than upper price."
                    )
                    return

                if upper_price > current_price:
                    QMessageBox.warning(
                        self, "Invalid Range",
                        "Upper price cannot be greater than current price for bid-ask strategy."
                    )
                    return

                self.positions = calculate_bid_ask_distribution(
                    current_price=upper_price,
                    lower_price=lower_price,
                    total_usd=total_usd,
                    n_positions=n_positions,
                    fee_tier=fee_tier,
                    distribution_type=distribution_type
                )

            # Update displays
            self.position_table.set_positions(self.positions, current_price)
            self.price_chart.set_data(self.positions, current_price)

        except Exception as e:
            QMessageBox.critical(
                self, "Calculation Error",
                f"Failed to calculate positions:\n{str(e)}"
            )

    def get_positions(self):
        """Return calculated positions."""
        return self.positions

    def get_current_price(self) -> float:
        """Return current price."""
        return self.current_price_spin.value()
