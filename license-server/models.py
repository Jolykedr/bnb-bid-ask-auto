"""
Database models for license server.
"""

from datetime import datetime

from sqlalchemy import Column, String, Boolean, DateTime, Integer, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import DATABASE_URL

Base = declarative_base()


class LicenseKey(Base):
    __tablename__ = "license_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Ключ хранится как SHA-256 хеш (необратимо)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)

    # Короткий префикс для идентификации (первые 8 символов ключа, например "LL-A1B2")
    key_prefix = Column(String(16), nullable=False)

    # Информация о клиенте
    user_label = Column(String(256), nullable=False)  # "client@email.com" или имя

    # Привязка к железу
    hwid = Column(String(128), nullable=True)  # None = ещё не активирован
    max_devices = Column(Integer, default=1, nullable=False)

    # Сроки
    created_at = Column(DateTime, default=lambda: datetime.utcnow(), nullable=False)
    expires_at = Column(DateTime, nullable=False)

    # Статус
    is_active = Column(Boolean, default=True, nullable=False)
    is_blocked = Column(Boolean, default=False, nullable=False)
    block_reason = Column(String(512), nullable=True)

    # Последняя проверка
    last_validated_at = Column(DateTime, nullable=True)
    last_ip = Column(String(45), nullable=True)
    validation_count = Column(Integer, default=0, nullable=False)

    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    def __repr__(self):
        return f"<LicenseKey {self.key_prefix}... user={self.user_label} active={self.is_active}>"


# --- Database setup ---

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
