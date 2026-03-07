"""
License Checker - server-based validation with HWID binding and certificate pinning.

Flow:
    1. Client reads saved license key from local file
    2. Generates HWID (hardware fingerprint)
    3. Sends (key, hwid) to license server via HTTPS with cert pinning
    4. Server validates and responds
    5. Client caches valid response for 24h (offline grace period)

Usage:
    from licensing.license_checker import LicenseChecker

    checker = LicenseChecker()
    result = checker.validate()
    if not result["valid"]:
        print(result["error"])
        exit(1)
"""

import hashlib
import json
import os
import platform
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ============================================================
# LICENSE SERVER
# ============================================================
LICENSE_SERVER_URL = "https://79.137.198.213:8443"

# ============================================================
# PINNED CERTIFICATE (paste content of server.crt here after setup)
# This ensures the client ONLY trusts your specific server.
# ============================================================
PINNED_CERT = """
-----BEGIN CERTIFICATE-----
MIIBmDCCAT6gAwIBAgIUPfhrqQkc7g9yXGSz4phoWZDhf9YwCgYIKoZIzj0EAwIw
GTEXMBUGA1UEAwwObGljZW5zZS1zZXJ2ZXIwHhcNMjYwMzA3MDkyMzE2WhcNMzYw
MzA0MDkyMzE2WjAZMRcwFQYDVQQDDA5saWNlbnNlLXNlcnZlcjBZMBMGByqGSM49
AgEGCCqGSM49AwEHA0IABGDQhI/Ebf/nZh5v0aUAwSGA5vCox3i/DW4QLNrNXlZI
RX9//7pDErtt/eGW//MP0dutiYfsuA9TP6MW4b7avhOjZDBiMB0GA1UdDgQWBBTt
He6E+fQkp0+VO6hKJNneRI8F3zAfBgNVHSMEGDAWgBTtHe6E+fQkp0+VO6hKJNne
RI8F3zAPBgNVHRMBAf8EBTADAQH/MA8GA1UdEQQIMAaHBE+JxtUwCgYIKoZIzj0E
AwIDSAAwRQIgdMYUUDx1862/seWhfbUqAGAQ54AQfp8QaTvLJSkryHcCIQCVVxxj
7jH+gVjdhXPCL3nu8f/Hmw+l2VijTiVRE7vMqg==
-----END CERTIFICATE-----
""".strip()

# ============================================================
# LOCAL PATHS
# ============================================================
APP_DIR = Path(__file__).parent.parent
LICENSE_KEY_FILE = APP_DIR / "license.key"  # Stores the raw license key
CACHE_FILE = APP_DIR / ".license_cache"     # Cached validation result
CACHE_TTL = 86400  # 24 hours in seconds


class LicenseError(Exception):
    pass


class LicenseExpiredError(LicenseError):
    pass


class LicenseInvalidError(LicenseError):
    pass


class LicenseNotFoundError(LicenseError):
    pass


def get_hwid() -> str:
    """
    Generate a hardware ID based on machine-specific identifiers.
    Stable across reboots, changes on hardware/OS reinstall.
    """
    parts = []

    # Machine name + processor
    parts.append(platform.node())
    parts.append(platform.machine())
    parts.append(platform.processor())

    # Windows: use machine GUID from registry
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["reg", "query",
                 r"HKLM\SOFTWARE\Microsoft\Cryptography",
                 "/v", "MachineGuid"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "MachineGuid" in line:
                    guid = line.split()[-1]
                    parts.append(guid)
                    break
        except Exception:
            pass

    # Linux: use machine-id
    elif sys.platform == "linux":
        for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
            try:
                parts.append(Path(path).read_text().strip())
                break
            except Exception:
                pass

    # macOS: use hardware UUID
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["system_profiler", "SPHardwareDataType"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "Hardware UUID" in line:
                    parts.append(line.split(":")[-1].strip())
                    break
        except Exception:
            pass

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def _create_ssl_context() -> ssl.SSLContext:
    """Create SSL context with certificate pinning."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    if PINNED_CERT and "PASTE_SERVER" not in PINNED_CERT:
        # Write pinned cert to temp file for loading
        cert_path = Path(tempfile.gettempdir()) / "ll_license_cert.pem"
        cert_path.write_text(PINNED_CERT)
        ctx.load_verify_locations(str(cert_path))
        ctx.check_hostname = False  # Self-signed cert uses IP, not hostname
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        # No pinned cert yet (development) — skip verification
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    return ctx


def _server_request(endpoint: str, data: dict, timeout: int = 10) -> dict:
    """Make HTTPS request to license server."""
    url = f"{LICENSE_SERVER_URL}{endpoint}"
    body = json.dumps(data).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    ctx = _create_ssl_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
            return {"valid": False, "error": err_body.get("detail", str(e))}
        except Exception:
            return {"valid": False, "error": f"Server error: {e.code}"}
    except urllib.error.URLError as e:
        raise ConnectionError(f"Cannot reach license server: {e.reason}")
    except Exception as e:
        raise ConnectionError(f"Connection error: {e}")


def _load_cache() -> Optional[dict]:
    """Load cached validation result if still fresh."""
    try:
        if not CACHE_FILE.exists():
            return None
        raw = CACHE_FILE.read_text()
        cache = json.loads(raw)
        if time.time() - cache.get("cached_at", 0) < CACHE_TTL:
            return cache
    except Exception:
        pass
    return None


def _save_cache(result: dict):
    """Save validation result to cache."""
    try:
        result["cached_at"] = time.time()
        CACHE_FILE.write_text(json.dumps(result))
    except Exception:
        pass


def _clear_cache():
    """Remove cached validation."""
    try:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
    except Exception:
        pass


class LicenseChecker:
    """
    Server-based license checker with HWID binding and offline cache.
    """

    def __init__(self):
        self.hwid = get_hwid()

    def get_license_key(self) -> Optional[str]:
        """Read license key from file."""
        # Check multiple locations
        search_paths = [
            LICENSE_KEY_FILE,
            APP_DIR / "license.lic",  # backwards compat
            Path.home() / ".bnb_ladder" / "license.key",
        ]
        for path in search_paths:
            if path.exists():
                key = path.read_text().strip()
                if key:
                    return key
        return None

    def save_license_key(self, key: str):
        """Save license key to file."""
        LICENSE_KEY_FILE.write_text(key.strip())

    def validate(self, force_online: bool = False) -> dict:
        """
        Validate license.

        Args:
            force_online: Skip cache and check server directly

        Returns:
            dict with: valid, error, expires_at, days_remaining
        """
        result = {
            "valid": False,
            "error": None,
            "expires_at": None,
            "days_remaining": None,
        }

        # 1. Get license key
        key = self.get_license_key()
        if not key:
            result["error"] = "License key not found. Enter your key in Settings."
            return result

        # 2. Check cache (unless forced online)
        if not force_online:
            cached = _load_cache()
            if cached and cached.get("valid"):
                cached.pop("cached_at", None)
                return cached

        # 3. Online validation
        try:
            server_result = _server_request("/validate", {
                "license_key": key,
                "hwid": self.hwid,
            })

            if server_result.get("valid"):
                _save_cache(server_result)
            else:
                _clear_cache()

            return server_result

        except ConnectionError:
            # Server unreachable — try cache even if expired (grace period)
            cached = _load_cache()
            if cached and cached.get("valid"):
                cached.pop("cached_at", None)
                cached["offline_mode"] = True
                return cached

            result["error"] = (
                "Cannot reach license server and no cached validation. "
                "Check your internet connection."
            )
            return result

    def activate(self, key: str) -> dict:
        """
        Activate a new license key.

        Args:
            key: License key string (LL-XXXX-XXXX-...)

        Returns:
            dict with: valid, error, expires_at, days_remaining
        """
        try:
            result = _server_request("/activate", {
                "license_key": key,
                "hwid": self.hwid,
            })

            if result.get("valid"):
                self.save_license_key(key)
                _save_cache(result)

            return result

        except ConnectionError as e:
            return {"valid": False, "error": str(e)}

    def verify_or_exit(self, show_info: bool = True):
        """Validate license and exit if invalid."""
        result = self.validate()

        if not result["valid"]:
            print("=" * 60)
            print("LICENSE ERROR")
            print("=" * 60)
            print(f"  {result['error']}")
            print()
            print("Contact support to resolve this issue.")
            print("=" * 60)
            sys.exit(1)

        if show_info:
            days = result.get("days_remaining", "?")
            expires = result.get("expires_at", "?")
            offline = " (offline)" if result.get("offline_mode") else ""
            print(f"License: OK | {days} days remaining | Expires: {expires}{offline}")


# ============================================================
# Backwards-compatible exports
# ============================================================

def find_license_file(search_paths=None) -> Optional[str]:
    """Backwards compatibility — now checks for license.key."""
    checker = LicenseChecker()
    key = checker.get_license_key()
    return str(LICENSE_KEY_FILE) if key else None


def require_license(license_path: str = "license.key", show_info: bool = True):
    """Decorator for functions requiring a valid license."""
    import functools

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            checker = LicenseChecker()
            checker.verify_or_exit(show_info=show_info)
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="License Checker")
    parser.add_argument("--activate", "-a", help="Activate with a license key")
    parser.add_argument("--validate", "-v", action="store_true", help="Validate current license")
    parser.add_argument("--hwid", action="store_true", help="Show this machine's HWID")
    args = parser.parse_args()

    if args.hwid:
        print(f"HWID: {get_hwid()}")

    elif args.activate:
        checker = LicenseChecker()
        result = checker.activate(args.activate)
        if result["valid"]:
            print(f"Activated! Expires: {result['expires_at']}, Days: {result['days_remaining']}")
        else:
            print(f"Activation failed: {result['error']}")
            sys.exit(1)

    else:
        checker = LicenseChecker()
        result = checker.validate()
        print("=" * 50)
        if result["valid"]:
            print(f"  Status: VALID")
            print(f"  Expires: {result.get('expires_at', '?')}")
            print(f"  Days remaining: {result.get('days_remaining', '?')}")
        else:
            print(f"  Status: INVALID")
            print(f"  Error: {result['error']}")
        print("=" * 50)
