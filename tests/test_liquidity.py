"""
Comprehensive tests for src/math/liquidity.py

Tests all public functions:
- decimal_sqrt
- usd_to_wei
- calculate_liquidity_for_amount0
- calculate_liquidity_for_amount1
- calculate_liquidity
- calculate_amount0_for_liquidity
- calculate_amount1_for_liquidity
- calculate_amounts
- calculate_liquidity_from_usd
- LiquidityAmounts dataclass
"""

import pytest
import math
from decimal import Decimal

from src.math.liquidity import (
    decimal_sqrt,
    usd_to_wei,
    LiquidityAmounts,
    calculate_liquidity_for_amount0,
    calculate_liquidity_for_amount1,
    calculate_liquidity,
    calculate_amount0_for_liquidity,
    calculate_amount1_for_liquidity,
    calculate_amounts,
    calculate_liquidity_from_usd,
)


# ---------------------------------------------------------------------------
# LiquidityAmounts dataclass
# ---------------------------------------------------------------------------

class TestLiquidityAmounts:
    """Tests for the LiquidityAmounts dataclass."""

    def test_construction(self):
        """Dataclass can be constructed with positional args."""
        la = LiquidityAmounts(amount0=100, amount1=200, liquidity=300)
        assert la.amount0 == 100
        assert la.amount1 == 200
        assert la.liquidity == 300

    def test_fields_are_accessible(self):
        """All fields are readable attributes."""
        la = LiquidityAmounts(amount0=0, amount1=0, liquidity=0)
        assert hasattr(la, "amount0")
        assert hasattr(la, "amount1")
        assert hasattr(la, "liquidity")

    def test_equality(self):
        """Two instances with same values are equal (dataclass default)."""
        a = LiquidityAmounts(amount0=1, amount1=2, liquidity=3)
        b = LiquidityAmounts(amount0=1, amount1=2, liquidity=3)
        assert a == b

    def test_inequality(self):
        """Different values produce unequal instances."""
        a = LiquidityAmounts(amount0=1, amount1=2, liquidity=3)
        b = LiquidityAmounts(amount0=10, amount1=2, liquidity=3)
        assert a != b

    def test_repr(self):
        """repr contains the class name and field values."""
        la = LiquidityAmounts(amount0=5, amount1=10, liquidity=15)
        r = repr(la)
        assert "LiquidityAmounts" in r
        assert "5" in r
        assert "10" in r
        assert "15" in r

    def test_large_values(self):
        """Dataclass handles very large ints (typical for wei amounts)."""
        big = 10**30
        la = LiquidityAmounts(amount0=big, amount1=big * 2, liquidity=big * 3)
        assert la.amount0 == big
        assert la.amount1 == big * 2
        assert la.liquidity == big * 3

    def test_zero_values(self):
        """Dataclass accepts all-zero fields."""
        la = LiquidityAmounts(amount0=0, amount1=0, liquidity=0)
        assert la.amount0 == 0
        assert la.amount1 == 0
        assert la.liquidity == 0


# ---------------------------------------------------------------------------
# decimal_sqrt
# ---------------------------------------------------------------------------

class TestDecimalSqrt:
    """Tests for decimal_sqrt()."""

    def test_sqrt_of_4(self):
        assert decimal_sqrt(4) == Decimal(2)

    def test_sqrt_of_1(self):
        assert decimal_sqrt(1) == Decimal(1)

    def test_sqrt_of_0(self):
        assert decimal_sqrt(0) == Decimal(0)

    def test_sqrt_of_0_25(self):
        result = decimal_sqrt(0.25)
        assert result == Decimal("0.5")

    def test_sqrt_of_2(self):
        result = decimal_sqrt(2)
        # Python math.sqrt(2) for comparison
        expected = Decimal(str(math.sqrt(2)))
        # High-precision Decimal result should be close
        assert abs(float(result) - math.sqrt(2)) < 1e-12

    def test_sqrt_of_large_number(self):
        """sqrt(10^20) = 10^10."""
        result = decimal_sqrt(10**20)
        assert result == Decimal(10**10)

    def test_sqrt_of_large_non_perfect_square(self):
        """sqrt(2 * 10^18) should be precise."""
        result = decimal_sqrt(2 * 10**18)
        expected = math.sqrt(2) * 10**9
        assert abs(float(result) - expected) < 1.0  # within 1 unit

    def test_returns_decimal_type(self):
        """Return type is always Decimal."""
        assert isinstance(decimal_sqrt(9), Decimal)
        assert isinstance(decimal_sqrt(2.5), Decimal)
        assert isinstance(decimal_sqrt(Decimal("7")), Decimal)

    def test_accepts_float(self):
        result = decimal_sqrt(2.25)
        assert result == Decimal("1.5")

    def test_accepts_decimal_input(self):
        result = decimal_sqrt(Decimal("16"))
        assert result == Decimal(4)

    def test_precision_higher_than_float(self):
        """Decimal sqrt should provide more than 15 significant digits."""
        result = decimal_sqrt(2)
        result_str = str(result)
        # Remove "0." prefix, count digits
        digits = result_str.replace(".", "").lstrip("0")
        assert len(digits) >= 20  # well above float's ~15 digits


# ---------------------------------------------------------------------------
# usd_to_wei
# ---------------------------------------------------------------------------

class TestUsdToWei:
    """Tests for usd_to_wei()."""

    def test_100_with_18_decimals(self):
        assert usd_to_wei(100.0, 18) == 100_000_000_000_000_000_000

    def test_100_with_6_decimals(self):
        assert usd_to_wei(100.0, 6) == 100_000_000

    def test_tiny_amount_18_decimals(self):
        """0.000001 with 18 decimals -> 1_000_000_000_000."""
        assert usd_to_wei(0.000001, 18) == 1_000_000_000_000

    def test_zero(self):
        assert usd_to_wei(0, 18) == 0
        assert usd_to_wei(0.0, 6) == 0

    def test_1_with_0_decimals(self):
        """Edge case: token with 0 decimals."""
        assert usd_to_wei(1.0, 0) == 1

    def test_fractional_truncated(self):
        """Result is truncated, not rounded."""
        # 1.999999 * 10^6 = 1999999.0 -> int = 1999999
        assert usd_to_wei(1.999999, 6) == 1_999_999

    def test_very_small_amount_truncated(self):
        """Amount smaller than 1 wei (6 dec) is truncated to 0."""
        # 0.0000001 * 10^6 = 0.1 -> int(0.1) = 0
        assert usd_to_wei(0.0000001, 6) == 0

    def test_large_amount(self):
        """Large USD amount with 18 decimals."""
        result = usd_to_wei(1_000_000.0, 18)
        assert result == 1_000_000 * 10**18

    def test_returns_int(self):
        assert isinstance(usd_to_wei(1.5, 18), int)

    def test_1_with_18_decimals(self):
        assert usd_to_wei(1.0, 18) == 10**18

    def test_0_5_with_6_decimals(self):
        assert usd_to_wei(0.5, 6) == 500_000


# ---------------------------------------------------------------------------
# calculate_liquidity_for_amount0
# ---------------------------------------------------------------------------

class TestCalculateLiquidityForAmount0:
    """Tests for calculate_liquidity_for_amount0()."""

    def test_basic_positive(self):
        """Basic call returns positive liquidity."""
        sqrt_lower = math.sqrt(1.0)
        sqrt_upper = math.sqrt(2.0)
        amount0 = 10**18  # 1 token with 18 decimals

        liquidity = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
        assert liquidity > 0

    def test_raises_on_equal_prices(self):
        with pytest.raises(ValueError, match="sqrt_price_upper must be > sqrt_price_lower"):
            calculate_liquidity_for_amount0(1.0, 1.0, 100)

    def test_raises_on_inverted_prices(self):
        with pytest.raises(ValueError, match="sqrt_price_upper must be > sqrt_price_lower"):
            calculate_liquidity_for_amount0(2.0, 1.0, 100)

    def test_formula_correctness(self):
        """Verify against manual formula: L = amount0 * (upper * lower) / (upper - lower)."""
        sqrt_lower = Decimal("10.0")
        sqrt_upper = Decimal("20.0")
        amount0 = 1_000_000

        expected = int(Decimal(str(amount0)) * sqrt_upper * sqrt_lower / (sqrt_upper - sqrt_lower))
        result = calculate_liquidity_for_amount0(float(sqrt_lower), float(sqrt_upper), amount0)
        assert result == expected

    def test_narrow_range(self):
        """Very narrow price range produces very high liquidity (concentrated)."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(100.01)
        amount0 = 10**18

        liquidity = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
        assert liquidity > 10**18  # much larger than amount due to narrow range

    def test_wide_range(self):
        """Wide range produces lower liquidity per unit than narrow range."""
        sqrt_lower = math.sqrt(1.0)
        sqrt_upper = math.sqrt(10000.0)
        amount0 = 10**18

        liq_wide = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
        assert liq_wide > 0

        # Compare with narrow range -- narrow should give higher liquidity
        sqrt_lower_narrow = math.sqrt(100.0)
        sqrt_upper_narrow = math.sqrt(100.01)
        liq_narrow = calculate_liquidity_for_amount0(sqrt_lower_narrow, sqrt_upper_narrow, amount0)
        assert liq_narrow > liq_wide

    def test_zero_amount(self):
        """Zero amount produces zero liquidity."""
        sqrt_lower = math.sqrt(1.0)
        sqrt_upper = math.sqrt(2.0)
        assert calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, 0) == 0

    def test_large_amount(self):
        """Works with very large wei amounts."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount0 = 10**30  # huge amount

        liquidity = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
        assert liquidity > 0


# ---------------------------------------------------------------------------
# calculate_liquidity_for_amount1
# ---------------------------------------------------------------------------

class TestCalculateLiquidityForAmount1:
    """Tests for calculate_liquidity_for_amount1()."""

    def test_basic_positive(self):
        sqrt_lower = math.sqrt(1.0)
        sqrt_upper = math.sqrt(2.0)
        amount1 = 10**18

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amount1)
        assert liquidity > 0

    def test_raises_on_equal_prices(self):
        with pytest.raises(ValueError, match="sqrt_price_upper must be > sqrt_price_lower"):
            calculate_liquidity_for_amount1(1.0, 1.0, 100)

    def test_raises_on_inverted_prices(self):
        with pytest.raises(ValueError, match="sqrt_price_upper must be > sqrt_price_lower"):
            calculate_liquidity_for_amount1(2.0, 1.0, 100)

    def test_formula_correctness(self):
        """Verify against manual formula: L = amount1 / (upper - lower)."""
        sqrt_lower = Decimal("10.0")
        sqrt_upper = Decimal("20.0")
        amount1 = 5_000_000

        expected = int(Decimal(str(amount1)) / (sqrt_upper - sqrt_lower))
        result = calculate_liquidity_for_amount1(float(sqrt_lower), float(sqrt_upper), amount1)
        assert result == expected

    def test_narrow_range(self):
        """Narrow range => high liquidity."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(100.01)
        amount1 = 10**18

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amount1)
        assert liquidity > amount1  # concentrated

    def test_zero_amount(self):
        sqrt_lower = math.sqrt(1.0)
        sqrt_upper = math.sqrt(2.0)
        assert calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, 0) == 0

    def test_large_amount(self):
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount1 = 10**30

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amount1)
        assert liquidity > 0


# ---------------------------------------------------------------------------
# calculate_liquidity
# ---------------------------------------------------------------------------

class TestCalculateLiquidity:
    """Tests for calculate_liquidity() -- three-case dispatcher."""

    # --- Case 1: current < lower => uses amount0 ---

    def test_current_below_lower_uses_amount0(self):
        """When current price < lower, only amount0 is needed."""
        sqrt_current = math.sqrt(50.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount0 = 10**18

        liquidity = calculate_liquidity(sqrt_current, sqrt_lower, sqrt_upper, amount0=amount0)
        assert liquidity > 0

        # Should be same as calling calculate_liquidity_for_amount0 directly
        expected = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
        assert liquidity == expected

    def test_current_below_lower_missing_amount0_raises(self):
        """When current < lower and only amount1 given, raises ValueError."""
        sqrt_current = math.sqrt(50.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)

        with pytest.raises(ValueError, match="amount0 required"):
            calculate_liquidity(sqrt_current, sqrt_lower, sqrt_upper, amount1=10**18)

    # --- Case 2: current > upper => uses amount1 ---

    def test_current_above_upper_uses_amount1(self):
        """When current price > upper, only amount1 is needed."""
        sqrt_current = math.sqrt(500.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount1 = 10**18

        liquidity = calculate_liquidity(sqrt_current, sqrt_lower, sqrt_upper, amount1=amount1)
        assert liquidity > 0

        expected = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amount1)
        assert liquidity == expected

    def test_current_above_upper_missing_amount1_raises(self):
        """When current > upper and only amount0 given, raises ValueError."""
        sqrt_current = math.sqrt(500.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)

        with pytest.raises(ValueError, match="amount1 required"):
            calculate_liquidity(sqrt_current, sqrt_lower, sqrt_upper, amount0=10**18)

    # --- Case 3: lower <= current <= upper => both amounts ---

    def test_in_range_uses_both_amounts(self):
        """When current is in range, uses min(L0, L1)."""
        sqrt_current = math.sqrt(150.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount0 = 10**18
        amount1 = 10**18

        liquidity = calculate_liquidity(
            sqrt_current, sqrt_lower, sqrt_upper,
            amount0=amount0, amount1=amount1
        )
        assert liquidity > 0

        # Verify it equals min(L0, L1)
        l0 = calculate_liquidity_for_amount0(sqrt_current, sqrt_upper, amount0)
        l1 = calculate_liquidity_for_amount1(sqrt_lower, sqrt_current, amount1)
        assert liquidity == min(l0, l1)

    def test_in_range_only_amount0(self):
        """In range with only amount0 provided (amount1 is None)."""
        sqrt_current = math.sqrt(150.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount0 = 10**18

        liquidity = calculate_liquidity(
            sqrt_current, sqrt_lower, sqrt_upper,
            amount0=amount0
        )
        assert liquidity > 0

        expected = calculate_liquidity_for_amount0(sqrt_current, sqrt_upper, amount0)
        assert liquidity == expected

    def test_in_range_only_amount1(self):
        """In range with only amount1 provided (amount0 is None)."""
        sqrt_current = math.sqrt(150.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount1 = 10**18

        liquidity = calculate_liquidity(
            sqrt_current, sqrt_lower, sqrt_upper,
            amount1=amount1
        )
        assert liquidity > 0

        expected = calculate_liquidity_for_amount1(sqrt_lower, sqrt_current, amount1)
        assert liquidity == expected

    # --- Neither amount provided ---

    def test_no_amounts_raises(self):
        """Raises ValueError if neither amount0 nor amount1 is given."""
        with pytest.raises(ValueError, match="Either amount0 or amount1 must be provided"):
            calculate_liquidity(1.0, 0.5, 1.5)

    # --- Boundary: current exactly at lower / upper ---

    def test_current_at_lower_with_amount1_raises(self):
        """current == lower with only amount1: in-range branch calls
        calculate_liquidity_for_amount1(lower, current) where current == lower,
        which means upper == lower => raises ValueError.
        """
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount1 = 10**18

        with pytest.raises(ValueError, match="sqrt_price_upper must be > sqrt_price_lower"):
            calculate_liquidity(
                sqrt_lower, sqrt_lower, sqrt_upper,
                amount1=amount1
            )

    def test_current_at_lower_with_amount0(self):
        """current == lower with only amount0: in-range, uses L0."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount0 = 10**18

        liquidity = calculate_liquidity(
            sqrt_lower, sqrt_lower, sqrt_upper,
            amount0=amount0
        )
        expected = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
        assert liquidity == expected

    def test_current_at_upper_boundary(self):
        """current == upper: in-range, uses L1 from (lower, current)."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        amount1 = 10**18

        liquidity = calculate_liquidity(
            sqrt_upper, sqrt_lower, sqrt_upper,
            amount1=amount1
        )
        expected = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amount1)
        assert liquidity == expected


# ---------------------------------------------------------------------------
# calculate_amount0_for_liquidity
# ---------------------------------------------------------------------------

class TestCalculateAmount0ForLiquidity:
    """Tests for calculate_amount0_for_liquidity()."""

    def test_basic_positive(self):
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**18

        amount0 = calculate_amount0_for_liquidity(sqrt_lower, sqrt_upper, liquidity)
        assert amount0 > 0

    def test_raises_on_equal_prices(self):
        with pytest.raises(ValueError):
            calculate_amount0_for_liquidity(1.0, 1.0, 100)

    def test_raises_on_inverted_prices(self):
        with pytest.raises(ValueError):
            calculate_amount0_for_liquidity(2.0, 1.0, 100)

    def test_formula_correctness(self):
        """amount0 = L * (upper - lower) / (upper * lower)."""
        sqrt_lower = Decimal("10.0")
        sqrt_upper = Decimal("20.0")
        liquidity = 1_000_000

        expected = int(
            Decimal(str(liquidity)) * (sqrt_upper - sqrt_lower) / (sqrt_upper * sqrt_lower)
        )
        result = calculate_amount0_for_liquidity(float(sqrt_lower), float(sqrt_upper), liquidity)
        assert result == expected

    def test_zero_liquidity(self):
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        assert calculate_amount0_for_liquidity(sqrt_lower, sqrt_upper, 0) == 0

    def test_roundtrip_with_liquidity_for_amount0(self):
        """amount0 -> L -> amount0 should roundtrip within rounding tolerance."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        original_amount0 = 10**18

        liquidity = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, original_amount0)
        recovered = calculate_amount0_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        # Allow small rounding error from integer truncation
        assert abs(recovered - original_amount0) <= 2

    def test_roundtrip_small_amount(self):
        """Roundtrip with small amount."""
        sqrt_lower = math.sqrt(500.0)
        sqrt_upper = math.sqrt(600.0)
        original_amount0 = 1_000_000  # 1 USDC in 6-decimal

        liquidity = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, original_amount0)
        recovered = calculate_amount0_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        assert abs(recovered - original_amount0) <= 2

    def test_roundtrip_large_amount(self):
        """Roundtrip with very large amount (whale position)."""
        sqrt_lower = math.sqrt(1000.0)
        sqrt_upper = math.sqrt(2000.0)
        original_amount0 = 10**30

        liquidity = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, original_amount0)
        recovered = calculate_amount0_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        assert abs(recovered - original_amount0) <= 2


# ---------------------------------------------------------------------------
# calculate_amount1_for_liquidity
# ---------------------------------------------------------------------------

class TestCalculateAmount1ForLiquidity:
    """Tests for calculate_amount1_for_liquidity()."""

    def test_basic_positive(self):
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**18

        amount1 = calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, liquidity)
        assert amount1 > 0

    def test_raises_on_equal_prices(self):
        with pytest.raises(ValueError):
            calculate_amount1_for_liquidity(1.0, 1.0, 100)

    def test_raises_on_inverted_prices(self):
        with pytest.raises(ValueError):
            calculate_amount1_for_liquidity(2.0, 1.0, 100)

    def test_formula_correctness(self):
        """amount1 = L * (upper - lower)."""
        sqrt_lower = Decimal("10.0")
        sqrt_upper = Decimal("20.0")
        liquidity = 2_000_000

        expected = int(Decimal(str(liquidity)) * (sqrt_upper - sqrt_lower))
        result = calculate_amount1_for_liquidity(float(sqrt_lower), float(sqrt_upper), liquidity)
        assert result == expected

    def test_zero_liquidity(self):
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        assert calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, 0) == 0

    def test_roundtrip_with_liquidity_for_amount1(self):
        """amount1 -> L -> amount1 should roundtrip within rounding tolerance."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        original_amount1 = 10**18

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, original_amount1)
        recovered = calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        assert abs(recovered - original_amount1) <= 2

    def test_roundtrip_6_decimal_token(self):
        """Roundtrip for a 6-decimal token (e.g. USDC)."""
        sqrt_lower = math.sqrt(500.0)
        sqrt_upper = math.sqrt(600.0)
        original_amount1 = 1_000 * 10**6  # 1000 USDC

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, original_amount1)
        recovered = calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        assert abs(recovered - original_amount1) <= 2

    def test_roundtrip_large_amount(self):
        """Roundtrip with very large amounts allows proportional rounding error."""
        sqrt_lower = math.sqrt(1000.0)
        sqrt_upper = math.sqrt(2000.0)
        original_amount1 = 10**30

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, original_amount1)
        recovered = calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        # For very large values, integer truncation can cause errors proportional
        # to the sqrt_price range. Allow relative tolerance of 1e-20.
        assert abs(recovered - original_amount1) / original_amount1 < 1e-20


# ---------------------------------------------------------------------------
# calculate_amounts
# ---------------------------------------------------------------------------

class TestCalculateAmounts:
    """Tests for calculate_amounts()."""

    def test_current_below_range_only_amount0(self):
        """When current < lower, position is entirely in token0."""
        sqrt_current = math.sqrt(50.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**18

        result = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)

        assert isinstance(result, LiquidityAmounts)
        assert result.amount0 > 0
        assert result.amount1 == 0
        assert result.liquidity == liquidity

    def test_current_above_range_only_amount1(self):
        """When current > upper, position is entirely in token1."""
        sqrt_current = math.sqrt(500.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**18

        result = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)

        assert result.amount0 == 0
        assert result.amount1 > 0
        assert result.liquidity == liquidity

    def test_current_in_range_both_amounts(self):
        """When lower <= current <= upper, both amounts are non-zero."""
        sqrt_current = math.sqrt(150.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**18

        result = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)

        assert result.amount0 > 0
        assert result.amount1 > 0
        assert result.liquidity == liquidity

    def test_returns_liquidity_amounts(self):
        """Return type is LiquidityAmounts."""
        result = calculate_amounts(1.0, 0.5, 1.5, 100)
        assert isinstance(result, LiquidityAmounts)

    def test_in_range_amounts_consistent_with_component_functions(self):
        """In-range amounts match the individual calculation functions."""
        sqrt_current = math.sqrt(150.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**20

        result = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)

        expected_a0 = calculate_amount0_for_liquidity(sqrt_current, sqrt_upper, liquidity)
        expected_a1 = calculate_amount1_for_liquidity(sqrt_lower, sqrt_current, liquidity)

        assert result.amount0 == expected_a0
        assert result.amount1 == expected_a1

    def test_below_range_amounts_consistent(self):
        """Below-range amount0 matches the component function."""
        sqrt_current = math.sqrt(50.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**20

        result = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)

        expected_a0 = calculate_amount0_for_liquidity(sqrt_lower, sqrt_upper, liquidity)
        assert result.amount0 == expected_a0

    def test_above_range_amounts_consistent(self):
        """Above-range amount1 matches the component function."""
        sqrt_current = math.sqrt(500.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        liquidity = 10**20

        result = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)

        expected_a1 = calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, liquidity)
        assert result.amount1 == expected_a1

    def test_zero_liquidity(self):
        """Zero liquidity gives zero amounts."""
        result = calculate_amounts(math.sqrt(150.0), math.sqrt(100.0), math.sqrt(200.0), 0)
        assert result.amount0 == 0
        assert result.amount1 == 0
        assert result.liquidity == 0

    def test_liquidity_roundtrip_below_range(self):
        """amount0 from calculate_amounts can reconstruct liquidity."""
        sqrt_current = math.sqrt(50.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        original_liq = 10**18

        amounts = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, original_liq)
        recovered_liq = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amounts.amount0)

        # Two integer truncations (L->amount->L) compound rounding error
        assert abs(recovered_liq - original_liq) / original_liq < 1e-14

    def test_liquidity_roundtrip_above_range(self):
        """amount1 from calculate_amounts can reconstruct liquidity."""
        sqrt_current = math.sqrt(500.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        original_liq = 10**18

        amounts = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, original_liq)
        recovered_liq = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amounts.amount1)

        assert abs(recovered_liq - original_liq) <= 2


# ---------------------------------------------------------------------------
# calculate_liquidity_from_usd
# ---------------------------------------------------------------------------

class TestCalculateLiquidityFromUsd:
    """Tests for calculate_liquidity_from_usd()."""

    # --- token1_is_stable=True scenarios ---

    def test_position_below_current_token1_stable(self):
        """Position below current price: stablecoin (token1) is used.
        current_price > price_upper => position_below=True.
        """
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )
        assert liquidity > 0

    def test_position_above_current_token1_stable(self):
        """Position above current price: volatile (token0) is used.
        current_price < price_lower => position_above=True.
        """
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=700.0,
            price_upper=800.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )
        assert liquidity > 0

    def test_position_in_range_token1_stable(self):
        """Position in range: falls into the 'not position_above' branch => uses token1."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=500.0,
            price_upper=700.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )
        assert liquidity > 0

    # --- token1_is_stable=False scenarios ---

    def test_position_above_current_token0_stable(self):
        """token0 is stable. Position above current => uses stablecoin (token0)."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=700.0,
            price_upper=800.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=False,
        )
        assert liquidity > 0

    def test_position_below_current_token0_stable(self):
        """token0 is stable. Position below current => uses volatile (token1)."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=False,
        )
        assert liquidity > 0

    def test_position_in_range_token0_stable(self):
        """token0 is stable, position in range => 'not position_below' => uses token0."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=500.0,
            price_upper=700.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=False,
        )
        assert liquidity > 0

    # --- Different decimals (6 vs 18) ---

    def test_6_decimal_stablecoin_token1(self):
        """USDC (6 dec) as token1 stablecoin, volatile (18 dec) as token0."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=6,
            token1_is_stable=True,
        )
        assert liquidity > 0

    def test_6_decimal_stablecoin_token0(self):
        """USDC (6 dec) as token0 stablecoin, volatile (18 dec) as token1."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=700.0,
            price_upper=800.0,
            current_price=600.0,
            token0_decimals=6,
            token1_decimals=18,
            token1_is_stable=False,
        )
        assert liquidity > 0

    # --- Proportionality / sanity checks ---

    def test_double_usd_roughly_doubles_liquidity(self):
        """Doubling USD amount should roughly double the liquidity."""
        liq1 = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
        )
        liq2 = calculate_liquidity_from_usd(
            usd_amount=2000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
        )
        ratio = liq2 / liq1
        assert 1.9 < ratio < 2.1

    def test_zero_usd_returns_zero(self):
        """Zero USD amount should give zero liquidity."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=0.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
        )
        assert liquidity == 0

    def test_narrow_range_higher_liquidity(self):
        """Narrower price range should produce higher liquidity for same USD."""
        liq_wide = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=100.0,
            price_upper=500.0,
            current_price=600.0,
        )
        liq_narrow = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
        )
        assert liq_narrow > liq_wide

    def test_default_decimals_are_18(self):
        """Default decimals (omitted) should behave same as explicit 18/18."""
        liq_default = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
        )
        liq_explicit = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )
        assert liq_default == liq_explicit

    def test_default_token1_is_stable(self):
        """Default token1_is_stable should be True."""
        liq_default = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
        )
        liq_explicit = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )
        assert liq_default == liq_explicit

    def test_small_usd_amount(self):
        """Very small USD amount should still produce valid liquidity."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=0.01,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
        )
        assert liquidity >= 0

    def test_large_usd_amount(self):
        """Large USD amount (whale)."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1_000_000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
        )
        assert liquidity > 0

    # --- Realistic BNB/USDT scenario ---

    def test_bnb_usdt_position_below(self):
        """Realistic BNB/USDT: BNB at 600, position from 400-500 (below current).
        token1_is_stable=True (USDT is token1), 18 dec for both on BSC.
        """
        liquidity = calculate_liquidity_from_usd(
            usd_amount=500.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )
        assert liquidity > 0

    # --- Realistic BASE WETH/USDC scenario ---

    def test_base_weth_usdc_position_below(self):
        """BASE WETH/USDC: ETH at 3000, position 2000-2500 (below current).
        token1_is_stable=True (USDC 6 dec), token0 = WETH (18 dec).
        """
        liquidity = calculate_liquidity_from_usd(
            usd_amount=500.0,
            price_lower=2000.0,
            price_upper=2500.0,
            current_price=3000.0,
            token0_decimals=18,
            token1_decimals=6,
            token1_is_stable=True,
        )
        assert liquidity > 0


# ---------------------------------------------------------------------------
# Cross-function integration tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration tests combining multiple liquidity functions."""

    def test_full_cycle_below_range(self):
        """USD -> liquidity -> amounts -> liquidity roundtrip (below range)."""
        usd = 1000.0
        price_lower = 400.0
        price_upper = 500.0
        current_price = 600.0  # above range

        # Step 1: USD -> liquidity
        liquidity = calculate_liquidity_from_usd(
            usd_amount=usd,
            price_lower=price_lower,
            price_upper=price_upper,
            current_price=current_price,
        )
        assert liquidity > 0

        # Step 2: liquidity -> amounts
        sqrt_current = math.sqrt(current_price)
        sqrt_lower = math.sqrt(price_lower)
        sqrt_upper = math.sqrt(price_upper)

        amounts = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)
        # Position is below current => all in token1
        assert amounts.amount0 == 0
        assert amounts.amount1 > 0

        # Step 3: amounts -> liquidity (reverse)
        recovered_liq = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amounts.amount1)
        assert abs(recovered_liq - liquidity) <= 2

    def test_full_cycle_above_range(self):
        """USD -> liquidity -> amounts -> liquidity roundtrip (above range)."""
        usd = 1000.0
        price_lower = 700.0
        price_upper = 800.0
        current_price = 600.0  # below range

        liquidity = calculate_liquidity_from_usd(
            usd_amount=usd,
            price_lower=price_lower,
            price_upper=price_upper,
            current_price=current_price,
        )
        assert liquidity > 0

        sqrt_current = math.sqrt(current_price)
        sqrt_lower = math.sqrt(price_lower)
        sqrt_upper = math.sqrt(price_upper)

        amounts = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, liquidity)
        # Position is above current => all in token0
        assert amounts.amount0 > 0
        assert amounts.amount1 == 0

        recovered_liq = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amounts.amount0)
        # Multiple float->Decimal conversions and integer truncations compound
        assert abs(recovered_liq - liquidity) / max(liquidity, 1) < 1e-15

    def test_symmetry_amount0_amount1(self):
        """For a unit-price range, check L from amount0 and L from amount1 relate properly.

        At prices around 1.0, with sqrt prices close to 1.0, the two formulas
        should produce consistent results when fed the correct amounts.
        """
        sqrt_lower = math.sqrt(0.9)
        sqrt_upper = math.sqrt(1.1)
        liquidity = 10**20

        a0 = calculate_amount0_for_liquidity(sqrt_lower, sqrt_upper, liquidity)
        a1 = calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        # Now recover liquidity from each amount
        l_from_a0 = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, a0)
        l_from_a1 = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, a1)

        # Both should be close to the original
        assert abs(l_from_a0 - liquidity) <= 2
        assert abs(l_from_a1 - liquidity) <= 2

    def test_calculate_amounts_then_calculate_liquidity(self):
        """calculate_amounts -> calculate_liquidity roundtrip in range."""
        sqrt_current = math.sqrt(150.0)
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        original_liq = 10**20

        amounts = calculate_amounts(sqrt_current, sqrt_lower, sqrt_upper, original_liq)

        # Use both amounts to recover liquidity
        recovered_liq = calculate_liquidity(
            sqrt_current, sqrt_lower, sqrt_upper,
            amount0=amounts.amount0, amount1=amounts.amount1
        )

        # min(L0, L1) should be close to original (relative tolerance for large values)
        assert abs(recovered_liq - original_liq) / original_liq < 1e-15


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
