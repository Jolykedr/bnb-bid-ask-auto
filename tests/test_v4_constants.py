"""
Tests for V4 constants, addresses, and fee/tick utility functions.

Tests module: src.contracts.v4.constants
"""

import pytest
from src.contracts.v4.constants import (
    V4Protocol,
    V4Addresses,
    get_v4_addresses,
    fee_percent_to_v4,
    v4_fee_to_percent,
    suggest_tick_spacing,
    MAX_V4_FEE,
    MIN_V4_FEE,
    COMMON_TICK_SPACINGS,
    UNISWAP_V4_ADDRESSES,
    PANCAKESWAP_V4_ADDRESSES,
)


# ---------------------------------------------------------------------------
# V4Protocol enum
# ---------------------------------------------------------------------------

class TestV4Protocol:
    """Tests for the V4Protocol enum."""

    def test_uniswap_value(self):
        """UNISWAP member has value 'uniswap'."""
        assert V4Protocol.UNISWAP.value == "uniswap"

    def test_pancakeswap_value(self):
        """PANCAKESWAP member has value 'pancakeswap'."""
        assert V4Protocol.PANCAKESWAP.value == "pancakeswap"

    def test_enum_members_count(self):
        """Enum has exactly two members."""
        assert len(V4Protocol) == 2

    def test_enum_is_distinct(self):
        """The two protocol values are different."""
        assert V4Protocol.UNISWAP != V4Protocol.PANCAKESWAP


# ---------------------------------------------------------------------------
# V4Addresses dataclass
# ---------------------------------------------------------------------------

class TestV4Addresses:
    """Tests for the V4Addresses dataclass."""

    def test_required_fields(self):
        """pool_manager and position_manager are required."""
        addr = V4Addresses(
            pool_manager="0xAA",
            position_manager="0xBB",
        )
        assert addr.pool_manager == "0xAA"
        assert addr.position_manager == "0xBB"

    def test_optional_fields_default_none(self):
        """Optional fields default to None when not provided."""
        addr = V4Addresses(
            pool_manager="0xAA",
            position_manager="0xBB",
        )
        assert addr.quoter is None
        assert addr.universal_router is None
        assert addr.vault is None
        assert addr.state_view is None

    def test_all_fields_set(self):
        """All fields can be populated."""
        addr = V4Addresses(
            pool_manager="0x01",
            position_manager="0x02",
            quoter="0x03",
            universal_router="0x04",
            vault="0x05",
            state_view="0x06",
        )
        assert addr.pool_manager == "0x01"
        assert addr.position_manager == "0x02"
        assert addr.quoter == "0x03"
        assert addr.universal_router == "0x04"
        assert addr.vault == "0x05"
        assert addr.state_view == "0x06"


# ---------------------------------------------------------------------------
# get_v4_addresses
# ---------------------------------------------------------------------------

class TestGetV4Addresses:
    """Tests for get_v4_addresses()."""

    # -- Uniswap --------------------------------------------------------

    def test_uniswap_bnb_chain(self):
        """Uniswap + chain 56 returns valid addresses with state_view."""
        addr = get_v4_addresses(56, V4Protocol.UNISWAP)
        assert addr is not None
        assert addr.pool_manager is not None
        assert addr.position_manager is not None
        assert addr.quoter is not None
        assert addr.state_view is not None

    def test_uniswap_ethereum(self):
        """Uniswap + chain 1 returns valid addresses."""
        addr = get_v4_addresses(1, V4Protocol.UNISWAP)
        assert addr is not None
        assert addr.pool_manager is not None
        assert addr.position_manager is not None
        assert addr.quoter is not None

    def test_uniswap_base(self):
        """Uniswap + chain 8453 returns addresses including state_view."""
        addr = get_v4_addresses(8453, V4Protocol.UNISWAP)
        assert addr is not None
        assert addr.pool_manager is not None
        assert addr.position_manager is not None
        assert addr.state_view is not None

    def test_uniswap_ethereum_no_state_view(self):
        """Uniswap Ethereum (chain 1) has no state_view configured."""
        addr = get_v4_addresses(1, V4Protocol.UNISWAP)
        assert addr is not None
        assert addr.state_view is None

    def test_uniswap_unknown_chain_returns_none(self):
        """Uniswap + unsupported chain returns None."""
        assert get_v4_addresses(999, V4Protocol.UNISWAP) is None

    # -- PancakeSwap ----------------------------------------------------

    def test_pancakeswap_bnb_chain(self):
        """PancakeSwap + chain 56 returns addresses with vault."""
        addr = get_v4_addresses(56, V4Protocol.PANCAKESWAP)
        assert addr is not None
        assert addr.pool_manager is not None
        assert addr.position_manager is not None
        assert addr.vault is not None

    def test_pancakeswap_ethereum_returns_none(self):
        """PancakeSwap + chain 1 is not configured, returns None."""
        assert get_v4_addresses(1, V4Protocol.PANCAKESWAP) is None

    def test_pancakeswap_unknown_chain_returns_none(self):
        """PancakeSwap + unknown chain returns None."""
        assert get_v4_addresses(12345, V4Protocol.PANCAKESWAP) is None

    # -- Address format -------------------------------------------------

    def test_addresses_are_hex_strings(self):
        """All returned address strings start with '0x'."""
        for chain_id in (1, 56, 8453):
            addr = get_v4_addresses(chain_id, V4Protocol.UNISWAP)
            if addr is None:
                continue
            assert addr.pool_manager.startswith("0x")
            assert addr.position_manager.startswith("0x")
            if addr.quoter:
                assert addr.quoter.startswith("0x")
            if addr.universal_router:
                assert addr.universal_router.startswith("0x")
            if addr.state_view:
                assert addr.state_view.startswith("0x")

    def test_addresses_match_raw_dict_uniswap(self):
        """get_v4_addresses returns the same object as the raw dict lookup."""
        for chain_id in UNISWAP_V4_ADDRESSES:
            assert get_v4_addresses(chain_id, V4Protocol.UNISWAP) is UNISWAP_V4_ADDRESSES[chain_id]

    def test_addresses_match_raw_dict_pancakeswap(self):
        """get_v4_addresses returns the same object as the raw dict lookup."""
        for chain_id in PANCAKESWAP_V4_ADDRESSES:
            assert get_v4_addresses(chain_id, V4Protocol.PANCAKESWAP) is PANCAKESWAP_V4_ADDRESSES[chain_id]


# ---------------------------------------------------------------------------
# fee_percent_to_v4
# ---------------------------------------------------------------------------

class TestFeePercentToV4:
    """Tests for fee_percent_to_v4()."""

    def test_standard_fee_0_3(self):
        """0.3% -> 3000."""
        assert fee_percent_to_v4(0.3) == 3000

    def test_standard_fee_1_0(self):
        """1.0% -> 10000."""
        assert fee_percent_to_v4(1.0) == 10000

    def test_fractional_fee_3_333(self):
        """3.333% -> 33330."""
        assert fee_percent_to_v4(3.333) == 33330

    def test_float_precision_3_8998(self):
        """3.8998% -> 38998 (round handles float precision)."""
        assert fee_percent_to_v4(3.8998) == 38998

    def test_zero_fee(self):
        """0.0% -> 0."""
        assert fee_percent_to_v4(0.0) == 0

    def test_max_fee_100_percent(self):
        """100.0% -> 1_000_000."""
        assert fee_percent_to_v4(100.0) == 1_000_000

    def test_small_fee_0_01(self):
        """0.01% -> 100."""
        assert fee_percent_to_v4(0.01) == 100

    def test_small_fee_0_05(self):
        """0.05% -> 500."""
        assert fee_percent_to_v4(0.05) == 500

    def test_returns_int_type(self):
        """Return value is always an int."""
        assert isinstance(fee_percent_to_v4(0.3), int)
        assert isinstance(fee_percent_to_v4(1.0), int)


# ---------------------------------------------------------------------------
# v4_fee_to_percent
# ---------------------------------------------------------------------------

class TestV4FeeToPercent:
    """Tests for v4_fee_to_percent()."""

    def test_3000_to_0_3(self):
        """3000 -> 0.3%."""
        assert v4_fee_to_percent(3000) == pytest.approx(0.3)

    def test_10000_to_1_0(self):
        """10000 -> 1.0%."""
        assert v4_fee_to_percent(10000) == pytest.approx(1.0)

    def test_0_to_0(self):
        """0 -> 0.0%."""
        assert v4_fee_to_percent(0) == 0.0

    def test_1000000_to_100(self):
        """1_000_000 -> 100.0%."""
        assert v4_fee_to_percent(1_000_000) == pytest.approx(100.0)

    def test_returns_float_type(self):
        """Return value is a float."""
        assert isinstance(v4_fee_to_percent(3000), float)


# ---------------------------------------------------------------------------
# Roundtrip: fee_percent_to_v4 <-> v4_fee_to_percent
# ---------------------------------------------------------------------------

class TestFeeRoundtrip:
    """Tests for roundtrip conversion between percent and V4 fee."""

    @pytest.mark.parametrize("percent", [0.0, 0.01, 0.05, 0.3, 1.0, 3.333, 5.0, 100.0])
    def test_percent_to_v4_and_back(self, percent):
        """percent -> V4 -> percent should roundtrip within tolerance."""
        v4 = fee_percent_to_v4(percent)
        recovered = v4_fee_to_percent(v4)
        assert recovered == pytest.approx(percent, abs=1e-4)

    @pytest.mark.parametrize("v4_fee", [0, 100, 500, 3000, 10000, 33330, 1_000_000])
    def test_v4_to_percent_and_back(self, v4_fee):
        """V4 fee -> percent -> V4 fee should be exact."""
        percent = v4_fee_to_percent(v4_fee)
        recovered = fee_percent_to_v4(percent)
        assert recovered == v4_fee


# ---------------------------------------------------------------------------
# suggest_tick_spacing
# ---------------------------------------------------------------------------

class TestSuggestTickSpacing:
    """Tests for suggest_tick_spacing()."""

    def test_fee_0_3_gives_60(self):
        """0.3% fee -> tick_spacing 60."""
        assert suggest_tick_spacing(0.3) == 60

    def test_fee_1_0_gives_200(self):
        """1.0% fee -> tick_spacing 200."""
        assert suggest_tick_spacing(1.0) == 200

    def test_fee_3_3321_gives_666(self):
        """3.3321% fee -> round(3.3321 * 200) = 666."""
        assert suggest_tick_spacing(3.3321) == 666

    def test_very_small_fee_minimum_1(self):
        """Very small fee still returns minimum tick_spacing 1."""
        assert suggest_tick_spacing(0.001) == 1

    def test_fee_0_005_gives_1(self):
        """0.005% -> max(1, round(0.005 * 200)) = max(1, 1) = 1."""
        assert suggest_tick_spacing(0.005) == 1

    def test_zero_fee_gives_1(self):
        """0.0% fee -> max(1, 0) = 1 (minimum is 1)."""
        assert suggest_tick_spacing(0.0) == 1

    def test_returns_int(self):
        """Return value is always an int."""
        assert isinstance(suggest_tick_spacing(0.3), int)

    def test_large_fee(self):
        """Large fee percentage produces proportional tick spacing."""
        # 10% -> round(10 * 200) = 2000
        assert suggest_tick_spacing(10.0) == 2000

    def test_fractional_rounding(self):
        """Rounding behavior for fractional results."""
        # 0.123% -> round(0.123 * 200) = round(24.6) = 25
        assert suggest_tick_spacing(0.123) == 25


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestConstants:
    """Tests for module-level constants."""

    def test_max_v4_fee(self):
        """MAX_V4_FEE is 1_000_000."""
        assert MAX_V4_FEE == 1_000_000

    def test_min_v4_fee(self):
        """MIN_V4_FEE is 0."""
        assert MIN_V4_FEE == 0

    def test_common_tick_spacings_is_dict(self):
        """COMMON_TICK_SPACINGS is a dict."""
        assert isinstance(COMMON_TICK_SPACINGS, dict)

    def test_common_tick_spacings_known_keys(self):
        """COMMON_TICK_SPACINGS contains expected entries."""
        assert 1 in COMMON_TICK_SPACINGS
        assert 10 in COMMON_TICK_SPACINGS
        assert 60 in COMMON_TICK_SPACINGS
        assert 100 in COMMON_TICK_SPACINGS
        assert 200 in COMMON_TICK_SPACINGS

    def test_common_tick_spacings_values_match_keys(self):
        """In the current implementation, keys equal values."""
        for key, value in COMMON_TICK_SPACINGS.items():
            assert key == value

    def test_max_fee_equals_100_percent_conversion(self):
        """MAX_V4_FEE should equal fee_percent_to_v4(100.0)."""
        assert MAX_V4_FEE == fee_percent_to_v4(100.0)

    def test_min_fee_equals_0_percent_conversion(self):
        """MIN_V4_FEE should equal fee_percent_to_v4(0.0)."""
        assert MIN_V4_FEE == fee_percent_to_v4(0.0)


# ---------------------------------------------------------------------------
# Address registry completeness
# ---------------------------------------------------------------------------

class TestAddressRegistries:
    """Tests for the raw address registry dictionaries."""

    def test_uniswap_chains(self):
        """Uniswap registry has entries for chains 1, 56, 8453."""
        assert set(UNISWAP_V4_ADDRESSES.keys()) == {1, 56, 8453}

    def test_pancakeswap_chains(self):
        """PancakeSwap registry currently only has chain 56."""
        assert set(PANCAKESWAP_V4_ADDRESSES.keys()) == {56}

    def test_uniswap_bnb_has_state_view(self):
        """Uniswap BNB (56) has a state_view address."""
        assert UNISWAP_V4_ADDRESSES[56].state_view is not None

    def test_uniswap_base_has_state_view(self):
        """Uniswap Base (8453) has a state_view address."""
        assert UNISWAP_V4_ADDRESSES[8453].state_view is not None

    def test_uniswap_ethereum_no_state_view(self):
        """Uniswap Ethereum (1) does not have state_view."""
        assert UNISWAP_V4_ADDRESSES[1].state_view is None

    def test_pancakeswap_bnb_has_vault(self):
        """PancakeSwap BNB (56) has a vault address."""
        assert PANCAKESWAP_V4_ADDRESSES[56].vault is not None

    def test_pancakeswap_bnb_has_quoter(self):
        """PancakeSwap BNB (56) has a quoter address."""
        assert PANCAKESWAP_V4_ADDRESSES[56].quoter is not None

    def test_all_addresses_are_42_char_hex(self):
        """Every non-None address string is 42 characters (0x + 40 hex digits)."""
        all_registries = list(UNISWAP_V4_ADDRESSES.values()) + list(PANCAKESWAP_V4_ADDRESSES.values())
        for addr_obj in all_registries:
            for field_name in ("pool_manager", "position_manager", "quoter",
                               "universal_router", "vault", "state_view"):
                val = getattr(addr_obj, field_name)
                if val is not None:
                    assert len(val) == 42, f"{field_name} = {val!r} is not 42 chars"
                    assert val.startswith("0x"), f"{field_name} = {val!r} missing 0x prefix"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
