"""
Price Chart Widget

Visual representation of the bid-ask ladder on a price scale.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QLinearGradient
from typing import List, Optional


class PriceChartWidget(QWidget):
    """
    Custom widget for visualizing liquidity ladder positions.

    Draws a vertical price scale with horizontal bars representing
    liquidity at each price level.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.positions = []
        self.current_price = None
        self.setMinimumHeight(300)
        self.setMinimumWidth(200)

    def set_data(self, positions: List, current_price: Optional[float] = None):
        """
        Set position data for visualization.

        Args:
            positions: List of BidAskPosition objects
            current_price: Current market price
        """
        self.positions = positions
        self.current_price = current_price
        self.update()

    def clear(self):
        """Clear all data."""
        self.positions = []
        self.current_price = None
        self.update()

    @staticmethod
    def _format_price(price: float) -> str:
        """Format price without scientific notation, handling sub-dollar prices."""
        if price == 0:
            return "0"
        abs_price = abs(price)
        if abs_price >= 1000:
            return f"{price:,.0f}"
        elif abs_price >= 1:
            return f"{price:,.2f}"
        elif abs_price >= 0.0001:
            return f"{price:.6f}".rstrip('0').rstrip('.')
        else:
            return f"{price:.10f}".rstrip('0').rstrip('.')

    def paintEvent(self, event):
        """Custom paint event for drawing the chart."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        painter.fillRect(self.rect(), QColor("#16213e"))

        if not self.positions:
            # Draw placeholder text
            painter.setPen(QColor("#606070"))
            painter.setFont(QFont("Segoe UI", 12))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Calculate positions to see visualization"
            )
            return

        # Margins
        margin_left = 80
        margin_right = 20
        margin_top = 40
        margin_bottom = 40

        chart_width = self.width() - margin_left - margin_right
        chart_height = self.height() - margin_top - margin_bottom

        # Calculate price range
        min_price = min(p.price_lower for p in self.positions)
        max_price = max(p.price_upper for p in self.positions)

        if self.current_price:
            max_price = max(max_price, self.current_price * 1.05)

        price_range = max_price - min_price
        if price_range == 0:
            price_range = 1

        # Calculate max USD for scaling bars
        max_usd = max(p.usd_amount for p in self.positions)

        # Draw title
        painter.setPen(QColor("#e94560"))
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        painter.drawText(10, 25, "Liquidity Distribution")

        # Draw price axis
        painter.setPen(QPen(QColor("#0f3460"), 2))
        painter.drawLine(
            margin_left, margin_top,
            margin_left, self.height() - margin_bottom
        )

        # Draw price labels
        painter.setPen(QColor("#a0a0a0"))
        painter.setFont(QFont("Segoe UI", 9))

        num_labels = 6
        for i in range(num_labels):
            price = max_price - (i / (num_labels - 1)) * price_range
            y = margin_top + (i / (num_labels - 1)) * chart_height

            # Price label (smart formatting for sub-dollar prices)
            painter.drawText(5, int(y + 4), f"${self._format_price(price)}")

            # Grid line
            painter.setPen(QPen(QColor("#0f3460"), 1, Qt.PenStyle.DotLine))
            painter.drawLine(margin_left, int(y), self.width() - margin_right, int(y))
            painter.setPen(QColor("#a0a0a0"))

        # Draw current price line if available
        if self.current_price and min_price <= self.current_price <= max_price:
            y_current = margin_top + ((max_price - self.current_price) / price_range) * chart_height

            painter.setPen(QPen(QColor("#00b894"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(margin_left, int(y_current), self.width() - margin_right, int(y_current))

            # Current price label
            painter.setPen(QColor("#00b894"))
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(
                self.width() - margin_right - 100, int(y_current - 5),
                f"Current: ${self._format_price(self.current_price)}"
            )

        # Draw position bars
        bar_spacing = 3

        for pos in self.positions:
            # Calculate Y positions
            y_top = margin_top + ((max_price - pos.price_upper) / price_range) * chart_height
            y_bottom = margin_top + ((max_price - pos.price_lower) / price_range) * chart_height

            # Calculate bar width based on USD amount
            bar_width = (pos.usd_amount / max_usd) * (chart_width - 20)

            # Create gradient for bar
            gradient = QLinearGradient(margin_left + 5, y_top, margin_left + 5 + bar_width, y_top)
            gradient.setColorAt(0, QColor("#e94560"))
            gradient.setColorAt(1, QColor("#0f3460"))

            # Draw bar
            rect = QRectF(
                margin_left + 5,
                y_top + bar_spacing,
                bar_width,
                max(y_bottom - y_top - bar_spacing * 2, 10)
            )

            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 4, 4)

            # Draw USD label on bar
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 8))

            label_text = f"${pos.usd_amount:,.0f}"
            text_y = (y_top + y_bottom) / 2 + 4

            if bar_width > 60:
                # Draw inside bar
                painter.drawText(int(margin_left + 10), int(text_y), label_text)
            else:
                # Draw outside bar
                painter.drawText(int(margin_left + bar_width + 10), int(text_y), label_text)

        # Draw legend
        legend_y = self.height() - 20
        painter.setFont(QFont("Segoe UI", 9))

        painter.setPen(QColor("#a0a0a0"))
        painter.drawText(margin_left, legend_y, "Bar width = USD amount | Height = Price range")
