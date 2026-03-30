"""
Dashboard Tab

Portfolio overview: stats cards, cumulative PnL chart, active pairs, recent trades.
Mirrors the web version's DashboardPage.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QGridLayout, QScrollArea,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush,
    QLinearGradient, QPainterPath,
)

import time
import datetime
import logging

from src.storage.pnl_store import (
    get_dashboard_stats, get_cumulative_pnl, get_recent_trades,
    get_open_positions, remove_open_positions,
    get_latest_snapshots_bulk,
)
from src.math.apr import calc_aggregate_apr

logger = logging.getLogger(__name__)

# ── Colors ────────────────────────────────────────────────
GREEN = "#00b894"
RED = "#ff6b6b"
YELLOW = "#fdcb6e"
BLUE = "#3b82f6"
PURPLE = "#a855f7"
TEXT = "#ffffff"
TEXT_DIM = "#a0a0a0"
TEXT_MUTED = "#606070"
CARD_BG = "#16213e"
CARD_BORDER = "#0f3460"
BG = "#1a1a2e"

CHAIN_NAMES = {56: "BNB", 8453: "Base", 1: "ETH"}


def _pnl_color(v: float) -> str:
    if v > 0:
        return GREEN
    if v < 0:
        return RED
    return TEXT_DIM


def _format_usd(v: float) -> str:
    sign = "" if v >= 0 else "-"
    return f"{sign}${abs(v):.2f}"


# ── Cumulative PnL Chart (QPainter) ──────────────────────

class PnlChartWidget(QWidget):
    """Custom-painted cumulative PnL line chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []  # [{date, pnl}, ...]
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, data: list):
        self._data = data
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(0, 0, w, h, QColor(CARD_BG))

        if len(self._data) < 2:
            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(QFont("Segoe UI", 11))
            msg = "No closed trades yet" if not self._data else "Need 2+ trades for chart"
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, msg)
            painter.end()
            return

        # Margins
        ml, mr, mt, mb = 55, 15, 15, 35
        cw = w - ml - mr
        ch = h - mt - mb

        if cw < 40 or ch < 40:
            painter.end()
            return

        pnl_values = [p['pnl'] for p in self._data]
        min_pnl = min(pnl_values)
        max_pnl = max(pnl_values)

        # Add padding
        spread = max_pnl - min_pnl
        if spread == 0:
            spread = abs(max_pnl) * 0.2 if max_pnl != 0 else 1
        padding = spread * 0.1
        y_min = min_pnl - padding
        y_max = max_pnl + padding

        def to_x(i):
            return ml + (i / (len(self._data) - 1)) * cw

        def to_y(v):
            return mt + ch - ((v - y_min) / (y_max - y_min)) * ch

        # Grid lines (horizontal)
        painter.setPen(QPen(QColor("#1e293b"), 1, Qt.PenStyle.DashLine))
        n_grid = 4
        for i in range(n_grid + 1):
            y_val = y_min + (y_max - y_min) * i / n_grid
            y_px = to_y(y_val)
            painter.drawLine(QPointF(ml, y_px), QPointF(w - mr, y_px))
            # Y-axis label
            painter.setPen(QColor(TEXT_DIM))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(
                QRectF(0, y_px - 8, ml - 5, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"${y_val:.0f}",
            )
            painter.setPen(QPen(QColor("#1e293b"), 1, Qt.PenStyle.DashLine))

        # Zero line
        if y_min < 0 < y_max:
            y_zero = to_y(0)
            painter.setPen(QPen(QColor("#475569"), 1, Qt.PenStyle.DashDotLine))
            painter.drawLine(QPointF(ml, y_zero), QPointF(w - mr, y_zero))

        # X-axis labels (show ~5 evenly spaced dates)
        painter.setPen(QColor(TEXT_DIM))
        painter.setFont(QFont("Segoe UI", 8))
        n_labels = min(5, len(self._data))
        for i in range(n_labels):
            idx = int(i * (len(self._data) - 1) / max(n_labels - 1, 1))
            x_px = to_x(idx)
            ts = self._data[idx]['date']
            dt = datetime.datetime.fromtimestamp(ts)
            label = dt.strftime("%m-%d")
            painter.drawText(
                QRectF(x_px - 25, h - mb + 5, 50, 20),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                label,
            )

        # Build path
        path = QPainterPath()
        points = []
        for i, d in enumerate(self._data):
            px = to_x(i)
            py = to_y(d['pnl'])
            points.append(QPointF(px, py))

        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)

        # Line color based on final PnL
        final_pnl = pnl_values[-1]
        line_color = QColor(GREEN if final_pnl >= 0 else RED)

        # Fill gradient under the line
        fill_path = QPainterPath(path)
        fill_path.lineTo(QPointF(points[-1].x(), mt + ch))
        fill_path.lineTo(QPointF(points[0].x(), mt + ch))
        fill_path.closeSubpath()

        gradient = QLinearGradient(0, mt, 0, mt + ch)
        fill_color = QColor(line_color)
        fill_color.setAlpha(40)
        gradient.setColorAt(0, fill_color)
        fill_color2 = QColor(line_color)
        fill_color2.setAlpha(5)
        gradient.setColorAt(1, fill_color2)
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(fill_path)

        # Draw line
        painter.setPen(QPen(line_color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # Draw dots on each point
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(line_color))
        for pt in points:
            painter.drawEllipse(pt, 3, 3)

        painter.end()


# ── Stats Card ────────────────────────────────────────────

class StatsCard(QFrame):
    """A single stats card with icon color, title, value, subtitle."""

    def __init__(self, title: str, icon_color: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"StatsCard {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER}; "
            f"border-radius: 8px; padding: 12px; }}"
        )
        self.setObjectName("StatsCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # Title row
        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        dot = QLabel("\u25cf")
        dot.setStyleSheet(f"color: {icon_color}; font-size: 14px; background: transparent; border: none;")
        title_row.addWidget(dot)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        layout.addLayout(title_row)

        # Value
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(f"color: {TEXT}; font-size: 22px; font-weight: bold; background: transparent; border: none;")
        layout.addWidget(self.value_label)

        # Subtitle
        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px; background: transparent; border: none;")
        layout.addWidget(self.sub_label)

    def set_value(self, text: str, color: str = TEXT):
        self.value_label.setText(text)
        self.value_label.setStyleSheet(
            f"color: {color}; font-size: 22px; font-weight: bold; background: transparent; border: none;"
        )

    def set_subtitle(self, text: str):
        self.sub_label.setText(text)


# ── Dashboard Tab ─────────────────────────────────────────

class DashboardTab(QWidget):
    """Portfolio overview dashboard."""

    # Emitted when user clicks an active pair; carries (token_ids, protocol, chain_id)
    pair_clicked = pyqtSignal(list, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._positions_data = {}  # mirror of manage_tab's positions_data
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 10, 15, 10)
        main_layout.setSpacing(12)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setStyleSheet(f"color: {TEXT}; font-size: 18px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedWidth(90)
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        main_layout.addLayout(header)

        # ── Stats Cards Row ──
        cards_layout = QGridLayout()
        cards_layout.setSpacing(10)

        self.card_pnl = StatsCard("Realized PnL", GREEN)
        self.card_winrate = StatsCard("Win Rate", YELLOW)
        self.card_open = StatsCard("Open Positions", BLUE)
        self.card_invested = StatsCard("Open Invested", PURPLE)

        cards_layout.addWidget(self.card_pnl, 0, 0)
        cards_layout.addWidget(self.card_winrate, 0, 1)
        cards_layout.addWidget(self.card_open, 0, 2)
        cards_layout.addWidget(self.card_invested, 0, 3)
        main_layout.addLayout(cards_layout)

        # ── Middle Row: Chart + Active Pairs ──
        mid_layout = QHBoxLayout()
        mid_layout.setSpacing(12)

        # Chart (3/5)
        chart_frame = QFrame()
        chart_frame.setStyleSheet(
            f"QFrame {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 8px; }}"
        )
        chart_vbox = QVBoxLayout(chart_frame)
        chart_vbox.setContentsMargins(12, 10, 12, 10)

        chart_title = QLabel("Cumulative PnL")
        chart_title.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px; font-weight: bold; border: none;")
        chart_vbox.addWidget(chart_title)

        self.pnl_chart = PnlChartWidget()
        chart_vbox.addWidget(self.pnl_chart)

        mid_layout.addWidget(chart_frame, 3)

        # Active Pairs (2/5)
        pairs_frame = QFrame()
        pairs_frame.setStyleSheet(
            f"QFrame {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 8px; }}"
        )
        pairs_vbox = QVBoxLayout(pairs_frame)
        pairs_vbox.setContentsMargins(12, 10, 12, 10)

        self.pairs_title = QLabel("Active Pairs")
        self.pairs_title.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px; font-weight: bold; border: none;")
        pairs_vbox.addWidget(self.pairs_title)

        self.pairs_container = QVBoxLayout()
        self.pairs_container.setSpacing(6)
        self._pairs_placeholder = QLabel("No open positions")
        self._pairs_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pairs_placeholder.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; padding: 30px; border: none;")
        self.pairs_container.addWidget(self._pairs_placeholder)
        self.pairs_container.addStretch()
        pairs_vbox.addLayout(self.pairs_container)

        mid_layout.addWidget(pairs_frame, 2)
        main_layout.addLayout(mid_layout, 1)

        # ── Recent Trades Table ──
        trades_frame = QFrame()
        trades_frame.setStyleSheet(
            f"QFrame {{ background-color: {CARD_BG}; border: 1px solid {CARD_BORDER}; border-radius: 8px; }}"
        )
        trades_vbox = QVBoxLayout(trades_frame)
        trades_vbox.setContentsMargins(12, 10, 12, 10)

        trades_title = QLabel("Recent Trades")
        trades_title.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px; font-weight: bold; border: none;")
        trades_vbox.addWidget(trades_title)

        self.trades_table = QTableWidget()
        self.trades_table.setColumnCount(7)
        self.trades_table.setHorizontalHeaderLabels([
            "Pair", "Positions", "Invested", "Received", "PnL", "%", "Date"
        ])
        self.trades_table.horizontalHeader().setStretchLastSection(True)
        self.trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.trades_table.verticalHeader().setVisible(False)
        self.trades_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.trades_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.trades_table.setMaximumHeight(200)
        trades_vbox.addWidget(self.trades_table)

        self.no_trades_label = QLabel("No closed trades yet. Create your first ladder!")
        self.no_trades_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_trades_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; padding: 20px; border: none;")
        trades_vbox.addWidget(self.no_trades_label)

        main_layout.addWidget(trades_frame)

    def refresh(self):
        """Reload all dashboard data."""
        self._load_saved_positions()
        self._load_stats()
        self._load_chart()
        self._load_recent_trades()
        self._load_active_pairs()

    def _load_saved_positions(self):
        """Load persisted open positions from SQLite (survives app restart).

        SQLite is the source of truth — closed positions are removed from DB
        by manage_tab, so replacing in-memory data ensures stale entries disappear.
        """
        try:
            saved = get_open_positions()
            # Replace in-memory data with DB state (authoritative after restart
            # and after close operations that call remove_open_positions)
            self._positions_data = saved if saved else {}
        except Exception as e:
            logger.warning(f"Failed to load saved positions: {e}")

    def update_positions_data(self, positions_data: dict):
        """Merge incoming positions into cached data.

        - New/updated positions are added or overwritten.
        - Positions with liquidity 0 (closed) are removed.
        - Previously loaded positions from other protocols are preserved.
        """
        if not positions_data:
            return
        for tid, pos in positions_data.items():
            if isinstance(pos, dict) and pos.get('liquidity', 0) > 0:
                self._positions_data[tid] = pos
            else:
                self._positions_data.pop(tid, None)
        self._load_active_pairs()
        self._load_open_stats()

    def _load_stats(self):
        """Load aggregate stats from PnL store."""
        try:
            stats = get_dashboard_stats()
            pnl = stats['total_realized_pnl']
            self.card_pnl.set_value(_format_usd(pnl), _pnl_color(pnl))
            self.card_pnl.set_subtitle(f"{stats['total_trades']} trades")

            self.card_winrate.set_value(f"{stats['win_rate']}%")
            wins = stats['wins']
            losses = stats['losses']
            self.card_winrate.set_subtitle(
                f"{wins}W / {losses}L"
            )
        except Exception as e:
            logger.warning(f"Failed to load dashboard stats: {e}")

    def _load_open_stats(self):
        """Update open positions / invested cards from live position data."""
        from config import STABLECOINS, is_stablecoin
        from src.math.liquidity import calc_usd_from_liquidity

        open_count = 0
        total_invested = 0.0

        for tid, pos in self._positions_data.items():
            if not isinstance(pos, dict):
                continue
            liq = pos.get('liquidity', 0)
            if liq <= 0:
                continue
            open_count += 1

            # Calculate USD value
            tick_lower = pos.get('tick_lower', 0)
            tick_upper = pos.get('tick_upper', 0)
            current_tick = pos.get('current_tick', None)
            dec0 = pos.get('token0_decimals', 18)
            dec1 = pos.get('token1_decimals', 18)
            t0 = pos.get('token0', '').lower()
            t1 = pos.get('token1', '').lower()

            try:
                raw_price = pos.get('current_price', 0)
                if raw_price and raw_price > 0 and tick_lower < tick_upper:
                    t0_is_stable = t0 in STABLECOINS
                    human_price = (1 / raw_price) if t0_is_stable else raw_price
                    usd = calc_usd_from_liquidity(
                        tick_lower, tick_upper, liq, human_price,
                        t0, t1, dec0, dec1, cur_tick=current_tick,
                    )
                    if usd:
                        total_invested += usd
            except Exception:
                pass

        # Count unique pairs
        pairs_set = set()
        for pos in self._positions_data.values():
            if isinstance(pos, dict) and pos.get('liquidity', 0) > 0:
                t0 = pos.get('token0', '')
                t1 = pos.get('token1', '')
                if t0 and t1:
                    pairs_set.add((t0.lower(), t1.lower()))

        self.card_open.set_value(str(open_count))
        self.card_open.set_subtitle(f"in {len(pairs_set)} pairs")

        self.card_invested.set_value(f"${total_invested:.2f}")
        self.card_invested.set_subtitle("current exposure")

    def _load_chart(self):
        """Load cumulative PnL chart data."""
        try:
            data = get_cumulative_pnl()
            self.pnl_chart.set_data(data)
        except Exception as e:
            logger.warning(f"Failed to load PnL chart: {e}")

    def _load_recent_trades(self):
        """Load recent trades into the table."""
        try:
            trades = get_recent_trades(limit=10)

            if not trades:
                self.trades_table.hide()
                self.no_trades_label.show()
                return

            self.no_trades_label.hide()
            self.trades_table.show()
            self.trades_table.setRowCount(len(trades))

            for row, t in enumerate(trades):
                # Pair
                pair_item = QTableWidgetItem(t.pair)
                chain_name = CHAIN_NAMES.get(t.chain_id, "")
                pair_item.setToolTip(f"{t.protocol.upper()} {chain_name}")
                self.trades_table.setItem(row, 0, pair_item)

                # Positions count
                n_item = QTableWidgetItem(str(t.n_positions))
                n_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.trades_table.setItem(row, 1, n_item)

                # Invested
                inv_item = QTableWidgetItem(f"${t.invested_usd:.2f}")
                inv_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.trades_table.setItem(row, 2, inv_item)

                # Received
                rec_item = QTableWidgetItem(f"${t.received_usd:.2f}")
                rec_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.trades_table.setItem(row, 3, rec_item)

                # PnL
                pnl_item = QTableWidgetItem(_format_usd(t.pnl_usd))
                pnl_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                pnl_item.setForeground(QColor(_pnl_color(t.pnl_usd)))
                font = pnl_item.font()
                font.setBold(True)
                pnl_item.setFont(font)
                self.trades_table.setItem(row, 4, pnl_item)

                # PnL %
                pct_str = f"{'+' if t.pnl_percent >= 0 else ''}{t.pnl_percent:.1f}%"
                pct_item = QTableWidgetItem(pct_str)
                pct_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                pct_item.setForeground(QColor(_pnl_color(t.pnl_percent)))
                self.trades_table.setItem(row, 5, pct_item)

                # Date
                dt = datetime.datetime.fromtimestamp(t.closed_at)
                date_item = QTableWidgetItem(dt.strftime("%Y-%m-%d %H:%M"))
                date_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                date_item.setForeground(QColor(TEXT_DIM))
                self.trades_table.setItem(row, 6, date_item)

        except Exception as e:
            logger.warning(f"Failed to load recent trades: {e}")

    def _load_active_pairs(self):
        """Build active pairs list from live positions data."""
        from config import STABLECOINS
        from src.math.liquidity import calc_usd_from_liquidity

        # Clear existing pair widgets
        while self.pairs_container.count() > 0:
            item = self.pairs_container.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Group positions by pair
        pairs = {}  # (t0, t1) -> {count, invested, sym0, sym1, protocol, token_ids}
        for tid, pos in self._positions_data.items():
            if not isinstance(pos, dict) or pos.get('liquidity', 0) <= 0:
                continue
            t0 = pos.get('token0', '').lower()
            t1 = pos.get('token1', '').lower()
            key = (t0, t1)

            if key not in pairs:
                pairs[key] = {
                    'sym0': pos.get('token0_symbol', t0[:6]),
                    'sym1': pos.get('token1_symbol', t1[:6]),
                    'protocol': pos.get('protocol', 'v3'),
                    'chain_id': pos.get('chain_id', 56),
                    'count': 0,
                    'usd': 0.0,
                    'token_ids': [],
                }
            pairs[key]['count'] += 1
            pairs[key]['token_ids'].append(tid)

            # Approximate USD
            tick_lower = pos.get('tick_lower', 0)
            tick_upper = pos.get('tick_upper', 0)
            current_tick = pos.get('current_tick', None)
            dec0 = pos.get('token0_decimals', 18)
            dec1 = pos.get('token1_decimals', 18)
            liq = pos.get('liquidity', 0)

            if tick_lower < tick_upper and liq > 0:
                try:
                    raw_price = pos.get('current_price', 0)
                    if raw_price and raw_price > 0:
                        t0_is_stable = t0 in STABLECOINS
                        human_price = (1 / raw_price) if t0_is_stable else raw_price
                        usd = calc_usd_from_liquidity(
                            tick_lower, tick_upper, liq, human_price,
                            t0, t1, dec0, dec1, cur_tick=current_tick,
                        )
                        if usd:
                            pairs[key]['usd'] += usd
                except Exception:
                    pass

        if not pairs:
            placeholder = QLabel("No open positions")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; padding: 30px; border: none;")
            self.pairs_container.addWidget(placeholder)
            self.pairs_container.addStretch()
            self.pairs_title.setText("Active Pairs")
            return

        self.pairs_title.setText(f"Active Pairs ({len(pairs)})")

        # Fetch APR snapshots for all active token_ids
        all_token_ids = []
        for info in pairs.values():
            all_token_ids.extend(info['token_ids'])
        all_snapshots = get_latest_snapshots_bulk(all_token_ids) if all_token_ids else {}

        for (t0, t1), info in pairs.items():
            token_ids = info['token_ids']
            protocol = info['protocol']
            chain_id = info['chain_id']

            # Calculate aggregate APR for this pair
            pair_apr_map = {}
            pair_value_map = {}
            for tid in token_ids:
                snap = all_snapshots.get(tid)
                if snap:
                    pair_apr_map[tid] = snap.smoothed_daily_apr
                    pair_value_map[tid] = snap.position_value_usd
            pair_apr = calc_aggregate_apr(pair_apr_map, pair_value_map)

            # Row container: [delete_btn] [pair_btn]
            row_widget = QWidget()
            row_widget.setStyleSheet("background: transparent; border: none;")
            row_hlayout = QHBoxLayout(row_widget)
            row_hlayout.setContentsMargins(0, 0, 0, 0)
            row_hlayout.setSpacing(4)

            # Delete button on the left
            del_btn = QPushButton("\u2715")
            del_btn.setFixedSize(22, 22)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; "
                f"font-size: 11px; border: none; border-radius: 11px; }}"
                f"QPushButton:hover {{ color: {RED}; background-color: rgba(255,107,107,0.15); }}"
            )
            del_btn.setToolTip("Remove from dashboard")
            del_btn.clicked.connect(lambda checked, ids=token_ids: self._remove_pair(ids))
            row_hlayout.addWidget(del_btn)

            # Pair button
            row_btn = QPushButton()
            row_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            row_btn.setStyleSheet(
                f"QPushButton {{ background-color: {BG}; border: 1px solid {CARD_BORDER}; "
                f"border-radius: 6px; padding: 8px 12px; text-align: left; }}"
                f"QPushButton:hover {{ border-color: #3b82f6; background-color: #1f3a5f; }}"
            )
            row_btn.clicked.connect(lambda checked, ids=token_ids, proto=protocol, cid=chain_id: self.pair_clicked.emit(ids, proto, cid))

            row_layout = QHBoxLayout(row_btn)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(8)

            # Pair name
            pair_label = QLabel(f"{info['sym0']}/{info['sym1']}")
            pair_label.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: bold; background: transparent; border: none;")
            pair_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row_layout.addWidget(pair_label)

            # Protocol + chain badge
            chain_name = CHAIN_NAMES.get(chain_id, str(chain_id))
            proto_lbl = QLabel(f"{info['protocol'].upper()} | {chain_name}")
            proto_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px; background: transparent; border: none;")
            proto_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row_layout.addWidget(proto_lbl)

            row_layout.addStretch()

            # Position count + USD + APR
            right_text = f"{info['count']} pos  |  ${info['usd']:.2f}"
            if pair_apr is not None:
                right_text += f"  |  APR: {pair_apr:.1f}%/d"
            right_lbl = QLabel(right_text)
            right_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px; background: transparent; border: none;")
            right_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row_layout.addWidget(right_lbl)

            # Arrow
            arrow_lbl = QLabel("\u203a")
            arrow_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 16px; background: transparent; border: none;")
            arrow_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row_layout.addWidget(arrow_lbl)

            row_hlayout.addWidget(row_btn, 1)

            self.pairs_container.addWidget(row_widget)

        self.pairs_container.addStretch()

    def _remove_pair(self, token_ids: list):
        """Remove a pair's positions from dashboard and SQLite."""
        # Remove from in-memory data
        for tid in token_ids:
            self._positions_data.pop(tid, None)
        # Remove from SQLite
        remove_open_positions(token_ids)
        # Rebuild UI
        self._load_active_pairs()
        self._load_open_stats()
