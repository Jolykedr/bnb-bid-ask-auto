"""
Manage Tab

Tab for managing existing liquidity positions.
Enhanced with persistence, auto-loading, PnL tracking, and price progress bars.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QTextEdit, QProgressBar, QSpinBox, QFrame,
    QStyledItemDelegate, QCheckBox, QComboBox, QDoubleSpinBox,
    QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QRect, QMutex, QMutexLocker, QTimer
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QPen, QLinearGradient, QPainterPath

import json
import time
import logging

from web3 import Web3
from config import V3_DEXES, is_stablecoin, STABLECOINS, STABLE_TOKENS
from src.math.ticks import tick_to_price
from src.math.liquidity import calculate_amounts
from src.contracts.position_manager import UniswapV3PositionManager
from src.contracts.pool_factory import PoolFactory
from src.contracts.v4 import V4PositionManager, V4Protocol
from src.contracts.v4.constants import get_v4_addresses, UNISWAP_V4_ADDRESSES, PANCAKESWAP_V4_ADDRESSES
from src.contracts.v4.pool_manager import V4PoolManager
from src.liquidity_provider import LiquidityProvider
from src.dex_swap import DexSwap
from ui.swap_preview_dialog import SwapPreviewDialog

logger = logging.getLogger(__name__)


def _resolve_v4_protocol(protocol_str: str) -> V4Protocol:
    """Convert protocol string to V4Protocol enum."""
    if protocol_str == "v4_pancake":
        return V4Protocol.PANCAKESWAP
    return V4Protocol.UNISWAP


def _create_v4_position_manager(w3, account, protocol_str: str, chain_id: int = 56, proxy: dict = None) -> V4PositionManager:
    """Create V4PositionManager for the given protocol."""
    target_protocol = _resolve_v4_protocol(protocol_str)
    return V4PositionManager(
        w3,
        account=account,
        protocol=target_protocol,
        chain_id=chain_id,
        proxy=proxy
    )


def _load_v4_position_to_dict(
    w3, account, token_id: int, protocol_str: str, chain_id: int = 56,
    v4_pm: V4PositionManager = None
) -> dict:
    """
    Load a V4 position and return a dict with all fields.

    Creates V4PositionManager if not provided.
    Gets pool state, token decimals, current price.
    """
    target_protocol = _resolve_v4_protocol(protocol_str)

    if v4_pm is None:
        v4_pm = _create_v4_position_manager(w3, account, protocol_str, chain_id)

    v4_pos = v4_pm.get_position(token_id)

    position = {
        'token_id': token_id,
        'token0': v4_pos.pool_key.currency0,
        'token1': v4_pos.pool_key.currency1,
        'fee': v4_pos.pool_key.fee,
        'tick_spacing': v4_pos.pool_key.tick_spacing,
        'tick_lower': v4_pos.tick_lower,
        'tick_upper': v4_pos.tick_upper,
        'liquidity': v4_pos.liquidity,
        'tokens_owed0': 0,
        'tokens_owed1': 0,
        'protocol': protocol_str,
    }

    # Get pool state and token info
    try:
        pool_mgr = V4PoolManager(w3, protocol=target_protocol, chain_id=chain_id)
        pool_state = pool_mgr.get_pool_state(v4_pos.pool_key)
        if pool_state.initialized:
            position['current_tick'] = pool_state.tick
            for addr_field, dec_key, sym_key in [
                (v4_pos.pool_key.currency0, 'token0_decimals', 'token0_symbol'),
                (v4_pos.pool_key.currency1, 'token1_decimals', 'token1_symbol'),
            ]:
                if addr_field and addr_field != "0x0000000000000000000000000000000000000000":
                    try:
                        tc = w3.eth.contract(
                            address=Web3.to_checksum_address(addr_field),
                            abi=[
                                {"constant": True, "inputs": [], "name": "decimals",
                                 "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
                                {"constant": True, "inputs": [], "name": "symbol",
                                 "outputs": [{"name": "", "type": "string"}], "type": "function"},
                            ]
                        )
                        position[dec_key] = tc.functions.decimals().call()
                        position[sym_key] = tc.functions.symbol().call()
                    except Exception as e:
                        logger.warning(
                            f"Failed to get decimals for {addr_field} (token {token_id}): {e}. "
                            f"NOT defaulting to 18 — prices may be wrong."
                        )

            dec0 = position.get('token0_decimals', 18)
            dec1 = position.get('token1_decimals', 18)
            position['current_price'] = pool_mgr.sqrt_price_x96_to_price(
                pool_state.sqrt_price_x96, dec0, dec1
            )
    except Exception as e:
        logger.debug(f"Could not get V4 pool state for token {token_id}: {e}")

    return position


class NumericTableWidgetItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically by the value stored in UserRole."""

    def __init__(self, text: str, sort_value: float = 0.0):
        super().__init__(text)
        self.setData(Qt.ItemDataRole.UserRole, sort_value)

    def __lt__(self, other):
        if isinstance(other, QTableWidgetItem):
            my_val = self.data(Qt.ItemDataRole.UserRole)
            other_val = other.data(Qt.ItemDataRole.UserRole)
            if isinstance(my_val, (int, float)) and isinstance(other_val, (int, float)):
                return my_val < other_val
        return super().__lt__(other)


class PriceProgressDelegate(QStyledItemDelegate):
    """
    Range Progress delegate.

    The full bar represents a wider range than the position.
    The position range is shown as a highlighted segment inside.
    Current price is a vertical marker that can be outside the position.
    """

    def _format_price(self, price: float) -> str:
        if price is None or price == float('inf') or price != price:
            return "N/A"
        if price < 0:
            return "N/A"
        if price > 1e12:
            return "???"
        if price < 0.0000001:
            return f"{price:.9f}"
        if price < 0.001:
            return f"{price:.7f}"
        if price < 1:
            return f"{price:.5f}"
        if price < 100:
            return f"{price:.3f}"
        if price < 1000:
            return f"{price:.2f}"
        if price < 1000000:
            return f"{price:,.0f}"
        return f"{price:.2e}"

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(42)
        return size

    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if data is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(option.rect, option.palette.base())

        price_lower = data.get('price_lower', 0)
        price_upper = data.get('price_upper', 1)
        current_price = data.get('current_price', None)
        in_range = data.get('in_range', False)

        is_valid = True
        if price_lower is None or price_upper is None or current_price is None:
            is_valid = False
        elif price_lower <= 0 or price_upper <= 0 or current_price <= 0:
            is_valid = False
        elif price_upper == float('inf') or price_lower == float('inf'):
            is_valid = False
        elif price_upper > 1e15 or price_lower > 1e15:
            is_valid = False
        elif price_upper <= price_lower:
            is_valid = False

        rect = option.rect.adjusted(6, 2, -6, -2)
        bar_height = 12
        bar_y = rect.top() + 1

        if not is_valid:
            painter.setPen(QColor(128, 128, 128))
            font = painter.font()
            font.setPointSize(7)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No price data")
            painter.restore()
            return

        # --- Compute the visible range (wider than position) ---
        # The bar shows ~3x the position range, centered on position midpoint
        pos_range = price_upper - price_lower
        padding = pos_range * 1.0  # 1x padding on each side
        vis_min = max(0, price_lower - padding)
        vis_max = price_upper + padding

        # Make sure current_price is visible too
        if current_price < vis_min:
            vis_min = current_price - pos_range * 0.2
        if current_price > vis_max:
            vis_max = current_price + pos_range * 0.2
        vis_min = max(0, vis_min)

        vis_range = vis_max - vis_min
        if vis_range <= 0:
            vis_range = 1

        def price_to_x(p):
            frac = (p - vis_min) / vis_range
            return bar_rect.left() + int(frac * bar_rect.width())

        # --- Bar background track ---
        bar_rect_full = rect.adjusted(0, 0, 0, -(rect.height() - bar_height - 2))
        bar_rect = bar_rect_full
        bar_rect.moveTop(bar_y)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(30, 33, 43)))
        painter.drawRoundedRect(bar_rect, 4, 4)

        # --- Position range segment (highlighted) ---
        seg_left = price_to_x(price_lower)
        seg_right = price_to_x(price_upper)
        seg_left = max(bar_rect.left(), min(seg_left, bar_rect.right()))
        seg_right = max(seg_left + 2, min(seg_right, bar_rect.right()))

        if in_range:
            gradient = QLinearGradient(seg_left, 0, seg_right, 0)
            gradient.setColorAt(0, QColor(0, 184, 148))
            gradient.setColorAt(1, QColor(76, 175, 80))
        else:
            gradient = QLinearGradient(seg_left, 0, seg_right, 0)
            gradient.setColorAt(0, QColor(255, 152, 0))
            gradient.setColorAt(1, QColor(255, 107, 53))

        painter.setBrush(QBrush(gradient))
        seg_rect = QRect(seg_left, bar_rect.top() + 1, seg_right - seg_left, bar_rect.height() - 2)
        painter.drawRoundedRect(seg_rect, 3, 3)

        # --- Position range border (subtle bracket marks) ---
        pen = QPen(QColor(100, 100, 110), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        bracket_h = 3
        painter.drawLine(seg_left, bar_rect.top() - 1, seg_left, bar_rect.top() - 1 - bracket_h)
        painter.drawLine(seg_left, bar_rect.bottom() + 1, seg_left, bar_rect.bottom() + 1 + bracket_h)
        painter.drawLine(seg_right, bar_rect.top() - 1, seg_right, bar_rect.top() - 1 - bracket_h)
        painter.drawLine(seg_right, bar_rect.bottom() + 1, seg_right, bar_rect.bottom() + 1 + bracket_h)

        # --- Current price marker (vertical line + small triangle) ---
        cp_x = price_to_x(current_price)
        cp_x = max(bar_rect.left() + 1, min(cp_x, bar_rect.right() - 1))

        pen = QPen(QColor(255, 255, 255), 2)
        painter.setPen(pen)
        painter.drawLine(cp_x, bar_rect.top() - 1, cp_x, bar_rect.bottom() + 1)

        tri = QPainterPath()
        tri.moveTo(cp_x - 3, bar_rect.bottom() + 2)
        tri.lineTo(cp_x + 3, bar_rect.bottom() + 2)
        tri.lineTo(cp_x, bar_rect.bottom() + 5)
        tri.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.fillPath(tri, QBrush(QColor(255, 255, 255)))

        # --- Price labels (one row: lower | current | upper) ---
        font = painter.font()
        font.setPointSize(6)
        painter.setFont(font)

        label_y = bar_rect.bottom() + 6
        label_h = 12

        # Lower price — left-aligned
        painter.setPen(QColor(130, 130, 140))
        painter.drawText(
            rect.left(), label_y, rect.width() // 3, label_h,
            Qt.AlignmentFlag.AlignLeft,
            self._format_price(price_lower)
        )

        # Upper price — right-aligned
        painter.drawText(
            rect.right() - rect.width() // 3, label_y, rect.width() // 3, label_h,
            Qt.AlignmentFlag.AlignRight,
            self._format_price(price_upper)
        )

        # Current price — centered at marker position, colored
        if in_range:
            painter.setPen(QColor(0, 230, 180))
        else:
            painter.setPen(QColor(255, 152, 0))
        painter.drawText(
            cp_x - 30, label_y, 60, label_h,
            Qt.AlignmentFlag.AlignCenter,
            self._format_price(current_price)
        )

        painter.restore()


class LoadPositionWorker(QThread):
    """Worker thread for loading a single position."""

    position_loaded = pyqtSignal(int, dict)  # token_id, position_data
    error = pyqtSignal(int, str)  # token_id, error message
    not_owned = pyqtSignal(int, str)  # token_id, actual owner (position exists but not owned by wallet)

    def __init__(self, provider, token_id, pool_factory=None, check_ownership=True, protocol="v3"):
        super().__init__()
        self.provider = provider
        self.token_id = token_id
        self.pool_factory = pool_factory
        self.check_ownership = check_ownership
        self.protocol = protocol  # "v3" or "v4"

    def run(self):
        try:
            # Check if V4 protocol selected
            is_v4_protocol = self.protocol in ("v4", "v4_pancake")

            if is_v4_protocol:
                # Use V4 position manager
                self._load_v4_position()
                return

            # V3 position loading via multicall (2 RPC instead of ~17)
            from src.utils import BatchRPC

            # Determine position manager address
            pm_address = self.provider.position_manager_address

            if self.protocol == "v3_uniswap":
                try:
                    chain_id = getattr(self.provider, 'chain_id', 56)
                    if chain_id in V3_DEXES and "uniswap" in V3_DEXES[chain_id]:
                        uniswap_config = V3_DEXES[chain_id]["uniswap"]
                        pm_address = uniswap_config.position_manager
                        logger.debug(f"[V3 Load] Using Uniswap V3 PM: {pm_address}")
                except Exception as pm_err:
                    logger.warning(f"[V3 Load] Error resolving Uniswap V3 PM: {pm_err}")

            # Phase 1: ownerOf + positions in one multicall (2 calls → 1 RPC)
            batch1 = BatchRPC(self.provider.w3)
            batch1.add_erc721_owner_of(pm_address, self.token_id)
            batch1.add_v3_position(pm_address, self.token_id)
            results1 = batch1.execute()

            owner = results1[0]
            position = results1[1]

            if self.check_ownership:
                if owner is None:
                    self.error.emit(self.token_id, "Position does not exist (NFT burned)")
                    return
                if owner.lower() != self.provider.account.address.lower():
                    self.not_owned.emit(self.token_id, owner)
                    return

            if position is None:
                self.error.emit(self.token_id, "Failed to decode position data")
                return

            # Phase 2: pool + token data in one multicall (5-6 calls → 1 RPC)
            if self.pool_factory and position['liquidity'] > 0:
                try:
                    factory_address = self.pool_factory.factory_address
                    batch2 = BatchRPC(self.provider.w3)
                    batch2.add_pool_address(factory_address, position['token0'], position['token1'], position['fee'])
                    batch2.add_decimals(position['token0'])
                    batch2.add_decimals(position['token1'])
                    batch2.add_erc20_symbol(position['token0'])
                    batch2.add_erc20_symbol(position['token1'])
                    results2 = batch2.execute()

                    pool_addr = results2[0]
                    dec0 = results2[1] if results2[1] is not None else 18
                    dec1 = results2[2] if results2[2] is not None else 18
                    sym0 = results2[3] or '???'
                    sym1 = results2[4] or '???'

                    position['token0_decimals'] = dec0
                    position['token1_decimals'] = dec1
                    position['token0_symbol'] = sym0
                    position['token1_symbol'] = sym1

                    # Get pool price via separate slot0 call (needs pool_addr from phase 2)
                    if pool_addr:
                        batch3 = BatchRPC(self.provider.w3)
                        batch3.add_pool_slot0(pool_addr)
                        results3 = batch3.execute()
                        slot0 = results3[0]
                        if slot0 and slot0['sqrtPriceX96'] > 0:
                            current_price = self.pool_factory.sqrt_price_x96_to_price(
                                slot0['sqrtPriceX96'], dec0, dec1
                            )
                            position['current_price'] = current_price
                            position['current_tick'] = slot0['tick']
                except Exception as e:
                    logger.warning(f"Error fetching pool info via multicall: {e}")

            self.position_loaded.emit(self.token_id, position)

        except Exception as e:
            self.error.emit(self.token_id, str(e))
        except BaseException as e:
            logger.critical(f"BaseException in LoadPositionWorker: {e}", exc_info=True)

    def _load_v4_position(self):
        """Load a V4 position using shared helper."""
        try:
            if not self.provider:
                self.error.emit(self.token_id, "No provider available")
                return

            if not hasattr(self.provider, 'w3') or self.provider.w3 is None:
                self.error.emit(self.token_id, "Provider has no Web3 connection")
                return

            chain_id = getattr(self.provider, 'chain_id', 56)
            v4_pm = _create_v4_position_manager(
                self.provider.w3, self.provider.account, self.protocol, chain_id,
                proxy=getattr(self.provider, 'proxy', None)
            )

            # Check ownership
            if self.check_ownership:
                try:
                    owner = v4_pm.get_owner_of(self.token_id)
                except Exception as owner_err:
                    self.error.emit(self.token_id, f"Failed to check ownership: {str(owner_err)}")
                    return

                if owner is None:
                    self.error.emit(self.token_id, "V4 Position does not exist (NFT burned)")
                    return

                wallet_addr = self.provider.account.address if self.provider.account else None
                if not wallet_addr:
                    self.error.emit(self.token_id, "No wallet connected")
                    return

                if owner.lower() != wallet_addr.lower():
                    self.not_owned.emit(self.token_id, owner)
                    return

            # Load position data using helper
            position = _load_v4_position_to_dict(
                self.provider.w3, self.provider.account,
                self.token_id, self.protocol, chain_id, v4_pm
            )

            self.position_loaded.emit(self.token_id, position)

        except Exception as e:
            logger.error(f"[V4 Load] Failed to load position {self.token_id}: {e}", exc_info=True)
            self.error.emit(self.token_id, f"V4 Error: {str(e)}")


class ScanWalletWorker(QThread):
    """Worker thread for scanning wallet for all positions."""

    progress = pyqtSignal(str)  # progress message
    position_found = pyqtSignal(int, dict)  # token_id, position_data
    scan_result = pyqtSignal(int, list, str)  # total found, list of token_ids, protocol

    def __init__(self, provider, pool_factory=None, protocol="v3"):
        super().__init__()
        self.provider = provider
        self.pool_factory = pool_factory
        self.protocol = protocol  # "v3", "v4_uniswap", or "v4_pancake"

    def run(self):
        try:
            # Determine protocol type
            is_v4 = self.protocol in ("v4", "v4_pancake")

            if self.protocol == "v4_pancake":
                protocol_name = "PancakeSwap V4"
            elif self.protocol == "v4":
                protocol_name = "Uniswap V4"
            else:
                protocol_name = "PancakeSwap V3"

            self.progress.emit(f"Scanning wallet for {protocol_name} positions...")
            address = self.provider.account.address
            chain_id = getattr(self.provider, 'chain_id', 56)

            if is_v4:
                v4_pm = _create_v4_position_manager(
                    self.provider.w3, self.provider.account, self.protocol, chain_id,
                    proxy=getattr(self.provider, 'proxy', None)
                )

                pm_addr = getattr(v4_pm, 'position_manager_address', 'unknown')
                self.progress.emit(f"V4 Position Manager: {pm_addr}")

                token_ids = v4_pm.get_position_token_ids(address)
                self.progress.emit(f"Found {len(token_ids)} V4 positions")

                for i, token_id in enumerate(token_ids):
                    self.progress.emit(f"Loading V4 position {i+1}/{len(token_ids)} (ID: {token_id})...")
                    try:
                        position = _load_v4_position_to_dict(
                            self.provider.w3, self.provider.account,
                            token_id, self.protocol, chain_id, v4_pm
                        )
                        self.position_found.emit(token_id, position)
                    except Exception as e:
                        self.progress.emit(f"Error loading V4 position {token_id}: {e}")

                self.scan_result.emit(len(token_ids), token_ids, self.protocol)

            else:
                # Use V3 position manager
                # Check if we need a different position manager for Uniswap V3
                if self.protocol == "v3_uniswap":
                    try:
                        chain_id = getattr(self.provider, 'chain_id', 56)
                        if chain_id in V3_DEXES and "uniswap" in V3_DEXES[chain_id]:
                            uniswap_config = V3_DEXES[chain_id]["uniswap"]
                            position_manager = UniswapV3PositionManager(
                                self.provider.w3,
                                uniswap_config.position_manager,
                                self.provider.account
                            )
                            self.progress.emit(f"Using Uniswap V3 Position Manager: {uniswap_config.position_manager[:20]}...")
                        else:
                            position_manager = self.provider.position_manager
                            self.progress.emit("Warning: Uniswap V3 not configured for this chain, using default")
                    except Exception as e:
                        self.progress.emit(f"Error setting up Uniswap V3: {e}, using default")
                        position_manager = self.provider.position_manager
                    protocol_name = "Uniswap V3"
                else:
                    position_manager = self.provider.position_manager
                    protocol_name = "PancakeSwap V3"

                token_ids = position_manager.get_position_token_ids(address)
                self.progress.emit(f"Found {len(token_ids)} {protocol_name} positions")

                for i, token_id in enumerate(token_ids):
                    self.progress.emit(f"Loading {protocol_name} position {i+1}/{len(token_ids)} (ID: {token_id})...")
                    try:
                        position = position_manager.get_position(token_id)
                        position['protocol'] = self.protocol  # v3 or v3_uniswap

                        # Get pool info if available
                        if self.pool_factory and position['liquidity'] > 0:
                            try:
                                pool_addr = self.pool_factory.get_pool_address(
                                    position['token0'],
                                    position['token1'],
                                    position['fee']
                                )
                                if pool_addr:
                                    pool_info = self.pool_factory.get_pool_info(pool_addr)
                                    token0_info = self.pool_factory.get_token_info(position['token0'])
                                    token1_info = self.pool_factory.get_token_info(position['token1'])

                                    position['current_price'] = self.pool_factory.sqrt_price_x96_to_price(
                                        pool_info.sqrt_price_x96,
                                        token0_info.decimals,
                                        token1_info.decimals
                                    )
                                    position['current_tick'] = pool_info.tick
                                    position['token0_symbol'] = token0_info.symbol
                                    position['token1_symbol'] = token1_info.symbol
                                    position['token0_decimals'] = token0_info.decimals
                                    position['token1_decimals'] = token1_info.decimals
                            except Exception as e:
                                logger.warning(f"Error fetching pool info for {token_id}: {e}")

                        self.position_found.emit(token_id, position)

                    except Exception as e:
                        self.progress.emit(f"Error loading position {token_id}: {e}")

                self.scan_result.emit(len(token_ids), token_ids, self.protocol)

        except Exception as e:
            self.progress.emit(f"Scan failed: {e}")
            self.scan_result.emit(0, [], self.protocol)
        except BaseException as e:
            logger.critical(f"BaseException in ScanWalletWorker: {e}", exc_info=True)


class ClosePositionsWorker(QThread):
    """Worker thread for closing positions with optional auto-sell."""

    progress = pyqtSignal(str)
    close_result = pyqtSignal(bool, str, dict)

    def __init__(self, provider, token_ids, positions_data=None,
                 chain_id=56, initial_investment=0):
        super().__init__()
        self.provider = provider
        self.token_ids = token_ids
        self.positions_data = positions_data or {}
        self.chain_id = chain_id
        self.initial_investment = initial_investment

    def run(self):
        try:
            # Separate V3, V3 Uniswap, and V4 positions
            v3_pancake_ids = []
            v3_uniswap_ids = []
            v4_ids = []

            for tid in self.token_ids:
                pos = self.positions_data.get(tid, {})
                protocol = pos.get('protocol', '') if pos else ''
                if protocol.startswith('v4'):
                    v4_ids.append(tid)
                elif protocol == 'v3_uniswap':
                    v3_uniswap_ids.append(tid)
                else:
                    v3_pancake_ids.append(tid)

            results = []

            # Close PancakeSwap V3 positions
            if v3_pancake_ids:
                self.progress.emit(f"Closing {len(v3_pancake_ids)} PancakeSwap V3 positions...")
                try:
                    tx_hash, success, gas_used = self.provider.close_positions(
                        v3_pancake_ids,
                        timeout=300
                    )
                    results.append(('PancakeSwap V3', tx_hash, success, gas_used))
                except Exception as e:
                    self.progress.emit(f"PancakeSwap V3 close failed: {e}")
                    results.append(('PancakeSwap V3', None, False, 0))

            # Close Uniswap V3 positions
            if v3_uniswap_ids:
                self.progress.emit(f"Closing {len(v3_uniswap_ids)} Uniswap V3 positions...")
                try:
                    chain_id = getattr(self.provider, 'chain_id', 56)
                    if chain_id in V3_DEXES and "uniswap" in V3_DEXES[chain_id]:
                        uniswap_config = V3_DEXES[chain_id]["uniswap"]
                        self.progress.emit(f"Using Uniswap V3 PM: {uniswap_config.position_manager[:20]}...")

                        # Create provider with Uniswap V3 position manager
                        uniswap_provider = LiquidityProvider(
                            rpc_url=self.provider.w3.provider.endpoint_uri,
                            private_key=self.provider.account.key.hex() if hasattr(self.provider.account, 'key') else None,
                            position_manager_address=uniswap_config.position_manager,
                            chain_id=chain_id
                        )

                        tx_hash, success, gas_used = uniswap_provider.close_positions(
                            v3_uniswap_ids,
                            timeout=300
                        )
                        results.append(('Uniswap V3', tx_hash, success, gas_used))
                    else:
                        self.progress.emit("Uniswap V3 not configured for this chain")
                        results.append(('Uniswap V3', None, False, 0))
                except Exception as e:
                    self.progress.emit(f"Uniswap V3 close failed: {e}")
                    results.append(('Uniswap V3', None, False, 0))

            # Close V4 positions (protocol-aware)
            if v4_ids:
                self.progress.emit(f"Closing {len(v4_ids)} V4 positions...")
                chain_id = getattr(self.provider, 'chain_id', 56)

                # Group by protocol to create separate PMs
                v4_by_protocol = {}
                for tid in v4_ids:
                    pos = self.positions_data.get(tid, {})
                    proto = pos.get('protocol', 'v4')
                    v4_by_protocol.setdefault(proto, []).append(tid)

                for proto_str, tids in v4_by_protocol.items():
                    try:
                        v4_pm = _create_v4_position_manager(
                            self.provider.w3, self.provider.account, proto_str, chain_id,
                            proxy=getattr(self.provider, 'proxy', None)
                        )

                        for tid in tids:
                            self.progress.emit(f"Closing V4 position {tid} ({proto_str})...")
                            try:
                                pos_data = self.positions_data.get(tid, {})
                                token0 = pos_data.get('token0', '')
                                token1 = pos_data.get('token1', '')
                                liquidity = pos_data.get('liquidity', 0)

                                null_addr = "0x0000000000000000000000000000000000000000"

                                if token0 and token1 and token0 != null_addr and token1 != null_addr:
                                    tx_hash, _, _ = v4_pm.close_position_with_tokens(
                                        tid,
                                        currency0=token0,
                                        currency1=token1,
                                        liquidity=liquidity,
                                        recipient=self.provider.account.address,
                                        timeout=300
                                    )
                                else:
                                    tx_hash, _, _ = v4_pm.close_position(
                                        tid,
                                        recipient=self.provider.account.address,
                                        timeout=300
                                    )
                                results.append(('V4', tx_hash, True, 0))
                            except Exception as e:
                                self.progress.emit(f"V4 position {tid} close failed: {e}")
                                results.append(('V4', None, False, 0))

                    except Exception as e:
                        self.progress.emit(f"V4 close failed ({proto_str}): {e}")
                        results.append(('V4', None, False, 0))

            # Check overall success
            all_success = all(r[2] for r in results) if results else False
            tx_hashes = [r[1] for r in results if r[1]]

            # Build result data
            result_data = {
                'tx_hash': ', '.join(tx_hashes) if tx_hashes else 'N/A',
                'gas_used': 0,
                'initial_investment': self.initial_investment,
                'positions_data': self.positions_data,
                'token_ids': self.token_ids,
            }

            if all_success:
                self.close_result.emit(True, "Positions closed successfully", result_data)
            else:
                self.close_result.emit(False, "Some positions failed to close", result_data)

        except Exception as e:
            self.close_result.emit(False, str(e), {})
        except BaseException as e:
            logger.critical(f"BaseException in ClosePositionsWorker: {e}", exc_info=True)
            try:
                self.close_result.emit(False, f"Fatal: {e}", {})
            except Exception:
                pass

class BatchCloseWorker(QThread):
    """Worker thread for batch closing ALL V4 positions in ONE transaction."""

    progress = pyqtSignal(str)
    close_result = pyqtSignal(bool, str, dict)

    def __init__(self, provider, positions: list,
                 initial_investment=0):
        super().__init__()
        self.provider = provider
        self.positions = positions  # List of position dicts
        self.initial_investment = initial_investment
        self.chain_id = getattr(provider, 'chain_id', 56)

    def run(self):
        try:
            self.progress.emit(f"Preparing batch close for {len(self.positions)} positions...")

            w3 = self.provider.w3
            wallet = self.provider.account.address

            # Detect protocol from positions data (all positions in batch must be same protocol)
            proto_str = self.positions[0].get('protocol', 'v4') if self.positions else 'v4'
            v4_pm = _create_v4_position_manager(w3, self.provider.account, proto_str, self.chain_id,
                                                proxy=getattr(self.provider, 'proxy', None))

            self.progress.emit("Building batch transaction...")

            # Call batch close
            tx_hash, success, gas_used = v4_pm.close_positions_batch(
                self.positions,
                recipient=wallet,
                timeout=300
            )

            result_data = {
                'tx_hash': tx_hash,
                'gas_used': gas_used,
                'initial_investment': self.initial_investment,
                'positions': self.positions,
            }

            if success:
                self.close_result.emit(True, f"Closed {len(self.positions)} positions", result_data)
            else:
                self.close_result.emit(False, f"Transaction reverted. TX: {tx_hash}", result_data)

        except Exception as e:
            self.progress.emit(f"Batch close failed: {e}")
            self.close_result.emit(False, str(e), {})
        except BaseException as e:
            logger.critical(f"BaseException in BatchCloseWorker: {e}", exc_info=True)
            try:
                self.close_result.emit(False, f"Fatal: {e}", {})
            except Exception:
                pass



class SwapWorker(QThread):
    """Worker thread for executing swaps after position close (via KyberSwap/DEX)."""

    progress = pyqtSignal(str)
    swap_result = pyqtSignal(dict)  # Final results dict

    def __init__(self, w3, chain_id: int, tokens: list, private_key: str,
                 output_token: str, slippage: float = 3.0, max_price_impact: float = 5.0,
                 initial_investment: float = 0, proxy: dict = None,
                 swap_mode: str = "auto"):
        super().__init__()
        self.w3 = w3
        self.chain_id = chain_id
        self.tokens = tokens  # List of {'address', 'symbol', 'decimals', 'amount'}
        self.private_key = private_key
        self.output_token = output_token
        self.slippage = slippage
        self.max_price_impact = max_price_impact
        self.initial_investment = initial_investment
        self.proxy = proxy
        self.swap_mode = swap_mode

    def run(self):
        try:
            swapper = DexSwap(self.w3, self.chain_id, max_price_impact=self.max_price_impact, proxy=self.proxy)
            wallet = swapper.w3.eth.account.from_key(self.private_key).address
            self.progress.emit(f"Selling tokens via KyberSwap/DEX (slippage: {self.slippage}%)...")

            results = {'total_usd': 0.0, 'swaps': [], 'skipped': []}

            for token in self.tokens:
                addr = token['address']
                amount = token.get('amount', 0)

                if amount <= 0:
                    continue

                # Skip stablecoins (add to total as 1:1)
                if swapper.is_stable_token(addr):
                    usd_val = amount / (10 ** token.get('decimals', 18))
                    self.progress.emit(f"  Skipping {token['symbol']} (stablecoin) — ${usd_val:.2f}")
                    results['total_usd'] += usd_val
                    results['skipped'].append(addr)
                    continue

                self.progress.emit(f"  Selling {token['symbol']}...")

                result = swapper.swap(
                    from_token=addr,
                    to_token=self.output_token,
                    amount_in=amount,
                    wallet_address=wallet,
                    private_key=self.private_key,
                    slippage=self.slippage,
                    swap_mode=self.swap_mode
                )

                if result.success:
                    self.progress.emit(f"  ✅ {token['symbol']}: ${result.to_amount_usd:.2f}")
                    results['total_usd'] += result.to_amount_usd
                else:
                    self.progress.emit(f"  ❌ {token['symbol']}: {result.error}")

                results['swaps'].append({
                    'token': token['symbol'],
                    'success': result.success,
                    'usd': result.to_amount_usd,
                    'tx_hash': result.tx_hash,
                    'error': result.error,
                })

            # Calculate PnL
            if self.initial_investment > 0:
                pnl = results['total_usd'] - self.initial_investment
                pnl_percent = (pnl / self.initial_investment) * 100
                results['pnl'] = pnl
                results['pnl_percent'] = pnl_percent
                results['initial_investment'] = self.initial_investment

            self.progress.emit(f"Swap complete. Total: ${results['total_usd']:.2f}")
            self.swap_result.emit(results)

        except Exception as e:
            logger.error(f"SwapWorker error: {e}", exc_info=True)
            self.swap_result.emit({'total_usd': 0, 'swaps': [], 'skipped': [], 'error': str(e)})
        except BaseException as e:
            logger.critical(f"BaseException in SwapWorker: {e}", exc_info=True)
            try:
                self.swap_result.emit({'total_usd': 0, 'swaps': [], 'error': f"Fatal: {e}"})
            except Exception:
                pass


class ManageTab(QWidget):
    """
    Tab for managing existing liquidity positions.

    Features:
    - Load positions by token IDs
    - Auto-save and load positions
    - View position details with PnL and fees
    - Price progress bar showing current price in range
    - Close positions
    - Collect fees
    """

    # Signal to notify when positions are added externally
    positions_updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.provider = None
        self.pool_factory = None
        self.positions_data = {}  # token_id -> position data
        self._positions_mutex = QMutex()  # Protects positions_data access
        self.worker = None
        self.load_workers = []  # Legacy compat — now means _active_workers
        self.scan_worker = None
        self._worker_queue = []  # [(token_id, protocol)] pending work
        self._active_workers = []  # Currently running workers
        self.MAX_CONCURRENT_WORKERS = 8
        # Batch table update state
        self._pending_updates = {}  # {token_id: position} — accumulated for batch flush
        self._row_index = {}  # {token_id: row_number} — O(1) row lookup
        self.settings = QSettings("BNBLiquidityLadder", "Positions")
        self.setup_ui()
        # QTimer for batch table updates (must be created after setup_ui)
        self._update_timer = QTimer()
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(200)
        self._update_timer.timeout.connect(self._flush_table_updates)
        self._load_saved_positions()

    def reload_settings(self):
        """Reload settings from QSettings (called when settings dialog closes)."""
        pass  # manage_tab reads QSettings at operation time

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for entire tab
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_widget = QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setSpacing(10)

        # Top section - Input
        top_group = QGroupBox("Position Management")
        top_layout = QVBoxLayout(top_group)

        # Token IDs input
        ids_layout = QHBoxLayout()
        ids_layout.addWidget(QLabel("Token IDs (comma-separated):"))
        self.token_ids_input = QLineEdit()
        self.token_ids_input.setPlaceholderText("12345, 12346, 12347")
        ids_layout.addWidget(self.token_ids_input)

        self.load_btn = QPushButton("Load Positions")
        self.load_btn.clicked.connect(self._load_positions)
        ids_layout.addWidget(self.load_btn)

        self.refresh_btn = QPushButton("Refresh All")
        self.refresh_btn.clicked.connect(self._refresh_all_positions)
        ids_layout.addWidget(self.refresh_btn)

        # Protocol selector for scanning
        self.scan_protocol_combo = QComboBox()
        self.scan_protocol_combo.addItem("V4 Uniswap", "v4")
        self.scan_protocol_combo.addItem("V4 PancakeSwap", "v4_pancake")
        self.scan_protocol_combo.addItem("V3 Uniswap", "v3_uniswap")
        self.scan_protocol_combo.addItem("V3 PancakeSwap", "v3")
        self.scan_protocol_combo.setToolTip("Select which protocol to scan")
        self.scan_protocol_combo.setMinimumWidth(150)
        ids_layout.addWidget(self.scan_protocol_combo)

        self.scan_btn = QPushButton("🔍 Scan Wallet")
        self.scan_btn.setToolTip("Scan blockchain for all positions owned by this wallet")
        self.scan_btn.clicked.connect(self._scan_wallet)
        self.scan_btn.setObjectName("primaryButton")
        ids_layout.addWidget(self.scan_btn)

        top_layout.addLayout(ids_layout)

        # Filter options row
        filter_layout = QHBoxLayout()
        self.hide_empty_cb = QCheckBox("Hide empty positions (liquidity = 0 or < $0.00001)")
        self.hide_empty_cb.setChecked(True)  # Hide empty by default
        self.hide_empty_cb.toggled.connect(self._on_filter_changed)
        filter_layout.addWidget(self.hide_empty_cb)
        filter_layout.addStretch()
        top_layout.addLayout(filter_layout)

        # Or single ID input
        single_layout = QHBoxLayout()
        single_layout.addWidget(QLabel("Or enter single ID:"))
        self.single_id_spin = QSpinBox()
        self.single_id_spin.setRange(0, 999999999)
        single_layout.addWidget(self.single_id_spin)

        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self._add_single_id)
        single_layout.addWidget(self.add_btn)
        single_layout.addStretch()
        top_layout.addLayout(single_layout)

        layout.addWidget(top_group)

        # Positions table
        table_group = QGroupBox("Loaded Positions")
        table_layout = QVBoxLayout(table_group)

        self.positions_table = QTableWidget()
        self.positions_table.setColumnCount(9)
        self.positions_table.setHorizontalHeaderLabels([
            "Token ID", "Pair", "Fee",
            "Price Range", "Liquidity", "Fees Earned",
            "PnL", "Status", "Range Progress"
        ])

        header = self.positions_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)

        # Set custom delegate for progress bar column
        self.positions_table.setItemDelegateForColumn(8, PriceProgressDelegate())

        self.positions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.positions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.positions_table.setSortingEnabled(True)
        self.positions_table.setMinimumHeight(550)

        table_layout.addWidget(self.positions_table)

        # PnL summary row
        pnl_row = QHBoxLayout()
        self.pnl_summary_label = QLabel("Total: —")
        self.pnl_summary_label.setStyleSheet("color: #aaa; font-size: 12px; padding: 4px 8px;")
        pnl_row.addWidget(self.pnl_summary_label)
        pnl_row.addStretch()

        self.refresh_pnl_btn = QPushButton("Refresh PnL")
        self.refresh_pnl_btn.setToolTip("Refresh all positions and recalculate PnL")
        self.refresh_pnl_btn.clicked.connect(self._refresh_pnl)
        pnl_row.addWidget(self.refresh_pnl_btn)
        table_layout.addLayout(pnl_row)

        layout.addWidget(table_group)

        # Actions Group (separate from table to avoid overlapping)
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setSpacing(8)

        # Row 1: Close settings
        close_settings_layout = QHBoxLayout()
        close_settings_layout.addWidget(QLabel("Close Slippage:"))
        self.close_slippage_spin = QDoubleSpinBox()
        self.close_slippage_spin.setRange(0.1, 100.0)
        self.close_slippage_spin.setValue(5.0)
        self.close_slippage_spin.setSuffix(" %")
        self.close_slippage_spin.setToolTip("Max slippage when closing positions (0.1% - 100%)")
        self.close_slippage_spin.setFixedWidth(85)
        close_settings_layout.addWidget(self.close_slippage_spin)

        self.auto_sell_cb = QCheckBox("Auto-sell tokens")
        self.auto_sell_cb.setToolTip(
            "Automatically sell received tokens (except USDT/USDC/BNB/ETH)\n"
            "via PancakeSwap/Uniswap Router"
        )
        self.auto_sell_cb.setChecked(False)
        close_settings_layout.addWidget(self.auto_sell_cb)

        self.skip_preview_cb = QCheckBox("Skip preview")
        self.skip_preview_cb.setToolTip(
            "Skip swap preview dialog and sell immediately"
        )
        self.skip_preview_cb.setChecked(False)
        close_settings_layout.addWidget(self.skip_preview_cb)

        close_settings_layout.addWidget(QLabel("Swap Slip:"))
        self.swap_slippage_spin = QDoubleSpinBox()
        self.swap_slippage_spin.setRange(0.5, 50.0)
        self.swap_slippage_spin.setValue(3.0)
        self.swap_slippage_spin.setSuffix(" %")
        self.swap_slippage_spin.setToolTip("Slippage for auto-sell swaps (0.5% - 50%)")
        self.swap_slippage_spin.setFixedWidth(75)
        close_settings_layout.addWidget(self.swap_slippage_spin)

        close_settings_layout.addWidget(QLabel("Max Impact:"))
        self.max_impact_spin = QDoubleSpinBox()
        self.max_impact_spin.setRange(0, 50.0)
        self.max_impact_spin.setValue(5.0)
        self.max_impact_spin.setSuffix(" %")
        self.max_impact_spin.setSpecialValueText("Off")
        self.max_impact_spin.setToolTip("Max price impact for swaps (0 = disabled)")
        self.max_impact_spin.setFixedWidth(75)
        close_settings_layout.addWidget(self.max_impact_spin)

        close_settings_layout.addWidget(QLabel("Initial $:"))
        self.initial_investment_spin = QDoubleSpinBox()
        self.initial_investment_spin.setRange(0, 10000000)
        self.initial_investment_spin.setValue(0)
        self.initial_investment_spin.setDecimals(2)
        self.initial_investment_spin.setToolTip(
            "Enter your initial investment to calculate PnL after closing positions"
        )
        self.initial_investment_spin.setFixedWidth(85)
        close_settings_layout.addWidget(self.initial_investment_spin)

        close_settings_layout.addWidget(QLabel("Swap DEX:"))
        self.swap_mode_combo = QComboBox()
        self.swap_mode_combo.addItem("Авто (Kyber→V2→V3)", "auto")
        self.swap_mode_combo.addItem("KyberSwap", "kyber")
        self.swap_mode_combo.addItem("V2", "v2")
        self.swap_mode_combo.addItem("V3", "v3")
        self.swap_mode_combo.setCurrentIndex(0)
        self.swap_mode_combo.setToolTip("Метод свапа: Авто = Kyber→V2→V3 по приоритету")
        self.swap_mode_combo.setFixedWidth(150)
        close_settings_layout.addWidget(self.swap_mode_combo)

        close_settings_layout.addStretch()
        actions_layout.addLayout(close_settings_layout)

        # Row 2: Action buttons
        action_layout = QHBoxLayout()

        self.close_selected_btn = QPushButton("Close Selected")
        self.close_selected_btn.setObjectName("dangerButton")
        self.close_selected_btn.clicked.connect(self._close_selected)
        self.close_selected_btn.setEnabled(False)
        action_layout.addWidget(self.close_selected_btn)

        self.close_all_btn = QPushButton("Close All")
        self.close_all_btn.setObjectName("dangerButton")
        self.close_all_btn.clicked.connect(self._close_all)
        self.close_all_btn.setEnabled(False)
        action_layout.addWidget(self.close_all_btn)

        self.batch_close_btn = QPushButton("🚀 Close All V4 (1 TX)")
        self.batch_close_btn.setToolTip("Close all V4 positions in a single transaction (gas efficient)")
        self.batch_close_btn.setObjectName("dangerButton")
        self.batch_close_btn.clicked.connect(self._batch_close_all)
        self.batch_close_btn.setEnabled(False)
        action_layout.addWidget(self.batch_close_btn)

        self.remove_selected_btn = QPushButton("Remove from List")
        self.remove_selected_btn.clicked.connect(self._remove_selected)
        action_layout.addWidget(self.remove_selected_btn)

        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self._clear_list)
        action_layout.addWidget(self.clear_btn)

        action_layout.addStretch()
        actions_layout.addLayout(action_layout)

        layout.addWidget(actions_group)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Log
        log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self.log_text)

        layout.addWidget(log_group)

        # Status
        self.status_label = QLabel("Connect wallet in Create tab to manage positions")
        self.status_label.setObjectName("subtitleLabel")
        layout.addWidget(self.status_label)

        # Finish scroll area setup
        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)

    def set_provider(self, provider):
        """Set the liquidity provider instance."""
        # Skip full reload if same provider is already set
        if provider and provider is self.provider:
            return
        self.provider = provider
        if provider:
            # Detect V4 provider and its specific protocol
            is_v4 = hasattr(provider, 'create_pool_only')
            if is_v4:
                provider_protocol = getattr(provider, 'protocol', V4Protocol.UNISWAP)

                if provider_protocol == V4Protocol.PANCAKESWAP:
                    self.status_label.setText("Ready to manage PancakeSwap V4 positions")
                    self._log("PancakeSwap V4 Provider connected")
                    idx = self.scan_protocol_combo.findData("v4_pancake")
                else:
                    self.status_label.setText("Ready to manage Uniswap V4 positions")
                    self._log("Uniswap V4 Provider connected")
                    idx = self.scan_protocol_combo.findData("v4")

                if idx >= 0:
                    self.scan_protocol_combo.setCurrentIndex(idx)
            else:
                # V3 provider - detect if Uniswap V3 or PancakeSwap V3
                v3_dex_name = "V3"
                v3_protocol_data = "v3"  # default to PancakeSwap V3

                try:
                    chain_id = getattr(provider, 'chain_id', 56)
                    pm_address = getattr(provider, 'position_manager_address', None)

                    if pm_address and chain_id in V3_DEXES:
                        pm_lower = pm_address.lower()
                        for dex_name, dex_config in V3_DEXES[chain_id].items():
                            if dex_config.position_manager.lower() == pm_lower:
                                v3_dex_name = dex_config.name
                                if "uniswap" in dex_name.lower():
                                    v3_protocol_data = "v3_uniswap"
                                break
                except Exception as detect_err:
                    logger.warning(f"Could not detect V3 DEX: {detect_err}")

                self.status_label.setText(f"Ready to manage {v3_dex_name} positions")
                self._log(f"{v3_dex_name} Provider connected")

                # Set correct V3 protocol in combo
                idx = self.scan_protocol_combo.findData(v3_protocol_data)
                if idx >= 0:
                    self.scan_protocol_combo.setCurrentIndex(idx)

            # Create pool factory for fetching current prices (V3 only, but harmless for V4)
            try:
                self.pool_factory = PoolFactory(
                    provider.w3,
                    provider.account,
                    chain_id=provider.chain_id
                )
            except Exception as e:
                self._log(f"Note: PoolFactory not initialized (OK for V4): {e}")
                self.pool_factory = None

            # Auto-load saved positions on connect
            self._refresh_all_positions()
        else:
            self.status_label.setText("Connect wallet in Create tab to manage positions")
            self.pool_factory = None

    def add_positions(self, token_ids: list):
        """
        Add positions to the list (called externally when new positions are created).

        Args:
            token_ids: List of new token IDs to add
        """
        # Add to input field
        current_text = self.token_ids_input.text().strip()
        new_ids_str = ", ".join(str(tid) for tid in token_ids)

        if current_text:
            self.token_ids_input.setText(f"{current_text}, {new_ids_str}")
        else:
            self.token_ids_input.setText(new_ids_str)

        # Add to positions data (will be populated when loaded)
        for token_id in token_ids:
            if token_id not in self.positions_data:
                self.positions_data[token_id] = None  # Placeholder

        # Save and reload
        self._save_positions()

        if self.provider:
            self._load_positions_by_ids(token_ids)

        self._log(f"Added new positions: {token_ids}")
        self.positions_updated.emit()

    def _log(self, message: str):
        """Add message to log."""
        self.log_text.append(message)

    def _save_positions(self):
        """Save positions to QSettings including protocol info."""
        # Save list of token IDs (for backward compatibility)
        token_ids = list(self.positions_data.keys())
        self.settings.setValue("token_ids", json.dumps(token_ids))

        # Save position protocols - minimal data to restore protocol on reload
        positions_protocols = {}
        for tid, pos in self.positions_data.items():
            if pos and isinstance(pos, dict):
                protocol = pos.get('protocol', 'v3')
                positions_protocols[str(tid)] = protocol

        self.settings.setValue("positions_protocols", json.dumps(positions_protocols))
        self._log(f"Saved {len(token_ids)} positions to storage")

    def _load_saved_positions(self):
        """Load saved positions from QSettings."""
        try:
            saved_ids = self.settings.value("token_ids", "[]")
            token_ids = json.loads(saved_ids)

            # Load saved protocols
            saved_protocols = self.settings.value("positions_protocols", "{}")
            positions_protocols = json.loads(saved_protocols)

            if token_ids:
                self.token_ids_input.setText(", ".join(str(tid) for tid in token_ids))
                for token_id in token_ids:
                    # Initialize with protocol info if available
                    protocol = positions_protocols.get(str(token_id), None)
                    if protocol:
                        self.positions_data[token_id] = {'protocol': protocol}
                    else:
                        self.positions_data[token_id] = None
                self._log(f"Loaded {len(token_ids)} saved position IDs")
        except Exception as e:
            self._log(f"Error loading saved positions: {e}")

    def _add_single_id(self):
        """Add single token ID to the list."""
        token_id = self.single_id_spin.value()
        if token_id > 0:
            current_text = self.token_ids_input.text().strip()
            if current_text:
                self.token_ids_input.setText(f"{current_text}, {token_id}")
            else:
                self.token_ids_input.setText(str(token_id))
            self._log(f"Added token ID: {token_id}")

    def _parse_token_ids(self) -> list:
        """Parse token IDs from input."""
        text = self.token_ids_input.text().strip()
        if not text:
            return []

        try:
            ids = [int(x.strip()) for x in text.split(",") if x.strip()]
            return list(set(ids))  # Remove duplicates
        except ValueError:
            return []

    def _load_positions(self):
        """Load position details from blockchain."""
        if not self.provider:
            QMessageBox.warning(
                self, "Error",
                "Please connect wallet in the Create tab first."
            )
            return

        token_ids = self._parse_token_ids()
        if not token_ids:
            QMessageBox.warning(self, "Error", "Please enter valid token IDs.")
            return

        self._load_positions_by_ids(token_ids)

    def _cancel_load_workers(self):
        """Cancel all running load workers and clear queue."""
        # Clear pending queue first
        self._worker_queue.clear()

        # Disconnect signals and schedule deletion — do NOT block UI with wait()
        for w in self._active_workers:
            try:
                w.position_loaded.disconnect()
                w.error.disconnect()
                w.not_owned.disconnect()
                w.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            # Connect finished to deleteLater so cleanup happens when thread actually ends
            try:
                w.finished.connect(w.deleteLater)
            except (TypeError, RuntimeError):
                pass
            # Non-blocking: quit() for event-loop threads; run()-based threads
            # will finish naturally. Do NOT wait() on UI thread.
            if w.isRunning():
                w.quit()
        self._active_workers.clear()
        self.load_workers.clear()  # keep in sync

    def _load_positions_by_ids(self, token_ids: list, protocol: str = None, cancel_existing: bool = True):
        """Load specific positions by their IDs using a worker queue."""
        if cancel_existing:
            self._cancel_load_workers()

        try:
            self.progress_bar.show()

            if protocol is None:
                protocol = self.scan_protocol_combo.currentData()
            if protocol is None:
                protocol = "v3"

            if protocol and protocol.startswith("v4"):
                protocol_name = "V4 PancakeSwap" if protocol == "v4_pancake" else "V4 Uniswap"
            elif protocol == "v3_uniswap":
                protocol_name = "V3 Uniswap"
            else:
                protocol_name = "V3 PancakeSwap"
            self._log(f"Loading {len(token_ids)} {protocol_name} positions...")

            # Queue all work items instead of starting all at once
            for token_id in token_ids:
                self._worker_queue.append((token_id, protocol))

            # Start first batch of workers
            self._start_next_workers()
        except Exception as e:
            logger.exception(f"Error loading positions: {e}")
            self._log(f"❌ Error loading positions: {e}")
            self.progress_bar.hide()

    def _start_next_workers(self):
        """Start workers from queue up to MAX_CONCURRENT_WORKERS."""
        while len(self._active_workers) < self.MAX_CONCURRENT_WORKERS and self._worker_queue:
            token_id, protocol = self._worker_queue.pop(0)
            try:
                worker = LoadPositionWorker(
                    self.provider, token_id, self.pool_factory,
                    check_ownership=True, protocol=protocol
                )
                worker.position_loaded.connect(self._on_position_loaded, Qt.ConnectionType.QueuedConnection)
                worker.error.connect(self._on_position_error, Qt.ConnectionType.QueuedConnection)
                worker.not_owned.connect(self._on_position_not_owned, Qt.ConnectionType.QueuedConnection)
                worker.finished.connect(lambda w=worker: self._on_worker_finished(w), Qt.ConnectionType.QueuedConnection)
                self._active_workers.append(worker)
                self.load_workers.append(worker)  # keep legacy list in sync
                worker.start()
            except Exception as worker_err:
                self._log(f"❌ Failed to start worker for {token_id}: {worker_err}")

    def _on_position_loaded(self, token_id: int, position: dict):
        """Handle position loaded — batch updates via QTimer."""
        try:
            logger.debug(f"_on_position_loaded: token_id={token_id}")
            with QMutexLocker(self._positions_mutex):
                self.positions_data[token_id] = position
            # Queue for batch table update instead of immediate update
            self._pending_updates[token_id] = position
            if not self._update_timer.isActive():
                self._update_timer.start()
            protocol = position.get('protocol', 'unknown')
            self._log(f"✅ Loaded position #{token_id} ({protocol})")
        except Exception as e:
            logger.exception(f"Error displaying position {token_id}: {e}")
            self._log(f"❌ Error displaying position {token_id}: {e}")

    def _flush_table_updates(self):
        """Flush all pending position updates to the table in one batch."""
        if not self._pending_updates:
            return
        updates = dict(self._pending_updates)
        self._pending_updates.clear()

        # Disable sorting ONCE for the entire batch
        self.positions_table.setSortingEnabled(False)
        try:
            for token_id, position in updates.items():
                try:
                    self._update_table_row_inner(token_id, position)
                except Exception as e:
                    logger.error(f"Error updating table row for {token_id}: {e}", exc_info=True)
        finally:
            self.positions_table.setSortingEnabled(True)
        self._update_buttons()

    def _on_position_error(self, token_id: int, error: str):
        """Handle position load error."""
        self._log(f"❌ Position {token_id}: {error}")
        # Remove from positions if it doesn't exist
        with QMutexLocker(self._positions_mutex):
            if token_id in self.positions_data:
                del self.positions_data[token_id]
        # Update input field to remove invalid IDs
        self._update_token_ids_input()

    def _on_position_not_owned(self, token_id: int, owner: str):
        """Handle position that exists but is not owned by current wallet."""
        # Get selected protocol for context
        selected_protocol = self.scan_protocol_combo.currentData()
        if selected_protocol == "v4":
            protocol_name = "Uniswap V4"
            alt_protocol = "Try PancakeSwap V4 instead?"
        elif selected_protocol == "v4_pancake":
            protocol_name = "PancakeSwap V4"
            alt_protocol = "Try Uniswap V4 instead?"
        elif selected_protocol == "v3_uniswap":
            protocol_name = "Uniswap V3"
            alt_protocol = "Try PancakeSwap V3 instead?"
        else:
            protocol_name = "PancakeSwap V3"
            alt_protocol = "Try other protocols?"

        self._log(f"⚠️ Position {token_id} not owned by you on {protocol_name}")
        self._log(f"   Owner: {owner[:8]}...{owner[-6:]}")
        self._log(f"   Hint: {alt_protocol}")

        # Remove from positions
        with QMutexLocker(self._positions_mutex):
            if token_id in self.positions_data:
                del self.positions_data[token_id]
        # Update input field
        self._update_token_ids_input()

    def _update_token_ids_input(self):
        """Update token IDs input field to reflect current positions."""
        remaining_ids = list(self.positions_data.keys())
        self.token_ids_input.setText(", ".join(str(tid) for tid in remaining_ids))

    def _scan_wallet(self):
        """Scan wallet for all positions."""
        if not self.provider:
            QMessageBox.warning(
                self, "Error",
                "Please connect wallet in the Create tab first."
            )
            return

        # Get selected protocol
        selected_protocol = self.scan_protocol_combo.currentData()
        if selected_protocol == "v4_pancake":
            protocol_name = "PancakeSwap V4"
        elif selected_protocol == "v4":
            protocol_name = "Uniswap V4"
        elif selected_protocol == "v3_uniswap":
            protocol_name = "Uniswap V3"
        else:
            protocol_name = "PancakeSwap V3"

        # Confirm if there are existing positions
        if self.positions_data:
            reply = QMessageBox.question(
                self, "Confirm Scan",
                f"This will scan for {protocol_name} positions and replace the current list.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Clear existing data
        self.positions_table.setRowCount(0)
        self.positions_data = {}
        self.token_ids_input.clear()

        # Stop previous scan if still running
        if hasattr(self, 'scan_worker') and self.scan_worker is not None:
            try:
                self.scan_worker.scan_finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            if self.scan_worker.isRunning():
                self.scan_worker.quit()
                self.scan_worker.finished.connect(self.scan_worker.deleteLater)
            else:
                self.scan_worker.deleteLater()
            self.scan_worker = None

        # Start scan worker with selected protocol
        self.progress_bar.show()
        self.scan_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)

        self.scan_worker = ScanWalletWorker(self.provider, self.pool_factory, protocol=selected_protocol)
        self.scan_worker.progress.connect(self._on_scan_progress, Qt.ConnectionType.QueuedConnection)
        self.scan_worker.position_found.connect(self._on_scan_position_found, Qt.ConnectionType.QueuedConnection)
        self.scan_worker.scan_result.connect(self._on_scan_finished, Qt.ConnectionType.QueuedConnection)
        self.scan_worker.start()

    def _on_scan_progress(self, message: str):
        """Handle scan progress messages."""
        self._log(message)

    def _on_scan_position_found(self, token_id: int, position: dict):
        """Handle position found during scan."""
        try:
            liquidity = position.get('liquidity', 0)
            self._log(f"  Found #{token_id}: liquidity={liquidity:,}")

            self.positions_data[token_id] = position

            # Only show in table if not filtering or has liquidity
            if not self.hide_empty_cb.isChecked() or liquidity > 0:
                self._update_table_row(token_id, position)
        except Exception as e:
            logger.error(f"Error handling scan position {token_id}: {e}", exc_info=True)

    def _on_filter_changed(self, checked: bool):
        """Handle filter checkbox change - rebuild table."""
        self._rebuild_table()

    def _rebuild_table(self):
        """Rebuild table with current filter settings."""
        self.positions_table.setRowCount(0)
        self._row_index.clear()  # Reset row index on full rebuild
        hide_empty = self.hide_empty_cb.isChecked()

        shown_count = 0
        hidden_count = 0

        # Batch all updates: disable sorting once
        # Take snapshot to avoid RuntimeError: dictionary changed size during iteration
        positions_snapshot = list(self.positions_data.items())
        self.positions_table.setSortingEnabled(False)
        try:
            for token_id, position in positions_snapshot:
                if position:
                    liquidity = position.get('liquidity', 0)
                    usd_val = position.get('_usd_value')
                    if hide_empty:
                        if liquidity == 0:
                            hidden_count += 1
                            continue
                        if usd_val is not None and 0 <= usd_val < 0.00001:
                            hidden_count += 1
                            continue
                    try:
                        self._update_table_row_inner(token_id, position)
                        shown_count += 1
                    except Exception as e:
                        logger.error(f"Error rebuilding row for {token_id}: {e}", exc_info=True)
        finally:
            self.positions_table.setSortingEnabled(True)

        if hidden_count > 0:
            self._log(f"Showing {shown_count} positions ({hidden_count} empty hidden)")

        self._update_buttons()

    def _on_scan_finished(self, total_found: int, token_ids: list, protocol: str = "v3"):
        """Handle scan completion."""
        if self.scan_worker is not None:
            # Don't wait() — signal means thread is done or finishing
            self.scan_worker.deleteLater()
            self.scan_worker = None
        self.progress_bar.hide()
        self.scan_btn.setEnabled(True)
        self.load_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)

        # Update input field with found token IDs
        self.token_ids_input.setText(", ".join(str(tid) for tid in token_ids))

        # Count active vs empty positions
        active_count = sum(
            1 for p in self.positions_data.values()
            if p and p.get('liquidity', 0) > 0
        )
        empty_count = total_found - active_count

        # Save positions
        self._save_positions()

        # Rebuild table with filter
        self._rebuild_table()
        self._update_pnl_summary()

        if protocol == "v4_pancake":
            protocol_name = "PancakeSwap V4"
        elif protocol == "v4":
            protocol_name = "Uniswap V4"
        elif protocol == "v3_uniswap":
            protocol_name = "Uniswap V3"
        else:
            protocol_name = "PancakeSwap V3"

        if total_found > 0:
            self._log(f"✅ Scan complete! Found {total_found} {protocol_name} positions: {active_count} active, {empty_count} empty")
            QMessageBox.information(
                self, "Scan Complete",
                f"Found {total_found} {protocol_name} position(s) owned by your wallet.\n\n"
                f"• Active (with liquidity): {active_count}\n"
                f"• Empty (liquidity = 0): {empty_count}\n\n"
                f"Token IDs: {token_ids}\n\n"
                f"{'Empty positions are hidden. Uncheck filter to see them.' if empty_count > 0 and self.hide_empty_cb.isChecked() else ''}"
            )
        else:
            self._log(f"Scan complete - no {protocol_name} positions found")
            # Suggest other protocols
            if protocol.startswith("v4"):
                other_protocol = "PancakeSwap V3 or other V4 protocol"
            else:
                other_protocol = "Uniswap V4 or PancakeSwap V4"
            QMessageBox.information(
                self, "Scan Complete",
                f"No {protocol_name} positions found for this wallet.\n\n"
                f"Make sure you're connected with the correct wallet.\n\n"
                f"Try scanning {other_protocol} using the protocol selector."
            )

    def _on_worker_finished(self, worker):
        """Handle worker completion — start next from queue."""
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        if worker in self.load_workers:
            self.load_workers.remove(worker)
        worker.deleteLater()

        # Start more workers from queue
        self._start_next_workers()

        # All done when no active workers AND queue is empty
        if not self._active_workers and not self._worker_queue:
            # Flush any remaining batched table updates
            if self._pending_updates:
                self._flush_table_updates()
            self.progress_bar.hide()
            self._save_positions()
            self._update_pnl_summary()
            self._log(f"All positions loaded ({len(self.positions_data)} total)")

    def _update_table_row(self, token_id: int, position: dict):
        """Update or add a table row for a position."""
        # Temporarily disable sorting while updating to avoid row index corruption
        self.positions_table.setSortingEnabled(False)
        try:
            self._update_table_row_inner(token_id, position)
        finally:
            self.positions_table.setSortingEnabled(True)

    def _update_table_row_inner(self, token_id: int, position: dict):
        """Inner implementation of table row update."""
        # O(1) row lookup via index, fallback to O(n) scan if index stale
        row = self._row_index.get(token_id, -1)
        if row >= 0 and row < self.positions_table.rowCount():
            # Verify the row still has our token_id (could be stale after removeRow)
            item = self.positions_table.item(row, 0)
            if item:
                try:
                    txt = item.text()
                    eid = int(txt.split(":")[1]) if ":" in txt else int(txt)
                    if eid != token_id:
                        row = -1  # stale, do full scan
                except ValueError:
                    row = -1
            else:
                row = -1
        else:
            row = -1

        # Fallback: full scan (only when index is stale)
        if row == -1:
            for r in range(self.positions_table.rowCount()):
                item = self.positions_table.item(r, 0)
                if item:
                    try:
                        txt = item.text()
                        eid = int(txt.split(":")[1]) if ":" in txt else int(txt)
                        if eid == token_id:
                            row = r
                            break
                    except ValueError:
                        continue

        if row == -1:
            row = self.positions_table.rowCount()
            self.positions_table.insertRow(row)

        # Update row index
        self._row_index[token_id] = row

        # Token ID with protocol prefix
        protocol = position.get('protocol', 'v3')
        # Check if it's a V4 protocol (either "v4" or "v4_pancake")
        is_v4 = protocol and protocol.startswith("v4")
        protocol_prefix = "V4" if is_v4 else "V3"
        token_id_item = NumericTableWidgetItem(f"{protocol_prefix}:{token_id}", float(token_id))
        self.positions_table.setItem(row, 0, token_id_item)

        # Detect if token0 is a stablecoin (need to invert prices)
        token0_addr = position.get('token0', '')
        token1_addr = position.get('token1', '')
        token0_sym = position.get('token0_symbol', '')
        token1_sym = position.get('token1_symbol', '')

        # Use centralized stablecoin detection (supports BNB, BASE, ETH)
        token0_is_stable = bool(token0_addr) and is_stablecoin(token0_addr)
        token1_is_stable = bool(token1_addr) and is_stablecoin(token1_addr)

        # Default symbols if not found
        if not token0_sym:
            token0_sym = token0_addr[:8] if token0_addr else '???'
        if not token1_sym:
            token1_sym = token1_addr[:8] if token1_addr else '???'

        if is_v4:
            logger.debug(f"V4 Position {token_id}: token0={token0_sym} (stable={token0_is_stable}), token1={token1_sym} (stable={token1_is_stable})")

        # Pair - show as volatile/stablecoin
        if token0_is_stable and not token1_is_stable:
            pair_str = f"{token1_sym}/{token0_sym}"
        else:
            pair_str = f"{token0_sym}/{token1_sym}"
        self.positions_table.setItem(row, 1, QTableWidgetItem(pair_str))

        # Fee
        fee = position.get('fee', 0)
        fee_pct = fee / 10000 if fee else 0
        self.positions_table.setItem(row, 2, QTableWidgetItem(f"{fee_pct}%"))

        # Price Range (convert ticks to prices)
        tick_lower = position.get('tick_lower', 0)
        tick_upper = position.get('tick_upper', 0)

        # Debug logging for V4 positions
        if is_v4:
            logger.debug(f"V4 Position {token_id}: tick_lower={tick_lower}, tick_upper={tick_upper}, liquidity={position.get('liquidity', 0)}")

        # Validate tick values are in valid range
        MIN_TICK, MAX_TICK = -887272, 887272
        ticks_valid = (MIN_TICK <= tick_lower <= MAX_TICK and MIN_TICK <= tick_upper <= MAX_TICK)

        if not ticks_valid:
            logger.warning(f"Invalid tick values for position {token_id}: {tick_lower}/{tick_upper}")
            raw_price_lower = 0
            raw_price_upper = float('inf')
        else:
            # tick_to_price gives token1/token0 (raw, without decimals)
            # Use try/except because extreme ticks can cause overflow
            try:
                raw_price_lower = tick_to_price(tick_lower)
                raw_price_upper = tick_to_price(tick_upper)
                if is_v4:
                    logger.debug(f"V4 Position {token_id}: raw prices = {raw_price_lower:.10f} - {raw_price_upper:.10f}")
            except (OverflowError, ValueError) as e:
                # Extreme tick values - display raw ticks instead
                logger.warning(f"tick_to_price overflow for ticks {tick_lower}/{tick_upper}: {e}")
                raw_price_lower = 0
                raw_price_upper = float('inf')

        # Adjust for decimals: human_price = raw_price / 10^(decimals0 - decimals1)
        dec0 = position.get('token0_decimals', 18)
        dec1 = position.get('token1_decimals', 18)
        decimals_diff = dec0 - dec1
        if decimals_diff != 0:
            try:
                adjustment = 10 ** decimals_diff
                raw_price_lower /= adjustment
                raw_price_upper /= adjustment
            except (OverflowError, ZeroDivisionError):
                pass  # Keep raw prices if adjustment fails

        # If token0 is stablecoin, invert to show TOKEN/USD price (how much USD per token)
        # tick_to_price gives token1/token0, so if token0=USDT, token1=MEME, we get MEME/USDT
        # We need to invert to get USDT/MEME = price of MEME in USD
        if token0_is_stable and not token1_is_stable:
            # Invert prices (and swap lower/upper since inverting flips the range)
            if is_v4:
                logger.debug(f"V4 Position {token_id}: Inverting prices (token0 is stablecoin)")
            if raw_price_lower > 0 and raw_price_upper > 0 and raw_price_upper != float('inf'):
                try:
                    price_lower = 1 / raw_price_upper
                    price_upper = 1 / raw_price_lower if raw_price_lower > 0 else float('inf')
                except (OverflowError, ZeroDivisionError):
                    price_lower = raw_price_lower
                    price_upper = raw_price_upper
            else:
                price_lower = raw_price_lower
                price_upper = raw_price_upper
        else:
            price_lower = raw_price_lower
            price_upper = raw_price_upper

        if is_v4:
            logger.debug(f"V4 Position {token_id}: final prices = ${price_lower:.10f} - ${price_upper:.10f}")

        # Format price range (handle infinity and extreme values)
        # Check for invalid/extreme values that indicate bad tick extraction
        is_price_valid = (
            price_upper != float('inf') and
            price_lower != 0 and
            price_upper < 1e12 and  # Trillion - anything above is likely wrong
            price_lower < 1e12 and
            price_lower > 0 and
            price_upper > 0
        )

        if not is_price_valid:
            # Extreme or invalid prices - show ticks instead
            price_range_str = f"Tick {tick_lower} - {tick_upper}"
        elif price_lower < 0.0000001:
            price_range_str = f"${price_lower:.10f} - ${price_upper:.10f}"
        elif price_lower < 0.001:
            price_range_str = f"${price_lower:.8f} - ${price_upper:.8f}"
        elif price_lower < 1:
            # Most meme tokens fall here - show 6 decimals
            price_range_str = f"${price_lower:.6f} - ${price_upper:.6f}"
        elif price_lower < 100:
            price_range_str = f"${price_lower:.4f} - ${price_upper:.4f}"
        elif price_lower < 10000:
            price_range_str = f"${price_lower:.2f} - ${price_upper:.2f}"
        else:
            price_range_str = f"${price_lower:,.0f} - ${price_upper:,.0f}"
        self.positions_table.setItem(row, 3, QTableWidgetItem(price_range_str))

        # Liquidity — convert raw L to USD value
        liquidity = position.get('liquidity', 0)
        current_tick = position.get('current_tick', None)
        usd_value = 0.0
        try:
            if liquidity > 0 and ticks_valid and current_tick is not None:
                # sqrt prices from ticks
                sqrt_lower = 1.0001 ** (tick_lower / 2)
                sqrt_upper = 1.0001 ** (tick_upper / 2)
                sqrt_current = 1.0001 ** (current_tick / 2)

                amounts = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)
                amount0_human = amounts.amount0 / (10 ** dec0) if amounts.amount0 > 0 else 0
                amount1_human = amounts.amount1 / (10 ** dec1) if amounts.amount1 > 0 else 0

                if token0_is_stable and not token1_is_stable:
                    # token0 is USD, token1 is volatile; raw price = token1/token0
                    # current_price (inverted) = USD per volatile token
                    if price_upper > 0 and price_lower > 0:
                        mid_price = (price_lower + price_upper) / 2
                        usd_value = amount0_human + amount1_human * mid_price
                    else:
                        usd_value = amount0_human
                elif token1_is_stable and not token0_is_stable:
                    # token1 is USD, token0 is volatile; raw price = token1/token0
                    raw_current = position.get('current_price', 0)
                    if raw_current and raw_current > 0:
                        usd_value = amount1_human + amount0_human * raw_current
                    else:
                        usd_value = amount1_human
                else:
                    # No stablecoin — show raw L as fallback
                    usd_value = -1
        except Exception:
            usd_value = -1

        # Store computed USD value for filtering
        position['_usd_value'] = usd_value

        # Hide dust positions (< $0.00001) when filter is on
        if self.hide_empty_cb.isChecked():
            if liquidity == 0 or (0 <= usd_value < 0.00001):
                # Remove the row if it was added
                if row < self.positions_table.rowCount():
                    self.positions_table.removeRow(row)
                    # Invalidate row index — rows shifted
                    self._row_index.pop(token_id, None)
                    self._row_index = {k: (v if v < row else v - 1)
                                       for k, v in self._row_index.items()}
                return

        if usd_value >= 0:
            if usd_value >= 1000:
                liq_str = f"${usd_value:,.0f}"
            elif usd_value >= 1:
                liq_str = f"${usd_value:.2f}"
            elif usd_value >= 0.01:
                liq_str = f"${usd_value:.4f}"
            else:
                liq_str = f"${usd_value:.6f}"
        else:
            # Fallback: no stablecoin or error
            if liquidity >= 1e18:
                liq_str = f"{liquidity/1e18:.2f}e18"
            elif liquidity >= 1e6:
                liq_str = f"{liquidity/1e6:.2f}M"
            else:
                liq_str = f"{liquidity:,.0f}"

        liq_item = NumericTableWidgetItem(liq_str, usd_value)
        self.positions_table.setItem(row, 4, liq_item)

        # Fees Earned (tokens_owed)
        fees0 = position.get('tokens_owed0', 0)
        fees1 = position.get('tokens_owed1', 0)

        # Format fees with decimals
        fees0_formatted = fees0 / (10 ** dec0) if fees0 > 0 else 0
        fees1_formatted = fees1 / (10 ** dec1) if fees1 > 0 else 0

        # Show fees as volatile / stablecoin
        if token0_is_stable and not token1_is_stable:
            fees_str = f"{fees1_formatted:.6f} / {fees0_formatted:.4f}"
            stable_fees = fees0_formatted
        else:
            fees_str = f"{fees0_formatted:.6f} / {fees1_formatted:.4f}"
            stable_fees = fees1_formatted
        fees_item = NumericTableWidgetItem(fees_str, stable_fees)
        self.positions_table.setItem(row, 5, fees_item)

        # PnL — show per-position value relative to proportional initial investment
        initial_total = self.initial_investment_spin.value()
        active_positions = sum(
            1 for p in self.positions_data.values()
            if isinstance(p, dict) and p.get('liquidity', 0) > 0
        )
        if initial_total > 0 and active_positions > 0 and usd_value > 0:
            initial_per_pos = initial_total / active_positions
            pnl_val = usd_value + stable_fees - initial_per_pos
            pnl_sign = "+" if pnl_val >= 0 else ""
            pnl_str = f"{pnl_sign}${pnl_val:.2f}"
            pnl_item = NumericTableWidgetItem(pnl_str, pnl_val)
            pnl_item.setForeground(QColor(76, 175, 80) if pnl_val >= 0 else QColor(255, 107, 107))
        elif usd_value > 0:
            # No initial investment set — show current value as "worth"
            pnl_str = f"${usd_value:.2f}"
            pnl_item = NumericTableWidgetItem(pnl_str, usd_value)
            if stable_fees > 0:
                pnl_str = f"${usd_value:.2f} +${stable_fees:.4f}"
                pnl_item = NumericTableWidgetItem(pnl_str, usd_value + stable_fees)
            pnl_item.setForeground(QColor(200, 200, 200))
        else:
            pnl_item = NumericTableWidgetItem("—", 0.0)
        self.positions_table.setItem(row, 6, pnl_item)

        # Status (current_tick already fetched above for liquidity calc)
        in_range = tick_lower <= current_tick <= tick_upper if current_tick is not None else False

        if liquidity == 0:
            status = "Empty"
            status_color = QColor(128, 128, 128)
        elif in_range:
            status = "In Range"
            status_color = QColor(76, 175, 80)
        else:
            status = "Out of Range"
            status_color = QColor(255, 152, 0)

        status_item = QTableWidgetItem(status)
        status_item.setForeground(status_color)
        self.positions_table.setItem(row, 7, status_item)

        # Range Progress (with custom delegate)
        # Only set valid data if prices are valid
        if is_price_valid:
            # current_price from pool is raw token1/token0, need to process it
            raw_current_price = position.get('current_price', None)
            if raw_current_price is not None:
                # Invert if token0 is stablecoin
                if token0_is_stable and not token1_is_stable and raw_current_price > 0:
                    try:
                        current_price = 1 / raw_current_price
                    except (OverflowError, ZeroDivisionError):
                        current_price = price_lower
                else:
                    current_price = raw_current_price
            else:
                # Fallback to middle of range
                current_price = (price_lower + price_upper) / 2

            # Ensure prices are valid for progress display
            display_price_lower = price_lower if price_lower > 0 else 0
            display_price_upper = price_upper if price_upper > 0 else current_price * 2
            display_current = current_price if current_price > 0 else display_price_lower
        else:
            # Invalid prices - set None values so delegate shows "Invalid price data"
            display_price_lower = None
            display_price_upper = None
            display_current = None

        progress_item = QTableWidgetItem()
        progress_item.setData(Qt.ItemDataRole.UserRole, {
            'price_lower': display_price_lower,
            'price_upper': display_price_upper,
            'current_price': display_current,
            'in_range': in_range if is_price_valid else False
        })
        self.positions_table.setItem(row, 8, progress_item)

        # Set row height for progress bar
        self.positions_table.setRowHeight(row, 44)

    def _refresh_all_positions(self):
        """Refresh all loaded positions."""
        if not self.provider:
            return

        token_ids = list(self.positions_data.keys())
        if not token_ids:
            # Try to load from input
            token_ids = self._parse_token_ids()
            if not token_ids:
                return

        # Group positions by their stored protocol
        # This ensures V4 Uniswap, V4 PancakeSwap, V3, etc. are loaded with correct managers
        positions_by_protocol = {}
        for token_id in token_ids:
            pos = self.positions_data.get(token_id)
            if pos and isinstance(pos, dict):
                stored_protocol = pos.get('protocol', None)
            else:
                stored_protocol = None

            if stored_protocol is None:
                # No stored protocol - use combo box selection
                stored_protocol = self.scan_protocol_combo.currentData() or "v3"

            if stored_protocol not in positions_by_protocol:
                positions_by_protocol[stored_protocol] = []
            positions_by_protocol[stored_protocol].append(token_id)

        # Cancel existing workers ONCE before creating new ones
        self._cancel_load_workers()

        # Load all protocol groups (append mode — don't cancel between groups)
        for protocol, ids in positions_by_protocol.items():
            if ids:
                self._log(f"Refreshing {len(ids)} {protocol} positions...")
                self._load_positions_by_ids(ids, protocol=protocol, cancel_existing=False)

    def _refresh_pnl(self):
        """Refresh PnL — reload all positions and update PnL summary."""
        if not self.provider:
            QMessageBox.warning(self, "Error", "Connect wallet first")
            return
        self._refresh_all_positions()
        # Summary will be updated as positions load in _on_position_loaded → _update_pnl_summary

    def _update_pnl_summary(self):
        """Calculate and display PnL summary below the table."""
        total_value = 0.0
        total_fees = 0.0
        active_count = 0

        for token_id, position in self.positions_data.items():
            if not isinstance(position, dict):
                continue
            liquidity = position.get('liquidity', 0)
            if liquidity <= 0:
                continue
            active_count += 1

            # Calculate current USD value
            tick_lower = position.get('tick_lower', 0)
            tick_upper = position.get('tick_upper', 0)
            current_tick = position.get('current_tick', None)
            dec0 = position.get('token0_decimals', 18)
            dec1 = position.get('token1_decimals', 18)
            token0 = position.get('token0', '').lower()
            token1 = position.get('token1', '').lower()
            token0_is_stable = token0 in STABLECOINS
            token1_is_stable = token1 in STABLECOINS

            try:
                if current_tick is not None and tick_lower < tick_upper:
                    sqrt_lower = 1.0001 ** (tick_lower / 2)
                    sqrt_upper = 1.0001 ** (tick_upper / 2)
                    sqrt_current = 1.0001 ** (current_tick / 2)
                    amounts = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)
                    a0 = amounts.amount0 / (10 ** dec0) if amounts.amount0 > 0 else 0
                    a1 = amounts.amount1 / (10 ** dec1) if amounts.amount1 > 0 else 0

                    raw_price = position.get('current_price', 0)
                    if token0_is_stable and not token1_is_stable:
                        # token0 is USD; raw_price = token1/token0; invert to get USD per volatile
                        if raw_price and raw_price > 0:
                            usd = a0 + a1 * (1 / raw_price)
                        else:
                            usd = a0
                    elif token1_is_stable and not token0_is_stable:
                        usd = a1 + a0 * (raw_price if raw_price > 0 else 0)
                    else:
                        usd = 0
                    total_value += usd
            except Exception:
                pass

            # Fees
            fees0 = position.get('tokens_owed0', 0)
            fees1 = position.get('tokens_owed1', 0)
            if token0_is_stable:
                total_fees += fees0 / (10 ** dec0)
            elif token1_is_stable:
                total_fees += fees1 / (10 ** dec1)

        # Build summary text
        initial = self.initial_investment_spin.value()
        parts = [f"Positions: {active_count}", f"Value: ${total_value:.2f}"]
        if total_fees > 0:
            parts.append(f"Fees: ${total_fees:.4f}")
        if initial > 0:
            pnl = total_value + total_fees - initial
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_pct = (pnl / initial * 100) if initial > 0 else 0
            parts.append(f"PnL: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)")
            color = "#00b894" if pnl >= 0 else "#ff6b6b"
            self.pnl_summary_label.setStyleSheet(f"color: {color}; font-size: 12px; padding: 4px 8px;")
        else:
            self.pnl_summary_label.setStyleSheet("color: #aaa; font-size: 12px; padding: 4px 8px;")

        self.pnl_summary_label.setText(" | ".join(parts))

    def _update_buttons(self):
        """Update button states based on positions."""
        has_positions = len(self.positions_data) > 0
        has_active = any(
            p and p.get('liquidity', 0) > 0
            for p in self.positions_data.values()
        )

        # Count V4 positions with liquidity for batch close
        v4_active_count = sum(
            1 for p in self.positions_data.values()
            if p and p.get('protocol', '').startswith('v4') and p.get('liquidity', 0) > 0
        )

        self.close_selected_btn.setEnabled(has_active)
        self.close_all_btn.setEnabled(has_active)
        self.batch_close_btn.setEnabled(v4_active_count > 0)
        if v4_active_count > 0:
            self.batch_close_btn.setText(f"🚀 Close All V4 (1 TX) [{v4_active_count}]")

    def _get_selected_token_ids(self) -> list:
        """Get token IDs of selected rows."""
        selected_rows = set(item.row() for item in self.positions_table.selectedItems())
        result = []
        for row in selected_rows:
            item = self.positions_table.item(row, 0)
            if item:
                # Try to get from UserRole first (raw token_id, stored as float)
                raw_id = item.data(Qt.ItemDataRole.UserRole)
                if raw_id is not None:
                    token_id = int(raw_id)
                else:
                    # Fallback: parse from text (may have "V3:" or "V4:" prefix)
                    text = item.text()
                    if ":" in text:
                        token_id = int(text.split(":")[1])
                    else:
                        token_id = int(text)
                result.append(token_id)
        return result

    def _remove_selected(self):
        """Remove selected positions from the list (doesn't close them)."""
        token_ids = self._get_selected_token_ids()
        if not token_ids:
            QMessageBox.warning(self, "Error", "Please select positions to remove.")
            return

        for token_id in token_ids:
            if token_id in self.positions_data:
                del self.positions_data[token_id]

        # Update input field
        remaining_ids = list(self.positions_data.keys())
        self.token_ids_input.setText(", ".join(str(tid) for tid in remaining_ids))

        # Rebuild table
        self.positions_table.setRowCount(0)
        for token_id, position in self.positions_data.items():
            if position:
                self._update_table_row(token_id, position)

        self._save_positions()
        self._update_buttons()
        self._log(f"Removed positions: {token_ids}")

    def _close_selected(self):
        """Close selected positions."""
        token_ids = self._get_selected_token_ids()
        if not token_ids:
            QMessageBox.warning(self, "Error", "Please select positions to close.")
            return

        # Filter to only active positions
        active_ids = [
            tid for tid in token_ids
            if tid in self.positions_data
            and self.positions_data[tid]
            and self.positions_data[tid].get('liquidity', 0) > 0
        ]

        if not active_ids:
            QMessageBox.warning(self, "Error", "No active positions selected.")
            return

        self._close_positions(active_ids)

    def _close_all(self):
        """Close all loaded positions."""
        token_ids = [
            tid for tid, p in self.positions_data.items()
            if p and p.get('liquidity', 0) > 0
        ]
        if not token_ids:
            QMessageBox.warning(self, "Error", "No active positions to close.")
            return

        self._close_positions(token_ids)

    def _batch_close_all(self):
        """Close ALL V4 positions in ONE transaction (gas efficient)."""
        if not self.provider:
            QMessageBox.warning(self, "Error", "Provider not connected.")
            return

        # Get all V4 positions with liquidity
        v4_positions = [
            p for p in self.positions_data.values()
            if p and p.get('protocol', '').startswith('v4') and p.get('liquidity', 0) > 0
        ]

        if not v4_positions:
            QMessageBox.warning(self, "Error", "No active V4 positions to close.")
            return

        # Check for null addresses
        null_addr = "0x0000000000000000000000000000000000000000"
        invalid_positions = [
            p for p in v4_positions
            if not p.get('token0') or not p.get('token1')
            or p.get('token0') == null_addr or p.get('token1') == null_addr
        ]

        if invalid_positions:
            QMessageBox.warning(
                self, "Error",
                f"{len(invalid_positions)} position(s) have missing token addresses.\n"
                "Please rescan positions to get the token addresses."
            )
            return

        # Check auto-sell settings
        auto_sell = self.auto_sell_cb.isChecked()
        swap_slippage = self.swap_slippage_spin.value()
        initial_investment = self.initial_investment_spin.value()

        # Show confirmation
        token_ids = [p['token_id'] for p in v4_positions]
        confirm_msg = (
            f"Close {len(v4_positions)} V4 position(s) in ONE transaction?\n\n"
            f"Token IDs: {token_ids}\n\n"
            "This is more gas-efficient than closing one by one.\n\n"
            "If ANY position fails, the entire transaction will revert."
        )

        if auto_sell:
            confirm_msg += f"\n\nAuto-sell: After close, a preview of the swap will be shown"

        reply = QMessageBox.question(
            self, "Confirm Batch Close",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Save swap settings for preview/swap step
        self._pending_auto_sell = auto_sell
        self._pending_swap_slippage = swap_slippage
        self._pending_max_price_impact = self.max_impact_spin.value()
        self._pending_swap_mode = self.swap_mode_combo.currentData() or "auto"
        self._pending_private_key = None

        if auto_sell:
            try:
                main_window = self.window()
                if hasattr(main_window, 'create_tab') and hasattr(main_window.create_tab, 'private_key'):
                    self._pending_private_key = main_window.create_tab.private_key
                elif hasattr(self.provider, 'account') and hasattr(self.provider.account, 'key'):
                    self._pending_private_key = self.provider.account.key.hex()
            except Exception:
                pass

            if not self._pending_private_key:
                QMessageBox.warning(
                    self, "Auto-sell Error",
                    "Could not get private key for auto-sell.\n"
                    "Please ensure wallet is connected in Create tab."
                )
                return

        # Start batch close worker
        self.progress_bar.show()
        self.close_selected_btn.setEnabled(False)
        self.close_all_btn.setEnabled(False)
        self.batch_close_btn.setEnabled(False)

        self._log(f"Starting batch close of {len(v4_positions)} V4 positions...")

        self.worker = BatchCloseWorker(
            self.provider, v4_positions,
            initial_investment=initial_investment,
        )
        self.worker.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self.worker.close_result.connect(self._on_batch_close_finished, Qt.ConnectionType.QueuedConnection)
        self.worker.start()

    def _on_batch_close_finished(self, success: bool, message: str, data: dict):
        """Handle batch close completion. Shows swap preview if auto-sell enabled."""
        try:
            if self.worker is not None:
                self.worker.deleteLater()
                self.worker = None
            self.progress_bar.hide()
            self.close_selected_btn.setEnabled(True)
            self.close_all_btn.setEnabled(True)
            self.batch_close_btn.setEnabled(True)

            if success:
                self._log(f"SUCCESS: {message}")
                self._log(f"TX Hash: {data.get('tx_hash', 'N/A')}")

                # Check if auto-sell with preview is pending
                if getattr(self, '_pending_auto_sell', False) and self._pending_private_key:
                    self._log("Preparing swap preview...")
                    self._show_swap_preview(data)
                else:
                    QMessageBox.information(
                        self, "Batch Close Success",
                        f"All positions closed successfully in 1 transaction!\n\n"
                        f"TX: {data.get('tx_hash', 'N/A')}\n"
                        f"Gas Used: {data.get('gas_used', 'N/A')}"
                    )
                    self._refresh_all_positions()
            else:
                self._log(f"FAILED: {message}")
                QMessageBox.critical(self, "Batch Close Failed", f"Failed:\n{message}")
        except Exception as e:
            logger.exception(f"Error in _on_batch_close_finished: {e}")
            self._log(f"Error in batch close handler: {e}")

    def _close_positions(self, token_ids: list):
        """Close specified positions."""
        if not self.provider:
            QMessageBox.warning(self, "Error", "Provider not connected.")
            return

        # Check auto-sell settings
        auto_sell = self.auto_sell_cb.isChecked()
        swap_slippage = self.swap_slippage_spin.value()

        # Build confirmation message
        confirm_msg = (
            f"Close {len(token_ids)} position(s)?\n\n"
            f"Token IDs: {token_ids}\n\n"
            "This will:\n"
            "1. Remove all liquidity\n"
            "2. Collect tokens and fees\n"
            "(NFT positions are kept, not burned)"
        )

        if auto_sell:
            confirm_msg += f"\n\n✅ Auto-sell: After close, a preview of the swap will be shown"

        reply = QMessageBox.question(
            self, "Confirm Close",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Save swap settings for preview/swap step
        self._pending_auto_sell = auto_sell
        self._pending_swap_slippage = swap_slippage
        self._pending_max_price_impact = self.max_impact_spin.value()
        self._pending_swap_mode = self.swap_mode_combo.currentData() or "auto"
        self._pending_private_key = None

        if auto_sell:
            try:
                main_window = self.window()
                if hasattr(main_window, 'create_tab') and hasattr(main_window.create_tab, 'private_key'):
                    self._pending_private_key = main_window.create_tab.private_key
                elif hasattr(self.provider, 'account') and hasattr(self.provider.account, 'key'):
                    self._pending_private_key = self.provider.account.key.hex()
            except Exception:
                pass

            if not self._pending_private_key:
                QMessageBox.warning(
                    self, "Auto-sell Error",
                    "Could not get private key for auto-sell.\n"
                    "Please ensure wallet is connected in Create tab."
                )
                return

        # Get chain ID
        chain_id = getattr(self.provider, 'chain_id', 56)

        # Start worker
        self.progress_bar.show()
        self.close_selected_btn.setEnabled(False)
        self.close_all_btn.setEnabled(False)
        self.batch_close_btn.setEnabled(False)

        # Get initial investment for PnL calculation
        initial_investment = self.initial_investment_spin.value()

        self.worker = ClosePositionsWorker(
            self.provider, token_ids, self.positions_data,
            chain_id=chain_id,
            initial_investment=initial_investment,
        )
        self.worker.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self.worker.close_result.connect(self._on_close_finished, Qt.ConnectionType.QueuedConnection)
        self.worker.start()

    def _on_progress(self, message: str):
        """Handle progress updates."""
        self._log(message)

    def _on_close_finished(self, success: bool, message: str, data: dict):
        """Handle close completion. Shows swap preview if auto-sell enabled."""
        try:
            if self.worker is not None:
                self.worker.deleteLater()
                self.worker = None
            self.progress_bar.hide()
            self.close_selected_btn.setEnabled(True)
            self.close_all_btn.setEnabled(True)
            self.batch_close_btn.setEnabled(True)

            if success:
                self._log(f"SUCCESS: {message}")
                self._log(f"TX Hash: {data.get('tx_hash', 'N/A')}")

                # Check if auto-sell with preview is pending
                if getattr(self, '_pending_auto_sell', False) and self._pending_private_key:
                    self._log("Preparing swap preview...")
                    self._show_swap_preview(data)
                else:
                    QMessageBox.information(
                        self, "Success",
                        f"Positions closed successfully!\n\nTX: {data.get('tx_hash', 'N/A')}"
                    )
                    self._refresh_all_positions()
            else:
                self._log(f"FAILED: {message}")
                QMessageBox.critical(self, "Error", f"Failed to close positions:\n{message}")
        except Exception as e:
            logger.exception(f"Error in _on_close_finished: {e}")
            self._log(f"Error in close handler: {e}")

    def _show_swap_preview(self, close_data: dict):
        """Show swap preview dialog after successful position close."""
        try:
            chain_id = getattr(self.provider, 'chain_id', 56)
            w3 = self.provider.w3

            # Collect volatile tokens from positions
            positions_data = close_data.get('positions_data', {})
            token_ids = close_data.get('token_ids', [])
            positions = close_data.get('positions', [])

            tokens_to_sell = []
            seen = set()

            # From ClosePositionsWorker (has positions_data + token_ids)
            if positions_data and token_ids:
                for tid in token_ids:
                    pos = positions_data.get(tid, {})
                    if not pos:
                        continue
                    for key, dec_key, sym_key in [
                        ('token0', 'token0_decimals', 'token0_symbol'),
                        ('token1', 'token1_decimals', 'token1_symbol'),
                    ]:
                        addr = pos.get(key, '')
                        if not addr or addr.lower() in seen:
                            continue
                        if addr.lower() in STABLE_TOKENS:
                            continue
                        seen.add(addr.lower())
                        tokens_to_sell.append({
                            'address': addr,
                            'decimals': pos.get(dec_key, 18),
                            'symbol': pos.get(sym_key, 'TOKEN'),
                            'amount': 0,
                        })

            # From BatchCloseWorker (has positions list)
            if positions:
                for pos in positions:
                    for key, dec_key, sym_key in [
                        ('token0', 'token0_decimals', 'token0_symbol'),
                        ('token1', 'token1_decimals', 'token1_symbol'),
                    ]:
                        addr = pos.get(key, '')
                        if not addr or addr.lower() in seen:
                            continue
                        if addr.lower() in STABLE_TOKENS:
                            continue
                        seen.add(addr.lower())
                        tokens_to_sell.append({
                            'address': addr,
                            'decimals': pos.get(dec_key, 18),
                            'symbol': pos.get(sym_key, 'TOKEN'),
                            'amount': 0,
                        })

            if not tokens_to_sell:
                self._log("No volatile tokens to sell (all stablecoins)")
                QMessageBox.information(
                    self, "Success",
                    f"Positions closed!\n\nTX: {close_data.get('tx_hash', 'N/A')}\n\n"
                    "No volatile tokens to sell."
                )
                self._refresh_all_positions()
                return

            # Fetch current wallet balances (sell entire balance, like web version)
            erc20_abi = [
                {"constant": True, "inputs": [{"name": "account", "type": "address"}],
                 "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
            ]
            wallet = self.provider.account.address

            for token in tokens_to_sell:
                try:
                    contract = w3.eth.contract(
                        address=Web3.to_checksum_address(token['address']), abi=erc20_abi
                    )
                    token['amount'] = contract.functions.balanceOf(
                        Web3.to_checksum_address(wallet)
                    ).call()
                except Exception as e:
                    self._log(f"Failed to get balance for {token['symbol']}: {e}")
                    token['amount'] = 0

            # Filter out zero-balance tokens
            tokens_to_sell = [t for t in tokens_to_sell if t.get('amount', 0) > 0]

            if not tokens_to_sell:
                self._log("No tokens with balance to sell")
                QMessageBox.information(
                    self, "Success",
                    f"Positions closed!\n\nTX: {close_data.get('tx_hash', 'N/A')}\n\n"
                    "No tokens received to sell."
                )
                self._refresh_all_positions()
                return

            # Get output token
            proxy = getattr(self.provider, 'proxy', None)
            swapper = DexSwap(w3, chain_id, use_kyber=False, proxy=proxy)
            output_token = swapper.get_output_token()

            # Save close_data for PnL display after swap
            self._close_data = close_data

            # Skip preview → sell immediately
            if self.skip_preview_cb.isChecked():
                self._log("Skipping preview, selling immediately...")
                self._on_swap_confirmed(tokens_to_sell)
                return

            # Show preview dialog
            dialog = SwapPreviewDialog(
                self, tokens_to_sell, chain_id, w3,
                output_token=output_token,
                slippage=self._pending_swap_slippage,
                max_price_impact=self._pending_max_price_impact,
                proxy=proxy,
                swap_mode=self._pending_swap_mode,
            )
            dialog.confirmed.connect(self._on_swap_confirmed)
            result = dialog.exec()

            if result != SwapPreviewDialog.DialogCode.Accepted:
                self._log("Swap cancelled by user")
                QMessageBox.information(
                    self, "Success",
                    f"Positions closed!\n\nTX: {close_data.get('tx_hash', 'N/A')}\n\n"
                    "Swap cancelled."
                )
                self._refresh_all_positions()

        except Exception as e:
            logger.exception(f"Error showing swap preview: {e}")
            self._log(f"Error showing swap preview: {e}")
            self._refresh_all_positions()

    def _on_swap_confirmed(self, tokens: list):
        """Handle swap confirmation from preview dialog."""
        try:
            chain_id = getattr(self.provider, 'chain_id', 56)
            w3 = self.provider.w3
            proxy = getattr(self.provider, 'proxy', None)
            swapper = DexSwap(w3, chain_id, use_kyber=False, proxy=proxy)
            output_token = swapper.get_output_token()

            self.progress_bar.show()
            self.close_selected_btn.setEnabled(False)
            self.close_all_btn.setEnabled(False)
            self.batch_close_btn.setEnabled(False)

            initial_investment = self.initial_investment_spin.value()
            swap_mode = getattr(self, '_pending_swap_mode', 'auto')

            self._swap_worker = SwapWorker(
                w3, chain_id, tokens,
                private_key=self._pending_private_key,
                output_token=output_token,
                slippage=self._pending_swap_slippage,
                max_price_impact=self._pending_max_price_impact,
                initial_investment=initial_investment,
                proxy=proxy,
                swap_mode=swap_mode,
            )
            self._swap_worker.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
            self._swap_worker.swap_result.connect(self._on_swap_finished, Qt.ConnectionType.QueuedConnection)
            self._swap_worker.start()
        except Exception as e:
            logger.exception(f"Error starting swap: {e}")
            self._log(f"Error starting swap: {e}")

    def _on_swap_finished(self, results: dict):
        """Handle swap completion — show results."""
        try:
            if hasattr(self, '_swap_worker') and self._swap_worker is not None:
                self._swap_worker.deleteLater()
                self._swap_worker = None
            self.progress_bar.hide()
            self.close_selected_btn.setEnabled(True)
            self.close_all_btn.setEnabled(True)
            self.batch_close_btn.setEnabled(True)

            close_data = getattr(self, '_close_data', {})
            tx_hash = close_data.get('tx_hash', 'N/A')

            total_usd = results.get('total_usd', 0)
            swaps = results.get('swaps', [])
            pnl = results.get('pnl')
            pnl_percent = results.get('pnl_percent')
            initial = results.get('initial_investment', 0)

            result_msg = f"Positions closed & tokens sold!\n\nClose TX: {tx_hash}\n"
            result_msg += f"\n{'='*40}\n"
            result_msg += f"SWAP RESULTS\n"
            result_msg += f"{'='*40}\n"
            result_msg += f"\nTotal received: ${total_usd:.2f}\n"

            if initial > 0 and pnl is not None:
                pnl_sign = "+" if pnl >= 0 else ""
                result_msg += f"\nPnL SUMMARY:\n"
                result_msg += f"  Initial investment: ${initial:.2f}\n"
                result_msg += f"  Final amount: ${total_usd:.2f}\n"
                result_msg += f"  Profit/Loss: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_percent:.1f}%)\n"
                self._log(f"PnL: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_percent:.1f}%)")

            if swaps:
                result_msg += f"\nSwaps ({len(swaps)}):\n"
                for swap in swaps:
                    if swap.get('success'):
                        result_msg += f"  OK {swap.get('token', '?')}: ${swap.get('usd', 0):.2f}\n"
                    else:
                        result_msg += f"  FAIL {swap.get('token', '?')}: {swap.get('error', 'Failed')}\n"

            self._log(f"Swap total: ${total_usd:.2f}")

            QMessageBox.information(self, "Close & Sell Complete", result_msg)
            self._refresh_all_positions()

        except Exception as e:
            logger.exception(f"Error in _on_swap_finished: {e}")
            self._log(f"Error in swap handler: {e}")

    def _clear_list(self):
        """Clear the positions list."""
        reply = QMessageBox.question(
            self, "Confirm Clear",
            "Clear all positions from the list?\n\n"
            "This will NOT close your positions on-chain, just remove them from tracking.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.positions_table.setRowCount(0)
        self.positions_data = {}
        self.token_ids_input.clear()
        self.close_selected_btn.setEnabled(False)
        self.close_all_btn.setEnabled(False)
        self.batch_close_btn.setEnabled(False)
        self._save_positions()
        self._log("List cleared")
