"""
License Server Configuration.
Все секреты читаются из переменных окружения / .env файла.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# --- Database ---
DATABASE_URL = os.getenv("LICENSE_DB_URL", f"sqlite:///{BASE_DIR / 'licenses.db'}")

# --- Admin ---
# Пароль для админских эндпоинтов (генерация ключей, сброс HWID и т.д.)
# Генерируй: python -c "import secrets; print(secrets.token_urlsafe(32))"
ADMIN_SECRET = os.getenv("LICENSE_ADMIN_SECRET", "")

# --- Rate Limiting ---
RATE_LIMIT_VALIDATE = int(os.getenv("RATE_LIMIT_VALIDATE", "10"))  # req/min per IP
RATE_LIMIT_ACTIVATE = int(os.getenv("RATE_LIMIT_ACTIVATE", "5"))   # req/min per IP

# --- SSL ---
SSL_CERT_FILE = os.getenv("SSL_CERT_FILE", str(BASE_DIR / "certs" / "server.crt"))
SSL_KEY_FILE = os.getenv("SSL_KEY_FILE", str(BASE_DIR / "certs" / "server.key"))

# --- Server ---
HOST = os.getenv("LICENSE_HOST", "0.0.0.0")
PORT = int(os.getenv("LICENSE_PORT", "8443"))
