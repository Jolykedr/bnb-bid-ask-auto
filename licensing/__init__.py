"""
Licensing module for BNB Liquidity Ladder.

Для разработчика (тебя):
    from licensing.license_generator import generate_license, init_keys

Для софта (клиентам):
    from licensing.license_checker import require_license, LicenseChecker
"""

from .license_checker import (
    LicenseChecker,
    LicenseError,
    LicenseExpiredError,
    LicenseInvalidError,
    LicenseNotFoundError,
    require_license,
    require_feature,
    find_license_file,
)

__all__ = [
    "LicenseChecker",
    "LicenseError",
    "LicenseExpiredError",
    "LicenseInvalidError",
    "LicenseNotFoundError",
    "require_license",
    "require_feature",
    "find_license_file",
]
