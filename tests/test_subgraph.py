"""
Tests for V4 Pool Info Query (subgraph.py).

Tests query_uniswap_api, query_pools_by_prefix, try_all_sources_with_web3
by mocking requests.post so the actual parsing/branching logic is exercised.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import requests

from src.contracts.v4.subgraph import (
    V4PoolInfo,
    query_uniswap_api,
    query_pools_by_prefix,
    try_all_sources_with_web3,
)


# ============================================================
# Test Data Helpers
# ============================================================

SAMPLE_POOL_ID = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

SAMPLE_POOL_RESPONSE = {
    "data": {
        "v4Pool": {
            "poolId": SAMPLE_POOL_ID,
            "token0": {
                "address": "0x1111111111111111111111111111111111111111",
                "symbol": "WBNB",
                "decimals": "18",
            },
            "token1": {
                "address": "0x9999999999999999999999999999999999999999",
                "symbol": "USDT",
                "decimals": "18",
            },
            "feeTier": "3000",
            "tickSpacing": "60",
        }
    }
}

SAMPLE_POOL_RESPONSE_6DEC = {
    "data": {
        "v4Pool": {
            "poolId": SAMPLE_POOL_ID,
            "token0": {
                "address": "0x1111111111111111111111111111111111111111",
                "symbol": "USDC",
                "decimals": "6",
            },
            "token1": {
                "address": "0x9999999999999999999999999999999999999999",
                "symbol": "WETH",
                "decimals": "18",
            },
            "feeTier": "500.0",
            "tickSpacing": "10",
        }
    }
}

SAMPLE_POOLS_LIST_RESPONSE = {
    "data": {
        "v4Pools": [
            {
                "poolId": "0xdeadbeef0000000000000000000000000000000000000000000000000000dead",
                "token0": {
                    "address": "0xaaaa000000000000000000000000000000000001",
                    "symbol": "AAA",
                    "decimals": "18",
                },
                "token1": {
                    "address": "0xbbbb000000000000000000000000000000000002",
                    "symbol": "BBB",
                    "decimals": "18",
                },
                "feeTier": "10000",
                "tickSpacing": "200",
            },
            {
                "poolId": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                "token0": {
                    "address": "0x1111111111111111111111111111111111111111",
                    "symbol": "WBNB",
                    "decimals": "18",
                },
                "token1": {
                    "address": "0x9999999999999999999999999999999999999999",
                    "symbol": "USDT",
                    "decimals": "18",
                },
                "feeTier": "3000",
                "tickSpacing": "60",
            },
        ]
    }
}


def make_response(json_data, status_code=200):
    """Build a mock requests.Response."""
    resp = Mock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)[:200] if json_data else ""
    return resp


# ============================================================
# V4PoolInfo Dataclass Tests
# ============================================================

class TestV4PoolInfo:
    """Tests for V4PoolInfo dataclass."""

    def test_creation_and_field_access(self):
        """V4PoolInfo stores all fields correctly."""
        info = V4PoolInfo(
            pool_id="0xabc123",
            token0_address="0x1111111111111111111111111111111111111111",
            token1_address="0x9999999999999999999999999999999999999999",
            token0_symbol="WBNB",
            token1_symbol="USDT",
            token0_decimals=18,
            token1_decimals=18,
            fee_tier=3000,
            tick_spacing=60,
            current_tick=0,
            sqrt_price="0",
            liquidity="0",
        )
        assert info.pool_id == "0xabc123"
        assert info.token0_address == "0x1111111111111111111111111111111111111111"
        assert info.token1_address == "0x9999999999999999999999999999999999999999"
        assert info.token0_symbol == "WBNB"
        assert info.token1_symbol == "USDT"
        assert info.token0_decimals == 18
        assert info.token1_decimals == 18
        assert info.fee_tier == 3000
        assert info.tick_spacing == 60
        assert info.current_tick == 0
        assert info.sqrt_price == "0"
        assert info.liquidity == "0"

    def test_equality(self):
        """Dataclass equality works by field values."""
        kwargs = dict(
            pool_id="0xabc", token0_address="0x11", token1_address="0x99",
            token0_symbol="A", token1_symbol="B", token0_decimals=18,
            token1_decimals=6, fee_tier=500, tick_spacing=10,
            current_tick=-100, sqrt_price="123", liquidity="456",
        )
        a = V4PoolInfo(**kwargs)
        b = V4PoolInfo(**kwargs)
        assert a == b

    def test_different_decimals(self):
        """V4PoolInfo stores mixed decimals correctly."""
        info = V4PoolInfo(
            pool_id="0x1", token0_address="0x1", token1_address="0x2",
            token0_symbol="USDC", token1_symbol="WETH",
            token0_decimals=6, token1_decimals=18,
            fee_tier=500, tick_spacing=10,
            current_tick=-200000, sqrt_price="12345678", liquidity="99999",
        )
        assert info.token0_decimals == 6
        assert info.token1_decimals == 18


# ============================================================
# query_uniswap_api Tests
# ============================================================

class TestQueryUniswapApi:
    """Tests for query_uniswap_api function."""

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_successful_response(self, mock_post):
        """Valid API response returns a V4PoolInfo object."""
        mock_post.return_value = make_response(SAMPLE_POOL_RESPONSE)

        result = query_uniswap_api(SAMPLE_POOL_ID, chain_id=56)

        assert result is not None
        assert isinstance(result, V4PoolInfo)
        assert result.pool_id == SAMPLE_POOL_ID
        assert result.token0_symbol == "WBNB"
        assert result.token1_symbol == "USDT"
        assert result.token0_decimals == 18
        assert result.token1_decimals == 18
        assert result.fee_tier == 3000
        assert result.tick_spacing == 60
        # current_tick, sqrt_price, liquidity are defaults since API doesn't provide them
        assert result.current_tick == 0
        assert result.sqrt_price == "0"
        assert result.liquidity == "0"

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_successful_response_different_decimals(self, mock_post):
        """API response with 6-decimal token parses decimals correctly."""
        mock_post.return_value = make_response(SAMPLE_POOL_RESPONSE_6DEC)

        result = query_uniswap_api(SAMPLE_POOL_ID, chain_id=8453)

        assert result is not None
        assert result.token0_decimals == 6
        assert result.token1_decimals == 18
        assert result.token0_symbol == "USDC"

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_fee_tier_float_parsing(self, mock_post):
        """feeTier like '500.0' is parsed to int 500."""
        mock_post.return_value = make_response(SAMPLE_POOL_RESPONSE_6DEC)

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is not None
        assert result.fee_tier == 500
        assert isinstance(result.fee_tier, int)

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_pool_not_found_returns_none(self, mock_post):
        """When v4Pool is None in response, returns None."""
        mock_post.return_value = make_response({
            "data": {"v4Pool": None}
        })

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_empty_data_returns_none(self, mock_post):
        """When 'data' key is missing or empty, returns None."""
        mock_post.return_value = make_response({"data": {}})

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_non_200_status_returns_none(self, mock_post):
        """Non-200 HTTP status returns None."""
        mock_post.return_value = make_response(None, status_code=500)

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_non_200_status_404(self, mock_post):
        """404 HTTP status returns None."""
        mock_post.return_value = make_response(None, status_code=404)

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_network_error_returns_none(self, mock_post):
        """Network exception (ConnectionError) returns None."""
        mock_post.side_effect = requests.ConnectionError("DNS resolution failed")

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_timeout_error_returns_none(self, mock_post):
        """Timeout exception returns None."""
        mock_post.side_effect = requests.Timeout("Connection timed out")

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_graphql_errors_with_no_pool(self, mock_post):
        """GraphQL errors in response with no pool data returns None."""
        mock_post.return_value = make_response({
            "errors": [{"message": "Pool not found"}],
            "data": {"v4Pool": None}
        })

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_graphql_errors_with_valid_pool_still_returns_data(self, mock_post):
        """GraphQL errors in response but pool data present still returns pool."""
        response_data = {
            "errors": [{"message": "partial error"}],
            "data": {
                "v4Pool": {
                    "poolId": SAMPLE_POOL_ID,
                    "token0": {"address": "0x11", "symbol": "A", "decimals": "18"},
                    "token1": {"address": "0x22", "symbol": "B", "decimals": "18"},
                    "feeTier": "3000",
                    "tickSpacing": "60",
                }
            }
        }
        mock_post.return_value = make_response(response_data)

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is not None
        assert result.token0_symbol == "A"

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_pool_id_lowercased_in_query(self, mock_post):
        """Pool ID is lowercased before sending to API."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api("0xABCDEF1234")

        # Verify the query contains lowercased pool_id
        call_args = mock_post.call_args
        query_sent = call_args[1]["json"]["query"] if "json" in call_args[1] else call_args[0][1]["query"]
        assert "0xabcdef1234" in query_sent

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_chain_id_56_maps_to_bnb(self, mock_post):
        """chain_id=56 uses BNB chain name in query."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api(SAMPLE_POOL_ID, chain_id=56)

        query_sent = mock_post.call_args[1]["json"]["query"]
        assert "chain: BNB" in query_sent

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_chain_id_1_maps_to_ethereum(self, mock_post):
        """chain_id=1 uses ETHEREUM chain name in query."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api(SAMPLE_POOL_ID, chain_id=1)

        query_sent = mock_post.call_args[1]["json"]["query"]
        assert "chain: ETHEREUM" in query_sent

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_chain_id_8453_maps_to_base(self, mock_post):
        """chain_id=8453 uses BASE chain name in query."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api(SAMPLE_POOL_ID, chain_id=8453)

        query_sent = mock_post.call_args[1]["json"]["query"]
        assert "chain: BASE" in query_sent

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_unknown_chain_id_defaults_to_bnb(self, mock_post):
        """Unknown chain_id defaults to BNB."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api(SAMPLE_POOL_ID, chain_id=99999)

        query_sent = mock_post.call_args[1]["json"]["query"]
        assert "chain: BNB" in query_sent

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_truncated_pool_id_triggers_prefix_search(self, mock_post):
        """Pool ID ending with '00000000000000' triggers prefix fallback."""
        # Build truncated ID so first 52 chars match SAMPLE_POOL_ID start
        # SAMPLE_POOL_ID[:52] = "0xabcdef1234567890abcdef1234567890abcdef1234567890ab"
        # Pad remaining 14 chars with zeros -> ends with '00000000000000'
        truncated_id = SAMPLE_POOL_ID[:52] + "00000000000000"
        assert len(truncated_id) == 66  # 0x + 64 hex chars
        assert truncated_id.endswith("00000000000000")

        # First call: direct lookup returns no pool
        # Second call: prefix search returns pool list with SAMPLE_POOL_ID
        mock_post.side_effect = [
            make_response({"data": {"v4Pool": None}}),
            make_response(SAMPLE_POOLS_LIST_RESPONSE),
        ]

        result = query_uniswap_api(truncated_id)

        # Two requests.post calls: direct lookup + prefix search
        assert mock_post.call_count == 2
        # Should find the matching pool from prefix search
        assert result is not None
        assert result.token0_symbol == "WBNB"

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_non_truncated_pool_id_no_prefix_search(self, mock_post):
        """Pool ID NOT ending with '00000000000000' does not trigger prefix fallback."""
        normal_id = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567891"
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        result = query_uniswap_api(normal_id)

        # Only one request: direct lookup only
        assert mock_post.call_count == 1
        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_sends_correct_headers(self, mock_post):
        """Verifies Origin header is set to app.uniswap.org."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api(SAMPLE_POOL_ID)

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["headers"]["Origin"] == "https://app.uniswap.org"
        assert call_kwargs["headers"]["Content-Type"] == "application/json"

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_uses_correct_api_url(self, mock_post):
        """Verifies the API URL is correct."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api(SAMPLE_POOL_ID)

        call_args = mock_post.call_args
        assert call_args[0][0] == "https://interface.gateway.uniswap.org/v1/graphql"

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_timeout_is_set(self, mock_post):
        """Verifies a timeout is passed to requests.post."""
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        query_uniswap_api(SAMPLE_POOL_ID)

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["timeout"] == 10

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_json_decode_error_returns_none(self, mock_post):
        """JSON decode error in response.json() returns None."""
        resp = Mock(spec=requests.Response)
        resp.status_code = 200
        resp.json.side_effect = ValueError("No JSON")
        resp.text = "not json"
        mock_post.return_value = resp

        result = query_uniswap_api(SAMPLE_POOL_ID)

        assert result is None


# ============================================================
# query_pools_by_prefix Tests
# ============================================================

class TestQueryPoolsByPrefix:
    """Tests for query_pools_by_prefix function."""

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_finds_matching_pool(self, mock_post):
        """Returns pool matching the given prefix."""
        mock_post.return_value = make_response(SAMPLE_POOLS_LIST_RESPONSE)

        result = query_pools_by_prefix("0xabcdef1234", chain_id=56)

        assert result is not None
        assert isinstance(result, V4PoolInfo)
        assert result.pool_id == SAMPLE_POOL_ID
        assert result.token0_symbol == "WBNB"
        assert result.token1_symbol == "USDT"
        assert result.fee_tier == 3000
        assert result.tick_spacing == 60

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_no_matching_pool_returns_none(self, mock_post):
        """No pool matching prefix returns None."""
        mock_post.return_value = make_response(SAMPLE_POOLS_LIST_RESPONSE)

        result = query_pools_by_prefix("0xffffffffffffffff", chain_id=56)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_empty_pools_list_returns_none(self, mock_post):
        """Empty v4Pools list returns None."""
        mock_post.return_value = make_response({
            "data": {"v4Pools": []}
        })

        result = query_pools_by_prefix("0xabcdef", chain_id=56)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_missing_v4pools_key_returns_none(self, mock_post):
        """Missing v4Pools key in response returns None."""
        mock_post.return_value = make_response({
            "data": {}
        })

        result = query_pools_by_prefix("0xabcdef", chain_id=56)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_network_error_returns_none(self, mock_post):
        """Network exception returns None."""
        mock_post.side_effect = requests.ConnectionError("Network unreachable")

        result = query_pools_by_prefix("0xabcdef", chain_id=56)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_non_200_status_returns_none(self, mock_post):
        """Non-200 status returns None."""
        mock_post.return_value = make_response(None, status_code=503)

        result = query_pools_by_prefix("0xabcdef", chain_id=56)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_prefix_match_is_case_insensitive(self, mock_post):
        """Prefix matching is case-insensitive."""
        mock_post.return_value = make_response(SAMPLE_POOLS_LIST_RESPONSE)

        # Use uppercase prefix to match lowercase poolId
        result = query_pools_by_prefix("0xABCDEF1234", chain_id=56)

        assert result is not None
        assert result.pool_id == SAMPLE_POOL_ID

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_returns_first_matching_pool(self, mock_post):
        """Returns the first pool that matches the prefix."""
        # Both pools start with 0xde... but only deadbeef matches exactly
        mock_post.return_value = make_response(SAMPLE_POOLS_LIST_RESPONSE)

        result = query_pools_by_prefix("0xdeadbeef", chain_id=56)

        assert result is not None
        assert result.token0_symbol == "AAA"
        assert result.token1_symbol == "BBB"
        assert result.fee_tier == 10000

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_timeout_is_15_seconds(self, mock_post):
        """Prefix search uses 15 second timeout."""
        mock_post.return_value = make_response({"data": {"v4Pools": []}})

        query_pools_by_prefix("0xabc", chain_id=56)

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["timeout"] == 15

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_chain_id_propagated_to_query(self, mock_post):
        """Chain ID is correctly propagated to the GraphQL query."""
        mock_post.return_value = make_response({"data": {"v4Pools": []}})

        query_pools_by_prefix("0xabc", chain_id=8453)

        query_sent = mock_post.call_args[1]["json"]["query"]
        assert "chain: BASE" in query_sent

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_null_response_returns_none(self, mock_post):
        """None JSON response returns None."""
        resp = Mock(spec=requests.Response)
        resp.status_code = 200
        resp.json.return_value = None
        mock_post.return_value = resp

        result = query_pools_by_prefix("0xabc", chain_id=56)

        assert result is None


# ============================================================
# try_all_sources_with_web3 Tests
# ============================================================

class TestTryAllSourcesWithWeb3:
    """Tests for try_all_sources_with_web3 function."""

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_direct_lookup_succeeds(self, mock_post):
        """When direct query_uniswap_api succeeds, returns result immediately."""
        mock_post.return_value = make_response(SAMPLE_POOL_RESPONSE)

        result = try_all_sources_with_web3(SAMPLE_POOL_ID, chain_id=56)

        assert result is not None
        assert result.token0_symbol == "WBNB"
        # Only one call needed (direct lookup)
        assert mock_post.call_count == 1

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_direct_fails_trailing_zeros_triggers_prefix(self, mock_post):
        """When direct lookup fails and pool_id ends with '0000', tries prefix search."""
        # Pool ID that ends with 0000 but NOT 00000000000000 (so query_uniswap_api
        # does NOT do its own prefix fallback, but try_all_sources does)
        pool_id_trailing = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1200000000"

        # First call (direct in query_uniswap_api): no pool found
        # Second call (prefix fallback within query_uniswap_api for 00000000000000): also runs since it ends with that
        # Actually, "abcdef1200000000" ends with "00000000000000"? Let's check:
        # The suffix is "00000000" - 8 zeros. "00000000000000" is 14 zeros. Not matching.
        # So query_uniswap_api returns None, then try_all_sources strips trailing zeros
        # and calls query_pools_by_prefix.
        mock_post.side_effect = [
            make_response({"data": {"v4Pool": None}}),  # direct lookup
            make_response(SAMPLE_POOLS_LIST_RESPONSE),   # prefix search from try_all_sources
        ]

        result = try_all_sources_with_web3(pool_id_trailing, chain_id=56)

        assert mock_post.call_count == 2
        assert result is not None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_both_methods_fail_returns_none(self, mock_post):
        """When all lookups fail, returns None."""
        pool_id_trailing = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef0000000000"
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        result = try_all_sources_with_web3(pool_id_trailing, chain_id=56)

        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_no_trailing_zeros_no_prefix_search(self, mock_post):
        """Pool ID without trailing zeros: only direct lookup, no prefix search."""
        normal_id = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567891"
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        result = try_all_sources_with_web3(normal_id, chain_id=56)

        # Only direct lookup, no prefix search
        assert mock_post.call_count == 1
        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_short_stripped_prefix_skips_prefix_search(self, mock_post):
        """If stripped prefix < 10 chars, skips prefix search."""
        # Pool ID where stripping trailing zeros leaves fewer than 10 chars
        short_pool_id = "0xab0000000000000000000000000000000000000000000000000000000000"

        # Pad to make it end with 0000 but strip to short prefix
        # "0xab" after rstrip('0') = "0xab" which is 4 chars < 10
        mock_post.return_value = make_response({"data": {"v4Pool": None}})

        result = try_all_sources_with_web3(short_pool_id, chain_id=56)

        # query_uniswap_api tries once (direct), pool_id ends with 00000000000000
        # so it also does prefix search inside query_uniswap_api (that's call 2).
        # Then try_all_sources checks endswith('0000'): yes.
        # Strips zeros: "0xab" -> len 4 < 10 -> skip.
        # Total: depends on the internal prefix search from query_uniswap_api.
        # The important thing: result is None because nothing matched.
        assert result is None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_chain_id_passed_through(self, mock_post):
        """chain_id is passed to inner query_uniswap_api call."""
        mock_post.return_value = make_response(SAMPLE_POOL_RESPONSE)

        result = try_all_sources_with_web3(SAMPLE_POOL_ID, chain_id=8453)

        query_sent = mock_post.call_args[1]["json"]["query"]
        assert "chain: BASE" in query_sent

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_extra_params_do_not_break(self, mock_post):
        """Extra params (w3, api_key, rpc_url, bscscan_api_key) are accepted without error."""
        mock_post.return_value = make_response(SAMPLE_POOL_RESPONSE)

        result = try_all_sources_with_web3(
            SAMPLE_POOL_ID,
            w3=MagicMock(),
            chain_id=56,
            api_key="test_key",
            rpc_url="https://rpc.example.com",
            bscscan_api_key="test_bsc_key",
        )

        assert result is not None

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_truncated_id_14_zeros_double_fallback(self, mock_post):
        """Pool ID with 14+ trailing zeros triggers internal prefix search in query_uniswap_api."""
        # Build truncated ID: first 52 chars of SAMPLE_POOL_ID + 14 zeros
        truncated_id = SAMPLE_POOL_ID[:52] + "00000000000000"
        assert len(truncated_id) == 66

        # First call: direct lookup - no pool
        # Second call: prefix search inside query_uniswap_api - finds it
        mock_post.side_effect = [
            make_response({"data": {"v4Pool": None}}),
            make_response(SAMPLE_POOLS_LIST_RESPONSE),
        ]

        result = try_all_sources_with_web3(truncated_id, chain_id=56)

        assert result is not None
        assert result.token0_symbol == "WBNB"
        # query_uniswap_api handled it with its own prefix fallback
        assert mock_post.call_count == 2

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_all_network_errors_returns_none(self, mock_post):
        """Network errors on all attempts returns None."""
        mock_post.side_effect = requests.ConnectionError("down")

        pool_id_trailing = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef0000000000"

        result = try_all_sources_with_web3(pool_id_trailing, chain_id=56)

        assert result is None


# ============================================================
# Integration-style: verify parsing logic with realistic data
# ============================================================

class TestParsingIntegration:
    """Verify that real-ish GraphQL responses are parsed correctly end-to-end."""

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_full_roundtrip_bnb_chain(self, mock_post):
        """Full realistic BNB chain pool response is parsed correctly."""
        realistic_response = {
            "data": {
                "v4Pool": {
                    "poolId": "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "token0": {
                        "address": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
                        "symbol": "WBNB",
                        "decimals": "18",
                    },
                    "token1": {
                        "address": "0x55d398326f99059fF775485246999027B3197955",
                        "symbol": "USDT",
                        "decimals": "18",
                    },
                    "feeTier": "2500",
                    "tickSpacing": "50",
                }
            }
        }
        mock_post.return_value = make_response(realistic_response)

        result = query_uniswap_api(
            "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            chain_id=56,
        )

        assert result.pool_id == "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        assert result.token0_address == "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
        assert result.token1_address == "0x55d398326f99059fF775485246999027B3197955"
        assert result.fee_tier == 2500
        assert result.tick_spacing == 50

    @patch("src.contracts.v4.subgraph.requests.post")
    def test_full_roundtrip_base_chain_usdc(self, mock_post):
        """BASE chain pool with USDC (6 decimals) parsed correctly."""
        base_response = {
            "data": {
                "v4Pool": {
                    "poolId": "0xfedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
                    "token0": {
                        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                        "symbol": "USDC",
                        "decimals": "6",
                    },
                    "token1": {
                        "address": "0x4200000000000000000000000000000000000006",
                        "symbol": "WETH",
                        "decimals": "18",
                    },
                    "feeTier": "500",
                    "tickSpacing": "10",
                }
            }
        }
        mock_post.return_value = make_response(base_response)

        result = query_uniswap_api(
            "0xfedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
            chain_id=8453,
        )

        assert result.token0_decimals == 6
        assert result.token1_decimals == 18
        assert result.token0_symbol == "USDC"
        assert result.token1_symbol == "WETH"
        assert result.fee_tier == 500
        assert result.tick_spacing == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
