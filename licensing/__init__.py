"""
Licensing module for BNB Liquidity Ladder.

Server-based license validation with HWID binding.

Usage:
    from licensing import LicenseChecker, LicenseError

    checker = LicenseChecker()
    result = checker.validate()
"""

from .license_checker import (
    LicenseChecker,
    LicenseError,
    LicenseExpiredError,
    LicenseInvalidError,
    LicenseNotFoundError,
    require_license,
    find_license_file,
)

__all__ = [
    "LicenseChecker",
    "LicenseError",
    "LicenseExpiredError",
    "LicenseInvalidError",
    "LicenseNotFoundError",
    "require_license",
    "find_license_file",
]
