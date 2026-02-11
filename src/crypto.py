"""
Secure Key Storage Module

Шифрование приватных ключей с использованием мастер-пароля.
Использует AES-256-GCM для аутентифицированного шифрования.

Формат зашифрованных данных:
    version (1 byte) + salt (16 bytes) + nonce (12 bytes) + ciphertext + tag (16 bytes)

Безопасность:
    - PBKDF2-SHA256 с 600,000 итерациями для деривации ключа
    - AES-256-GCM для шифрования (аутентифицированное)
    - Случайный salt и nonce для каждого шифрования
    - Защита от timing attacks через constant-time сравнения
"""

import base64
import ctypes
import secrets
from typing import Optional, Tuple

# Пробуем использовать cryptography, если не установлена - fallback на PyCryptodome
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    CRYPTO_BACKEND = "cryptography"
except ImportError:
    try:
        from Crypto.Cipher import AES
        from Crypto.Protocol.KDF import PBKDF2
        from Crypto.Hash import SHA256
        from Crypto.Random import get_random_bytes
        CRYPTO_BACKEND = "pycryptodome"
    except ImportError:
        CRYPTO_BACKEND = None


# Константы
VERSION = b'\x01'  # Версия формата для будущей совместимости
SALT_SIZE = 16     # 128 бит
NONCE_SIZE = 12    # 96 бит (рекомендуется для GCM)
KEY_SIZE = 32      # 256 бит для AES-256
TAG_SIZE = 16      # 128 бит (стандарт для GCM)
ITERATIONS = 600_000  # OWASP рекомендация для PBKDF2-SHA256 (2023)


def _secure_zero(data):
    """
    Обнуление байтов в памяти (best-effort для CPython).

    Работает с bytearray и bytes. Для immutable bytes использует ctypes
    для прямой записи в буфер (CPython-specific, не гарантируется).
    """
    if isinstance(data, bytearray):
        for i in range(len(data)):
            data[i] = 0
    elif isinstance(data, bytes) and len(data) > 0:
        try:
            buf = (ctypes.c_char * len(data)).from_address(id(data) + bytes.__basicsize__)
            ctypes.memset(buf, 0, len(data))
        except Exception:
            pass  # Не-CPython или другая ошибка — пропускаем


class CryptoError(Exception):
    """Базовое исключение для ошибок криптографии."""
    pass


class DecryptionError(CryptoError):
    """Ошибка расшифровки (неверный пароль или повреждённые данные)."""
    pass


class CryptoNotAvailable(CryptoError):
    """Криптографическая библиотека не установлена."""
    pass


def is_crypto_available() -> bool:
    """Проверка доступности криптографии."""
    return CRYPTO_BACKEND is not None


def get_crypto_backend() -> str:
    """Получение названия используемой библиотеки."""
    return CRYPTO_BACKEND or "none"


def _derive_key(password: str, salt: bytes) -> bytes:
    """
    Деривация ключа шифрования из пароля.

    Использует PBKDF2-SHA256 с высоким числом итераций.

    Args:
        password: Мастер-пароль пользователя
        salt: Случайная соль (16 байт)

    Returns:
        Ключ шифрования (32 байта)
    """
    if CRYPTO_BACKEND == "cryptography":
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_SIZE,
            salt=salt,
            iterations=ITERATIONS,
            backend=default_backend()
        )
        return kdf.derive(password.encode('utf-8'))

    elif CRYPTO_BACKEND == "pycryptodome":
        return PBKDF2(
            password.encode('utf-8'),
            salt,
            dkLen=KEY_SIZE,
            count=ITERATIONS,
            hmac_hash_module=SHA256
        )

    else:
        raise CryptoNotAvailable(
            "Для шифрования установите: pip install cryptography"
        )


def encrypt_key(private_key: str, password: str) -> str:
    """
    Шифрование приватного ключа мастер-паролем.

    Args:
        private_key: Приватный ключ (0x... или без префикса)
        password: Мастер-пароль

    Returns:
        Base64-encoded зашифрованные данные

    Raises:
        CryptoNotAvailable: Если криптобиблиотека не установлена
    """
    if not is_crypto_available():
        raise CryptoNotAvailable(
            "Для шифрования установите: pip install cryptography"
        )

    # Генерируем случайные salt и nonce
    salt = secrets.token_bytes(SALT_SIZE)
    nonce = secrets.token_bytes(NONCE_SIZE)

    # Деривация ключа из пароля
    key = _derive_key(password, salt)

    try:
        # Шифруем
        plaintext = bytearray(private_key.encode('utf-8'))

        if CRYPTO_BACKEND == "cryptography":
            aesgcm = AESGCM(key)
            ciphertext = aesgcm.encrypt(nonce, bytes(plaintext), None)  # ciphertext + tag

        elif CRYPTO_BACKEND == "pycryptodome":
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            ciphertext, tag = cipher.encrypt_and_digest(bytes(plaintext))
            ciphertext = ciphertext + tag  # Объединяем для совместимости

        # Формат: version + salt + nonce + ciphertext
        encrypted_data = VERSION + salt + nonce + ciphertext

        # Возвращаем как base64 для удобного хранения
        return base64.b64encode(encrypted_data).decode('ascii')
    finally:
        # Обнуление sensitive data
        if 'plaintext' in dir():
            _secure_zero(plaintext)
        _secure_zero(key)


def decrypt_key(encrypted_data: str, password: str) -> str:
    """
    Расшифровка приватного ключа.

    Args:
        encrypted_data: Base64-encoded зашифрованные данные
        password: Мастер-пароль

    Returns:
        Расшифрованный приватный ключ

    Raises:
        DecryptionError: Неверный пароль или повреждённые данные
        CryptoNotAvailable: Если криптобиблиотека не установлена
    """
    if not is_crypto_available():
        raise CryptoNotAvailable(
            "Для расшифровки установите: pip install cryptography"
        )

    try:
        # Декодируем base64
        data = base64.b64decode(encrypted_data.encode('ascii'))

        # Проверяем минимальную длину
        min_length = 1 + SALT_SIZE + NONCE_SIZE + TAG_SIZE
        if len(data) < min_length:
            raise DecryptionError("Повреждённые данные: слишком короткие")

        # Парсим структуру
        version = data[0:1]
        if version != VERSION:
            raise DecryptionError(f"Неподдерживаемая версия формата: {version.hex()}")

        salt = data[1:1+SALT_SIZE]
        nonce = data[1+SALT_SIZE:1+SALT_SIZE+NONCE_SIZE]
        ciphertext = data[1+SALT_SIZE+NONCE_SIZE:]

        # Деривация ключа
        key = _derive_key(password, salt)

        try:
            # Расшифровываем
            if CRYPTO_BACKEND == "cryptography":
                aesgcm = AESGCM(key)
                plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)

            elif CRYPTO_BACKEND == "pycryptodome":
                # Разделяем ciphertext и tag
                actual_ciphertext = ciphertext[:-TAG_SIZE]
                tag = ciphertext[-TAG_SIZE:]

                cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
                plaintext_bytes = cipher.decrypt_and_verify(actual_ciphertext, tag)

            result = plaintext_bytes.decode('utf-8')
            return result
        finally:
            # Обнуление sensitive data
            if 'plaintext_bytes' in dir():
                _secure_zero(plaintext_bytes)
            _secure_zero(key)

    except (ValueError, base64.binascii.Error) as e:
        raise DecryptionError(f"Повреждённые данные: {e}")
    except Exception as e:
        # Любая ошибка при расшифровке = неверный пароль или повреждение
        if "tag" in str(e).lower() or "authentication" in str(e).lower():
            raise DecryptionError("Неверный пароль")
        raise DecryptionError(f"Ошибка расшифровки: {e}")


def verify_password(encrypted_data: str, password: str) -> bool:
    """
    Проверка корректности пароля без возврата ключа.

    Args:
        encrypted_data: Зашифрованные данные
        password: Пароль для проверки

    Returns:
        True если пароль верный
    """
    try:
        decrypt_key(encrypted_data, password)
        return True
    except DecryptionError:
        return False


def generate_strong_password(length: int = 20) -> str:
    """
    Генерация криптографически стойкого пароля.

    Args:
        length: Длина пароля (минимум 12)

    Returns:
        Случайный пароль
    """
    if length < 12:
        length = 12

    # Используем URL-safe base64 для удобства
    # Каждый байт = ~1.33 символа в base64
    random_bytes = secrets.token_bytes((length * 3) // 4 + 1)
    password = base64.urlsafe_b64encode(random_bytes).decode('ascii')[:length]

    return password


def is_encrypted_format(data: str) -> bool:
    """
    Проверка, являются ли данные зашифрованным ключом.

    Отличает зашифрованный формат от старого base64-only.

    Args:
        data: Строка для проверки

    Returns:
        True если данные в зашифрованном формате
    """
    try:
        decoded = base64.b64decode(data.encode('ascii'))
        # Проверяем версию и минимальную длину
        if len(decoded) < 1 + SALT_SIZE + NONCE_SIZE + TAG_SIZE:
            return False
        return decoded[0:1] == VERSION
    except Exception:
        return False


# Для обратной совместимости со старым форматом (base64-only)
def migrate_from_base64(old_data: str, new_password: str) -> Optional[str]:
    """
    Миграция со старого формата (base64) на зашифрованный.

    Args:
        old_data: Старые данные в base64
        new_password: Новый мастер-пароль

    Returns:
        Зашифрованные данные или None если не удалось
    """
    try:
        # Декодируем старый формат
        private_key = base64.b64decode(old_data.encode('ascii')).decode('utf-8')

        # Проверяем что это похоже на приватный ключ
        if not (private_key.startswith('0x') or len(private_key) == 64):
            return None

        # Шифруем новым способом
        return encrypt_key(private_key, new_password)

    except Exception:
        return None
