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

# === ПРОВЕРКА ЛИЦЕНЗИИ ===
from licensing import LicenseChecker, find_license_file, LicenseError


def check_license_gui(app: QApplication) -> bool:
    """
    Проверка лицензии с GUI диалогом при ошибке.

    Returns:
        True если лицензия валидна, False если нет
    """
    license_path = find_license_file([
        "license.lic",
        os.path.join(os.path.dirname(__file__), "license.lic"),
    ])

    if not license_path:
        QMessageBox.critical(
            None,
            "Лицензия не найдена",
            "Файл лицензии не найден!\n\n"
            "Поместите файл license.lic в папку с программой.\n\n"
            "Для получения лицензии свяжитесь с разработчиком."
        )
        return False

    try:
        checker = LicenseChecker()
        result = checker.check_license(license_path)

        if not result["valid"]:
            QMessageBox.critical(
                None,
                "Ошибка лицензии",
                f"Лицензия недействительна!\n\n"
                f"Причина: {result['error']}\n\n"
                f"Для продления лицензии свяжитесь с разработчиком."
            )
            return False

        # Показать предупреждение если осталось мало дней
        days = result["days_remaining"]
        if days <= 7:
            QMessageBox.warning(
                None,
                "Лицензия истекает",
                f"Внимание! Лицензия истекает через {days} дней.\n\n"
                f"Свяжитесь с разработчиком для продления."
            )

        print(f"Лицензия: {result['user_id']} | "
              f"Осталось {days} дней | "
              f"До: {result['expires_at'].strftime('%Y-%m-%d')}")
        return True

    except LicenseError as e:
        QMessageBox.critical(
            None,
            "Ошибка лицензии",
            f"Ошибка проверки лицензии:\n\n{e}"
        )
        return False


def main():
    """Main entry point for the application."""
    # Install global exception handler to prevent silent crashes
    sys.excepthook = _global_exception_handler

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
