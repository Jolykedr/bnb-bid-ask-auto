"""
Uniswap V3 Tick Mathematics

Основные формулы:
- price(i) = 1.0001^i
- sqrtPriceX96 = sqrt(price) * 2^96

Tick spacing по fee tier:
- 0.01% (100) -> spacing 1
- 0.05% (500) -> spacing 10
- 0.30% (3000) -> spacing 60
- 1.00% (10000) -> spacing 200
"""

import math
from decimal import Decimal, getcontext

# Высокая точность для расчётов
getcontext().prec = 50

# Константы
Q96 = 2 ** 96
MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

# Fee tier -> tick spacing
FEE_TO_TICK_SPACING = {
    100: 1,      # 0.01%
    500: 10,     # 0.05%
    2500: 50,    # 0.25% (PancakeSwap)
    3000: 60,    # 0.30% (Uniswap)
    10000: 200,  # 1.00%
}


def price_to_tick(price: float, invert: bool = False) -> int:
    """
    Конвертация цены в тик.

    price(i) = 1.0001^i
    i = log(price) / log(1.0001)

    Args:
        price: Цена token1/token0 (pool price)
               ИЛИ цена token0/token1 если invert=True
        invert: Если True, инвертирует цену (1/price) перед расчётом тика.

            КОГДА ИСПОЛЬЗОВАТЬ invert=True:
            - Когда пользователь вводит цену как "TOKEN price in USD" (например, 0.01 USD за токен)
            - Когда стейблкоин является token0 в пуле (адрес стейблкоина < адрес токена)

            КОГДА ИСПОЛЬЗОВАТЬ invert=False:
            - Когда цена уже в формате pool price (token1/token0)
            - Когда стейблкоин является token1 в пуле (адрес стейблкоина > адрес токена)

            ВАЖНО: С invert=True:
            - Более низкая USD цена (например, $0.001) → БОЛЕЕ ВЫСОКИЙ тик
            - Более высокая USD цена (например, $0.01) → БОЛЕЕ НИЗКИЙ тик
            Это противоположно интуиции, но корректно для pool math.

    Returns:
        Tick (целое число)

    Example:
        # Pool: USDT (token0) / TOKEN (token1)
        # TOKEN стоит $0.01 (0.01 USDT)
        # 1 USDT = 100 TOKEN, pool_price = 100

        # Способ 1: напрямую pool price
        tick = price_to_tick(100)  # ≈ 46054

        # Способ 2: с инверсией (удобнее для пользователя)
        tick = price_to_tick(0.01, invert=True)  # ≈ 46054
    """
    if price <= 0:
        raise ValueError("Price must be positive")

    if invert:
        price = 1.0 / price

    tick = math.floor(math.log(price) / math.log(1.0001))
    return max(MIN_TICK, min(MAX_TICK, tick))


def tick_to_price(tick: int, invert: bool = False) -> float:
    """
    Конвертация тика в цену.

    Args:
        tick: Номер тика
        invert: Если True, возвращает цену token0/token1 (например USD price токена)

    Returns:
        Цена token1/token0 (или token0/token1 если invert=True)

    Example:
        # Pool: USDT (token0) / TOKEN (token1)
        # tick = 46054 соответствует pool_price ≈ 100 TOKEN/USDT

        price = tick_to_price(46054)  # ≈ 100 (TOKEN per USDT)
        usd_price = tick_to_price(46054, invert=True)  # ≈ 0.01 (USDT per TOKEN = TOKEN price)
    """
    pool_price = 1.0001 ** tick
    if invert:
        return 1.0 / pool_price
    return pool_price


def align_tick_to_spacing(tick: int, tick_spacing: int, round_down: bool = True) -> int:
    """
    Выравнивание тика к tick_spacing.

    В Uniswap V3/V4 можно использовать только тики, кратные tick_spacing.

    Args:
        tick: Исходный тик
        tick_spacing: Шаг тиков (зависит от fee tier)
        round_down: True = округление вниз (к -∞), False = вверх (к +∞)

    Returns:
        Выровненный тик
    """
    if tick % tick_spacing == 0:
        return tick  # Already aligned

    if round_down:
        # Floor division works correctly for both positive and negative
        return (tick // tick_spacing) * tick_spacing
    else:
        # Round UP (towards +∞)
        return ((tick // tick_spacing) + 1) * tick_spacing


def price_to_sqrt_price_x96(price: float) -> int:
    """
    Конвертация цены в sqrtPriceX96.

    sqrtPriceX96 = sqrt(price) * 2^96

    Args:
        price: Цена token1/token0

    Returns:
        sqrtPriceX96 (целое число)
    """
    if price <= 0:
        raise ValueError("Price must be positive")

    sqrt_price = math.sqrt(price)
    sqrt_price_x96 = int(sqrt_price * Q96)

    return max(MIN_SQRT_RATIO, min(MAX_SQRT_RATIO, sqrt_price_x96))


def sqrt_price_x96_to_price(sqrt_price_x96: int) -> float:
    """
    Конвертация sqrtPriceX96 в цену.

    price = (sqrtPriceX96 / 2^96)^2

    Args:
        sqrt_price_x96: sqrtPriceX96

    Returns:
        Цена token1/token0
    """
    sqrt_price = sqrt_price_x96 / Q96
    return sqrt_price ** 2


def tick_to_sqrt_price_x96(tick: int) -> int:
    """
    Конвертация тика в sqrtPriceX96.

    Args:
        tick: Номер тика

    Returns:
        sqrtPriceX96
    """
    price = tick_to_price(tick)
    return price_to_sqrt_price_x96(price)


def get_tick_spacing(fee: int, allow_custom: bool = False) -> int:
    """
    Получение tick_spacing по fee tier.

    Args:
        fee: Fee в базисных пунктах * 100 (500 = 0.05%, 3000 = 0.3%)
             Для V4: fee в сотых долях bip (3000 = 0.3%, 33330 = 3.333%)
        allow_custom: Разрешить кастомные fee tiers (для V4).
                      Если False, неизвестный fee вызовет ValueError.

    Returns:
        tick_spacing

    Raises:
        ValueError: Если fee не найден и allow_custom=False
    """
    # Check standard V3 fee tiers first
    if fee in FEE_TO_TICK_SPACING:
        return FEE_TO_TICK_SPACING[fee]

    # Для стандартного V3 режима - выбрасываем ошибку
    if not allow_custom:
        valid_fees = sorted(FEE_TO_TICK_SPACING.keys())
        raise ValueError(
            f"Unknown fee tier: {fee}. Valid V3 fee tiers are: {valid_fees}. "
            f"Use allow_custom=True for V4 custom fees."
        )

    # V4 custom fee - calculate tick spacing using formula: fee_percent * 200
    # This matches the UI suggestion in _suggest_tick_spacing
    fee_percent = fee / 10000  # Convert to percent
    tick_spacing = round(fee_percent * 200)
    return max(1, tick_spacing)  # Minimum spacing is 1


def get_price_range_for_tick_range(tick_lower: int, tick_upper: int) -> tuple[float, float]:
    """
    Получение диапазона цен для диапазона тиков.

    Args:
        tick_lower: Нижний тик
        tick_upper: Верхний тик

    Returns:
        (price_lower, price_upper)
    """
    return tick_to_price(tick_lower), tick_to_price(tick_upper)
