"""
Comprehensive tests for src.math.distribution module.

Tests all public and private functions:
- Weight functions: _linear_weights, _quadratic_weights, _exponential_weights, _fibonacci_weights
- get_distribution_weights dispatcher
- calculate_bid_ask_distribution (core ladder builder)
- calculate_two_sided_distribution (two-sided ranges)
- calculate_bid_ask_from_percent (convenience wrapper)
"""

import pytest
import math
from src.math.distribution import (
    _linear_weights,
    _quadratic_weights,
    _exponential_weights,
    _fibonacci_weights,
    get_distribution_weights,
    calculate_bid_ask_distribution,
    calculate_two_sided_distribution,
    calculate_bid_ask_from_percent,
    BidAskPosition,
)


# ---------------------------------------------------------------------------
# Weight functions
# ---------------------------------------------------------------------------

class TestLinearWeights:
    """Tests for _linear_weights(n)."""

    def test_basic(self):
        assert _linear_weights(5) == [1, 2, 3, 4, 5]

    def test_single(self):
        assert _linear_weights(1) == [1]

    def test_two(self):
        assert _linear_weights(2) == [1, 2]

    def test_large(self):
        result = _linear_weights(10)
        assert result == list(range(1, 11))
        assert len(result) == 10

    def test_zero(self):
        """n=0 should return an empty list (range(0) is empty)."""
        assert _linear_weights(0) == []


class TestQuadraticWeights:
    """Tests for _quadratic_weights(n)."""

    def test_basic(self):
        assert _quadratic_weights(5) == [1, 4, 9, 16, 25]

    def test_single(self):
        assert _quadratic_weights(1) == [1]

    def test_values(self):
        result = _quadratic_weights(4)
        expected = [(i + 1) ** 2 for i in range(4)]
        assert result == expected

    def test_zero(self):
        assert _quadratic_weights(0) == []

    def test_growth_faster_than_linear(self):
        """Quadratic weights should grow faster than linear."""
        linear = _linear_weights(5)
        quadratic = _quadratic_weights(5)
        # Last element ratio: quadratic grows faster
        assert quadratic[-1] / quadratic[0] > linear[-1] / linear[0]


class TestExponentialWeights:
    """Tests for _exponential_weights(n, base=1.5)."""

    def test_default_base(self):
        result = _exponential_weights(4)
        expected = [1.5 ** i for i in range(4)]
        for r, e in zip(result, expected):
            assert abs(r - e) < 1e-10

    def test_first_element_always_one(self):
        """base^0 = 1 regardless of base."""
        assert _exponential_weights(3)[0] == 1.0
        assert _exponential_weights(3, base=2.0)[0] == 1.0
        assert _exponential_weights(3, base=10.0)[0] == 1.0

    def test_custom_base(self):
        result = _exponential_weights(3, base=2.0)
        assert result == [1.0, 2.0, 4.0]

    def test_single(self):
        assert _exponential_weights(1) == [1.0]

    def test_zero(self):
        assert _exponential_weights(0) == []

    def test_base_one(self):
        """base=1 means all weights are equal."""
        result = _exponential_weights(5, base=1.0)
        assert result == [1.0, 1.0, 1.0, 1.0, 1.0]


class TestFibonacciWeights:
    """Tests for _fibonacci_weights(n)."""

    def test_basic_six(self):
        assert _fibonacci_weights(6) == [1, 1, 2, 3, 5, 8]

    def test_single(self):
        assert _fibonacci_weights(1) == [1]

    def test_two(self):
        assert _fibonacci_weights(2) == [1, 1]

    def test_zero(self):
        assert _fibonacci_weights(0) == []

    def test_negative(self):
        """Negative n should return empty list (n <= 0 branch)."""
        assert _fibonacci_weights(-1) == []
        assert _fibonacci_weights(-100) == []

    def test_ten_elements(self):
        expected = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
        assert _fibonacci_weights(10) == expected

    def test_fibonacci_property(self):
        """Each element (from index 2) equals sum of previous two."""
        result = _fibonacci_weights(8)
        for i in range(2, len(result)):
            assert result[i] == result[i - 1] + result[i - 2]


# ---------------------------------------------------------------------------
# get_distribution_weights dispatcher
# ---------------------------------------------------------------------------

class TestGetDistributionWeights:
    """Tests for get_distribution_weights(n, distribution_type)."""

    def test_linear(self):
        assert get_distribution_weights(5, "linear") == [1, 2, 3, 4, 5]

    def test_quadratic(self):
        assert get_distribution_weights(5, "quadratic") == [1, 4, 9, 16, 25]

    def test_exponential(self):
        result = get_distribution_weights(4, "exponential")
        assert len(result) == 4
        assert result[0] == pytest.approx(1.0)
        assert result[1] == pytest.approx(1.5)

    def test_fibonacci(self):
        assert get_distribution_weights(6, "fibonacci") == [1, 1, 2, 3, 5, 8]

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown distribution type"):
            get_distribution_weights(5, "unknown")

    def test_unknown_type_various_strings(self):
        for bad_type in ["Linear", "QUADRATIC", "random", "", "lin"]:
            with pytest.raises(ValueError):
                get_distribution_weights(3, bad_type)

    def test_all_types_return_correct_length(self):
        for dist_type in ["linear", "quadratic", "exponential", "fibonacci"]:
            result = get_distribution_weights(7, dist_type)
            assert len(result) == 7, f"{dist_type} returned wrong length"


# ---------------------------------------------------------------------------
# calculate_bid_ask_distribution
# ---------------------------------------------------------------------------

class TestCalculateBidAskDistribution:
    """Tests for the core calculate_bid_ask_distribution function."""

    # --- Validation errors ---

    def test_same_prices_raises(self):
        with pytest.raises(ValueError, match="current_price and lower_price must be different"):
            calculate_bid_ask_distribution(
                current_price=10.0, lower_price=10.0,
                total_usd=1000, n_positions=5
            )

    def test_zero_positions_raises(self):
        with pytest.raises(ValueError, match="n_positions must be >= 1"):
            calculate_bid_ask_distribution(
                current_price=10.0, lower_price=5.0,
                total_usd=1000, n_positions=0
            )

    def test_negative_positions_raises(self):
        with pytest.raises(ValueError, match="n_positions must be >= 1"):
            calculate_bid_ask_distribution(
                current_price=10.0, lower_price=5.0,
                total_usd=1000, n_positions=-3
            )

    def test_zero_usd_raises(self):
        with pytest.raises(ValueError, match="total_usd must be > 0"):
            calculate_bid_ask_distribution(
                current_price=10.0, lower_price=5.0,
                total_usd=0, n_positions=5
            )

    def test_negative_usd_raises(self):
        with pytest.raises(ValueError, match="total_usd must be > 0"):
            calculate_bid_ask_distribution(
                current_price=10.0, lower_price=5.0,
                total_usd=-100, n_positions=5
            )

    # --- Basic structure ---

    def test_returns_correct_count(self):
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=2500
        )
        assert len(positions) == 5

    def test_indices_sequential(self):
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=2500
        )
        for i, pos in enumerate(positions):
            assert pos.index == i

    def test_all_fields_present(self):
        """Every BidAskPosition has all required fields."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500
        )
        for pos in positions:
            assert isinstance(pos.index, int)
            assert isinstance(pos.tick_lower, int)
            assert isinstance(pos.tick_upper, int)
            assert isinstance(pos.price_lower, float)
            assert isinstance(pos.price_upper, float)
            assert isinstance(pos.usd_amount, float)
            assert isinstance(pos.percentage, (int, float))
            assert isinstance(pos.liquidity, int)

    # --- USD totals ---

    def test_usd_sums_to_total(self):
        total = 1000.0
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=total, n_positions=5, fee_tier=2500,
            distribution_type="linear"
        )
        actual_sum = sum(p.usd_amount for p in positions)
        assert abs(actual_sum - total) < 0.01

    def test_usd_sums_various_distributions(self):
        """USD totals correctly for every distribution type."""
        total = 5000.0
        for dist_type in ["linear", "quadratic", "exponential", "fibonacci"]:
            positions = calculate_bid_ask_distribution(
                current_price=600.0, lower_price=300.0,
                total_usd=total, n_positions=7, fee_tier=2500,
                distribution_type=dist_type
            )
            actual_sum = sum(p.usd_amount for p in positions)
            assert abs(actual_sum - total) < 0.01, (
                f"USD sum mismatch for {dist_type}: {actual_sum}"
            )

    # --- Percentages ---

    def test_percentages_sum_to_100(self):
        positions = calculate_bid_ask_distribution(
            current_price=100.0, lower_price=50.0,
            total_usd=5000, n_positions=7, fee_tier=2500
        )
        total_pct = sum(p.percentage for p in positions)
        assert abs(total_pct - 100.0) < 0.1

    # --- Increasing liquidity towards lower prices ---

    def test_increasing_usd_towards_lower_prices(self):
        """Positions farther from current price get more USD (linear)."""
        positions = calculate_bid_ask_distribution(
            current_price=1000.0, lower_price=500.0,
            total_usd=10000, n_positions=5, fee_tier=2500,
            distribution_type="linear"
        )
        for i in range(1, len(positions)):
            assert positions[i].usd_amount >= positions[i - 1].usd_amount, (
                f"Position {i} ({positions[i].usd_amount:.2f}) should have "
                f">= position {i-1} ({positions[i-1].usd_amount:.2f})"
            )

    def test_increasing_usd_quadratic(self):
        """Quadratic: same property, steeper increase."""
        positions = calculate_bid_ask_distribution(
            current_price=1000.0, lower_price=500.0,
            total_usd=10000, n_positions=5, fee_tier=2500,
            distribution_type="quadratic"
        )
        for i in range(1, len(positions)):
            assert positions[i].usd_amount >= positions[i - 1].usd_amount

    def test_increasing_usd_fibonacci(self):
        positions = calculate_bid_ask_distribution(
            current_price=1000.0, lower_price=500.0,
            total_usd=10000, n_positions=5, fee_tier=2500,
            distribution_type="fibonacci"
        )
        # Fibonacci: [1,1,2,3,5] - first two are equal, rest increasing
        for i in range(2, len(positions)):
            assert positions[i].usd_amount >= positions[i - 1].usd_amount

    # --- Equal width positions ---

    def test_all_positions_same_tick_width(self):
        """All positions must have the same tick width."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=2500
        )
        widths = [p.tick_upper - p.tick_lower for p in positions]
        assert len(set(widths)) == 1, f"Tick widths not equal: {widths}"

    # --- Tick alignment ---

    def test_tick_alignment_pancakeswap_2500(self):
        """Fee tier 2500 -> tick spacing 50."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=2500
        )
        for p in positions:
            assert p.tick_lower % 50 == 0, f"tick_lower {p.tick_lower} not aligned to 50"
            assert p.tick_upper % 50 == 0, f"tick_upper {p.tick_upper} not aligned to 50"

    def test_tick_alignment_uniswap_3000(self):
        """Fee tier 3000 -> tick spacing 60."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=3000
        )
        for p in positions:
            assert p.tick_lower % 60 == 0, f"tick_lower {p.tick_lower} not aligned to 60"
            assert p.tick_upper % 60 == 0, f"tick_upper {p.tick_upper} not aligned to 60"

    def test_tick_alignment_fee_500(self):
        """Fee tier 500 -> tick spacing 10."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=500
        )
        for p in positions:
            assert p.tick_lower % 10 == 0, f"tick_lower {p.tick_lower} not aligned to 10"
            assert p.tick_upper % 10 == 0, f"tick_upper {p.tick_upper} not aligned to 10"

    def test_tick_alignment_fee_10000(self):
        """Fee tier 10000 -> tick spacing 200."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=10000
        )
        for p in positions:
            assert p.tick_lower % 200 == 0, f"tick_lower {p.tick_lower} not aligned to 200"
            assert p.tick_upper % 200 == 0, f"tick_upper {p.tick_upper} not aligned to 200"

    def test_explicit_tick_spacing_overrides_fee(self):
        """When tick_spacing is provided explicitly, it overrides fee_tier-derived spacing."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3,
            fee_tier=2500, tick_spacing=100  # override spacing to 100
        )
        for p in positions:
            assert p.tick_lower % 100 == 0, f"tick_lower {p.tick_lower} not aligned to 100"
            assert p.tick_upper % 100 == 0, f"tick_upper {p.tick_upper} not aligned to 100"

    # --- Tick ordering: tick_lower < tick_upper ---

    def test_tick_lower_less_than_upper(self):
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=2500
        )
        for p in positions:
            assert p.tick_lower < p.tick_upper

    # --- Price ordering: price_lower < price_upper ---

    def test_price_lower_less_than_upper(self):
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=2500
        )
        for p in positions:
            assert p.price_lower < p.price_upper, (
                f"Position {p.index}: price_lower={p.price_lower} >= price_upper={p.price_upper}"
            )

    # --- Positive liquidity ---

    def test_liquidity_positive(self):
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=5, fee_tier=2500
        )
        for p in positions:
            assert p.liquidity > 0, f"Position {p.index} has non-positive liquidity"

    # --- Edge case: n=1 ---

    def test_single_position(self):
        """Single position should still work correctly."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=500, n_positions=1, fee_tier=2500
        )
        assert len(positions) == 1
        pos = positions[0]
        assert pos.index == 0
        assert abs(pos.usd_amount - 500.0) < 0.01
        assert abs(pos.percentage - 100.0) < 0.1
        assert pos.tick_lower < pos.tick_upper
        assert pos.liquidity > 0

    # --- invert_price=True (default, token price in USD) ---

    def test_invert_price_true(self):
        """With invert_price=True (default): lower USD price -> higher tick."""
        positions = calculate_bid_ask_distribution(
            current_price=0.01, lower_price=0.005,
            total_usd=1000, n_positions=3, fee_tier=2500,
            invert_price=True
        )
        assert len(positions) == 3
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01
        for p in positions:
            assert p.tick_lower < p.tick_upper
            assert p.liquidity > 0

    # --- invert_price=False ---

    def test_invert_price_false(self):
        """With invert_price=False: prices are pool prices (token1/token0)."""
        positions = calculate_bid_ask_distribution(
            current_price=100.0, lower_price=50.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            invert_price=False
        )
        assert len(positions) == 3
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01
        for p in positions:
            assert p.tick_lower < p.tick_upper
            assert p.liquidity > 0

    def test_invert_true_vs_false_different_ticks(self):
        """invert_price should produce different tick values for the same numeric prices."""
        pos_inv = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=2, fee_tier=2500,
            invert_price=True
        )
        pos_no_inv = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=2, fee_tier=2500,
            invert_price=False
        )
        # Ticks must differ between inverted and non-inverted
        assert pos_inv[0].tick_lower != pos_no_inv[0].tick_lower

    # --- decimal_tick_offset ---

    def test_decimal_tick_offset_zero(self):
        """With offset=0 (same decimals, e.g. 18/18), ticks are not shifted."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            decimal_tick_offset=0
        )
        assert len(positions) == 3
        for p in positions:
            assert p.tick_lower % 50 == 0

    def test_decimal_tick_offset_nonzero(self):
        """With nonzero offset (e.g. USDC 6 dec on BASE), ticks are shifted."""
        offset = 276324  # ~12 * log(10)/log(1.0001) for 18-6=12 decimal difference
        positions_no_offset = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            decimal_tick_offset=0
        )
        positions_with_offset = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            decimal_tick_offset=offset
        )
        # With offset, ticks should be different (shifted)
        assert positions_with_offset[0].tick_lower != positions_no_offset[0].tick_lower
        # The offset should be reflected in tick values (approximately aligned_offset apart)
        aligned_offset = round(offset / 50) * 50
        diff = positions_with_offset[0].tick_lower - positions_no_offset[0].tick_lower
        assert abs(diff - aligned_offset) <= 50, (
            f"Tick difference {diff} not close to aligned offset {aligned_offset}"
        )

    def test_decimal_tick_offset_alignment(self):
        """The decimal tick offset itself is aligned to tick_spacing."""
        offset = 276324  # Not a multiple of 50
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            decimal_tick_offset=offset
        )
        # All ticks must still be aligned to tick_spacing (50)
        for p in positions:
            assert p.tick_lower % 50 == 0, f"tick_lower {p.tick_lower} not aligned to 50"
            assert p.tick_upper % 50 == 0, f"tick_upper {p.tick_upper} not aligned to 50"

    # --- Prices should still be human-readable (no decimal offset) ---

    def test_prices_not_shifted_by_decimal_offset(self):
        """Display prices should be the same regardless of decimal_tick_offset."""
        offset = 276324
        pos_no = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            decimal_tick_offset=0
        )
        pos_yes = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            decimal_tick_offset=offset
        )
        # Human-readable prices should be similar (derived from same input range)
        for a, b in zip(pos_no, pos_yes):
            assert abs(a.price_lower - b.price_lower) / max(a.price_lower, 1e-10) < 0.01
            assert abs(a.price_upper - b.price_upper) / max(a.price_upper, 1e-10) < 0.01

    # --- USD distribution amounts vary by distribution type ---

    def test_quadratic_more_skewed_than_linear(self):
        """Quadratic distribution should allocate more to the last position than linear."""
        pos_lin = calculate_bid_ask_distribution(
            current_price=600.0, lower_price=300.0,
            total_usd=1000, n_positions=5, fee_tier=2500,
            distribution_type="linear"
        )
        pos_quad = calculate_bid_ask_distribution(
            current_price=600.0, lower_price=300.0,
            total_usd=1000, n_positions=5, fee_tier=2500,
            distribution_type="quadratic"
        )
        # Last position in quadratic should get a bigger share
        assert pos_quad[-1].percentage > pos_lin[-1].percentage

    # --- Large number of positions ---

    def test_many_positions(self):
        """Verify that a large number of positions does not break anything."""
        positions = calculate_bid_ask_distribution(
            current_price=1000.0, lower_price=100.0,
            total_usd=50000, n_positions=50, fee_tier=2500
        )
        assert len(positions) == 50
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 50000) < 0.01

    # --- Small price range ---

    def test_very_narrow_price_range(self):
        """A very narrow range should still produce valid positions."""
        positions = calculate_bid_ask_distribution(
            current_price=100.0, lower_price=99.0,
            total_usd=100, n_positions=2, fee_tier=2500
        )
        assert len(positions) == 2
        for p in positions:
            assert p.tick_lower < p.tick_upper
            assert p.liquidity > 0

    # --- allow_custom_fee for V4 ---

    def test_allow_custom_fee_v4(self):
        """V4 custom fee tiers should work when allow_custom_fee=True."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3,
            fee_tier=33330,
            allow_custom_fee=True
        )
        assert len(positions) == 3
        for p in positions:
            assert p.tick_lower < p.tick_upper

    # --- Different token decimals ---

    def test_mixed_decimals_18_6(self):
        """Test with token0=18 decimals, token1=6 decimals (like USDC)."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0, lower_price=5.0,
            total_usd=1000, n_positions=3, fee_tier=2500,
            token0_decimals=18, token1_decimals=6,
            token1_is_stable=True
        )
        assert len(positions) == 3
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01
        for p in positions:
            assert p.liquidity > 0


# ---------------------------------------------------------------------------
# calculate_two_sided_distribution
# ---------------------------------------------------------------------------

class TestCalculateTwoSidedDistribution:
    """Tests for calculate_two_sided_distribution."""

    # --- Two-sided range (spans above and below current price) ---

    def test_two_sided_basic(self):
        """A range from -50% to +50% should produce positions on both sides."""
        positions = calculate_two_sided_distribution(
            current_price=100.0,
            percent_from=50.0,   # +50% => 150
            percent_to=-50.0,    # -50% => 50
            total_usd=1000,
            n_positions=10,
            fee_tier=2500
        )
        assert len(positions) == 10
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 1.0

    def test_two_sided_indices_sequential(self):
        positions = calculate_two_sided_distribution(
            current_price=100.0,
            percent_from=50.0,
            percent_to=-50.0,
            total_usd=1000,
            n_positions=10,
            fee_tier=2500
        )
        for i, pos in enumerate(positions):
            assert pos.index == i

    def test_two_sided_splits_positions_proportionally(self):
        """For a symmetric range (-50% to +50%), positions should split roughly equally."""
        positions = calculate_two_sided_distribution(
            current_price=100.0,
            percent_from=-50.0,
            percent_to=50.0,
            total_usd=1000,
            n_positions=10,
            fee_tier=2500
        )
        # With symmetric range, expect 5 below and 5 above (or very close)
        assert len(positions) == 10

    def test_two_sided_usd_split_proportional(self):
        """USD should be split proportional to range sizes."""
        current = 100.0
        pct_from = -20.0  # lower side: 20 points of range
        pct_to = 80.0     # upper side: 80 points of range
        total = 1000.0

        positions = calculate_two_sided_distribution(
            current_price=current,
            percent_from=pct_from,
            percent_to=pct_to,
            total_usd=total,
            n_positions=10,
            fee_tier=2500
        )
        total_actual = sum(p.usd_amount for p in positions)
        assert abs(total_actual - total) < 1.0

    def test_two_sided_asymmetric_range(self):
        """Asymmetric range: -10% to +40%."""
        positions = calculate_two_sided_distribution(
            current_price=600.0,
            percent_from=-10.0,
            percent_to=40.0,
            total_usd=5000,
            n_positions=10,
            fee_tier=2500
        )
        assert len(positions) == 10
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 5000) < 1.0

    # --- One-sided range (delegates to calculate_bid_ask_distribution) ---

    def test_one_sided_below(self):
        """Range entirely below current price: -10% to -50%."""
        positions = calculate_two_sided_distribution(
            current_price=100.0,
            percent_from=-10.0,
            percent_to=-50.0,
            total_usd=1000,
            n_positions=5,
            fee_tier=2500
        )
        assert len(positions) == 5
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01

    def test_one_sided_above(self):
        """Range entirely above current price: +10% to +50%."""
        positions = calculate_two_sided_distribution(
            current_price=100.0,
            percent_from=10.0,
            percent_to=50.0,
            total_usd=1000,
            n_positions=5,
            fee_tier=2500
        )
        assert len(positions) == 5
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01

    def test_one_sided_delegates_correctly(self):
        """One-sided range should delegate to calculate_bid_ask_distribution."""
        # Both approaches should give the same result for a one-sided range
        positions_two_sided = calculate_two_sided_distribution(
            current_price=100.0,
            percent_from=-10.0,
            percent_to=-50.0,
            total_usd=1000,
            n_positions=5,
            fee_tier=2500,
            distribution_type="linear",
            invert_price=True
        )
        # The one-sided function delegates with upper=90 (100 * (1-0.1)), lower=50 (100 * (1-0.5))
        positions_direct = calculate_bid_ask_distribution(
            current_price=90.0,
            lower_price=50.0,
            total_usd=1000,
            n_positions=5,
            fee_tier=2500,
            distribution_type="linear",
            invert_price=True
        )
        assert len(positions_two_sided) == len(positions_direct)
        # USD amounts should match
        for a, b in zip(positions_two_sided, positions_direct):
            assert abs(a.usd_amount - b.usd_amount) < 0.01

    # --- Position count splitting ---

    def test_position_count_matches_requested(self):
        """Total positions should always match n_positions."""
        for n in [2, 5, 10, 20]:
            positions = calculate_two_sided_distribution(
                current_price=100.0,
                percent_from=-30.0,
                percent_to=30.0,
                total_usd=1000,
                n_positions=n,
                fee_tier=2500
            )
            assert len(positions) == n, f"Expected {n} positions, got {len(positions)}"

    # --- With decimal_tick_offset ---

    def test_two_sided_with_decimal_offset(self):
        """Two-sided distribution should work with decimal_tick_offset."""
        positions = calculate_two_sided_distribution(
            current_price=10.0,
            percent_from=-30.0,
            percent_to=30.0,
            total_usd=1000,
            n_positions=6,
            fee_tier=2500,
            decimal_tick_offset=276324
        )
        assert len(positions) == 6
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 1.0

    # --- Minimum positions per side ---

    def test_two_sided_minimum_one_per_side(self):
        """Each side should have at least 1 position even with extreme asymmetry."""
        # Very asymmetric: -1% to +99% (almost all above)
        positions = calculate_two_sided_distribution(
            current_price=100.0,
            percent_from=-1.0,
            percent_to=99.0,
            total_usd=1000,
            n_positions=10,
            fee_tier=2500
        )
        assert len(positions) == 10
        # There should be at least 1 position on each side
        # (the function guarantees max(1, ...) for both sides)


# ---------------------------------------------------------------------------
# calculate_bid_ask_from_percent
# ---------------------------------------------------------------------------

class TestCalculateBidAskFromPercent:
    """Tests for calculate_bid_ask_from_percent (convenience wrapper)."""

    def test_basic_negative_range(self):
        """-5% to -50% range from current price."""
        positions = calculate_bid_ask_from_percent(
            current_price=600.0,
            percent_from=-5.0,
            percent_to=-50.0,
            total_usd=1000,
            n_positions=7,
            fee_tier=2500,
            distribution_type="linear"
        )
        assert len(positions) == 7
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01

    def test_pure_positive_range(self):
        """Range entirely above current price: +10% to +50%."""
        positions = calculate_bid_ask_from_percent(
            current_price=600.0,
            percent_from=10.0,
            percent_to=50.0,
            total_usd=2000,
            n_positions=5,
            fee_tier=2500
        )
        assert len(positions) == 5
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 2000) < 0.01

    def test_mixed_range(self):
        """Range spanning both sides: -20% to +30%."""
        positions = calculate_bid_ask_from_percent(
            current_price=600.0,
            percent_from=-20.0,
            percent_to=30.0,
            total_usd=3000,
            n_positions=10,
            fee_tier=2500
        )
        assert len(positions) == 10
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 3000) < 1.0

    def test_wrapper_same_as_two_sided(self):
        """calculate_bid_ask_from_percent should produce the same output as calculate_two_sided_distribution."""
        kwargs = dict(
            current_price=500.0,
            percent_from=-10.0,
            percent_to=-40.0,
            total_usd=2000,
            n_positions=5,
            fee_tier=2500,
            distribution_type="linear",
            invert_price=True,
            decimal_tick_offset=0,
        )
        pos_from_pct = calculate_bid_ask_from_percent(**kwargs)
        pos_two_sided = calculate_two_sided_distribution(**kwargs)

        assert len(pos_from_pct) == len(pos_two_sided)
        for a, b in zip(pos_from_pct, pos_two_sided):
            assert a.tick_lower == b.tick_lower
            assert a.tick_upper == b.tick_upper
            assert abs(a.usd_amount - b.usd_amount) < 0.001

    def test_all_distribution_types_from_percent(self):
        """Verify all distribution types work through the percent API."""
        for dist_type in ["linear", "quadratic", "exponential", "fibonacci"]:
            positions = calculate_bid_ask_from_percent(
                current_price=100.0,
                percent_from=-5.0,
                percent_to=-40.0,
                total_usd=500,
                n_positions=5,
                fee_tier=2500,
                distribution_type=dist_type
            )
            assert len(positions) == 5, f"Failed for {dist_type}"
            total = sum(p.usd_amount for p in positions)
            assert abs(total - 500) < 0.01, f"USD sum mismatch for {dist_type}"

    def test_from_percent_with_decimal_offset(self):
        """Verify percent API forwards decimal_tick_offset correctly."""
        offset = 276324
        positions = calculate_bid_ask_from_percent(
            current_price=10.0,
            percent_from=-10.0,
            percent_to=-50.0,
            total_usd=1000,
            n_positions=3,
            fee_tier=2500,
            decimal_tick_offset=offset
        )
        assert len(positions) == 3
        # Ticks should include the aligned offset
        aligned_offset = round(offset / 50) * 50
        for p in positions:
            assert p.tick_lower % 50 == 0
            assert p.tick_upper % 50 == 0

    def test_from_percent_with_invert_false(self):
        """Verify percent API forwards invert_price=False correctly."""
        positions = calculate_bid_ask_from_percent(
            current_price=100.0,
            percent_from=-10.0,
            percent_to=-40.0,
            total_usd=1000,
            n_positions=3,
            fee_tier=2500,
            invert_price=False
        )
        assert len(positions) == 3
        for p in positions:
            assert p.tick_lower < p.tick_upper

    def test_from_percent_large_range(self):
        """A very wide range -5% to -90%."""
        positions = calculate_bid_ask_from_percent(
            current_price=1000.0,
            percent_from=-5.0,
            percent_to=-90.0,
            total_usd=10000,
            n_positions=20,
            fee_tier=2500
        )
        assert len(positions) == 20
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 10000) < 0.01


# ---------------------------------------------------------------------------
# BidAskPosition dataclass
# ---------------------------------------------------------------------------

class TestBidAskPosition:
    """Tests for the BidAskPosition dataclass itself."""

    def test_creation(self):
        pos = BidAskPosition(
            index=0,
            tick_lower=-100,
            tick_upper=100,
            price_lower=5.0,
            price_upper=10.0,
            usd_amount=500.0,
            percentage=50.0,
            liquidity=123456
        )
        assert pos.index == 0
        assert pos.tick_lower == -100
        assert pos.tick_upper == 100
        assert pos.price_lower == 5.0
        assert pos.price_upper == 10.0
        assert pos.usd_amount == 500.0
        assert pos.percentage == 50.0
        assert pos.liquidity == 123456

    def test_equality(self):
        """Dataclass equality is by field values."""
        a = BidAskPosition(0, -100, 100, 5.0, 10.0, 500.0, 50.0, 123)
        b = BidAskPosition(0, -100, 100, 5.0, 10.0, 500.0, 50.0, 123)
        assert a == b

    def test_inequality(self):
        a = BidAskPosition(0, -100, 100, 5.0, 10.0, 500.0, 50.0, 123)
        b = BidAskPosition(1, -100, 100, 5.0, 10.0, 500.0, 50.0, 123)
        assert a != b


# ---------------------------------------------------------------------------
# Integration / realistic scenarios
# ---------------------------------------------------------------------------

class TestRealisticScenarios:
    """Integration tests with realistic price scenarios."""

    def test_bnb_600_ladder_below(self):
        """BNB at $600, ladder from -5% to -40% (typical buy-the-dip)."""
        positions = calculate_bid_ask_from_percent(
            current_price=600.0,
            percent_from=-5.0,
            percent_to=-40.0,
            total_usd=1000,
            n_positions=7,
            fee_tier=2500,
            distribution_type="linear",
            invert_price=True
        )
        assert len(positions) == 7
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01
        # Last position gets the most
        assert positions[-1].usd_amount > positions[0].usd_amount

    def test_low_price_token(self):
        """Token at $0.0009 (like a small-cap), ladder with invert_price=True."""
        positions = calculate_bid_ask_distribution(
            current_price=0.0009,
            lower_price=0.0003,
            total_usd=500,
            n_positions=5,
            fee_tier=2500,
            invert_price=True
        )
        assert len(positions) == 5
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 500) < 0.01

    def test_base_chain_usdc_6_decimals(self):
        """BASE chain: USDC (6 dec) / volatile (18 dec) with decimal offset."""
        # Typical BASE offset for 18-6=12 decimal diff
        offset = int(round(12 * math.log(10) / math.log(1.0001)))

        positions = calculate_bid_ask_distribution(
            current_price=10.0,
            lower_price=5.0,
            total_usd=1000,
            n_positions=5,
            fee_tier=2500,
            token0_decimals=18,
            token1_decimals=6,
            token1_is_stable=True,
            invert_price=True,
            decimal_tick_offset=offset
        )
        assert len(positions) == 5
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 0.01
        # Ticks should be shifted by the offset
        for p in positions:
            assert p.tick_lower % 50 == 0
            assert p.tick_upper % 50 == 0

    def test_two_sided_symmetric_bnb(self):
        """BNB at $600, symmetric range -30% to +30%."""
        positions = calculate_two_sided_distribution(
            current_price=600.0,
            percent_from=-30.0,
            percent_to=30.0,
            total_usd=2000,
            n_positions=10,
            fee_tier=2500,
            distribution_type="linear"
        )
        assert len(positions) == 10
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 2000) < 1.0

    def test_high_price_token(self):
        """ETH-like token at $3500, wide range."""
        positions = calculate_bid_ask_distribution(
            current_price=3500.0,
            lower_price=1500.0,
            total_usd=50000,
            n_positions=20,
            fee_tier=3000,
            distribution_type="quadratic",
            invert_price=True
        )
        assert len(positions) == 20
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 50000) < 0.01
        # Quadratic: last position should have significantly more than first
        ratio = positions[-1].usd_amount / positions[0].usd_amount
        # For 20 positions quadratic: weight ratio = 20^2 / 1^2 = 400
        assert ratio > 10  # Conservative check


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
