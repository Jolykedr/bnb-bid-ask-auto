"""
Tests for math modules.
"""

import pytest
import math
from src.math.ticks import (
    price_to_tick,
    tick_to_price,
    price_to_sqrt_price_x96,
    sqrt_price_x96_to_price,
    align_tick_to_spacing,
    get_tick_spacing,
    Q96
)
from src.math.liquidity import (
    calculate_liquidity_for_amount0,
    calculate_liquidity_for_amount1,
    calculate_amount0_for_liquidity,
    calculate_amount1_for_liquidity,
    calculate_liquidity_from_usd
)
from src.math.distribution import (
    calculate_bid_ask_distribution,
    get_distribution_weights
)


class TestTicks:
    """Tests for tick calculations."""

    def test_price_to_tick_basic(self):
        """Test basic price to tick conversion."""
        # price = 1.0001^tick
        # tick = log(price) / log(1.0001)
        tick = price_to_tick(1.0)
        assert tick == 0

        tick = price_to_tick(1.0001)
        assert tick == 1

    def test_tick_to_price_basic(self):
        """Test basic tick to price conversion."""
        price = tick_to_price(0)
        assert price == 1.0

        price = tick_to_price(1)
        assert abs(price - 1.0001) < 1e-10

    def test_price_tick_roundtrip(self):
        """Test that price -> tick -> price is consistent."""
        test_prices = [0.01, 1.0, 100.0, 5000.0, 100000.0]

        for original_price in test_prices:
            tick = price_to_tick(original_price)
            recovered_price = tick_to_price(tick)

            # Should be within 0.01% (1 tick)
            assert abs(recovered_price - original_price) / original_price < 0.0001

    def test_sqrt_price_x96_conversion(self):
        """Test sqrtPriceX96 conversion."""
        price = 1.0
        sqrt_price_x96 = price_to_sqrt_price_x96(price)

        # sqrt(1) * 2^96 = 2^96
        assert sqrt_price_x96 == Q96

        # Roundtrip
        recovered = sqrt_price_x96_to_price(sqrt_price_x96)
        assert abs(recovered - price) < 1e-10

    def test_align_tick_to_spacing(self):
        """Test tick alignment to spacing."""
        # Fee tier 0.3% = spacing 60
        spacing = 60

        assert align_tick_to_spacing(100, spacing, round_down=True) == 60
        assert align_tick_to_spacing(100, spacing, round_down=False) == 120
        assert align_tick_to_spacing(120, spacing, round_down=True) == 120
        # Floor division: -100 // 60 = -2 → -2*60 = -120
        assert align_tick_to_spacing(-100, spacing, round_down=True) == -120

    def test_get_tick_spacing(self):
        """Test fee tier to tick spacing mapping."""
        assert get_tick_spacing(100) == 1      # 0.01%
        assert get_tick_spacing(500) == 10     # 0.05%
        assert get_tick_spacing(2500) == 50    # 0.25% PancakeSwap
        assert get_tick_spacing(3000) == 60    # 0.30% Uniswap
        assert get_tick_spacing(10000) == 200  # 1.00%

        with pytest.raises(ValueError):
            get_tick_spacing(999)


class TestLiquidity:
    """Tests for liquidity calculations."""

    def test_liquidity_for_amount0(self):
        """Test liquidity calculation from amount0."""
        sqrt_lower = math.sqrt(1.0)
        sqrt_upper = math.sqrt(2.0)
        amount0 = 1000000

        liquidity = calculate_liquidity_for_amount0(sqrt_lower, sqrt_upper, amount0)
        assert liquidity > 0

    def test_liquidity_for_amount1(self):
        """Test liquidity calculation from amount1."""
        sqrt_lower = math.sqrt(1.0)
        sqrt_upper = math.sqrt(2.0)
        amount1 = 1000000

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, amount1)
        assert liquidity > 0

    def test_amount_liquidity_roundtrip(self):
        """Test amount -> liquidity -> amount roundtrip."""
        sqrt_lower = math.sqrt(100.0)
        sqrt_upper = math.sqrt(200.0)
        original_amount1 = 1000000

        liquidity = calculate_liquidity_for_amount1(sqrt_lower, sqrt_upper, original_amount1)
        recovered_amount1 = calculate_amount1_for_liquidity(sqrt_lower, sqrt_upper, liquidity)

        # Should be very close (allow small rounding error from integer division)
        assert abs(recovered_amount1 - original_amount1) <= 2

    def test_calculate_liquidity_from_usd(self):
        """Test USD to liquidity conversion."""
        liquidity = calculate_liquidity_from_usd(
            usd_amount=1000.0,
            price_lower=400.0,
            price_upper=500.0,
            current_price=600.0,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True
        )

        assert liquidity > 0


class TestDistribution:
    """Tests for distribution calculations."""

    def test_linear_weights(self):
        """Test linear distribution weights."""
        weights = get_distribution_weights(5, "linear")
        assert weights == [1, 2, 3, 4, 5]

    def test_quadratic_weights(self):
        """Test quadratic distribution weights."""
        weights = get_distribution_weights(5, "quadratic")
        assert weights == [1, 4, 9, 16, 25]

    def test_fibonacci_weights(self):
        """Test fibonacci distribution weights."""
        weights = get_distribution_weights(6, "fibonacci")
        assert weights == [1, 1, 2, 3, 5, 8]

    def test_bid_ask_distribution_basic_pancakeswap(self):
        """Test basic bid-ask distribution calculation with PancakeSwap fee tier."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0,
            lower_price=5.0,
            total_usd=1000,
            n_positions=5,
            fee_tier=2500,  # PancakeSwap 0.25%
            distribution_type="linear"
        )

        assert len(positions) == 5

        # Total USD should sum to 1000
        total = sum(p.usd_amount for p in positions)
        assert abs(total - 1000) < 1

        # First position (top) should have less liquidity than last (bottom)
        assert positions[0].usd_amount < positions[-1].usd_amount

        # All positions should have valid ticks
        for p in positions:
            assert p.tick_lower < p.tick_upper
            assert p.price_lower < p.price_upper

        # Verify tick spacing for PancakeSwap (2500 fee = 50 tick spacing)
        for p in positions:
            assert p.tick_lower % 50 == 0, f"tickLower {p.tick_lower} not aligned to spacing 50"
            assert p.tick_upper % 50 == 0, f"tickUpper {p.tick_upper} not aligned to spacing 50"

    def test_bid_ask_distribution_basic_uniswap(self):
        """Test basic bid-ask distribution calculation with Uniswap fee tier."""
        positions = calculate_bid_ask_distribution(
            current_price=10.0,
            lower_price=5.0,
            total_usd=1000,
            n_positions=5,
            fee_tier=3000,  # Uniswap 0.3%
            distribution_type="linear"
        )

        assert len(positions) == 5

        # Verify tick spacing for Uniswap (3000 fee = 60 tick spacing)
        for p in positions:
            assert p.tick_lower % 60 == 0, f"tickLower {p.tick_lower} not aligned to spacing 60"
            assert p.tick_upper % 60 == 0, f"tickUpper {p.tick_upper} not aligned to spacing 60"

    def test_bid_ask_distribution_percentages(self):
        """Test that percentages sum to 100."""
        positions = calculate_bid_ask_distribution(
            current_price=100.0,
            lower_price=50.0,
            total_usd=5000,
            n_positions=7,
            fee_tier=2500,  # PancakeSwap 0.25%
            distribution_type="linear"
        )

        total_percentage = sum(p.percentage for p in positions)
        assert abs(total_percentage - 100.0) < 0.1

    def test_bid_ask_increasing_liquidity(self):
        """Test that liquidity increases towards lower prices."""
        positions = calculate_bid_ask_distribution(
            current_price=1000.0,
            lower_price=500.0,
            total_usd=10000,
            n_positions=5,
            fee_tier=2500,  # PancakeSwap 0.25%
            distribution_type="linear"
        )

        # Each position should have more USD than the previous (going down)
        for i in range(1, len(positions)):
            assert positions[i].usd_amount >= positions[i-1].usd_amount


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
