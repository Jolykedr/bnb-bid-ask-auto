"""
V4 Constants and Contract Addresses
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class V4Protocol(Enum):
    """Supported V4 protocols."""
    UNISWAP = "uniswap"
    PANCAKESWAP = "pancakeswap"


@dataclass
class V4Addresses:
    """V4 contract addresses for a specific chain."""
    pool_manager: str
    position_manager: str
    quoter: Optional[str] = None
    universal_router: Optional[str] = None
    vault: Optional[str] = None  # PancakeSwap specific
    state_view: Optional[str] = None  # Uniswap V4 StateView for reading pool state


# Uniswap V4 Addresses
UNISWAP_V4_ADDRESSES = {
    # BNB Chain (56)
    56: V4Addresses(
        pool_manager="0x28e2ea090877bf75740558f6bfb36a5ffee9e9df",
        position_manager="0x7a4a5c919ae2541aed11041a1aeee68f1287f95b",
        quoter="0x9f75dd27d6664c475b90e105573e550ff69437b0",
        universal_router="0x1906c1d672b88cd1b9ac7593301ca990f94eae07",
        state_view="0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4"
    ),
    # Ethereum Mainnet (1)
    1: V4Addresses(
        pool_manager="0x000000000004444c5dc75cb358380d2e3de08a90",
        position_manager="0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",
        quoter="0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203",
        universal_router="0x66a9893cc07d91d95644aedd05d03f95e1dba8af"
    ),
    # Base Mainnet (8453)
    8453: V4Addresses(
        pool_manager="0x498581ff718922c3f8e6a244956af099b2652b2b",
        position_manager="0x7c5f5a4bbd8fd63184577525326123b519429bdc",
        quoter="0x0d5e0f971ed27fbff6c2837bf31316121532048d",
        universal_router="0x6ff5693b99212da76ad316178a184ab56d299b43",
        state_view="0xa3c0c9b65bad0b08107aa264b0f3db444b867a71"
    ),
}

# PancakeSwap V4 (Infinity) Addresses
PANCAKESWAP_V4_ADDRESSES = {
    # BNB Chain (56)
    56: V4Addresses(
        pool_manager="0xa0FfB9c1CE1Fe56963B0321B32E7A0302114058b",  # CLPoolManager
        position_manager="0x55f4c8abA71A1e923edC303eb4fEfF14608cC226",  # CLPositionManager
        quoter="0xd0737C9762912dD34c3271197E362Aa736Df0926",  # CLQuoter
        universal_router="0xd9c500dff816a1da21a48a732d3498bf09dc9aeb",
        vault="0x238a358808379702088667322f80aC48bAd5e6c4"
    ),
}


def get_v4_addresses(chain_id: int, protocol: V4Protocol) -> Optional[V4Addresses]:
    """Get V4 addresses for a specific chain and protocol."""
    if protocol == V4Protocol.UNISWAP:
        return UNISWAP_V4_ADDRESSES.get(chain_id)
    elif protocol == V4Protocol.PANCAKESWAP:
        return PANCAKESWAP_V4_ADDRESSES.get(chain_id)
    return None


# V4 Fee Constants
# In V4, fee is specified in hundredths of a bip (1/1,000,000)
# So 3000 = 0.30%, 10000 = 1.00%, 33330 = 3.333%
MAX_V4_FEE = 1_000_000  # 100%
MIN_V4_FEE = 0


def fee_percent_to_v4(percent: float) -> int:
    """
    Convert fee percentage to V4 fee format.

    Uses round() to avoid floating point precision issues.
    E.g., 3.8998% should become 38998, not 38997 due to float errors.

    Args:
        percent: Fee in percentage (e.g., 0.3 for 0.3%, 3.333 for 3.333%)

    Returns:
        V4 fee value (e.g., 3000 for 0.3%, 33330 for 3.333%)
    """
    return round(percent * 10000)


def v4_fee_to_percent(v4_fee: int) -> float:
    """
    Convert V4 fee to percentage.

    Args:
        v4_fee: V4 fee value (e.g., 3000)

    Returns:
        Fee in percentage (e.g., 0.3)
    """
    return v4_fee / 10000


# Common tick spacings for V4
# In V4, tickSpacing can be any value, but common ones are:
COMMON_TICK_SPACINGS = {
    1: 1,      # Very tight - for stable pairs
    10: 10,    # Tight
    60: 60,    # Standard (like V3 0.3%)
    100: 100,  # Medium
    200: 200,  # Wide (like V3 1%)
}


def suggest_tick_spacing(fee_percent: float) -> int:
    """
    Calculate tick spacing based on fee using Uniswap V4 formula.

    Formula: tick_spacing = fee_percent × 200

    Example: 0.3% fee → tick_spacing = 60
             1.0% fee → tick_spacing = 200
             3.3321% fee → tick_spacing = 666

    Minimum tick_spacing is 1.
    """
    tick_spacing = round(fee_percent * 200)
    return max(1, tick_spacing)
