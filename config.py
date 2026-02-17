"""
Configuration for BNB Chain Liquidity Provider

Конфигурация для работы с PancakeSwap V3 на BNB Chain.
PancakeSwap использует форк Uniswap V3.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class ChainConfig:
    """Конфигурация сети."""
    chain_id: int
    rpc_url: str
    explorer_url: str
    native_token: str
    position_manager: str
    multicall3: str
    pool_factory: str = ""  # V3 Pool Factory address


@dataclass
class TokenConfig:
    """Конфигурация токена."""
    address: str
    symbol: str
    decimals: int


# ============================================================
# CHAIN CONFIGURATIONS
# ============================================================

# BNB Chain (BSC Mainnet) - PancakeSwap V3
BNB_CHAIN = ChainConfig(
    chain_id=56,
    rpc_url="https://bsc-dataseed.binance.org/",
    explorer_url="https://bscscan.com",
    native_token="BNB",
    # PancakeSwap V3 NonfungiblePositionManager
    position_manager="0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    # PancakeSwap V3 Factory
    pool_factory="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
)

# ============================================================
# UNISWAP V3 ON BSC (different from PancakeSwap V3!)
# ============================================================
# Uniswap V3 is officially deployed on BSC with different addresses

@dataclass
class V3DexConfig:
    """Configuration for V3 DEX (multiple DEXes per chain)."""
    name: str
    position_manager: str
    pool_factory: str
    fee_tiers: list  # Supported fee tiers
    swap_router: str = ""  # V3 swap router (SmartRouter for PCS, SwapRouter02 for Uni)

# Uniswap V3 on BSC
UNISWAP_V3_BSC = V3DexConfig(
    name="Uniswap V3",
    position_manager="0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613",
    pool_factory="0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7",
    fee_tiers=[100, 500, 3000, 10000],  # 0.01%, 0.05%, 0.3%, 1%
    swap_router="0xB971eF87ede563556b2ED4b1C0b0019111Dd85d2",  # Uniswap UniversalRouter BSC
)

# PancakeSwap V3 on BSC
PANCAKESWAP_V3_BSC = V3DexConfig(
    name="PancakeSwap V3",
    position_manager="0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    pool_factory="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
    fee_tiers=[100, 500, 2500, 10000],  # 0.01%, 0.05%, 0.25%, 1%
    swap_router="0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",  # PCS SmartRouter BSC
)

# Map of all V3 DEXes per chain
V3_DEXES = {
    56: {  # BSC
        "uniswap": UNISWAP_V3_BSC,
        "pancakeswap": PANCAKESWAP_V3_BSC,
    },
    1: {  # Ethereum
        "uniswap": V3DexConfig(
            name="Uniswap V3",
            position_manager="0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
            pool_factory="0x1F98431c8aD98523631AE4a59f267346ea31F984",
            fee_tiers=[100, 500, 3000, 10000],
            swap_router="0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",  # Uniswap SwapRouter02 ETH
        ),
        "pancakeswap": V3DexConfig(
            name="PancakeSwap V3",
            position_manager="0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
            pool_factory="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
            fee_tiers=[100, 500, 2500, 10000],
            swap_router="0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",  # PCS SmartRouter ETH
        ),
    },
    8453: {  # Base
        "uniswap": V3DexConfig(
            name="Uniswap V3",
            position_manager="0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
            pool_factory="0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
            fee_tiers=[100, 500, 3000, 10000],
            swap_router="0x2626664c2603336E57B271c5C0b26F421741e481",  # Uniswap SwapRouter02 BASE
        ),
        "pancakeswap": V3DexConfig(
            name="PancakeSwap V3",
            position_manager="0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
            pool_factory="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
            fee_tiers=[100, 500, 2500, 10000],  # 0.01%, 0.05%, 0.25%, 1%
            swap_router="0x678Aa4bF4E210cf2166753e054d5b7c31cc7fa86",  # PCS SmartRouter BASE
        ),
    },
}

# BNB Chain Testnet
BNB_TESTNET = ChainConfig(
    chain_id=97,
    rpc_url="https://data-seed-prebsc-1-s1.binance.org:8545/",
    explorer_url="https://testnet.bscscan.com",
    native_token="tBNB",
    position_manager="0x427bF5b37357632377eCbEC9de3626C71A5396c1",
    multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    # PancakeSwap V3 Factory (Testnet)
    pool_factory="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
)

# Ethereum Mainnet (для справки)
ETHEREUM = ChainConfig(
    chain_id=1,
    rpc_url="https://eth.llamarpc.com",
    explorer_url="https://etherscan.io",
    native_token="ETH",
    # Uniswap V3 NonfungiblePositionManager
    position_manager="0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
    multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    # Uniswap V3 Factory
    pool_factory="0x1F98431c8aD98523631AE4a59f267346ea31F984"
)

# Base Mainnet
# Note: mainnet.base.org has strict rate limits, use alternative RPC if needed:
# - https://base.llamarpc.com
# - https://base-rpc.publicnode.com
# - https://rpc.ankr.com/base
BASE = ChainConfig(
    chain_id=8453,
    rpc_url="https://base.llamarpc.com",
    explorer_url="https://basescan.org",
    native_token="ETH",
    # Uniswap V3 NonfungiblePositionManager (Base)
    position_manager="0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
    multicall3="0xcA11bde05977b3631167028862bE2a173976CA11",
    # Uniswap V3 Factory (Base)
    pool_factory="0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
)

# ============================================================
# TOKEN CONFIGURATIONS (BNB Chain)
# ============================================================

TOKENS_BNB: Dict[str, TokenConfig] = {
    "WBNB": TokenConfig(
        address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        symbol="WBNB",
        decimals=18
    ),
    "BUSD": TokenConfig(
        address="0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
        symbol="BUSD",
        decimals=18
    ),
    "USDT": TokenConfig(
        address="0x55d398326f99059fF775485246999027B3197955",
        symbol="USDT",
        decimals=18
    ),
    "USDC": TokenConfig(
        address="0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        symbol="USDC",
        decimals=18
    ),
    "CAKE": TokenConfig(
        address="0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
        symbol="CAKE",
        decimals=18
    ),
    "ETH": TokenConfig(
        address="0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
        symbol="ETH",
        decimals=18
    ),
    "BTCB": TokenConfig(
        address="0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
        symbol="BTCB",
        decimals=18
    ),
}

# ============================================================
# TOKEN CONFIGURATIONS (Base)
# ============================================================

TOKENS_BASE: Dict[str, TokenConfig] = {
    "WETH": TokenConfig(
        address="0x4200000000000000000000000000000000000006",
        symbol="WETH",
        decimals=18
    ),
    "USDC": TokenConfig(
        address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        symbol="USDC",
        decimals=6  # USDC on Base has 6 decimals
    ),
    "USDbC": TokenConfig(
        address="0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",
        symbol="USDbC",
        decimals=6  # Bridged USDC
    ),
    "DAI": TokenConfig(
        address="0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
        symbol="DAI",
        decimals=18
    ),
    "cbETH": TokenConfig(
        address="0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
        symbol="cbETH",
        decimals=18
    ),
}

# ============================================================
# FEE TIERS
# ============================================================

# PancakeSwap V3 fee tiers (same as Uniswap V3)
FEE_TIERS = {
    "LOWEST": 100,    # 0.01% - стейблкоины
    "LOW": 500,       # 0.05% - стабильные пары
    "MEDIUM_PSC": 2500,   # 0.25% - большинство пар (PancakeSwap specific)
    "MEDIUM_UNI": 3000,   # 0.30% - стандартный Uniswap tier
    "HIGH": 10000,    # 1.00% - экзотические пары
}

# Tick spacing для каждого fee tier
TICK_SPACING = {
    100: 1,
    500: 10,
    2500: 50,   # PancakeSwap specific
    3000: 60,   # Uniswap standard
    10000: 200,
}

# ============================================================
# DEFAULT SETTINGS
# ============================================================

DEFAULT_SLIPPAGE = 0.5  # 0.5%
DEFAULT_DEADLINE_MINUTES = 60
DEFAULT_GAS_LIMIT_MINT = 500000
DEFAULT_GAS_LIMIT_MULTICALL = 2000000

# ============================================================
# STABLECOIN REGISTRY (единый источник правды)
# ============================================================
# Адрес (lowercase) → decimals
# Используется для:
# - Определения invert_price (стейблкоин как currency0 → инверсия)
# - Определения decimals стейблкоина для расчёта сумм
# - Определения quote-токена в bid-ask лесенке

STABLECOINS: Dict[str, int] = {
    # BNB Chain (56)
    "0x55d398326f99059ff775485246999027b3197955": 18,  # USDT (BSC)
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18,  # USDC (BSC)
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": 18,  # BUSD (BSC)
    "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3": 18,  # DAI (BSC)
    # Base (8453)
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,   # USDC (Base)
    "0xd9aaec86b65d86f6a7b5b1b0c42ffa531710b6ca": 6,   # USDbC bridged (Base)
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": 18,  # DAI (Base)
    # Ethereum (1)
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,   # USDC (ETH)
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,   # USDT (ETH)
    "0x6b175474e89094c44da98b954eedeac495271d0f": 18,  # DAI (ETH)
}

# Множество адресов стейблкоинов (для быстрой проверки без decimals)
STABLECOIN_ADDRESSES = set(STABLECOINS.keys())


def is_stablecoin(address: str) -> bool:
    """Проверка является ли токен стейблкоином."""
    return address.lower() in STABLECOINS


def get_stablecoin_decimals(address: str) -> int:
    """Получить decimals стейблкоина. Возвращает 18 если не найден."""
    return STABLECOINS.get(address.lower(), 18)


# Токены которые НЕ нужно продавать (стейблкоины + wrapped native)
# Single source of truth — imported by dex_swap.py, okx_dex.py, manage_tab.py
STABLE_TOKENS: Dict[str, str] = {
    # BNB Chain (56)
    "0x55d398326f99059ff775485246999027b3197955": "USDT",  # BSC USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",  # BSC USDC
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD",  # BUSD
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",  # Wrapped BNB
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": "BNB",   # Native BNB

    # Ethereum (1)
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",  # ETH USDT
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",  # ETH USDC
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",  # WETH

    # Base (8453)
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",  # Base USDC
    "0x4200000000000000000000000000000000000006": "WETH",  # Base WETH
}


def is_stable_token(address: str) -> bool:
    """Проверка: стейблкоин или wrapped native (не продавать)."""
    return address.lower() in STABLE_TOKENS


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_chain_config(chain_id: int) -> ChainConfig:
    """Получение конфигурации по chain_id."""
    configs = {
        56: BNB_CHAIN,
        97: BNB_TESTNET,
        1: ETHEREUM,
        8453: BASE,
    }
    if chain_id not in configs:
        raise ValueError(f"Unknown chain_id: {chain_id}")
    return configs[chain_id]


def get_tokens_for_chain(chain_id: int) -> Dict[str, TokenConfig]:
    """Получение словаря токенов для сети."""
    tokens_map = {
        56: TOKENS_BNB,
        97: TOKENS_BNB,
        1: TOKENS_BNB,   # Ethereum — используем BNB tokens пока нет TOKENS_ETH
        8453: TOKENS_BASE,
    }
    return tokens_map.get(chain_id, TOKENS_BNB)


def get_token(symbol: str, chain_id: int = 56) -> TokenConfig:
    """Получение токена по символу."""
    if chain_id == 56:
        if symbol not in TOKENS_BNB:
            raise ValueError(f"Unknown token: {symbol}")
        return TOKENS_BNB[symbol]
    elif chain_id == 8453:
        if symbol not in TOKENS_BASE:
            raise ValueError(f"Unknown token: {symbol}")
        return TOKENS_BASE[symbol]
    raise ValueError(f"Tokens not configured for chain_id: {chain_id}")


def detect_v3_dex_by_pool(w3, pool_address: str, chain_id: int = 56) -> V3DexConfig:
    """
    Определить какому V3 DEX принадлежит пул по его factory адресу.

    Args:
        w3: Web3 instance
        pool_address: Адрес пула
        chain_id: ID сети

    Returns:
        V3DexConfig для соответствующего DEX
    """
    from web3 import Web3

    if chain_id not in V3_DEXES:
        raise ValueError(f"No V3 DEXes configured for chain_id: {chain_id}")

    pool_address = Web3.to_checksum_address(pool_address)

    # ABI для получения factory адреса из пула
    pool_abi = [
        {"inputs": [], "name": "factory", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    ]

    try:
        pool = w3.eth.contract(address=pool_address, abi=pool_abi)
        factory_address = pool.functions.factory().call()
        factory_address = Web3.to_checksum_address(factory_address)

        # Найти DEX по factory адресу
        for dex_name, dex_config in V3_DEXES[chain_id].items():
            if Web3.to_checksum_address(dex_config.pool_factory) == factory_address:
                return dex_config

        # Не найден - возвращаем первый доступный (PancakeSwap по умолчанию для BSC)
        raise ValueError(f"Unknown factory address: {factory_address}")

    except Exception as e:
        raise ValueError(f"Failed to detect V3 DEX for pool {pool_address}: {e}")


def get_v3_dex_config(dex_name: str, chain_id: int = 56) -> V3DexConfig:
    """Получить конфигурацию V3 DEX по имени."""
    if chain_id not in V3_DEXES:
        raise ValueError(f"No V3 DEXes configured for chain_id: {chain_id}")

    dex_name_lower = dex_name.lower()
    if "uniswap" in dex_name_lower:
        return V3_DEXES[chain_id].get("uniswap")
    elif "pancake" in dex_name_lower:
        return V3_DEXES[chain_id].get("pancakeswap")

    raise ValueError(f"Unknown DEX name: {dex_name}")
