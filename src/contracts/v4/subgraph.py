"""
V4 Pool Info Query - Lightweight version

Queries Uniswap interface API to get pool info from pool_id.
"""

import logging
import requests
from dataclasses import dataclass
from typing import Optional
from web3 import Web3

logger = logging.getLogger(__name__)


@dataclass
class V4PoolInfo:
    """V4 Pool information."""
    pool_id: str
    token0_address: str
    token1_address: str
    token0_symbol: str
    token1_symbol: str
    token0_decimals: int
    token1_decimals: int
    fee_tier: int
    tick_spacing: int
    current_tick: int
    sqrt_price: str
    liquidity: str


def query_uniswap_api(pool_id: str, chain_id: int = 56, proxy: dict = None) -> Optional[V4PoolInfo]:
    """
    Query Uniswap's interface API for pool info.

    This uses the same API that app.uniswap.org uses.
    Supports both full pool IDs and truncated/padded pool IDs (from positionInfo).
    """
    pool_id_lower = pool_id.lower()

    # Uniswap GraphQL gateway
    api_url = "https://interface.gateway.uniswap.org/v1/graphql"

    # Chain name mapping
    chain_names = {56: "BNB", 1: "ETHEREUM", 8453: "BASE"}
    chain_name = chain_names.get(chain_id, "BNB")

    # Build query with string concatenation (f-string escaping issues)
    # Note: tick, sqrtPrice, liquidity not available in this API
    query = '''
query GetV4Pool {
  v4Pool(chain: ''' + chain_name + ''', poolId: "''' + pool_id_lower + '''") {
    poolId
    token0 { address symbol decimals }
    token1 { address symbol decimals }
    feeTier
    tickSpacing
  }
}
'''

    try:
        logger.debug(f"[API] Querying Uniswap API for pool: {pool_id_lower[:20]}...")
        response = requests.post(
            api_url,
            json={"query": query},
            headers={
                "Content-Type": "application/json",
                "Origin": "https://app.uniswap.org"
            },
            timeout=10,
            proxies=proxy or {}
        )

        logger.debug(f"[API] Response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            logger.debug(f"[API] Response data keys: {data.keys() if data else 'None'}")

            # Check for errors in response
            if data and "errors" in data:
                logger.warning(f"[API] GraphQL errors: {data['errors']}")

            if data:
                pool = data.get("data", {}).get("v4Pool")
                logger.debug(f"[API] Pool found: {pool is not None}")

                if pool:
                    logger.info(f"[API] Pool tokens: {pool.get('token0', {}).get('symbol')}/{pool.get('token1', {}).get('symbol')}")
                    return V4PoolInfo(
                        pool_id=pool["poolId"],
                        token0_address=pool["token0"]["address"],
                        token1_address=pool["token1"]["address"],
                        token0_symbol=pool["token0"]["symbol"],
                        token1_symbol=pool["token1"]["symbol"],
                        token0_decimals=int(pool["token0"]["decimals"]),
                        token1_decimals=int(pool["token1"]["decimals"]),
                        fee_tier=int(float(pool["feeTier"])),
                        tick_spacing=int(pool["tickSpacing"]),
                        current_tick=0,  # Not available in API
                        sqrt_price="0",
                        liquidity="0"
                    )
                else:
                    logger.warning(f"[API] Pool not found in response. Full data: {data.get('data', {})}")
        else:
            logger.warning(f"[API] Non-200 response: {response.text[:200]}")
    except Exception as e:
        logger.error(f"[API] Uniswap API error: {e}")

    # If direct lookup failed and this looks like a zero-padded truncated ID,
    # try prefix search
    if pool_id_lower.endswith('00000000000000'):
        logger.debug(f"[API] Trying prefix search for truncated pool ID...")
        prefix = pool_id_lower[:52]  # 0x + 50 hex chars (25 bytes)
        return query_pools_by_prefix(prefix, chain_id, proxy=proxy)

    return None


def query_pools_by_prefix(prefix: str, chain_id: int = 56, proxy: dict = None) -> Optional[V4PoolInfo]:
    """
    Query V4 pools and find one matching the prefix.

    Used when we only have the truncated poolId from positionInfo.
    """
    api_url = "https://interface.gateway.uniswap.org/v1/graphql"

    chain_names = {56: "BNB", 1: "ETHEREUM", 8453: "BASE"}
    chain_name = chain_names.get(chain_id, "BNB")

    # Query all pools (API returns most recent/popular ones)
    query = '''
query GetV4Pools {
  v4Pools(first: 100, chain: ''' + chain_name + ''') {
    poolId
    token0 { address symbol decimals }
    token1 { address symbol decimals }
    feeTier
    tickSpacing
  }
}
'''

    try:
        response = requests.post(
            api_url,
            json={"query": query},
            headers={
                "Content-Type": "application/json",
                "Origin": "https://app.uniswap.org"
            },
            timeout=15,
            proxies=proxy or {}
        )

        if response.status_code == 200:
            data = response.json()
            if data:
                pools = data.get("data", {}).get("v4Pools", [])

                # Find pool matching prefix
                prefix_lower = prefix.lower()
                for pool in pools:
                    if pool["poolId"].lower().startswith(prefix_lower):
                        logger.info(f"[API] Found pool by prefix: {pool['poolId']}")
                        return V4PoolInfo(
                            pool_id=pool["poolId"],
                            token0_address=pool["token0"]["address"],
                            token1_address=pool["token1"]["address"],
                            token0_symbol=pool["token0"]["symbol"],
                            token1_symbol=pool["token1"]["symbol"],
                            token0_decimals=int(pool["token0"]["decimals"]),
                            token1_decimals=int(pool["token1"]["decimals"]),
                            fee_tier=int(float(pool["feeTier"])),
                            tick_spacing=int(pool["tickSpacing"]),
                            current_tick=0,
                            sqrt_price="0",
                            liquidity="0"
                        )

                logger.warning(f"[API] No pool found matching prefix {prefix_lower[:20]}...")
    except Exception as e:
        logger.error(f"[API] Prefix search error: {e}")

    return None


def try_all_sources_with_web3(
    pool_id: str,
    w3: Web3 = None,
    chain_id: int = 56,
    api_key: str = None,
    rpc_url: str = None,
    bscscan_api_key: str = None,
    proxy: dict = None
) -> Optional[V4PoolInfo]:
    """
    Try to get V4 pool info from various sources.

    1. Direct API lookup by exact pool ID
    2. Prefix search for truncated pool IDs (from positionInfo)
    """
    # Try Uniswap interface API (includes prefix search fallback)
    result = query_uniswap_api(pool_id, chain_id, proxy=proxy)
    if result:
        return result

    # If pool_id looks like it might be truncated (ends with zeros),
    # try prefix search explicitly
    pool_id_lower = pool_id.lower()
    if pool_id_lower.endswith('0000'):
        # Try with just the non-zero prefix
        # Strip trailing zeros to get the meaningful part
        stripped = pool_id_lower.rstrip('0')
        if len(stripped) >= 10:  # At least some meaningful prefix
            logger.debug(f"[API] Last resort: prefix search with {stripped[:20]}...")
            result = query_pools_by_prefix(stripped, chain_id, proxy=proxy)
            if result:
                return result

    return None
