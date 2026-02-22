"""
Swap Preview Dialog

Диалог предпросмотра свапа — показывает котировки перед выполнением.
Появляется после закрытия позиций, перед свапом токенов.
"""

import logging
from typing import List, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QAbstractItemView, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from web3 import Web3

from src.dex_swap import DexSwap

logger = logging.getLogger(__name__)


class QuoteWorker(QThread):
    """Фоновый поток для загрузки котировок."""

    quote_ready = pyqtSignal(int, dict)   # (row_index, quote_data)
    all_done = pyqtSignal(float)           # total_usd

    def __init__(self, w3, chain_id: int, tokens: list, output_token: str,
                 max_price_impact: float = 5.0, proxy: dict = None,
                 swap_mode: str = "auto"):
        super().__init__()
        self.w3 = w3
        self.chain_id = chain_id
        self.tokens = tokens
        self.output_token = output_token
        self.max_price_impact = max_price_impact
        self.proxy = proxy
        self.swap_mode = swap_mode

    def run(self):
        try:
            swapper = DexSwap(self.w3, self.chain_id, max_price_impact=self.max_price_impact, proxy=self.proxy)
            total_usd = 0.0

            for i, token in enumerate(self.tokens):
                try:
                    amount = token.get('amount', 0)
                    if amount == 0:
                        self.quote_ready.emit(i, {
                            'status': 'skip',
                            'reason': 'Zero balance',
                        })
                        continue

                    result = self._get_best_quote(swapper, token['address'], amount)
                    if result:
                        total_usd += result['amount_out_human']
                        self.quote_ready.emit(i, result)
                    else:
                        self.quote_ready.emit(i, {
                            'status': 'error',
                            'reason': 'No liquidity',
                        })

                except Exception as e:
                    logger.warning(f"Quote failed for {token.get('symbol', '?')}: {e}")
                    self.quote_ready.emit(i, {
                        'status': 'error',
                        'reason': str(e)[:100],
                    })

            self.all_done.emit(total_usd)

        except Exception as e:
            logger.error(f"QuoteWorker error: {e}", exc_info=True)
            self.all_done.emit(0.0)

    def _get_best_quote(self, swapper: DexSwap, token_address: str, amount: int) -> Optional[dict]:
        """Получить лучшую котировку в зависимости от swap_mode."""

        def _make_result(amount_out: int, route: str, source: str, price_impact: float = 0) -> Optional[dict]:
            if amount_out <= 0:
                return None
            try:
                out_decimals = swapper.get_token_decimals(self.output_token)
                out_human = amount_out / (10 ** out_decimals)
            except Exception:
                out_human = 0.0
            return {
                'status': 'ok',
                'amount_out': amount_out,
                'amount_out_human': out_human,
                'route': route,
                'price_impact': price_impact,
                'source': source,
            }

        def _try_kyber():
            kyber_quote = swapper.get_kyber_quote(token_address, self.output_token, amount)
            if kyber_quote and kyber_quote.amount_out > 0:
                return {
                    'status': 'ok',
                    'amount_out': kyber_quote.amount_out,
                    'amount_out_human': kyber_quote.amount_out_human,
                    'route': kyber_quote.route_description,
                    'price_impact': kyber_quote.price_impact,
                    'source': 'KyberSwap',
                }
            return None

        def _try_v2():
            v2_out = swapper.get_quote(token_address, self.output_token, amount)
            return _make_result(v2_out, 'V2', 'V2')

        def _try_v3():
            if not swapper.v3_available:
                return None
            v3_out, fee, _ = swapper.get_quote_v3(token_address, self.output_token, amount)
            return _make_result(v3_out, f'V3 (fee {fee/10000:.2f}%)', 'V3')

        # Принудительный режим
        if self.swap_mode == "kyber":
            return _try_kyber()
        if self.swap_mode == "v2":
            return _try_v2()
        if self.swap_mode == "v3":
            return _try_v3()

        # Auto: Kyber → V2 → V3
        result = _try_kyber()
        if result:
            return result

        result = _try_v2()
        if result:
            return result

        return _try_v3()


class SwapPreviewDialog(QDialog):
    """
    Диалог предпросмотра свапа.

    Показывает список токенов, их балансы и ожидаемые котировки.
    Юзер может подтвердить или отменить свап.
    """

    # Сигнал: юзер подтвердил свап (передаёт список токенов с котировками)
    confirmed = pyqtSignal(list)

    def __init__(self, parent, tokens: list, chain_id: int, w3,
                 output_token: str, slippage: float = 3.0,
                 max_price_impact: float = 5.0, proxy: dict = None,
                 swap_mode: str = "auto"):
        """
        Args:
            parent: Родительский виджет
            tokens: Список dict с {'address', 'symbol', 'decimals', 'amount'}
            chain_id: ID сети
            w3: Web3 instance
            output_token: Адрес выходного токена (USDT/USDC)
            slippage: Slippage в %
            max_price_impact: Макс. price impact в %
            proxy: Proxy config dict
            swap_mode: Режим свапа (auto/kyber/v2/v3)
        """
        super().__init__(parent)
        self.tokens = tokens
        self.chain_id = chain_id
        self.w3 = w3
        self.output_token = output_token
        self.slippage = slippage
        self.max_price_impact = max_price_impact
        self.proxy = proxy
        self.swap_mode = swap_mode
        self.quotes = {}  # {row_index: quote_data}
        self.quote_worker = None
        self.total_usd = 0.0

        self._init_ui()
        self._load_quotes()

    def _init_ui(self):
        self.setWindowTitle("Предпросмотр свапа")
        self.setMinimumWidth(600)
        self.setMinimumHeight(350)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Заголовок
        title = QLabel("Предпросмотр свапа токенов")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(11)
        title.setFont(title_font)
        layout.addWidget(title)

        # Таблица котировок
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Токен", "Баланс", "Получите", "Маршрут"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        # Заполнить строки
        self.table.setRowCount(len(self.tokens))
        for i, token in enumerate(self.tokens):
            # Токен
            self.table.setItem(i, 0, QTableWidgetItem(token.get('symbol', '?')))
            # Баланс
            amount = token.get('amount', 0)
            decimals = token.get('decimals', 18)
            balance_str = f"{amount / (10 ** decimals):,.6f}"
            self.table.setItem(i, 1, QTableWidgetItem(balance_str))
            # Получите — загружается
            loading_item = QTableWidgetItem("Загрузка...")
            loading_item.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(i, 2, loading_item)
            # Маршрут — загружается
            self.table.setItem(i, 3, QTableWidgetItem("..."))

        layout.addWidget(self.table)

        # Итого
        self.total_label = QLabel("Итого: загрузка...")
        total_font = QFont()
        total_font.setBold(True)
        self.total_label.setFont(total_font)
        layout.addWidget(self.total_label)

        # Slippage + swap mode info
        mode_names = {"auto": "Авто (Kyber→V2→V3)", "kyber": "KyberSwap", "v2": "V2", "v3": "V3"}
        mode_name = mode_names.get(self.swap_mode, self.swap_mode)
        info_label = QLabel(f"Slippage: {self.slippage}%  |  Max impact: {self.max_price_impact}%  |  DEX: {mode_name}")
        info_label.setStyleSheet("color: gray;")
        layout.addWidget(info_label)

        # Прогресс
        self.progress = QProgressBar()
        self.progress.setMaximum(len(self.tokens))
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(4)
        layout.addWidget(self.progress)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.confirm_btn = QPushButton("Подтвердить свап")
        self.confirm_btn.setEnabled(False)  # Активируется после загрузки котировок
        self.confirm_btn.setStyleSheet("QPushButton { background-color: #4CAF50; color: white; padding: 8px 20px; }")
        self.confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(self.confirm_btn)

        layout.addLayout(btn_layout)

    def _load_quotes(self):
        """Запустить загрузку котировок в фоне."""
        self.quote_worker = QuoteWorker(
            self.w3, self.chain_id, self.tokens, self.output_token,
            self.max_price_impact, proxy=self.proxy,
            swap_mode=self.swap_mode
        )
        self.quote_worker.quote_ready.connect(self._on_quote_ready, Qt.ConnectionType.QueuedConnection)
        self.quote_worker.all_done.connect(self._on_all_quotes_done, Qt.ConnectionType.QueuedConnection)
        self.quote_worker.start()

    def _on_quote_ready(self, row: int, data: dict):
        """Обновить строку таблицы с котировкой."""
        self.quotes[row] = data
        self.progress.setValue(len(self.quotes))

        status = data.get('status', 'error')

        if status == 'ok':
            out_human = data.get('amount_out_human', 0)
            item = QTableWidgetItem(f"~${out_human:,.2f}")
            item.setForeground(Qt.GlobalColor.darkGreen)
            self.table.setItem(row, 2, item)

            route = data.get('route', 'KyberSwap')
            route_item = QTableWidgetItem(route)
            self.table.setItem(row, 3, route_item)

        elif status == 'skip':
            item = QTableWidgetItem("Пропуск")
            item.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(row, 2, item)
            self.table.setItem(row, 3, QTableWidgetItem(data.get('reason', '')))

        else:  # error
            item = QTableWidgetItem("Ошибка")
            item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, 2, item)
            reason = data.get('reason', 'Unknown error')
            reason_item = QTableWidgetItem(reason)
            reason_item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, 3, reason_item)

    def _on_all_quotes_done(self, total_usd: float):
        """Все котировки загружены."""
        self.total_usd = total_usd
        self.total_label.setText(f"Итого: ~${total_usd:,.2f}")
        self.progress.hide()

        # Включить кнопку если есть хоть одна успешная котировка
        has_any_ok = any(q.get('status') == 'ok' for q in self.quotes.values())
        self.confirm_btn.setEnabled(has_any_ok)

        if not has_any_ok:
            self.total_label.setText("Нет доступных котировок для свапа")
            self.total_label.setStyleSheet("color: red;")

    def _on_confirm(self):
        """Юзер подтвердил свап."""
        # Собрать токены с успешными котировками
        confirmed_tokens = []
        for i, token in enumerate(self.tokens):
            quote = self.quotes.get(i, {})
            if quote.get('status') == 'ok':
                confirmed_tokens.append({
                    **token,
                    'expected_out': quote.get('amount_out', 0),
                    'expected_usd': quote.get('amount_out_human', 0),
                    'route': quote.get('route', ''),
                })

        self.confirmed.emit(confirmed_tokens)
        self.accept()

    def closeEvent(self, event):
        """Очистка при закрытии."""
        if self.quote_worker and self.quote_worker.isRunning():
            self.quote_worker.wait(5000)
        if self.quote_worker:
            self.quote_worker.deleteLater()
            self.quote_worker = None
        super().closeEvent(event)
