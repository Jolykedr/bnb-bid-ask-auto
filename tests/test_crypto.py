"""
Tests for src/crypto.py - Secure Key Storage Module.

Covers: is_crypto_available, get_crypto_backend, _derive_key, encrypt_key,
        decrypt_key, verify_password, generate_strong_password,
        is_encrypted_format, migrate_from_base64, and exception classes.
"""

import base64
import pytest

from src.crypto import (
    is_crypto_available,
    get_crypto_backend,
    _derive_key,
    encrypt_key,
    decrypt_key,
    verify_password,
    generate_strong_password,
    is_encrypted_format,
    migrate_from_base64,
    CryptoError,
    DecryptionError,
    CryptoNotAvailable,
    VERSION,
    SALT_SIZE,
    NONCE_SIZE,
    TAG_SIZE,
    KEY_SIZE,
    ITERATIONS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_KEY = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
SAMPLE_PASSWORD = "TestPassword_42!"


@pytest.fixture
def encrypted_sample():
    """Encrypt SAMPLE_KEY once for reuse across tests that need ciphertext."""
    return encrypt_key(SAMPLE_KEY, SAMPLE_PASSWORD)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class TestExceptions:
    """Verify exception class hierarchy."""

    def test_decryption_error_is_crypto_error(self):
        """DecryptionError should be a subclass of CryptoError."""
        assert issubclass(DecryptionError, CryptoError)

    def test_crypto_not_available_is_crypto_error(self):
        """CryptoNotAvailable should be a subclass of CryptoError."""
        assert issubclass(CryptoNotAvailable, CryptoError)

    def test_crypto_error_is_exception(self):
        """CryptoError should be a regular Exception."""
        assert issubclass(CryptoError, Exception)


# ---------------------------------------------------------------------------
# is_crypto_available / get_crypto_backend
# ---------------------------------------------------------------------------

class TestCryptoAvailability:
    """Tests for crypto availability helpers."""

    def test_is_crypto_available_returns_true(self):
        """Crypto library must be installed in the test environment."""
        assert is_crypto_available() is True

    def test_get_crypto_backend_returns_known_value(self):
        """Backend should be one of the known strings."""
        backend = get_crypto_backend()
        assert backend in ("cryptography", "pycryptodome")

    def test_get_crypto_backend_not_none(self):
        """Backend string should never be 'none' when crypto is available."""
        assert get_crypto_backend() != "none"


# ---------------------------------------------------------------------------
# _derive_key  (minimal due to PBKDF2 cost ~0.5s per call)
# ---------------------------------------------------------------------------

class TestDeriveKey:
    """Tests for PBKDF2 key derivation (kept minimal for speed)."""

    def test_returns_32_bytes(self):
        """Derived key must be exactly 32 bytes (AES-256)."""
        salt = b"\x00" * SALT_SIZE
        key = _derive_key("password", salt)
        assert isinstance(key, bytes)
        assert len(key) == KEY_SIZE

    def test_deterministic(self):
        """Same password + salt must produce the same key."""
        salt = b"\x01" * SALT_SIZE
        key1 = _derive_key("deterministic", salt)
        key2 = _derive_key("deterministic", salt)
        assert key1 == key2

    def test_different_salt_different_key(self):
        """Different salts must produce different keys."""
        salt_a = b"\xaa" * SALT_SIZE
        salt_b = b"\xbb" * SALT_SIZE
        key_a = _derive_key("same_password", salt_a)
        key_b = _derive_key("same_password", salt_b)
        assert key_a != key_b


# ---------------------------------------------------------------------------
# encrypt_key
# ---------------------------------------------------------------------------

class TestEncryptKey:
    """Tests for encrypt_key."""

    def test_returns_base64_string(self):
        """Encrypted output must be a valid Base64-encoded string."""
        result = encrypt_key(SAMPLE_KEY, SAMPLE_PASSWORD)
        assert isinstance(result, str)
        # Must decode without error
        decoded = base64.b64decode(result.encode("ascii"))
        assert len(decoded) > 0

    def test_encrypted_format_structure(self):
        """Binary payload: version(1) + salt(16) + nonce(12) + ciphertext + tag(16)."""
        result = encrypt_key(SAMPLE_KEY, SAMPLE_PASSWORD)
        decoded = base64.b64decode(result.encode("ascii"))

        min_length = 1 + SALT_SIZE + NONCE_SIZE + TAG_SIZE
        assert len(decoded) >= min_length

        # First byte is the format version
        assert decoded[0:1] == VERSION

    def test_each_call_produces_different_output(self):
        """Random salt and nonce mean every encryption is unique."""
        enc1 = encrypt_key(SAMPLE_KEY, SAMPLE_PASSWORD)
        enc2 = encrypt_key(SAMPLE_KEY, SAMPLE_PASSWORD)
        assert enc1 != enc2

    def test_ciphertext_contains_no_plaintext(self):
        """The encrypted blob must NOT contain the raw private key."""
        result = encrypt_key(SAMPLE_KEY, SAMPLE_PASSWORD)
        decoded = base64.b64decode(result.encode("ascii"))
        assert SAMPLE_KEY.encode("utf-8") not in decoded


# ---------------------------------------------------------------------------
# decrypt_key
# ---------------------------------------------------------------------------

class TestDecryptKey:
    """Tests for decrypt_key."""

    def test_roundtrip(self, encrypted_sample):
        """encrypt then decrypt must recover the original key."""
        decrypted = decrypt_key(encrypted_sample, SAMPLE_PASSWORD)
        assert decrypted == SAMPLE_KEY

    def test_roundtrip_key_without_0x_prefix(self):
        """Keys without 0x prefix should also round-trip correctly."""
        raw_hex = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        enc = encrypt_key(raw_hex, "pw123")
        assert decrypt_key(enc, "pw123") == raw_hex

    def test_wrong_password_raises_decryption_error(self, encrypted_sample):
        """Wrong password must raise DecryptionError."""
        with pytest.raises(DecryptionError):
            decrypt_key(encrypted_sample, "WrongPassword!")

    def test_corrupted_data_raises_decryption_error(self, encrypted_sample):
        """Flipping bits in ciphertext must raise DecryptionError."""
        decoded = bytearray(base64.b64decode(encrypted_sample.encode("ascii")))
        # Corrupt a byte in the ciphertext area (after version+salt+nonce)
        corrupt_index = 1 + SALT_SIZE + NONCE_SIZE + 2
        if corrupt_index < len(decoded):
            decoded[corrupt_index] ^= 0xFF
        corrupted = base64.b64encode(bytes(decoded)).decode("ascii")

        with pytest.raises(DecryptionError):
            decrypt_key(corrupted, SAMPLE_PASSWORD)

    def test_short_data_raises_decryption_error(self):
        """Data shorter than the minimum header raises DecryptionError."""
        short = base64.b64encode(b"\x01" + b"\x00" * 10).decode("ascii")
        with pytest.raises(DecryptionError):
            decrypt_key(short, SAMPLE_PASSWORD)

    def test_wrong_version_raises_decryption_error(self, encrypted_sample):
        """Unknown version byte must raise DecryptionError."""
        decoded = bytearray(base64.b64decode(encrypted_sample.encode("ascii")))
        decoded[0] = 0xFF  # Invalid version
        modified = base64.b64encode(bytes(decoded)).decode("ascii")

        with pytest.raises(DecryptionError):
            decrypt_key(modified, SAMPLE_PASSWORD)

    def test_empty_base64_raises_decryption_error(self):
        """Empty payload (valid base64 but no bytes) should raise."""
        empty_b64 = base64.b64encode(b"").decode("ascii")
        with pytest.raises(DecryptionError):
            decrypt_key(empty_b64, SAMPLE_PASSWORD)


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------

class TestVerifyPassword:
    """Tests for verify_password."""

    def test_correct_password_returns_true(self, encrypted_sample):
        """Correct password must return True."""
        assert verify_password(encrypted_sample, SAMPLE_PASSWORD) is True

    def test_wrong_password_returns_false(self, encrypted_sample):
        """Wrong password must return False (no exception)."""
        assert verify_password(encrypted_sample, "WrongPassword!") is False


# ---------------------------------------------------------------------------
# generate_strong_password
# ---------------------------------------------------------------------------

class TestGenerateStrongPassword:
    """Tests for generate_strong_password."""

    def test_default_length(self):
        """Default length is 20 characters."""
        pw = generate_strong_password()
        assert len(pw) == 20

    def test_custom_length(self):
        """Requested length should match exactly."""
        pw = generate_strong_password(length=30)
        assert len(pw) == 30

    def test_minimum_length_enforced(self):
        """Requesting fewer than 12 characters still returns at least 12."""
        pw = generate_strong_password(length=5)
        assert len(pw) >= 12

    def test_two_calls_produce_different_passwords(self):
        """Two consecutive calls must yield different passwords."""
        pw1 = generate_strong_password()
        pw2 = generate_strong_password()
        assert pw1 != pw2

    def test_returns_string(self):
        """Return type must be str."""
        pw = generate_strong_password()
        assert isinstance(pw, str)


# ---------------------------------------------------------------------------
# is_encrypted_format
# ---------------------------------------------------------------------------

class TestIsEncryptedFormat:
    """Tests for is_encrypted_format."""

    def test_encrypted_data_returns_true(self, encrypted_sample):
        """Data produced by encrypt_key must be recognized."""
        assert is_encrypted_format(encrypted_sample) is True

    def test_raw_hex_returns_false(self):
        """A raw hex private key string must NOT be recognized."""
        assert is_encrypted_format(
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        ) is False

    def test_random_text_returns_false(self):
        """Arbitrary text must return False."""
        assert is_encrypted_format("hello world") is False

    def test_empty_string_returns_false(self):
        """Empty string must return False."""
        assert is_encrypted_format("") is False

    def test_short_base64_returns_false(self):
        """Valid base64 but too short to be our format."""
        short = base64.b64encode(b"\x01\x02\x03").decode("ascii")
        assert is_encrypted_format(short) is False

    def test_wrong_version_returns_false(self):
        """Correct length but wrong version byte should return False."""
        # Build a blob with version=0x99 (not 0x01)
        fake = b"\x99" + b"\x00" * (SALT_SIZE + NONCE_SIZE + TAG_SIZE + 10)
        encoded = base64.b64encode(fake).decode("ascii")
        assert is_encrypted_format(encoded) is False


# ---------------------------------------------------------------------------
# migrate_from_base64
# ---------------------------------------------------------------------------

class TestMigrateFromBase64:
    """Tests for migrate_from_base64."""

    def test_valid_0x_key_migrates(self):
        """Old base64-encoded key starting with 0x should migrate."""
        raw_key = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        old_data = base64.b64encode(raw_key.encode("utf-8")).decode("ascii")

        result = migrate_from_base64(old_data, "new_password")

        assert result is not None
        assert is_encrypted_format(result) is True
        # Verify we can decrypt back to the original key
        assert decrypt_key(result, "new_password") == raw_key

    def test_valid_64_char_hex_key_migrates(self):
        """Old base64-encoded 64-char hex key (no 0x) should migrate."""
        raw_key = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        assert len(raw_key) == 64
        old_data = base64.b64encode(raw_key.encode("utf-8")).decode("ascii")

        result = migrate_from_base64(old_data, "migrate_pw")

        assert result is not None
        assert is_encrypted_format(result) is True
        assert decrypt_key(result, "migrate_pw") == raw_key

    def test_invalid_data_returns_none(self):
        """Non-key data should return None."""
        garbage = base64.b64encode(b"not a key at all").decode("ascii")
        assert migrate_from_base64(garbage, "pw") is None

    def test_non_base64_returns_none(self):
        """Completely invalid base64 should return None, not raise."""
        assert migrate_from_base64("%%%not_base64%%%", "pw") is None

    def test_empty_string_returns_none(self):
        """Empty input should return None."""
        assert migrate_from_base64("", "pw") is None


# ---------------------------------------------------------------------------
# Constants sanity check
# ---------------------------------------------------------------------------

class TestConstants:
    """Verify module-level constants match documented values."""

    def test_version(self):
        assert VERSION == b"\x01"

    def test_salt_size(self):
        assert SALT_SIZE == 16

    def test_nonce_size(self):
        assert NONCE_SIZE == 12

    def test_key_size(self):
        assert KEY_SIZE == 32

    def test_tag_size(self):
        assert TAG_SIZE == 16

    def test_iterations(self):
        assert ITERATIONS == 600_000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
