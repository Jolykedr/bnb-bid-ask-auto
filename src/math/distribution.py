"""
Bid-Ask Distribution Module

Стратегия "лесенка на покупку":
- Минимум ликвидности вверху (около текущей цены)
- Максимум ликвидности внизу (дальше от текущей цены)

Пример для токена $10, диапазон $5-$10, общая сумма $1000:
- $9-10: $50 (5%)
- $8-9: $100 (10%)
- $7-8: $150 (15%)
- $6-7: $250 (25%)
- $5-6: $450 (45%)

Чем ниже цена - тем больше покупаем.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Literal
from .ticks import price_to_tick, tick_to_price, align_tick_to_spacing, get_tick_spacing
from .liquidity import calculate_liquidity_from_usd, calculate_amounts, decimal_sqrt

logger = logging.getLogger(__name__)


@dataclass
class BidAskPosition:
    """Одна позиция в лесенке."""
    index: int                    # Порядковый номер (0 = ближе к текущей цене)
    tick_lower: int               # Нижний тик
    tick_upper: int               # Верхний тик
    price_lower: float            # Нижняя цена
    price_upper: float            # Верхняя цена
    usd_amount: float             # Сумма в USD
    percentage: float             # Процент от общей суммы
    liquidity: int                # Расчётная liquidity
    amount0: int = 0              # Token0 amount в wei (pool order)
    amount1: int = 0              # Token1 amount в wei (pool order)
    side: str = "bid"             # "bid" (стейбл вниз) / "ask" (volatile вверх)


DistributionType = Literal["linear", "quadratic", "exponential", "fibonacci"]


def _linear_weights(n: int) -> List[float]:
    """
    Линейное распределение: 1, 2, 3, ..., n
    """
    return [i + 1 for i in range(n)]


def _quadratic_weights(n: int) -> List[float]:
    """
    Квадратичное распределение: 1, 4, 9, ..., n^2
    Более агрессивное накопление внизу.
    """
    return [(i + 1) ** 2 for i in range(n)]


def _exponential_weights(n: int, base: float = 1.5) -> List[float]:
    """
    Экспоненциальное распределение: base^0, base^1, ..., base^(n-1)
    """
    return [base ** i for i in range(n)]


def _fibonacci_weights(n: int) -> List[float]:
    """
    Распределение по Фибоначчи: 1, 1, 2, 3, 5, 8, ...
    """
    if n <= 0:
        return []
    if n == 1:
        return [1]

    weights = [1, 1]
    for i in range(2, n):
        weights.append(weights[-1] + weights[-2])
    return weights[:n]


def solve_distribution(a: int, b: int) -> tuple:
    """
    Distribute `a` total spacings across `b` positions as uniformly as possible.

    Returns (widths_in_spacings, b_actual) where widths is a list of length
    b_actual giving each position's width in spacings (1 spacing = tick_spacing).

    Constraint: first (b-1) positions have equal width `x`, last position
    has width `y`. The "wider" direction is bounded — last cannot exceed
    1.35×x — but the "narrower" direction is unbounded (last can be 1).

    Solver:
        (b-1)·x + y = a
        1 ≤ y ≤ 1.35·x  (wider capped, narrower free since y can drop to 1)
        x, y integers ≥ 1

    Picks the smallest valid x, which yields the largest valid y — i.e. the
    solution closest to fully uniform when one exists.

    If no integer solution exists for the requested b, decrements b until one
    is found. b can also be clamped if a < b (every position needs ≥ 1 spacing).
    Method B (width-aware USD) handles the resulting USD asymmetry.
    """
    if b < 1:
        b = 1
    if a < 1:
        return ([1], 1)
    if b > a:
        b = a
    while b >= 2:
        # y = a - (b-1)·x
        #   y ≤ 1.35·x  =>  x ≥ a / (b - 1 + 1.35) = a / (b + 0.35)
        #   y ≥ 1       =>  x ≤ (a - 1) / (b - 1)
        x_min = max(1, math.ceil(a / (b + 0.35)))
        x_max = (a - 1) // (b - 1)
        if x_min <= x_max:
            x = x_min
            y = a - (b - 1) * x
            return ([x] * (b - 1) + [y], b)
        b -= 1
    return ([a], 1)


def get_distribution_weights(n: int, distribution_type: DistributionType) -> List[float]:
    """
    Получение весов распределения.

    Args:
        n: Количество позиций
        distribution_type: Тип распределения

    Returns:
        Список весов (не нормализованный)
    """
    if distribution_type == "linear":
        return _linear_weights(n)
    elif distribution_type == "quadratic":
        return _quadratic_weights(n)
    elif distribution_type == "exponential":
        return _exponential_weights(n)
    elif distribution_type == "fibonacci":
        return _fibonacci_weights(n)
    else:
        raise ValueError(f"Unknown distribution type: {distribution_type}")


def calculate_bid_ask_distribution(
    current_price: float,
    lower_price: float,
    total_usd: float,
    n_positions: int,
    fee_tier: int = 2500,
    distribution_type: DistributionType = "linear",
    token0_decimals: int = 18,
    token1_decimals: int = 18,
    token1_is_stable: bool = True,
    allow_custom_fee: bool = False,
    tick_spacing: int = None,
    invert_price: bool = True,
    decimal_tick_offset: int = 0
) -> List[BidAskPosition]:
    """
    Расчёт позиций для bid-ask лесенки.

    ВАЖНО: Это стратегия покупки при падении цены.
    - Диапазон от lower_price до current_price
    - Минимум ликвидности вверху (около current_price)
    - Максимум внизу (около lower_price)
    - ВСЕ ДИАПАЗОНЫ РАВНОЙ ШИРИНЫ

    Args:
        current_price: Верхняя граница диапазона (около текущей рыночной цены)
        lower_price: Нижняя граница всего диапазона
        total_usd: Общая сумма в USD
        n_positions: Количество позиций (рейнджей)
        fee_tier: Fee tier пула: 100, 500, 2500 (PancakeSwap), 3000 (Uniswap), 10000
        distribution_type: Тип распределения весов
        token0_decimals: Decimals token0
        token1_decimals: Decimals token1
        token1_is_stable: True если token1 - стейблкоин
        allow_custom_fee: Разрешить кастомные fee tiers (для V4)
        tick_spacing: Явный tick spacing (если None - вычисляется из fee_tier)
        invert_price: Флаг инверсии цены для расчёта тиков.

            СЕМАНТИКА invert_price:
            - True (default): Входные цены - это "TOKEN price in USD" (например, 0.005 USD за токен).
              Для расчёта тиков цена инвертируется в pool price формат.
              Используется когда пользователь вводит цену токена в долларах.

            - False: Входные цены уже в pool price формате (token1/token0).
              Используется когда стейблкоин - token1 в пуле (адрес стейблкоина > адрес токена).

            Определение в V3 провайдере (liquidity_provider.py):
              stablecoin_is_token1_in_pool = stablecoin_addr > volatile_addr
              invert_price = not stablecoin_is_token1_in_pool

    Returns:
        Список BidAskPosition от верхней к нижней

    Example:
        >>> positions = calculate_bid_ask_distribution(
        ...     current_price=10.0,
        ...     lower_price=5.0,
        ...     total_usd=1000,
        ...     n_positions=5,
        ...     distribution_type="linear"
        ... )
        >>> # Позиции: $9-10 (мало), $8-9 (больше), ..., $5-6 (максимум)
        >>> # Все диапазоны равны ~$1
    """
    # Убираем проверку порядка цен - направление определяется автоматически
    # При invert_price: lower USD price → higher tick, higher USD price → lower tick
    if current_price == lower_price:
        raise ValueError("current_price and lower_price must be different")
    if n_positions < 1:
        raise ValueError("n_positions must be >= 1")
    if total_usd <= 0:
        raise ValueError("total_usd must be > 0")

    # Use provided tick_spacing or calculate from fee_tier
    if tick_spacing is None:
        tick_spacing = get_tick_spacing(fee_tier, allow_custom=allow_custom_fee)

    # Вычисляем тики границ
    # ВАЖНО: invert_price=True означает, что входные цены - это "TOKEN price in USD"
    # и нужно их инвертировать для получения pool price (token1/token0)
    #
    # Пример: USDT/TOKEN pool (USDT=token0, TOKEN=token1)
    # - current_price = 0.0009 → pool_price = 1111 → tick ≈ 70154
    # - lower_price = 0.003 → pool_price = 333 → tick ≈ 57564
    #
    # Позиции создаются НИЖЕ текущего тика (57564-70154)
    # Это соответствует TOKEN ценам ВЫШЕ текущей ($0.003 > $0.0009)
    tick_current = price_to_tick(current_price, invert=invert_price)
    tick_limit = price_to_tick(lower_price, invert=invert_price)

    # Определяем какой тик выше для правильного выравнивания
    if tick_current > tick_limit:
        # Позиции НИЖЕ текущего тика (стандартный случай с invert_price)
        tick_upper_aligned = align_tick_to_spacing(tick_current, tick_spacing, round_down=True)
        tick_lower_aligned = align_tick_to_spacing(tick_limit, tick_spacing, round_down=True)
    else:
        # Позиции ВЫШЕ текущего тика
        tick_lower_aligned = align_tick_to_spacing(tick_current, tick_spacing, round_down=False)
        tick_upper_aligned = align_tick_to_spacing(tick_limit, tick_spacing, round_down=True)

    # Убеждаемся что upper > lower
    if tick_upper_aligned <= tick_lower_aligned:
        tick_upper_aligned = tick_lower_aligned + tick_spacing

    # Диапазон тиков
    total_ticks = tick_upper_aligned - tick_lower_aligned

    # Ограничиваем число позиций по доступному диапазону тиков
    max_positions = total_ticks // tick_spacing
    if max_positions < 1:
        max_positions = 1
    if n_positions > max_positions:
        logger.warning(
            f"n_positions={n_positions} exceeds available range "
            f"({total_ticks} ticks / {tick_spacing} spacing = {max_positions} max). "
            f"Clamping to {max_positions}."
        )
        n_positions = max_positions

    # Solve uniform-width distribution: first (n-1) positions have equal width
    # `x` spacings, last position has width `y` constrained to 1 ≤ y ≤ 1.35·x
    # (wider direction capped, narrower direction unbounded). If no integer
    # solution exists for the requested n, solve_distribution decrements n.
    total_spacings = total_ticks // tick_spacing  # >= 1
    widths_spacings, n_actual = solve_distribution(total_spacings, n_positions)
    if n_actual < n_positions:
        logger.warning(
            f"n_positions={n_positions} exceeds available spacings "
            f"(a={total_spacings}). Reduced to {n_actual}."
        )
        n_positions = n_actual

    widths_ticks = [w * tick_spacing for w in widths_spacings]

    # Width-aware USD distribution: weight scaled by (width / avg_width).
    # When all widths equal, avg_width == widths[i] and adjusted_weights == weights
    # (existing flow unchanged). When last is narrower → less USD; wider → more USD.
    # Density (USD/tick) follows the bid-ask shape smoothly.
    raw_weights = get_distribution_weights(n_positions, distribution_type)
    avg_width = sum(widths_ticks) / n_positions
    adjusted_weights = [
        raw_weights[i] * (widths_ticks[i] / avg_width)
        for i in range(n_positions)
    ]
    total_adjusted = sum(adjusted_weights)

    positions = []
    cumulative_ticks = 0

    # Определяем направление: от текущей цены (tick_current) к целевой (tick_limit)
    positions_go_down = tick_current > tick_limit  # True = позиции ниже текущего тика

    # Align decimal tick offset to tick_spacing (computed once, used for all positions)
    # Raw offset may not be a multiple of tick_spacing (e.g. 276324 % 200 = 124)
    if decimal_tick_offset != 0:
        aligned_offset = round(decimal_tick_offset / tick_spacing) * tick_spacing
    else:
        aligned_offset = 0

    for i in range(n_positions):
        # Position 0 is closest to current price; position N-1 is farthest.
        # widths_ticks[i] gives this position's width; last position carries
        # the leftover (variable) width — sum of widths fully covers the range.
        w = widths_ticks[i]
        if positions_go_down:
            # Positions extend DOWN from tick_upper_aligned toward tick_lower_aligned
            pos_tick_upper = tick_upper_aligned - cumulative_ticks
            pos_tick_lower = pos_tick_upper - w
        else:
            # Positions extend UP from tick_lower_aligned toward tick_upper_aligned
            pos_tick_lower = tick_lower_aligned + cumulative_ticks
            pos_tick_upper = pos_tick_lower + w
        cumulative_ticks += w

        # Цены для позиции (инвертируем обратно если нужно, для отображения пользователю)
        # These are HUMAN-READABLE prices (no decimal adjustment)
        pos_price_lower = tick_to_price(pos_tick_lower, invert=invert_price)
        pos_price_upper = tick_to_price(pos_tick_upper, invert=invert_price)

        # При инверсии цены поменяются местами (меньший тик = большая USD цена)
        if invert_price:
            pos_price_lower, pos_price_upper = pos_price_upper, pos_price_lower

        # USD for this position uses width-aware weight (preserves bid-ask
        # density shape regardless of variable last-position width).
        percentage = adjusted_weights[i] / total_adjusted
        usd_amount = total_usd * percentage

        # Apply aligned decimal tick offset for pool-space ticks
        pool_tick_lower = pos_tick_lower + aligned_offset
        pool_tick_upper = pos_tick_upper + aligned_offset

        # For liquidity calculation, ALWAYS use raw pool-space prices (invert=False).
        # The V4 contract computes amounts from sqrtPrice = sqrt(1.0001^tick).
        # Using invert=True would give 1/raw_price, causing wrong liquidity for
        # pairs where stablecoin has the lower address (invert_price=True).
        # Human-readable prices are only for display.
        #
        # CRITICAL: Raw prices follow POOL order (currency0/currency1), but
        # calculate_liquidity_from_usd expects decimals in pool order.
        # When invert_price=True, pool currency0 = stablecoin, currency1 = volatile.
        # We must map config decimals to pool order using token1_is_stable
        # (NOT blindly swap — config token0/token1 order is user-chosen, not fixed).
        if invert_price:
            # Pool currency0 = stablecoin. Find which config token is the stablecoin.
            if token1_is_stable:
                # config.token1 = stablecoin = pool currency0
                liq_t0_dec = token1_decimals
                liq_t1_dec = token0_decimals
            else:
                # config.token0 = stablecoin (or neither) = pool currency0
                liq_t0_dec = token0_decimals
                liq_t1_dec = token1_decimals
            liq_t1_stable = False          # pool's "token1" = volatile, not stable
        else:
            liq_t0_dec = token0_decimals
            liq_t1_dec = token1_decimals
            liq_t1_stable = token1_is_stable

        if decimal_tick_offset != 0:
            # Pool-space raw prices for correct liquidity computation
            raw_price_lower = tick_to_price(pool_tick_lower, invert=False)
            raw_price_upper = tick_to_price(pool_tick_upper, invert=False)

            pool_current_tick = price_to_tick(current_price, invert=invert_price) + aligned_offset
            raw_current_price = tick_to_price(pool_current_tick, invert=False)
        else:
            # Same decimals (e.g. BNB chain 18/18) — still use raw prices from ticks
            raw_price_lower = tick_to_price(pos_tick_lower, invert=False)
            raw_price_upper = tick_to_price(pos_tick_upper, invert=False)
            raw_current_price = tick_to_price(tick_current, invert=False)

        liquidity = calculate_liquidity_from_usd(
            usd_amount=usd_amount,
            price_lower=raw_price_lower,
            price_upper=raw_price_upper,
            current_price=raw_current_price,
            token0_decimals=liq_t0_dec,
            token1_decimals=liq_t1_dec,
            token1_is_stable=liq_t1_stable
        )

        # Calculate token amounts (amount0, amount1) and determine side
        sqrt_c = float(decimal_sqrt(raw_current_price))
        sqrt_l = float(decimal_sqrt(raw_price_lower))
        sqrt_u = float(decimal_sqrt(raw_price_upper))
        amounts = calculate_amounts(sqrt_c, sqrt_l, sqrt_u, liquidity)

        # Determine side based on position relative to current price (human-readable)
        # In pool space the relationship is inverted when invert_price=True
        if sqrt_c >= sqrt_u:
            pos_side = "ask" if invert_price else "bid"
        elif sqrt_c <= sqrt_l:
            pos_side = "bid" if invert_price else "ask"
        else:
            pos_side = "bid"  # in-range defaults to bid (stablecoin side)

        positions.append(BidAskPosition(
            index=i,
            tick_lower=pool_tick_lower,    # Pool-space ticks (with decimal offset)
            tick_upper=pool_tick_upper,    # Pool-space ticks (with decimal offset)
            price_lower=pos_price_lower,  # Human-readable prices for display
            price_upper=pos_price_upper,  # Human-readable prices for display
            usd_amount=usd_amount,
            percentage=percentage * 100,
            liquidity=liquidity,
            amount0=amounts.amount0,
            amount1=amounts.amount1,
            side=pos_side,
        ))

    return positions


def calculate_two_sided_distribution(
    current_price: float,
    percent_from: float,
    percent_to: float,
    total_usd: float,
    n_positions: int,
    fee_tier: int = 2500,
    distribution_type: DistributionType = "linear",
    token0_decimals: int = 18,
    token1_decimals: int = 18,
    token1_is_stable: bool = True,
    allow_custom_fee: bool = False,
    tick_spacing: int = None,
    invert_price: bool = True,
    decimal_tick_offset: int = 0
) -> List[BidAskPosition]:
    """
    Расчёт bid-ask лесенки с правильным двусторонним распределением.

    Когда диапазон охватывает ОБОИ стороны от текущей цены (выше И ниже),
    создаются ДВЕ отдельные распределения:
    - НИЖЕ текущей цены: наименьшая ликвидность около текущей, наибольшая внизу
    - ВЫШЕ текущей цены: наименьшая ликвидность около текущей, наибольшая вверху

    Пример: current_price=1, range +50% to -50%, 10 positions, $100 total
    - 5 позиций НИЖЕ (0.5 - 1.0): smallest at 0.95-1.0, largest at 0.5-0.6
    - 5 позиций ВЫШЕ (1.0 - 1.5): smallest at 1.0-1.1, largest at 1.4-1.5

    Args:
        current_price: Текущая цена токена
        percent_from: Начало диапазона в % (например +50 = +50%)
        percent_to: Конец диапазона в % (например -50 = -50%)
        total_usd: Общая сумма в USD
        n_positions: Общее количество позиций
        ...

    Returns:
        Список BidAskPosition (сначала позиции ниже, потом выше)
    """
    # Определяем границы диапазона
    price_from = current_price * (1 + percent_from / 100)
    price_to = current_price * (1 + percent_to / 100)

    # Убеждаемся что upper > lower
    upper_price = max(price_from, price_to)
    lower_price = min(price_from, price_to)

    # Проверяем, охватывает ли диапазон обе стороны от текущей цены
    spans_above = upper_price > current_price
    spans_below = lower_price < current_price
    is_two_sided = spans_above and spans_below

    if not is_two_sided:
        # Односторонний диапазон - используем стандартную функцию
        return calculate_bid_ask_distribution(
            current_price=upper_price,
            lower_price=lower_price,
            total_usd=total_usd,
            n_positions=n_positions,
            fee_tier=fee_tier,
            distribution_type=distribution_type,
            token0_decimals=token0_decimals,
            token1_decimals=token1_decimals,
            token1_is_stable=token1_is_stable,
            allow_custom_fee=allow_custom_fee,
            tick_spacing=tick_spacing,
            invert_price=invert_price,
            decimal_tick_offset=decimal_tick_offset
        )

    # Двусторонний диапазон - разделяем позиции и ликвидность
    range_below = current_price - lower_price  # Размер диапазона ниже текущей
    range_above = upper_price - current_price  # Размер диапазона выше текущей
    total_range = range_below + range_above

    # Пропорционально распределяем позиции
    positions_below = max(1, round(n_positions * range_below / total_range))
    positions_above = max(1, n_positions - positions_below)

    # Корректируем если округление дало неправильную сумму
    if positions_below + positions_above != n_positions:
        if positions_below + positions_above < n_positions:
            positions_below += 1
        else:
            positions_below = max(1, positions_below - 1)

    # Пропорционально распределяем USD
    usd_below = total_usd * range_below / total_range
    usd_above = total_usd * range_above / total_range

    all_positions = []

    # 1. Позиции НИЖЕ текущей цены (current_price -> lower_price)
    # Распределение: наименьшая ликвидность около current_price, наибольшая около lower_price
    if positions_below > 0:
        positions_lower = calculate_bid_ask_distribution(
            current_price=current_price,  # Начинаем от текущей цены
            lower_price=lower_price,
            total_usd=usd_below,
            n_positions=positions_below,
            fee_tier=fee_tier,
            distribution_type=distribution_type,
            token0_decimals=token0_decimals,
            token1_decimals=token1_decimals,
            token1_is_stable=token1_is_stable,
            allow_custom_fee=allow_custom_fee,
            tick_spacing=tick_spacing,
            invert_price=invert_price,
            decimal_tick_offset=decimal_tick_offset
        )
        all_positions.extend(positions_lower)

    # 2. Позиции ВЫШЕ текущей цены (current_price -> upper_price)
    # Распределение: наименьшая ликвидность около current_price, наибольшая около upper_price
    # Передаём current_price как "current" и upper_price как "lower" -
    # функция определит по тикам, что позиции идут вверх (tick_current < tick_limit)
    if positions_above > 0:
        positions_upper = calculate_bid_ask_distribution(
            current_price=current_price,  # Текущая цена как начало
            lower_price=upper_price,      # Верхняя граница как "конец"
            total_usd=usd_above,
            n_positions=positions_above,
            fee_tier=fee_tier,
            distribution_type=distribution_type,
            token0_decimals=token0_decimals,
            token1_decimals=token1_decimals,
            token1_is_stable=token1_is_stable,
            allow_custom_fee=allow_custom_fee,
            tick_spacing=tick_spacing,
            invert_price=invert_price,
            decimal_tick_offset=decimal_tick_offset
        )

        # Переназначаем индексы для продолжения нумерации
        for i, pos in enumerate(positions_upper):
            pos.index = len(all_positions) + i

        all_positions.extend(positions_upper)

    # Переиндексируем все позиции
    for i, pos in enumerate(all_positions):
        pos.index = i

    return all_positions


def calculate_bid_ask_from_percent(
    current_price: float,
    percent_from: float,
    percent_to: float,
    total_usd: float,
    n_positions: int,
    fee_tier: int = 2500,
    distribution_type: DistributionType = "linear",
    token0_decimals: int = 18,
    token1_decimals: int = 18,
    token1_is_stable: bool = True,
    allow_custom_fee: bool = False,
    tick_spacing: int = None,
    invert_price: bool = True,
    decimal_tick_offset: int = 0
) -> List[BidAskPosition]:
    """
    Расчёт bid-ask лесенки через проценты от текущей цены.

    Args:
        current_price: Текущая цена токена
        percent_from: Начало диапазона в % от текущей цены (например -5 = -5%)
        percent_to: Конец диапазона в % от текущей цены (например -50 = -50%)
        total_usd: Общая сумма в USD
        n_positions: Количество позиций
        fee_tier: Fee tier пула
        distribution_type: Тип распределения весов
        token0_decimals: Decimals token0
        token1_decimals: Decimals token1
        token1_is_stable: True если token1 - стейблкоин

    Returns:
        Список BidAskPosition

    Example:
        >>> # Лесенка от -5% до -50% от текущей цены
        >>> positions = calculate_bid_ask_from_percent(
        ...     current_price=600.0,
        ...     percent_from=-5,    # начать с -5% = $570
        ...     percent_to=-50,     # до -50% = $300
        ...     total_usd=1000,
        ...     n_positions=7
        ... )
    """
    # Используем новую функцию для двустороннего распределения
    # Она автоматически определит, нужно ли разделять на две стороны
    return calculate_two_sided_distribution(
        current_price=current_price,
        percent_from=percent_from,
        percent_to=percent_to,
        total_usd=total_usd,
        n_positions=n_positions,
        fee_tier=fee_tier,
        distribution_type=distribution_type,
        token0_decimals=token0_decimals,
        token1_decimals=token1_decimals,
        token1_is_stable=token1_is_stable,
        allow_custom_fee=allow_custom_fee,
        tick_spacing=tick_spacing,
        invert_price=invert_price,
        decimal_tick_offset=decimal_tick_offset
    )


def print_distribution(positions: List[BidAskPosition], current_price: float = None) -> None:
    """
    Красивый вывод распределения.

    Args:
        positions: Список позиций
        current_price: Текущая цена (для расчёта % от текущей)
    """
    logger.info("\n" + "=" * 75)
    logger.info("BID-ASK LADDER DISTRIBUTION")
    logger.info("=" * 75)

    total_usd = sum(p.usd_amount for p in positions)

    # Заголовок таблицы
    if current_price:
        logger.info(f"\nCurrent price: ${current_price:,.2f}")
        logger.info(f"\n{'#':<3} {'Price Range':<25} {'% from current':<18} {'Amount':<15} {'Share'}")
        logger.info("-" * 75)
    else:
        logger.info(f"\n{'#':<3} {'Price Range':<25} {'Width %':<12} {'Amount':<15} {'Share'}")
        logger.info("-" * 75)

    for pos in positions:
        width_pct = (pos.price_upper - pos.price_lower) / pos.price_upper * 100
        bar_length = int(pos.percentage / 3)
        bar = "█" * bar_length

        if current_price:
            pct_upper = (pos.price_upper / current_price - 1) * 100
            pct_lower = (pos.price_lower / current_price - 1) * 100
            pct_str = f"{pct_lower:+.1f}% to {pct_upper:+.1f}%"
            logger.info(f"{pos.index + 1:<3} ${pos.price_lower:>8.2f} - ${pos.price_upper:<8.2f}  {pct_str:<18} ${pos.usd_amount:>8,.0f}       {pos.percentage:>5.1f}% {bar}")
        else:
            logger.info(f"{pos.index + 1:<3} ${pos.price_lower:>8.2f} - ${pos.price_upper:<8.2f}  {width_pct:>5.2f}%      ${pos.usd_amount:>8,.0f}       {pos.percentage:>5.1f}% {bar}")

    logger.info("-" * 75)
    logger.info(f"TOTAL: ${total_usd:,.2f} across {len(positions)} positions")

    if positions:
        coverage = f"${positions[-1].price_lower:.2f} - ${positions[0].price_upper:.2f}"
        if current_price:
            pct_coverage_lower = (positions[-1].price_lower / current_price - 1) * 100
            pct_coverage_upper = (positions[0].price_upper / current_price - 1) * 100
            logger.info(f"Coverage: {coverage} ({pct_coverage_lower:+.1f}% to {pct_coverage_upper:+.1f}%)")
        else:
            logger.info(f"Coverage: {coverage}")

    logger.info("=" * 75)


# Быстрый пример использования
if __name__ == "__main__":
    current = 600.0

    logger.info("\n>>> Example: BNB $600, range -5% to -40%, 7 positions")
    positions = calculate_bid_ask_from_percent(
        current_price=current,
        percent_from=-5,
        percent_to=-40,
        total_usd=1000,
        n_positions=7,
        fee_tier=2500,
        distribution_type="linear"
    )
    print_distribution(positions, current_price=current)
