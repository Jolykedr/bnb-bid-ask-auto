"""
Uniswap V4 / PancakeSwap V4 ABIs

V4 uses a different architecture with PoolManager singleton
and action-based PositionManager.
"""

# V4 StateView ABI (for reading pool state - Uniswap V4)
# NOTE: StateView contract already knows the PoolManager address (via ImmutableState)
# so we only pass poolId, NOT poolManager address
V4_STATE_VIEW_ABI = [
    # Get pool slot0 (price, tick, fees)
    {
        "inputs": [
            {"name": "poolId", "type": "bytes32"}
        ],
        "name": "getSlot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "protocolFee", "type": "uint24"},
            {"name": "lpFee", "type": "uint24"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    # Get pool liquidity
    {
        "inputs": [
            {"name": "poolId", "type": "bytes32"}
        ],
        "name": "getLiquidity",
        "outputs": [{"name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function"
    },
    # Get pool tick info
    {
        "inputs": [
            {"name": "poolId", "type": "bytes32"},
            {"name": "tick", "type": "int24"}
        ],
        "name": "getTickInfo",
        "outputs": [
            {"name": "liquidityGross", "type": "uint128"},
            {"name": "liquidityNet", "type": "int128"},
            {"name": "feeGrowthOutside0X128", "type": "uint256"},
            {"name": "feeGrowthOutside1X128", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
]

# V4 PoolManager ABI (key functions)
V4_POOL_MANAGER_ABI = [
    # Pool initialization
    {
        "inputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "key",
                "type": "tuple"
            },
            {"name": "sqrtPriceX96", "type": "uint160"}
        ],
        "name": "initialize",
        "outputs": [{"name": "tick", "type": "int24"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # Get pool state (slot0)
    {
        "inputs": [
            {"name": "id", "type": "bytes32"}
        ],
        "name": "getSlot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "protocolFee", "type": "uint24"},
            {"name": "lpFee", "type": "uint24"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    # Get pool liquidity
    {
        "inputs": [
            {"name": "id", "type": "bytes32"}
        ],
        "name": "getLiquidity",
        "outputs": [{"name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function"
    },
    # Modify liquidity
    {
        "inputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "key",
                "type": "tuple"
            },
            {
                "components": [
                    {"name": "tickLower", "type": "int24"},
                    {"name": "tickUpper", "type": "int24"},
                    {"name": "liquidityDelta", "type": "int256"},
                    {"name": "salt", "type": "bytes32"}
                ],
                "name": "params",
                "type": "tuple"
            },
            {"name": "hookData", "type": "bytes"}
        ],
        "name": "modifyLiquidity",
        "outputs": [
            {"name": "callerDelta", "type": "int256"},
            {"name": "feesAccrued", "type": "int256"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # Unlock (for flash accounting)
    {
        "inputs": [{"name": "data", "type": "bytes"}],
        "name": "unlock",
        "outputs": [{"name": "", "type": "bytes"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
]

# V4 PositionManager ABI (Uniswap style)
V4_POSITION_MANAGER_ABI = [
    # Multicall
    {
        "inputs": [{"name": "data", "type": "bytes[]"}],
        "name": "multicall",
        "outputs": [{"name": "results", "type": "bytes[]"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # Mod liq with settle
    {
        "inputs": [
            {"name": "unlockData", "type": "bytes"},
            {"name": "deadline", "type": "uint256"}
        ],
        "name": "modifyLiquidities",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    # Initialize pool
    {
        "inputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "poolKey",
                "type": "tuple"
            },
            {"name": "sqrtPriceX96", "type": "uint160"}
        ],
        "name": "initializePool",
        "outputs": [{"name": "tick", "type": "int24"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # Get pool and position info - CORRECT V4 function that returns full PoolKey!
    # Returns (PoolKey, PositionInfo) where PositionInfo is packed data
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "getPoolAndPositionInfo",
        "outputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "poolKey",
                "type": "tuple"
            },
            {"name": "info", "type": "uint256"}  # Packed PositionInfo (hasSubscriber, tickLower, tickUpper)
        ],
        "stateMutability": "view",
        "type": "function"
    },
    # Get position liquidity
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "getPositionLiquidity",
        "outputs": [{"name": "liquidity", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function"
    },
    # Legacy getPositionInfo (may not exist on all contracts)
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "getPositionInfo",
        "outputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "poolKey",
                "type": "tuple"
            },
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "liquidity", "type": "uint128"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    # Mint position (simplified - actual uses Actions encoding)
    {
        "inputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "poolKey",
                "type": "tuple"
            },
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "liquidity", "type": "uint256"},
            {"name": "amount0Max", "type": "uint128"},
            {"name": "amount1Max", "type": "uint128"},
            {"name": "owner", "type": "address"},
            {"name": "hookData", "type": "bytes"}
        ],
        "name": "mint",
        "outputs": [
            {"name": "tokenId", "type": "uint256"},
            {"name": "liquidity", "type": "uint128"},
            {"name": "amount0", "type": "uint256"},
            {"name": "amount1", "type": "uint256"}
        ],
        "stateMutability": "payable",
        "type": "function"
    },
    # Burn position
    {
        "inputs": [
            {"name": "tokenId", "type": "uint256"},
            {"name": "amount0Min", "type": "uint128"},
            {"name": "amount1Min", "type": "uint128"},
            {"name": "hookData", "type": "bytes"}
        ],
        "name": "burn",
        "outputs": [
            {"name": "amount0", "type": "uint256"},
            {"name": "amount1", "type": "uint256"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # ERC721 functions
    {
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    # ERC721Enumerable functions for wallet scanning
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "index", "type": "uint256"}
        ],
        "name": "tokenOfOwnerByIndex",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    # Transfer event
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": True, "name": "tokenId", "type": "uint256"}
        ],
        "name": "Transfer",
        "type": "event"
    },
]

# PancakeSwap V4 (Infinity) specific ABIs
# CLPositionManager has slightly different structure
PANCAKE_V4_POSITION_MANAGER_ABI = [
    # Multicall
    {
        "inputs": [{"name": "data", "type": "bytes[]"}],
        "name": "multicall",
        "outputs": [{"name": "results", "type": "bytes[]"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # Mod liq payable
    {
        "inputs": [
            {"name": "payload", "type": "bytes"},
            {"name": "deadline", "type": "uint256"}
        ],
        "name": "modifyLiquidities",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    # Initialize pool and add liquidity in one call
    {
        "inputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "poolKey",
                "type": "tuple"
            },
            {"name": "sqrtPriceX96", "type": "uint160"}
        ],
        "name": "initializePool",
        "outputs": [{"name": "tick", "type": "int24"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    # Position info
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "positions",
        "outputs": [
            {
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"}
                ],
                "name": "poolKey",
                "type": "tuple"
            },
            {"name": "tickLower", "type": "int24"},
            {"name": "tickUpper", "type": "int24"},
            {"name": "liquidity", "type": "uint128"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    # ERC721 functions
    {
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "name": "ownerOf",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    # ERC721Enumerable functions for wallet scanning
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "index", "type": "uint256"}
        ],
        "name": "tokenOfOwnerByIndex",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    # Transfer event
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": True, "name": "tokenId", "type": "uint256"}
        ],
        "name": "Transfer",
        "type": "event"
    },
]

# V4 Actions encoding
# In V4, operations are encoded as actions
# Official codes from: https://github.com/Uniswap/v4-periphery/blob/main/src/libraries/Actions.sol
class V4Actions:
    """V4 Position Manager action codes (Uniswap V4 official)."""
    # Liquidity modification
    INCREASE_LIQUIDITY = 0x00
    DECREASE_LIQUIDITY = 0x01
    MINT_POSITION = 0x02
    BURN_POSITION = 0x03
    INCREASE_LIQUIDITY_FROM_DELTAS = 0x04
    MINT_POSITION_FROM_DELTAS = 0x05

    # Swaps
    SWAP_EXACT_IN_SINGLE = 0x06
    SWAP_EXACT_IN = 0x07
    SWAP_EXACT_OUT_SINGLE = 0x08
    SWAP_EXACT_OUT = 0x09

    # Donate
    DONATE = 0x0a

    # Settlements
    SETTLE = 0x0b
    SETTLE_ALL = 0x0c
    SETTLE_PAIR = 0x0d
    TAKE = 0x0e
    TAKE_ALL = 0x0f
    TAKE_PORTION = 0x10
    TAKE_PAIR = 0x11

    # Closing
    CLOSE_CURRENCY = 0x12
    CLEAR_OR_TAKE = 0x13
    SWEEP = 0x14

    # Wrapping
    WRAP = 0x15
    UNWRAP = 0x16

    # ERC6909
    MINT_6909 = 0x17
    BURN_6909 = 0x18


# PancakeSwap V4 may have different action codes
# TODO: Verify PancakeSwap V4 action codes if needed
class PancakeV4Actions:
    """PancakeSwap V4 action codes (may differ from Uniswap)."""
    # These need to be verified against PancakeSwap V4 contracts
    # For now, using same as Uniswap V4
    INCREASE_LIQUIDITY = 0x00
    DECREASE_LIQUIDITY = 0x01
    MINT_POSITION = 0x02
    BURN_POSITION = 0x03

    SETTLE = 0x0b
    SETTLE_ALL = 0x0c
    SETTLE_PAIR = 0x0d
    TAKE = 0x0e
    TAKE_ALL = 0x0f
    TAKE_PAIR = 0x11

    CLOSE_CURRENCY = 0x12
    CLEAR_OR_TAKE = 0x13
    SWEEP = 0x14
