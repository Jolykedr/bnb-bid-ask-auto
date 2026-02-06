"""
Master Password Dialog

Диалоги для ввода и создания мастер-пароля.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QCheckBox, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont


class PasswordStrengthIndicator(QProgressBar):
    """Индикатор силы пароля."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setMaximum(100)
        self.setFixedHeight(8)

    def update_strength(self, password: str):
        """Обновление индикатора на основе пароля."""
        strength = self._calculate_strength(password)
        self.setValue(strength)

        # Цвет в зависимости от силы
        if strength < 30:
            color = "#ff4444"  # Красный
        elif strength < 60:
            color = "#ffaa00"  # Оранжевый
        elif strength < 80:
            color = "#88cc00"  # Жёлто-зелёный
        else:
            color = "#00cc44"  # Зелёный

        self.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #444;
                border-radius: 4px;
                background: #2a2a2a;
            }}
            QProgressBar::chunk {{
                background: {color};
                border-radius: 3px;
            }}
        """)

    def _calculate_strength(self, password: str) -> int:
        """Расчёт силы пароля (0-100)."""
        if not password:
            return 0

        score = 0

        # Длина
        score += min(len(password) * 4, 40)

        # Разнообразие символов
        has_lower = any(c.islower() for c in password)
        has_upper = any(c.isupper() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_special = any(not c.isalnum() for c in password)

        if has_lower:
            score += 10
        if has_upper:
            score += 10
        if has_digit:
            score += 10
        if has_special:
            score += 15

        # Бонус за комбинации
        variety = sum([has_lower, has_upper, has_digit, has_special])
        if variety >= 3:
            score += 15

        return min(score, 100)


class MasterPasswordDialog(QDialog):
    """Диалог для ввода мастер-пароля при расшифровке."""

    def __init__(self, parent=None, title: str = "Мастер-пароль"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(350)
        self.password = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Заголовок
        title_label = QLabel("Введите мастер-пароль для расшифровки кошелька")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Поле пароля
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Мастер-пароль...")
        self.password_input.returnPressed.connect(self._on_ok)
        layout.addWidget(self.password_input)

        # Показать пароль
        self.show_password_cb = QCheckBox("Показать пароль")
        self.show_password_cb.toggled.connect(self._toggle_password_visibility)
        layout.addWidget(self.show_password_cb)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        # Фокус на поле пароля
        self.password_input.setFocus()

    def _toggle_password_visibility(self, show: bool):
        if show:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Password)

    def _on_ok(self):
        password = self.password_input.text()
        if not password:
            QMessageBox.warning(self, "Ошибка", "Введите пароль")
            return
        self.password = password
        self.accept()

    def get_password(self) -> str:
        """Получение введённого пароля."""
        return self.password


class CreatePasswordDialog(QDialog):
    """Диалог для создания нового мастер-пароля."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создание мастер-пароля")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.password = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Заголовок
        title_label = QLabel(
            "Создайте мастер-пароль для защиты приватного ключа.\n"
            "Этот пароль потребуется при каждом запуске приложения."
        )
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Предупреждение
        warning_label = QLabel(
            "Если вы забудете пароль, восстановить ключ будет невозможно!"
        )
        warning_label.setStyleSheet("color: #ff6b6b; font-weight: bold;")
        warning_label.setWordWrap(True)
        layout.addWidget(warning_label)

        # Пароль
        layout.addWidget(QLabel("Пароль:"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Минимум 8 символов...")
        self.password_input.textChanged.connect(self._on_password_changed)
        layout.addWidget(self.password_input)

        # Индикатор силы
        self.strength_indicator = PasswordStrengthIndicator()
        layout.addWidget(self.strength_indicator)

        self.strength_label = QLabel("")
        self.strength_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.strength_label)

        # Подтверждение
        layout.addWidget(QLabel("Подтверждение:"))
        self.confirm_input = QLineEdit()
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_input.setPlaceholderText("Повторите пароль...")
        self.confirm_input.textChanged.connect(self._on_confirm_changed)
        layout.addWidget(self.confirm_input)

        self.match_label = QLabel("")
        self.match_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.match_label)

        # Показать пароль
        self.show_password_cb = QCheckBox("Показать пароль")
        self.show_password_cb.toggled.connect(self._toggle_password_visibility)
        layout.addWidget(self.show_password_cb)

        layout.addSpacing(10)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self.ok_btn = QPushButton("Сохранить")
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

        # Фокус
        self.password_input.setFocus()

    def _toggle_password_visibility(self, show: bool):
        mode = QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(mode)
        self.confirm_input.setEchoMode(mode)

    def _on_password_changed(self, text: str):
        self.strength_indicator.update_strength(text)

        # Текстовая оценка
        strength = self.strength_indicator.value()
        if strength < 30:
            self.strength_label.setText("Слабый пароль")
            self.strength_label.setStyleSheet("color: #ff4444; font-size: 11px;")
        elif strength < 60:
            self.strength_label.setText("Средний пароль")
            self.strength_label.setStyleSheet("color: #ffaa00; font-size: 11px;")
        elif strength < 80:
            self.strength_label.setText("Хороший пароль")
            self.strength_label.setStyleSheet("color: #88cc00; font-size: 11px;")
        else:
            self.strength_label.setText("Отличный пароль!")
            self.strength_label.setStyleSheet("color: #00cc44; font-size: 11px;")

        self._validate()

    def _on_confirm_changed(self, text: str):
        self._validate()

    def _validate(self):
        password = self.password_input.text()
        confirm = self.confirm_input.text()

        # Проверка совпадения
        if confirm:
            if password == confirm:
                self.match_label.setText("Пароли совпадают")
                self.match_label.setStyleSheet("color: #00cc44; font-size: 11px;")
            else:
                self.match_label.setText("Пароли не совпадают")
                self.match_label.setStyleSheet("color: #ff4444; font-size: 11px;")
        else:
            self.match_label.setText("")

        # Активация кнопки
        is_valid = (
            len(password) >= 8 and
            password == confirm and
            self.strength_indicator.value() >= 30
        )
        self.ok_btn.setEnabled(is_valid)

    def _on_ok(self):
        self.password = self.password_input.text()
        self.accept()

    def get_password(self) -> str:
        """Получение созданного пароля."""
        return self.password


def ask_master_password(parent=None, title: str = "Мастер-пароль") -> str:
    """
    Показать диалог ввода мастер-пароля.

    Args:
        parent: Родительское окно
        title: Заголовок диалога

    Returns:
        Пароль или None если отменено
    """
    dialog = MasterPasswordDialog(parent, title)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_password()
    return None


def create_master_password(parent=None) -> str:
    """
    Показать диалог создания мастер-пароля.

    Args:
        parent: Родительское окно

    Returns:
        Новый пароль или None если отменено
    """
    dialog = CreatePasswordDialog(parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.get_password()
    return None
