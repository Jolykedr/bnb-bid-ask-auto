"""
Codex API (graph.codex.io) — pool search by token contract address.
Returns top 5 USDT/USDC pools sorted by liquidity.
"""

import logging
import re
import requests

logger = logging.getLogger(__name__)

CODEX_API_URL = "https://graph.codex.io/graphql"

_STABLECOIN_SYMBOLS = {"usdt", "usdc"}


def _safe_decimals(val, default=18):
    """Validate token decimals from external API (must be int in 0..24)."""
    try:
        d = int(val) if val is not None else default
        return d if 0 <= d <= 24 else default
    except (ValueError, TypeError):
        return default

_GRAPHQL_QUERY = """
query ListPairs($tokenAddress: String!, $networkId: Int!, $limit: Int) {
  listPairsWithMetadataForToken(
    tokenAddress: $tokenAddress
    networkId: $networkId
    limit: $limit
  ) {
    results {
      liquidity
      pair {
        address
        fee
      }
      token {
        address
        symbol
        decimals
      }
      backingToken {
        address
        symbol
        decimals
      }
      exchange {
        name
      }
    }
  }
}
"""


def is_contract_address(q: str) -> bool:
    """Check if query looks like an Ethereum address (0x + 40 hex chars)."""
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", q.strip()))


def search_pools_by_token(
    token_address: str,
    chain_id: int,
    dex_key: str,
    api_key: str,
    proxy: str | None = None,
) -> list[dict]:
    """
    Query Codex API for top 5 stablecoin pools (USDT/USDC) for a token.

    Args:
        token_address: Token contract address (0x...)
        chain_id: Chain ID (56 for BNB, 8453 for BASE)
        dex_key: DEX key ("pancakeswap" or "uniswap")
        api_key: Codex API key
        proxy: Optional proxy URL (socks5://... or http://...)

    Returns:
        List of pool dicts sorted by liquidity desc (max 5).
    """
    if not api_key:
        logger.warning("Codex API key not set — skipping pool search")
        return []

    proxies = None
    if proxy:
        proxies = {"https": proxy, "http": proxy}

    try:
        resp = requests.post(
            CODEX_API_URL,
            json={
                "query": _GRAPHQL_QUERY,
                "variables": {
                    "tokenAddress": token_address.lower(),
                    "networkId": chain_id,
                    "limit": 50,
                },
            },
            headers={"Authorization": api_key},
            timeout=10,
            proxies=proxies,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Codex API error for %s: %s", token_address, e)
        return []

    if "errors" in data:
        logger.warning("Codex API errors: %s", data["errors"])
        return []

    raw_results = (
        data.get("data", {})
        .get("listPairsWithMetadataForToken", {})
        .get("results")
    )
    if not raw_results:
        return []

    dex_lower = dex_key.lower()
    filtered = []

    for r in raw_results:
        exchange_name = (r.get("exchange") or {}).get("name") or ""
        if dex_lower not in exchange_name.lower():
            continue

        token_info = r.get("token") or {}
        backing_info = r.get("backingToken") or {}
        token_sym = (token_info.get("symbol") or "").lower()
        backing_sym = (backing_info.get("symbol") or "").lower()

        if backing_sym in _STABLECOIN_SYMBOLS:
            stable = backing_info
            volatile = token_info
        elif token_sym in _STABLECOIN_SYMBOLS:
            stable = token_info
            volatile = backing_info
        else:
            continue

        pair_info = r.get("pair") or {}
        pool_address = pair_info.get("address", "")
        if not pool_address:
            continue

        liq_usd = float(r.get("liquidity") or 0)
        fee_raw = pair_info.get("fee")

        # Determine token0/token1 by address order (pool convention)
        vol_addr = (volatile.get("address") or "").lower()
        stab_addr = (stable.get("address") or "").lower()

        try:
            if int(vol_addr, 16) < int(stab_addr, 16):
                t0, t1 = volatile, stable
            else:
                t0, t1 = stable, volatile

            filtered.append((liq_usd, {
                "pool_address": pool_address,
                "token0_symbol": t0.get("symbol", "???"),
                "token0_address": t0.get("address", ""),
                "token0_decimals": _safe_decimals(t0.get("decimals")),
                "token1_symbol": t1.get("symbol", "???"),
                "token1_address": t1.get("address", ""),
                "token1_decimals": _safe_decimals(t1.get("decimals")),
                "fee": int(fee_raw) if fee_raw is not None else 0,
                "dex": dex_key,
                "liquidity_usd": round(liq_usd, 2),
            }))
        except (ValueError, TypeError) as e:
            logger.debug("Skipping pair with invalid address: %s", e)
            continue

    filtered.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in filtered[:5]]
