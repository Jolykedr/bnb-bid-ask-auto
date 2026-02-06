"""
License Checker - встраивается в софт клиентов.

Проверяет подпись и срок действия лицензии.
Публичный ключ встроен в код (замени на свой после генерации).

Использование:
    from licensing.license_checker import require_license, LicenseChecker

    # Вариант 1: Декоратор
    @require_license("license.lic")
    def main():
        ...

    # Вариант 2: Прямая проверка
    checker = LicenseChecker()
    result = checker.check_license("license.lic")
    if not result["valid"]:
        print(f"License error: {result['error']}")
        exit(1)
"""

import os
import sys
import json
import base64
import hashlib
import functools
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
except ImportError:
    print("=" * 60)
    print("ОШИБКА: Требуется библиотека cryptography")
    print("Установи: pip install cryptography")
    print("=" * 60)
    sys.exit(1)


# ============================================================
# ПУБЛИЧНЫЙ КЛЮЧ - ЗАМЕНИ НА СВОЙ!
# Получи его командой: python license_generator.py show-public-key
# ============================================================
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAveRvmHvcC7EDxhUo7T9SQnN1AFg6RGHEbTEWl8MMD6A=
-----END PUBLIC KEY-----
"""
# ============================================================


class LicenseError(Exception):
    """Ошибка лицензии."""
    pass


class LicenseExpiredError(LicenseError):
    """Лицензия истекла."""
    pass


class LicenseInvalidError(LicenseError):
    """Лицензия недействительна (подпись не совпадает)."""
    pass


class LicenseNotFoundError(LicenseError):
    """Файл лицензии не найден."""
    pass


class LicenseChecker:
    """
    Проверщик лицензий.

    Использует Ed25519 для проверки подписи.
    """

    def __init__(self, public_key_pem: str = None):
        """
        Args:
            public_key_pem: PEM-encoded публичный ключ.
                           Если None, использует встроенный PUBLIC_KEY_PEM.
        """
        pem = public_key_pem or PUBLIC_KEY_PEM

        # Проверяем что ключ заменён
        if "AAAAAAAAAAAAA" in pem:
            raise LicenseError(
                "Публичный ключ не настроен!\n"
                "Замени PUBLIC_KEY_PEM в license_checker.py на свой ключ.\n"
                "Получи его: python license_generator.py show-public-key"
            )

        try:
            self.public_key = serialization.load_pem_public_key(pem.encode())
        except Exception as e:
            raise LicenseError(f"Не удалось загрузить публичный ключ: {e}")

    def check_license(self, license_path: str) -> dict:
        """
        Проверить файл лицензии.

        Args:
            license_path: Путь к файлу .lic

        Returns:
            dict с полями:
                - valid: bool - действительна ли лицензия
                - error: str | None - сообщение об ошибке
                - user_id: str | None - ID пользователя
                - expires_at: datetime | None - дата истечения
                - days_remaining: int | None - дней осталось
                - features: list | None - доступные функции
        """
        result = {
            "valid": False,
            "error": None,
            "user_id": None,
            "expires_at": None,
            "days_remaining": None,
            "features": None,
        }

        # 1. Проверяем существование файла
        license_file = Path(license_path)
        if not license_file.exists():
            result["error"] = f"Файл лицензии не найден: {license_path}"
            return result

        # 2. Читаем и парсим JSON
        try:
            with open(license_file, "r") as f:
                license_data = json.load(f)
        except json.JSONDecodeError as e:
            result["error"] = f"Некорректный формат лицензии: {e}"
            return result

        # 3. Проверяем структуру
        if "data" not in license_data or "signature" not in license_data:
            result["error"] = "Некорректная структура лицензии"
            return result

        data = license_data["data"]
        signature_b64 = license_data["signature"]

        # 4. Проверяем подпись
        try:
            data_bytes = json.dumps(data, sort_keys=True).encode()
            signature = base64.b64decode(signature_b64)
            self.public_key.verify(signature, data_bytes)
        except InvalidSignature:
            result["error"] = "Недействительная подпись лицензии"
            return result
        except Exception as e:
            result["error"] = f"Ошибка проверки подписи: {e}"
            return result

        # 5. Проверяем срок действия
        try:
            expires_at = datetime.fromisoformat(data["expires_at"])
            result["expires_at"] = expires_at
            result["user_id"] = data.get("user_id", "unknown")
            result["features"] = data.get("features", [])

            now = datetime.utcnow()
            if now > expires_at:
                days_expired = (now - expires_at).days
                result["error"] = f"Лицензия истекла {days_expired} дней назад"
                result["days_remaining"] = -days_expired
                return result

            result["days_remaining"] = (expires_at - now).days
            result["valid"] = True

        except (KeyError, ValueError) as e:
            result["error"] = f"Некорректные данные в лицензии: {e}"
            return result

        return result

    def verify_or_exit(self, license_path: str, show_info: bool = True):
        """
        Проверить лицензию и выйти если невалидна.

        Args:
            license_path: Путь к файлу лицензии
            show_info: Показывать информацию о лицензии
        """
        result = self.check_license(license_path)

        if not result["valid"]:
            print("=" * 60)
            print("ОШИБКА ЛИЦЕНЗИИ")
            print("=" * 60)
            print(f"  {result['error']}")
            print()
            print("Для продления лицензии свяжитесь с разработчиком.")
            print("=" * 60)
            sys.exit(1)

        if show_info:
            print(f"Лицензия: {result['user_id']} | "
                  f"Осталось {result['days_remaining']} дней | "
                  f"До: {result['expires_at'].strftime('%Y-%m-%d')}")

    def has_feature(self, license_path: str, feature: str) -> bool:
        """
        Проверить наличие функции в лицензии.

        Args:
            license_path: Путь к файлу лицензии
            feature: Название функции

        Returns:
            True если функция доступна
        """
        result = self.check_license(license_path)
        if not result["valid"]:
            return False

        features = result.get("features", [])
        return "full" in features or feature in features


def require_license(license_path: str = "license.lic", show_info: bool = True):
    """
    Декоратор для функций, требующих лицензию.

    Использование:
        @require_license("my_license.lic")
        def main():
            # Код выполнится только если лицензия валидна
            ...

    Args:
        license_path: Путь к файлу лицензии
        show_info: Показывать информацию о лицензии при запуске
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            checker = LicenseChecker()
            checker.verify_or_exit(license_path, show_info=show_info)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def require_feature(license_path: str, feature: str):
    """
    Декоратор для функций, требующих определённую функцию в лицензии.

    Использование:
        @require_feature("license.lic", "advanced_strategies")
        def use_advanced_strategies():
            ...

    Args:
        license_path: Путь к файлу лицензии
        feature: Требуемая функция
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            checker = LicenseChecker()

            if not checker.has_feature(license_path, feature):
                print(f"Функция '{feature}' недоступна в вашей лицензии.")
                print("Обратитесь к разработчику для апгрейда.")
                return None

            return func(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# Поиск файла лицензии
# ============================================================

def find_license_file(search_paths: list = None) -> Optional[str]:
    """
    Найти файл лицензии в стандартных местах.

    Args:
        search_paths: Дополнительные пути для поиска

    Returns:
        Путь к найденному файлу или None
    """
    default_paths = [
        "license.lic",
        "LICENSE.lic",
        Path.home() / ".bnb_ladder" / "license.lic",
        Path(__file__).parent.parent / "license.lic",
    ]

    all_paths = (search_paths or []) + default_paths

    for path in all_paths:
        path = Path(path)
        if path.exists():
            return str(path)

    return None


# ============================================================
# CLI для тестирования
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="License Checker")
    parser.add_argument("license_file", nargs="?", default="license.lic",
                       help="Путь к файлу лицензии")
    args = parser.parse_args()

    try:
        checker = LicenseChecker()
        result = checker.check_license(args.license_file)

        print("=" * 50)
        print("LICENSE CHECK RESULT")
        print("=" * 50)

        if result["valid"]:
            print(f"  Status: ✅ VALID")
            print(f"  User: {result['user_id']}")
            print(f"  Expires: {result['expires_at'].strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"  Days remaining: {result['days_remaining']}")
            print(f"  Features: {', '.join(result['features'])}")
        else:
            print(f"  Status: ❌ INVALID")
            print(f"  Error: {result['error']}")

        print("=" * 50)

    except LicenseError as e:
        print(f"Error: {e}")
        sys.exit(1)
