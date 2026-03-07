#!/usr/bin/env python3
"""
BNB Liquidity Ladder - Desktop Application

Entry point for the PyQt6-based desktop application.
Run this script to start the GUI.

Usage:
    python run_ui.py
"""

import sys
import os
import logging
import traceback
import threading

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

logger = logging.getLogger(__name__)


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """
    Глобальный обработчик исключений — логирует ошибку и показывает диалог
    вместо тихого падения приложения.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical(f"Unhandled exception:\n{error_msg}")

    try:
        QMessageBox.critical(
            None,
            "Critical Error",
            f"Unhandled error:\n\n{exc_value}\n\nSee logs for details."
        )
    except Exception:
        pass  # QApplication may not exist yet


def _thread_exception_handler(args):
    """
    Обработчик исключений в worker-потоках (threading.excepthook).
    Предотвращает тихое падение приложения из-за необработанного исключения в потоке.
    """
    error_msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    logger.critical(f"Unhandled thread exception ({args.thread}):\n{error_msg}")


# === ПРОВЕРКА ЛИЦЕНЗИИ ===
from licensing import LicenseChecker, LicenseError
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout


class LicenseKeyDialog(QDialog):
    """Dialog for entering a license key."""

    def __init__(self, error_msg: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("License Activation")
        self.setFixedWidth(450)
        self.key_value = ""

        layout = QVBoxLayout(self)

        if error_msg:
            err_label = QLabel(error_msg)
            err_label.setStyleSheet("color: red; font-weight: bold;")
            err_label.setWordWrap(True)
            layout.addWidget(err_label)

        layout.addWidget(QLabel("Enter your license key:"))

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("LL-XXXX-XXXX-XXXX-XXXX-XXXX")
        self.key_input.setFont(QFont("Consolas", 12))
        layout.addWidget(self.key_input)

        btn_layout = QHBoxLayout()
        activate_btn = QPushButton("Activate")
        activate_btn.clicked.connect(self._on_activate)
        cancel_btn = QPushButton("Exit")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(activate_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        self.key_input.returnPressed.connect(self._on_activate)

    def _on_activate(self):
        key = self.key_input.text().strip()
        if key:
            self.key_value = key
            self.accept()


def check_license_gui(app: QApplication) -> bool:
    """
    Server-based license check with GUI dialogs.
    If no key found or validation fails, shows activation dialog.

    Returns:
        True if license is valid, False if user cancelled
    """
    checker = LicenseChecker()
    error_msg = ""

    # Try existing key first
    if checker.get_license_key():
        result = checker.validate()
        if result["valid"]:
            days = result.get("days_remaining", 0)
            offline = " (offline)" if result.get("offline_mode") else ""
            if days is not None and days <= 7:
                QMessageBox.warning(
                    None,
                    "License expiring",
                    f"Your license expires in {days} days.\n\n"
                    f"Contact support for renewal."
                )
            print(f"License: OK | {days} days remaining{offline}")
            return True
        error_msg = result.get("error", "Unknown error")

    # No key or validation failed — show activation dialog
    while True:
        dialog = LicenseKeyDialog(error_msg=error_msg)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False  # User cancelled

        key = dialog.key_value
        result = checker.activate(key)

        if result["valid"]:
            days = result.get("days_remaining", "?")
            QMessageBox.information(
                None,
                "License activated",
                f"License activated successfully!\n\n"
                f"Valid for {days} days."
            )
            print(f"License: activated | {days} days remaining")
            return True

        error_msg = result.get("error", "Activation failed")


def main():
    """Main entry point for the application."""
    # Install global exception handlers to prevent silent crashes
    sys.excepthook = _global_exception_handler
    threading.excepthook = _thread_exception_handler

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler("bnb_ladder.log", encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

    # Create application
    app = QApplication(sys.argv)

    # Проверка лицензии ПЕРЕД созданием главного окна
    if not check_license_gui(app):
        sys.exit(1)

    # Set application info
    app.setApplicationName("BNB Liquidity Ladder")
    app.setOrganizationName("BNBLiquidityLadder")
    app.setApplicationVersion("1.0.0")

    # Set default font
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # Enable high DPI scaling
    app.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Import and create main window
    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    # Run application
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
