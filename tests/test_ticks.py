"""
Comprehensive tests for src.math.ticks module.

Covers all public functions:
    - price_to_tick
    - tick_to_price
    - align_tick_to_spacing
    - price_to_sqrt_price_x96
    - sqrt_price_x96_to_price
    - tick_to_sqrt_price_x96
    - get_tick_spacing
    - compute_decimal_tick_offset
    - get_price_range_for_tick_range
"""

import math
import pytest

from src.math.ticks import (
    price_to_tick,
    tick_to_price,
    align_tick_to_spacing,
    price_to_sqrt_price_x96,
    sqrt_price_x96_to_price,
    tick_to_sqrt_price_x96,
    get_tick_spacing,
    compute_decimal_tick_offset,
    get_price_range_for_tick_range,
    Q96,
    MIN_TICK,
    MAX_TICK,
    MIN_SQRT_RATIO,
    MAX_SQRT_RATIO,
    FEE_TO_TICK_SPACING,
)


# ---------------------------------------------------------------------------
# Test addresses used across multiple test classes
# ---------------------------------------------------------------------------
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # decimals=6, lower addr
VOLATILE_BASE = "0x9f86dB9fc6f7c9408e8Fda3Ff8ce4e78ac7a6b07"  # decimals=18, higher addr


# ===================================================================
# price_to_tick
# ===================================================================
class TestPriceToTick:
    """Tests for price_to_tick(price, invert)."""

    # --- basic identity / known values ---

    def test_price_one_gives_tick_zero(self):
        assert price_to_tick(1.0) == 0

    def test_price_1_0001_gives_tick_one(self):
        assert price_to_tick(1.0001) == 1

    def test_price_less_than_one_gives_negative_tick(self):
        tick = price_to_tick(0.5)
        assert tick < 0

    def test_price_greater_than_one_gives_positive_tick(self):
        tick = price_to_tick(2.0)
        assert tick > 0

    @pytest.mark.parametrize(
        "price, expected_tick",
        [
            (1.0, 0),
            (1.0001, 1),
            (1.0001 ** 100, 100),
            (1.0001 ** (-100), -101),  # floor makes it -101 due to float rounding
        ],
        ids=["one", "one-basis-point", "tick-100", "tick-neg100"],
    )
    def test_known_tick_values(self, price, expected_tick):
        """Verify exact tick for prices that are exact powers of 1.0001."""
        tick = price_to_tick(price)
        # Allow +/-1 tolerance for floating point edge cases
        assert abs(tick - expected_tick) <= 1

    # --- invert flag ---

    def test_invert_true_inverts_price_before_calculation(self):
        """invert=True should compute tick for 1/price."""
        tick_direct = price_to_tick(100.0)
        tick_inverted = price_to_tick(0.01, invert=True)
        assert tick_direct == tick_inverted

    def test_invert_true_price_one(self):
        """1/1 == 1, tick should still be 0."""
        assert price_to_tick(1.0, invert=True) == 0

    def test_invert_equivalence_multiple_prices(self):
        """price_to_tick(p) == price_to_tick(1/p, invert=True) for any p."""
        for price in [0.001, 0.1, 5.0, 1000.0, 50000.0]:
            assert price_to_tick(price) == price_to_tick(1.0 / price, invert=True)

    # --- error handling ---

    def test_zero_price_raises_value_error(self):
        with pytest.raises(ValueError, match="Price must be positive"):
            price_to_tick(0)

    def test_negative_price_raises_value_error(self):
        with pytest.raises(ValueError, match="Price must be positive"):
            price_to_tick(-1.0)

    # --- clamping ---

    def test_very_small_price_clamps_to_min_tick(self):
        """An extremely small price should not go below MIN_TICK."""
        tick = price_to_tick(1e-300)
        assert tick >= MIN_TICK

    def test_very_large_price_clamps_to_max_tick(self):
        """An extremely large price should not exceed MAX_TICK."""
        tick = price_to_tick(1e300)
        assert tick <= MAX_TICK

    def test_clamped_tick_equals_boundary(self):
        """When price is extreme, tick should be exactly at the boundary."""
        assert price_to_tick(1e-300) == MIN_TICK
        assert price_to_tick(1e300) == MAX_TICK

    # --- floor behaviour ---

    def test_floor_semantics(self):
        """Tick should be floor(log(price)/log(1.0001))."""
        price = 1.00015  # between tick 1 (1.0001) and tick 2 (1.00020001)
        tick = price_to_tick(price)
        assert tick == 1  # floor


# ===================================================================
# tick_to_price
# ===================================================================
class TestTickToPrice:
    """Tests for tick_to_price(tick, invert)."""

    def test_tick_zero_gives_price_one(self):
        assert tick_to_price(0) == 1.0

    def test_tick_one_gives_price_1_0001(self):
        assert abs(tick_to_price(1) - 1.0001) < 1e-10

    def test_negative_tick_gives_price_less_than_one(self):
        price = tick_to_price(-1000)
        assert 0 < price < 1.0

    def test_positive_tick_gives_price_greater_than_one(self):
        price = tick_to_price(1000)
        assert price > 1.0

    @pytest.mark.parametrize("tick", [-50000, -1000, -1, 0, 1, 1000, 50000])
    def test_tick_to_price_always_positive(self, tick):
        assert tick_to_price(tick) > 0

    # --- invert ---

    def test_invert_returns_reciprocal(self):
        tick = 46054
        pool_price = tick_to_price(tick)
        inverted_price = tick_to_price(tick, invert=True)
        assert abs(inverted_price - 1.0 / pool_price) < 1e-10

    def test_invert_tick_zero(self):
        """1/1 = 1."""
        assert tick_to_price(0, invert=True) == 1.0

    # --- roundtrip ---

    @pytest.mark.parametrize("price", [0.0001, 0.01, 1.0, 100.0, 5000.0, 100000.0])
    def test_price_tick_roundtrip(self, price):
        """price -> tick -> price should recover within 0.01% (1 tick)."""
        tick = price_to_tick(price)
        recovered = tick_to_price(tick)
        assert abs(recovered - price) / price < 0.0002  # 2 bps tolerance

    @pytest.mark.parametrize("tick", [-500000, -60, 0, 60, 500000])
    def test_tick_price_roundtrip(self, tick):
        """tick -> price -> tick should recover the same tick (or +/-1 for float)."""
        price = tick_to_price(tick)
        recovered_tick = price_to_tick(price)
        assert abs(recovered_tick - tick) <= 1


# ===================================================================
# align_tick_to_spacing
# ===================================================================
class TestAlignTickToSpacing:
    """Tests for align_tick_to_spacing(tick, tick_spacing, round_down)."""

    # --- already aligned ---

    @pytest.mark.parametrize("spacing", [1, 10, 50, 60, 200])
    def test_already_aligned_returns_unchanged(self, spacing):
        tick = spacing * 5  # guaranteed aligned
        assert align_tick_to_spacing(tick, spacing, round_down=True) == tick
        assert align_tick_to_spacing(tick, spacing, round_down=False) == tick

    def test_zero_tick_always_aligned(self):
        for spacing in [1, 10, 60, 200]:
            assert align_tick_to_spacing(0, spacing) == 0

    # --- round_down (towards -inf) ---

    @pytest.mark.parametrize(
        "tick, spacing, expected",
        [
            (100, 60, 60),
            (119, 60, 60),
            (61, 60, 60),
            (59, 60, 0),
            (1, 60, 0),
            (15, 10, 10),
            (99, 50, 50),
        ],
        ids=[
            "100/60->60", "119/60->60", "61/60->60", "59/60->0",
            "1/60->0", "15/10->10", "99/50->50",
        ],
    )
    def test_round_down_positive_ticks(self, tick, spacing, expected):
        assert align_tick_to_spacing(tick, spacing, round_down=True) == expected

    @pytest.mark.parametrize(
        "tick, spacing, expected",
        [
            (-1, 60, -60),
            (-59, 60, -60),
            (-60, 60, -60),      # already aligned
            (-61, 60, -120),
            (-100, 60, -120),
            (-15, 10, -20),
        ],
        ids=[
            "-1/60->-60", "-59/60->-60", "-60/60->-60",
            "-61/60->-120", "-100/60->-120", "-15/10->-20",
        ],
    )
    def test_round_down_negative_ticks(self, tick, spacing, expected):
        """Python floor division handles negatives correctly: -1//60 = -1 -> -1*60 = -60."""
        assert align_tick_to_spacing(tick, spacing, round_down=True) == expected

    # --- round_up (towards +inf) ---

    @pytest.mark.parametrize(
        "tick, spacing, expected",
        [
            (100, 60, 120),
            (1, 60, 60),
            (61, 60, 120),
            (-1, 60, 0),
            (-59, 60, 0),
            (-61, 60, -60),
            (-100, 60, -60),
        ],
        ids=[
            "100/60->120", "1/60->60", "61/60->120",
            "-1/60->0", "-59/60->0", "-61/60->-60", "-100/60->-60",
        ],
    )
    def test_round_up(self, tick, spacing, expected):
        assert align_tick_to_spacing(tick, spacing, round_down=False) == expected

    # --- spacing=1 (always aligned) ---

    @pytest.mark.parametrize("tick", [-500, -1, 0, 1, 500])
    def test_spacing_one_no_change(self, tick):
        assert align_tick_to_spacing(tick, 1, round_down=True) == tick
        assert align_tick_to_spacing(tick, 1, round_down=False) == tick


# ===================================================================
# price_to_sqrt_price_x96
# ===================================================================
class TestPriceToSqrtPriceX96:
    """Tests for price_to_sqrt_price_x96(price)."""

    def test_price_one_equals_q96(self):
        """sqrt(1) * 2^96 = 2^96."""
        assert price_to_sqrt_price_x96(1.0) == Q96

    def test_price_four(self):
        """sqrt(4) * 2^96 = 2 * 2^96."""
        result = price_to_sqrt_price_x96(4.0)
        expected = 2 * Q96
        assert abs(result - expected) <= 1  # int rounding

    def test_zero_price_raises_value_error(self):
        with pytest.raises(ValueError, match="Price must be positive"):
            price_to_sqrt_price_x96(0)

    def test_negative_price_raises_value_error(self):
        with pytest.raises(ValueError, match="Price must be positive"):
            price_to_sqrt_price_x96(-5.0)

    def test_result_is_integer(self):
        result = price_to_sqrt_price_x96(1234.5)
        assert isinstance(result, int)

    # --- clamping ---

    def test_very_small_price_clamps_to_min_sqrt_ratio(self):
        result = price_to_sqrt_price_x96(1e-40)
        assert result >= MIN_SQRT_RATIO

    def test_very_large_price_clamps_to_max_sqrt_ratio(self):
        result = price_to_sqrt_price_x96(1e40)
        assert result <= MAX_SQRT_RATIO

    # --- monotonicity ---

    def test_higher_price_gives_higher_sqrt_price_x96(self):
        a = price_to_sqrt_price_x96(10.0)
        b = price_to_sqrt_price_x96(100.0)
        assert b > a


# ===================================================================
# sqrt_price_x96_to_price
# ===================================================================
class TestSqrtPriceX96ToPrice:
    """Tests for sqrt_price_x96_to_price(sqrt_price_x96)."""

    def test_q96_gives_price_one(self):
        assert abs(sqrt_price_x96_to_price(Q96) - 1.0) < 1e-10

    def test_double_q96_gives_price_four(self):
        """(2*Q96 / Q96)^2 = 4."""
        price = sqrt_price_x96_to_price(2 * Q96)
        assert abs(price - 4.0) < 1e-10

    # --- roundtrip ---

    @pytest.mark.parametrize("price", [0.001, 0.1, 1.0, 10.0, 10000.0])
    def test_sqrt_price_x96_roundtrip(self, price):
        """price -> sqrtPriceX96 -> price should recover within tolerance."""
        sqrt_p = price_to_sqrt_price_x96(price)
        recovered = sqrt_price_x96_to_price(sqrt_p)
        # Allow generous tolerance: int truncation loses precision
        assert abs(recovered - price) / price < 1e-6

    def test_result_is_float(self):
        result = sqrt_price_x96_to_price(Q96)
        assert isinstance(result, float)


# ===================================================================
# tick_to_sqrt_price_x96
# ===================================================================
class TestTickToSqrtPriceX96:
    """Tests for tick_to_sqrt_price_x96(tick) -- composition of tick_to_price + price_to_sqrt_price_x96."""

    def test_tick_zero_equals_q96(self):
        """tick 0 -> price 1.0 -> sqrt(1)*2^96 = Q96."""
        assert tick_to_sqrt_price_x96(0) == Q96

    def test_matches_manual_composition(self):
        """Should equal price_to_sqrt_price_x96(tick_to_price(tick))."""
        for tick in [-50000, -100, 0, 100, 50000]:
            expected = price_to_sqrt_price_x96(tick_to_price(tick))
            assert tick_to_sqrt_price_x96(tick) == expected

    def test_positive_tick_larger_than_q96(self):
        """Positive tick -> price > 1 -> sqrtPriceX96 > Q96."""
        result = tick_to_sqrt_price_x96(1000)
        assert result > Q96

    def test_negative_tick_smaller_than_q96(self):
        """Negative tick -> price < 1 -> sqrtPriceX96 < Q96."""
        result = tick_to_sqrt_price_x96(-1000)
        assert result < Q96

    @pytest.mark.parametrize("tick", [-200000, -1, 0, 1, 200000])
    def test_result_within_sqrt_ratio_bounds(self, tick):
        result = tick_to_sqrt_price_x96(tick)
        assert MIN_SQRT_RATIO <= result <= MAX_SQRT_RATIO


# ===================================================================
# get_tick_spacing
# ===================================================================
class TestGetTickSpacing:
    """Tests for get_tick_spacing(fee, allow_custom)."""

    # --- standard V3 fee tiers ---

    @pytest.mark.parametrize(
        "fee, expected_spacing",
        [
            (100, 1),
            (500, 10),
            (2500, 50),
            (3000, 60),
            (10000, 200),
        ],
        ids=["0.01%", "0.05%", "0.25%-PCS", "0.30%-Uni", "1.00%"],
    )
    def test_standard_fee_tiers(self, fee, expected_spacing):
        assert get_tick_spacing(fee) == expected_spacing

    # --- standard fees work regardless of allow_custom ---

    @pytest.mark.parametrize("fee", [100, 500, 2500, 3000, 10000])
    def test_standard_fees_with_allow_custom_true(self, fee):
        assert get_tick_spacing(fee, allow_custom=True) == FEE_TO_TICK_SPACING[fee]

    # --- unknown fee, allow_custom=False ---

    @pytest.mark.parametrize("fee", [0, 1, 999, 5000, 99999])
    def test_unknown_fee_raises_value_error(self, fee):
        with pytest.raises(ValueError, match="Unknown fee tier"):
            get_tick_spacing(fee)

    # --- V4 custom fee logic ---

    def test_custom_fee_v4_formula(self):
        """Custom fee: tick_spacing = round(fee/10000 * 200), min 1."""
        # fee=5000 -> 0.5% -> 0.5*200 = 100
        assert get_tick_spacing(5000, allow_custom=True) == 100

    def test_custom_fee_v4_large(self):
        """fee=33330 -> 3.333% -> 3.333*200 = 666.6 -> 667."""
        assert get_tick_spacing(33330, allow_custom=True) == 667

    def test_custom_fee_minimum_spacing_is_one(self):
        """Very small custom fee should return spacing >= 1."""
        # fee=1 -> 0.0001% -> 0.0001*200 = 0.02 -> round(0.02)=0 -> max(1,0) = 1
        assert get_tick_spacing(1, allow_custom=True) == 1

    def test_custom_fee_zero_returns_one(self):
        """fee=0 -> 0*200=0 -> max(1,0)=1."""
        assert get_tick_spacing(0, allow_custom=True) == 1

    def test_custom_fee_exact_rounding(self):
        """fee=7500 -> 0.75% -> 0.75*200 = 150 (exact)."""
        assert get_tick_spacing(7500, allow_custom=True) == 150


# ===================================================================
# compute_decimal_tick_offset
# ===================================================================
class TestComputeDecimalTickOffset:
    """Tests for compute_decimal_tick_offset(addr0, dec0, addr1, dec1)."""

    # --- same decimals -> 0 ---

    def test_same_decimals_returns_zero_18_18(self):
        offset = compute_decimal_tick_offset(
            "0x1111111111111111111111111111111111111111", 18,
            "0x2222222222222222222222222222222222222222", 18,
        )
        assert offset == 0

    def test_same_decimals_returns_zero_6_6(self):
        offset = compute_decimal_tick_offset(
            "0x1111111111111111111111111111111111111111", 6,
            "0x2222222222222222222222222222222222222222", 6,
        )
        assert offset == 0

    # --- USDC (6 dec) + volatile (18 dec) on BASE ---

    def test_usdc_volatile_base_positive_offset(self):
        """
        USDC(6dec) at lower address = pool currency0,
        volatile(18dec) at higher address = pool currency1.
        dec_diff = 18 - 6 = 12 => offset = 12 * log(10)/log(1.0001) ~ 276324.
        """
        offset = compute_decimal_tick_offset(
            USDC_BASE, 6,
            VOLATILE_BASE, 18,
        )
        expected = int(round(12 * math.log(10) / math.log(1.0001)))
        assert offset == expected
        # Sanity: should be approximately 276324
        assert 276300 < offset < 276350

    def test_usdc_volatile_base_reversed_arg_order(self):
        """Passing tokens in reverse order should give the same result."""
        offset_normal = compute_decimal_tick_offset(USDC_BASE, 6, VOLATILE_BASE, 18)
        offset_reversed = compute_decimal_tick_offset(VOLATILE_BASE, 18, USDC_BASE, 6)
        assert offset_normal == offset_reversed

    # --- negative offset (currency0 has MORE decimals than currency1) ---

    def test_negative_offset_when_c0_has_more_decimals(self):
        """
        If the lower-address token has 18 decimals and the higher has 6,
        dec_diff = 6 - 18 = -12 => offset ~ -276324.
        """
        lower_addr = "0x1111111111111111111111111111111111111111"  # lower
        higher_addr = "0x9999999999999999999999999999999999999999"  # higher
        offset = compute_decimal_tick_offset(lower_addr, 18, higher_addr, 6)
        expected = int(round(-12 * math.log(10) / math.log(1.0001)))
        assert offset == expected
        assert offset < 0

    # --- address ordering ---

    def test_address_ordering_determines_pool_currency0(self):
        """
        Lower address is always pool currency0, regardless of argument order.
        Swapping token0/token1 in the arguments should not change the result.
        """
        addr_low = "0x0000000000000000000000000000000000000001"
        addr_high = "0xffffffffffffffffffffffffffffffffffffffff"

        offset_a = compute_decimal_tick_offset(addr_low, 6, addr_high, 18)
        offset_b = compute_decimal_tick_offset(addr_high, 18, addr_low, 6)
        assert offset_a == offset_b

    def test_case_insensitive_addresses(self):
        """Addresses with different casing should produce the same offset."""
        offset_lower = compute_decimal_tick_offset(
            USDC_BASE.lower(), 6,
            VOLATILE_BASE.lower(), 18,
        )
        offset_upper = compute_decimal_tick_offset(
            USDC_BASE.upper().replace("0X", "0x"), 6,
            VOLATILE_BASE.upper().replace("0X", "0x"), 18,
        )
        assert offset_lower == offset_upper

    # --- various decimal differences ---

    @pytest.mark.parametrize(
        "dec0, dec1, expected_dec_diff",
        [
            (6, 18, 12),
            (8, 18, 10),
            (6, 8, 2),
            (18, 6, -12),
        ],
        ids=["6-18=12", "8-18=10", "6-8=2", "18-6=-12"],
    )
    def test_various_decimal_differences(self, dec0, dec1, expected_dec_diff):
        """
        Always pass token with dec0 as the lower address, dec1 as the higher.
        dec_diff = pool_c1_dec - pool_c0_dec = dec1 - dec0.
        """
        lower_addr = "0x1111111111111111111111111111111111111111"
        higher_addr = "0x9999999999999999999999999999999999999999"
        offset = compute_decimal_tick_offset(lower_addr, dec0, higher_addr, dec1)
        expected = int(round(expected_dec_diff * math.log(10) / math.log(1.0001)))
        assert offset == expected

    # --- BNB chain (18/18) ---

    def test_bnb_chain_18_18_offset_zero(self):
        """On BNB chain both tokens are 18 dec, offset should be 0."""
        wbnb = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
        usdt_bsc = "0x55d398326f99059fF775485246999027B3197955"
        offset = compute_decimal_tick_offset(wbnb, 18, usdt_bsc, 18)
        assert offset == 0


# ===================================================================
# get_price_range_for_tick_range
# ===================================================================
class TestGetPriceRangeForTickRange:
    """Tests for get_price_range_for_tick_range(tick_lower, tick_upper)."""

    def test_returns_tuple_of_two_floats(self):
        result = get_price_range_for_tick_range(0, 100)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)
        assert isinstance(result[1], float)

    def test_tick_zero_to_zero(self):
        price_lower, price_upper = get_price_range_for_tick_range(0, 0)
        assert price_lower == 1.0
        assert price_upper == 1.0

    def test_lower_price_less_than_upper_price(self):
        """Lower tick should correspond to lower price."""
        price_lower, price_upper = get_price_range_for_tick_range(-1000, 1000)
        assert price_lower < price_upper

    def test_matches_tick_to_price_calls(self):
        """Should be identical to (tick_to_price(lower), tick_to_price(upper))."""
        tick_lower, tick_upper = -50000, 30000
        expected = (tick_to_price(tick_lower), tick_to_price(tick_upper))
        result = get_price_range_for_tick_range(tick_lower, tick_upper)
        assert result == expected

    @pytest.mark.parametrize(
        "tick_lower, tick_upper",
        [
            (-887272, 887272),
            (-60, 60),
            (0, 200),
            (-200, 0),
            (46000, 46200),
        ],
        ids=["full-range", "narrow", "positive-only", "negative-only", "deep-positive"],
    )
    def test_various_tick_ranges(self, tick_lower, tick_upper):
        p_low, p_high = get_price_range_for_tick_range(tick_lower, tick_upper)
        assert p_low > 0
        assert p_high > 0
        assert p_low <= p_high


# ===================================================================
# Constants sanity checks
# ===================================================================
class TestConstants:
    """Verify the module-level constants are correct."""

    def test_q96(self):
        assert Q96 == 2 ** 96

    def test_min_max_tick(self):
        assert MIN_TICK == -887272
        assert MAX_TICK == 887272
        assert MIN_TICK == -MAX_TICK

    def test_min_sqrt_ratio(self):
        assert MIN_SQRT_RATIO == 4295128739

    def test_max_sqrt_ratio(self):
        assert MAX_SQRT_RATIO == 1461446703485210103287273052203988822378723970342

    def test_fee_to_tick_spacing_contains_all_standard_tiers(self):
        expected_keys = {100, 500, 2500, 3000, 10000}
        assert set(FEE_TO_TICK_SPACING.keys()) == expected_keys


# ===================================================================
# Cross-function integration tests
# ===================================================================
class TestCrossFunctionIntegration:
    """Integration tests combining multiple functions."""

    def test_full_pipeline_price_to_sqrt_via_tick(self):
        """price -> tick -> sqrtPriceX96 vs price -> sqrtPriceX96 directly."""
        price = 2345.67
        # Route A: price -> tick -> price_approx -> sqrtPriceX96
        tick = price_to_tick(price)
        sqrt_via_tick = tick_to_sqrt_price_x96(tick)

        # Route B: price -> sqrtPriceX96 directly
        sqrt_direct = price_to_sqrt_price_x96(price)

        # The tick-route loses precision (floor), so allow some difference
        price_via_tick = sqrt_price_x96_to_price(sqrt_via_tick)
        price_direct = sqrt_price_x96_to_price(sqrt_direct)

        # Both should be very close to the original price
        assert abs(price_direct - price) / price < 1e-6
        assert abs(price_via_tick - price) / price < 0.0002  # tick granularity

    def test_aligned_tick_roundtrip_preserves_alignment(self):
        """Aligning a tick and converting to price and back should still be aligned."""
        spacing = 60
        original_tick = 12345
        aligned = align_tick_to_spacing(original_tick, spacing, round_down=True)
        price = tick_to_price(aligned)
        recovered_tick = price_to_tick(price)
        re_aligned = align_tick_to_spacing(recovered_tick, spacing, round_down=True)
        assert re_aligned == aligned

    def test_price_range_with_spacing_alignment(self):
        """Price range computed from aligned ticks should work correctly."""
        tick_lower = align_tick_to_spacing(-46080, 60, round_down=True)
        tick_upper = align_tick_to_spacing(-45000, 60, round_down=False)

        p_low, p_high = get_price_range_for_tick_range(tick_lower, tick_upper)
        assert p_low < p_high
        assert p_low > 0

    def test_decimal_offset_shifts_ticks_correctly(self):
        """
        On BASE, a token priced at $0.01 with USDC(6) / volatile(18):
        - Human tick = price_to_tick(0.01, invert=True)
        - Pool tick = human_tick + decimal_offset
        The offset should shift the tick into the correct range.
        """
        human_tick = price_to_tick(0.01, invert=True)
        offset = compute_decimal_tick_offset(USDC_BASE, 6, VOLATILE_BASE, 18)

        pool_tick = human_tick + offset
        # The pool tick should be different from the human tick
        assert pool_tick != human_tick
        # The offset should be ~276324
        assert abs(offset - 276324) < 50

    def test_get_tick_spacing_then_align(self):
        """Getting spacing from fee, then aligning a tick."""
        fee = 3000
        spacing = get_tick_spacing(fee)
        assert spacing == 60

        tick = 12345
        aligned_down = align_tick_to_spacing(tick, spacing, round_down=True)
        aligned_up = align_tick_to_spacing(tick, spacing, round_down=False)

        assert aligned_down % spacing == 0
        assert aligned_up % spacing == 0
        assert aligned_down < aligned_up
        assert aligned_down <= tick <= aligned_up


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
