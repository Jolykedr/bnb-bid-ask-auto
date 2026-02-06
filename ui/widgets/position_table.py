"""
Position Table Widget

Displays bid-ask ladder positions in a formatted table.
"""

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QWidget, QVBoxLayout,
    QHeaderView, QProgressBar, QHBoxLayout, QLabel
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from typing import List, Optional


class PositionTableWidget(QWidget):
    """
    Table widget for displaying liquidity positions.

    Shows: Index, Price Range, USD Amount, Percentage, Visual Bar
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def _format_price(self, price: float) -> str:
        """Format price with appropriate decimal places."""
        if price == 0:
            return "$0"
        elif price >= 1000:
            return f"${price:,.2f}"
        elif price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.6f}"
        else:
            return f"${price:.8f}"

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Summary label
        self.summary_label = QLabel()
        self.summary_label.setObjectName("subtitleLabel")
        layout.addWidget(self.summary_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "#", "Price Range", "From Current", "USD", "%", "Distribution"
        ])

        # Configure columns
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 60)

        # Disable editing
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)  # Disabled - dark theme looks better without

        layout.addWidget(self.table)

    def set_positions(self, positions: List, current_price: Optional[float] = None):
        """
        Populate table with position data.

        Args:
            positions: List of BidAskPosition objects
            current_price: Current price for calculating % from current
        """
        self.table.setRowCount(len(positions))

        total_usd = sum(p.usd_amount for p in positions)
        max_usd = max(p.usd_amount for p in positions) if positions else 1

        for row, pos in enumerate(positions):
            # Index
            index_item = QTableWidgetItem(str(pos.index + 1))
            index_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, index_item)

            # Price Range - use smart formatting for small prices
            price_range = f"{self._format_price(pos.price_lower)} - {self._format_price(pos.price_upper)}"
            self.table.setItem(row, 1, QTableWidgetItem(price_range))

            # From Current %
            if current_price:
                mid_price = (pos.price_lower + pos.price_upper) / 2
                pct_from_current = ((mid_price - current_price) / current_price) * 100
                pct_text = f"{pct_from_current:+.1f}%"
                pct_item = QTableWidgetItem(pct_text)

                # Color based on distance
                if pct_from_current > -10:
                    pct_item.setForeground(QColor("#00b894"))  # Green - close
                elif pct_from_current > -30:
                    pct_item.setForeground(QColor("#fdcb6e"))  # Yellow - medium
                else:
                    pct_item.setForeground(QColor("#e94560"))  # Red - far

                pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 2, pct_item)
            else:
                self.table.setItem(row, 2, QTableWidgetItem("-"))

            # USD Amount
            usd_item = QTableWidgetItem(f"${pos.usd_amount:,.2f}")
            usd_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, usd_item)

            # Percentage
            pct_item = QTableWidgetItem(f"{pos.percentage:.1f}%")
            pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, pct_item)

            # Distribution bar
            bar_widget = self._create_bar_widget(pos.usd_amount / max_usd * 100)
            self.table.setCellWidget(row, 5, bar_widget)

        # Update summary
        if positions:
            min_price = min(p.price_lower for p in positions)
            max_price = max(p.price_upper for p in positions)
            self.summary_label.setText(
                f"Total: ${total_usd:,.2f} | "
                f"Positions: {len(positions)} | "
                f"Range: {self._format_price(min_price)} - {self._format_price(max_price)}"
            )
        else:
            self.summary_label.setText("No positions")

    def _create_bar_widget(self, percentage: float) -> QWidget:
        """Create a progress bar widget for distribution visualization."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 2, 5, 2)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(percentage))
        bar.setTextVisible(False)
        bar.setMaximumHeight(15)

        # Style based on percentage
        if percentage > 66:
            bar.setStyleSheet("""
                QProgressBar { background-color: #0f3460; border-radius: 4px; }
                QProgressBar::chunk { background-color: #e94560; border-radius: 4px; }
            """)
        elif percentage > 33:
            bar.setStyleSheet("""
                QProgressBar { background-color: #0f3460; border-radius: 4px; }
                QProgressBar::chunk { background-color: #fdcb6e; border-radius: 4px; }
            """)
        else:
            bar.setStyleSheet("""
                QProgressBar { background-color: #0f3460; border-radius: 4px; }
                QProgressBar::chunk { background-color: #00b894; border-radius: 4px; }
            """)

        layout.addWidget(bar)
        return widget

    def clear(self):
        """Clear all positions from the table."""
        self.table.setRowCount(0)
        self.summary_label.setText("No positions")
