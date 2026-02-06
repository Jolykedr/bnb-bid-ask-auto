"""
Uniswap V4 / PancakeSwap V4 Contracts Module

V4 uses singleton architecture with custom fees support.
"""

from .pool_manager import V4PoolManager
from .position_manager import V4PositionManager
from .constants import (
    UNISWAP_V4_ADDRESSES,
    PANCAKESWAP_V4_ADDRESSES,
    V4Protocol,
    get_v4_addresses
)

__all__ = [
    'V4PoolManager',
    'V4PositionManager',
    'UNISWAP_V4_ADDRESSES',
    'PANCAKESWAP_V4_ADDRESSES',
    'V4Protocol',
    'get_v4_addresses'
]
