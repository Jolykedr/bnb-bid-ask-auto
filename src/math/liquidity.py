"""
Uniswap V3 Liquidity Mathematics

Формулы из whitepaper:
- L = amount0 * (sqrt(upper) * sqrt(lower)) / (sqrt(upper) - sqrt(lower))
- L = amount1 / (sqrt(upper) - sqrt(lower))

Когда текущая цена в диапазоне:
- L = amount0 * (sqrt(upper) * sqrt(current)) / (sqrt(upper) - sqrt(current))
- L = amount1 / (sqrt(current) - sqrt(lower))
"""

import math
from decimal import Decimal, getcontext
from dataclasses import dataclass

# Высокая точность для финансовых расчётов
getcontext().prec = 50


def decimal_sqrt(value: float | int | Decimal) -> Decimal:
    """
    Высокоточный квадратный корень через Decimal.

    Args:
        value: Число для извлечения корня

    Returns:
        Decimal результат с высокой точностью
    """
    d = Decimal(str(value))
    return d.sqrt()


def usd_to_wei(usd_amount: float, decimals: int) -> int:
    """
    Точное преобразование USD суммы в wei с использованием Decimal.

    Избегает проблем с точностью float при больших суммах или малых значениях.

    Args:
        usd_amount: Сумма в USD (или другой единице)
        decimals: Количество десятичных знаков токена (обычно 18 для ERC20, 6 для USDC)

    Returns:
        Количество в wei (smallest unit)

    Example:
        >>> usd_to_wei(100.0, 18)  # 100 USDT with 18 decimals
        100000000000000000000
        >>> usd_to_wei(0.000001, 18)  # Very small amount
        1000000000000
    """
    # Convert to Decimal for precise arithmetic
    amount_decimal = Decimal(str(usd_amount))
    multiplier = Decimal(10) ** decimals
    result = amount_decimal * multiplier
    # Truncate to integer (floor towards zero)
    return int(result)


@dataclass
class LiquidityAmounts:
    """Результат расчёта количества токенов."""
    amount0: int  # В wei/smallest unit
    amount1: int  # В wei/smallest unit
    liquidity: int


def calculate_liquidity_for_amount0(
    sqrt_price_lower: float,
    sqrt_price_upper: float,
    amount0: int
) -> int:
    """
    Расчёт liquidity по количеству token0.

    L = amount0 * (sqrt_upper * sqrt_lower) / (sqrt_upper - sqrt_lower)

    Используется когда текущая цена ВЫШЕ диапазона (позиция полностью в token0).
    Uses Decimal for precision with large numbers.
    """
    if sqrt_price_upper <= sqrt_price_lower:
        raise ValueError("sqrt_price_upper must be > sqrt_price_lower")

    # Use Decimal for precise calculation with large amounts
    d_amount0 = Decimal(str(amount0))
    d_upper = Decimal(str(sqrt_price_upper))
    d_lower = Decimal(str(sqrt_price_lower))

    numerator = d_amount0 * d_upper * d_lower
    denominator = d_upper - d_lower

    return int(numerator / denominator)


def calculate_liquidity_for_amount1(
    sqrt_price_lower: float,
    sqrt_price_upper: float,
    amount1: int
) -> int:
    """
    Расчёт liquidity по количеству token1.

    L = amount1 / (sqrt_upper - sqrt_lower)

    Используется когда текущая цена НИЖЕ диапазона (позиция полностью в token1).
    Uses Decimal for precision with large numbers.
    """
    if sqrt_price_upper <= sqrt_price_lower:
        raise ValueError("sqrt_price_upper must be > sqrt_price_lower")

    # Use Decimal for precise calculation
    d_amount1 = Decimal(str(amount1))
    d_upper = Decimal(str(sqrt_price_upper))
    d_lower = Decimal(str(sqrt_price_lower))

    return int(d_amount1 / (d_upper - d_lower))


def calculate_liquidity(
    sqrt_price_current: float,
    sqrt_price_lower: float,
    sqrt_price_upper: float,
    amount0: int = None,
    amount1: int = None
) -> int:
    """
    Расчёт liquidity для заданного диапазона.

    Три случая:
    1. current < lower: позиция полностью в token0
    2. current > upper: позиция полностью в token1
    3. lower <= current <= upper: позиция в обоих токенах

    Args:
        sqrt_price_current: sqrt(текущая цена)
        sqrt_price_lower: sqrt(нижняя граница)
        sqrt_price_upper: sqrt(верхняя граница)
        amount0: Количество token0 (опционально)
        amount1: Количество token1 (опционально)

    Returns:
        Liquidity (L)
    """
    if amount0 is None and amount1 is None:
        raise ValueError("Either amount0 or amount1 must be provided")

    # Случай 1: текущая цена ниже диапазона -> нужен только token0
    if sqrt_price_current < sqrt_price_lower:
        if amount0 is None:
            raise ValueError("amount0 required when current price < lower bound")
        return calculate_liquidity_for_amount0(sqrt_price_lower, sqrt_price_upper, amount0)

    # Случай 2: текущая цена выше диапазона -> нужен только token1
    if sqrt_price_current > sqrt_price_upper:
        if amount1 is None:
            raise ValueError("amount1 required when current price > upper bound")
        return calculate_liquidity_for_amount1(sqrt_price_lower, sqrt_price_upper, amount1)

    # Случай 3: текущая цена в диапазоне -> нужны оба токена
    # Используем минимум из двух liquidity (лимитирующий фактор)
    liquidity0 = None
    liquidity1 = None

    if amount0 is not None:
        liquidity0 = calculate_liquidity_for_amount0(sqrt_price_current, sqrt_price_upper, amount0)

    if amount1 is not None:
        liquidity1 = calculate_liquidity_for_amount1(sqrt_price_lower, sqrt_price_current, amount1)

    if liquidity0 is not None and liquidity1 is not None:
        return min(liquidity0, liquidity1)
    return liquidity0 or liquidity1


def calculate_amount0_for_liquidity(
    sqrt_price_lower: float,
    sqrt_price_upper: float,
    liquidity: int
) -> int:
    """
    Расчёт количества token0 для заданной liquidity.

    amount0 = L * (sqrt_upper - sqrt_lower) / (sqrt_upper * sqrt_lower)
    Uses Decimal for precision with large numbers.
    """
    if sqrt_price_upper <= sqrt_price_lower:
        raise ValueError("sqrt_price_upper must be > sqrt_price_lower")

    # Use Decimal for precise calculation
    d_liquidity = Decimal(str(liquidity))
    d_upper = Decimal(str(sqrt_price_upper))
    d_lower = Decimal(str(sqrt_price_lower))

    numerator = d_liquidity * (d_upper - d_lower)
    denominator = d_upper * d_lower

    return int(numerator / denominator)


def calculate_amount1_for_liquidity(
    sqrt_price_lower: float,
    sqrt_price_upper: float,
    liquidity: int
) -> int:
    """
    Расчёт количества token1 для заданной liquidity.

    amount1 = L * (sqrt_upper - sqrt_lower)
    Uses Decimal for precision with large numbers.
    """
    if sqrt_price_upper <= sqrt_price_lower:
        raise ValueError("sqrt_price_upper must be > sqrt_price_lower")

    # Use Decimal for precise calculation
    d_liquidity = Decimal(str(liquidity))
    d_upper = Decimal(str(sqrt_price_upper))
    d_lower = Decimal(str(sqrt_price_lower))

    return int(d_liquidity * (d_upper - d_lower))


def calculate_amounts(
    sqrt_price_current: float,
    sqrt_price_lower: float,
    sqrt_price_upper: float,
    liquidity: int
) -> LiquidityAmounts:
    """
    Расчёт количества обоих токенов для заданной liquidity.

    Args:
        sqrt_price_current: sqrt(текущая цена)
        sqrt_price_lower: sqrt(нижняя граница)
        sqrt_price_upper: sqrt(верхняя граница)
        liquidity: Liquidity (L)

    Returns:
        LiquidityAmounts с amount0 и amount1
    """
    amount0 = 0
    amount1 = 0

    # Случай 1: текущая цена на или ниже диапазона → 100% token0
    # Включает граничный случай sqrt_current == sqrt_lower (иначе Case 3
    # передаст sqrt_upper==sqrt_lower в calculate_amount1 → ValueError)
    if sqrt_price_current <= sqrt_price_lower:
        amount0 = calculate_amount0_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)

    # Случай 2: текущая цена на или выше диапазона → 100% token1
    # Включает граничный случай sqrt_current == sqrt_upper
    elif sqrt_price_current >= sqrt_price_upper:
        amount1 = calculate_amount1_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)

    # Случай 3: текущая цена строго в диапазоне
    else:
        amount0 = calculate_amount0_for_liquidity(sqrt_price_current, sqrt_price_upper, liquidity)
        amount1 = calculate_amount1_for_liquidity(sqrt_price_lower, sqrt_price_current, liquidity)

    return LiquidityAmounts(amount0=amount0, amount1=amount1, liquidity=liquidity)


def calculate_liquidity_from_usd(
    usd_amount: float,
    price_lower: float,
    price_upper: float,
    current_price: float,
    token0_decimals: int = 18,
    token1_decimals: int = 18,
    token1_is_stable: bool = True
) -> int:
    """
    Расчёт liquidity из суммы в USD.

    Эта функция рассчитывает примерное значение liquidity для превью/отображения.
    Выбор токена зависит от положения позиции относительно current_price:
    - Позиция НИЖЕ current_price: используем stablecoin
    - Позиция ВЫШЕ current_price: используем volatile token (конвертируем USD по цене)

    Args:
        usd_amount: Сумма в USD для этой позиции
        price_lower: Нижняя граница цены
        price_upper: Верхняя граница цены
        current_price: Текущая цена
        token0_decimals: Decimals token0
        token1_decimals: Decimals token1
        token1_is_stable: True если token1 - стейблкоин (USDC, USDT)

    Returns:
        Liquidity (L)
    """
    # Используем Decimal для высокой точности
    sqrt_price_lower = float(decimal_sqrt(price_lower))
    sqrt_price_upper = float(decimal_sqrt(price_upper))
    sqrt_price_current = float(decimal_sqrt(current_price))

    # Определяем положение позиции относительно текущей цены
    # Позиция НИЖЕ: current_price >= price_upper (sqrt_price_current >= sqrt_price_upper)
    # Позиция ВЫШЕ: current_price <= price_lower (sqrt_price_current <= sqrt_price_lower)
    position_below = sqrt_price_current >= sqrt_price_upper
    position_above = sqrt_price_current <= sqrt_price_lower

    # Decimal factor for converting raw pool price to USD-based price.
    # raw_price = token1_wei / token0_wei = 1.0001^tick
    # For same-decimal pairs (18/18) the factor is 1 (no-op).
    decimal_factor = 10 ** (token0_decimals - token1_decimals)

    if token1_is_stable:
        # token1 = стейблкоин, token0 = volatile
        if position_below:
            # Позиция НИЖЕ: только stablecoin (token1)
            amount1 = usd_to_wei(usd_amount, token1_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount1=amount1
            )
        elif position_above:
            # Позиция ВЫШЕ: только volatile (token0)
            # volatile_usd = raw_price * 10^(t0_dec - t1_dec)
            # For 18/18: volatile_usd = raw_price (no change)
            # For WETH(18)/USDC(6): volatile_usd = raw * 10^12 (e.g. 10^-9 * 10^12 = $1000)
            volatile_usd = current_price * decimal_factor
            amount0_in_tokens = usd_amount / volatile_usd if volatile_usd > 0 else 0
            amount0 = usd_to_wei(amount0_in_tokens, token0_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount0=amount0
            )
        else:
            # В ДИАПАЗОНЕ: нужны оба токена, считаем L напрямую
            # L = total_usd / (usd_per_L_stable + usd_per_L_volatile)
            # amount1_wei = L * (sqrt_c - sqrt_l), USD = amount1_wei / 10^t1_dec
            # amount0_wei = L * (1/sqrt_c - 1/sqrt_u), USD = amount0_wei * P_raw / 10^t1_dec
            # (P_raw converts token0_wei to token1_wei, then /10^t1_dec gives USD)
            usd_per_L_stable = (sqrt_price_current - sqrt_price_lower) / (10 ** token1_decimals)
            usd_per_L_volatile = (1 / sqrt_price_current - 1 / sqrt_price_upper) * current_price / (10 ** token1_decimals)
            usd_per_L = usd_per_L_stable + usd_per_L_volatile
            return int(usd_amount / usd_per_L) if usd_per_L > 0 else 0
    else:
        # token0 = стейблкоин, token1 = volatile
        if position_above:
            # Позиция ВЫШЕ: только stablecoin (token0)
            amount0 = usd_to_wei(usd_amount, token0_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount0=amount0
            )
        elif position_below:
            # Позиция НИЖЕ: только volatile (token1)
            # volatile_tokens_per_usd = raw_price * 10^(t0_dec - t1_dec)
            # For 18/18: = raw_price (no change)
            # For USDC(6)/volatile(18): = raw * 10^-12 (e.g. 10^15 * 10^-12 = 1000 tokens/$)
            amount1_in_tokens = usd_amount * current_price * decimal_factor
            amount1 = usd_to_wei(amount1_in_tokens, token1_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount1=amount1
            )
        else:
            # В ДИАПАЗОНЕ: нужны оба токена, считаем L напрямую
            # raw pool price P = token1(volatile)/token0(stable) in wei
            # amount0_wei = L * (1/sqrt_c - 1/sqrt_u), USD = amount0_wei / 10^t0_dec
            # amount1_wei = L * (sqrt_c - sqrt_l), USD = amount1_wei / (10^t0_dec * P_raw)
            # (dividing by P_raw converts token1_wei to token0_wei, then /10^t0_dec gives USD)
            usd_per_L_stable = (1 / sqrt_price_current - 1 / sqrt_price_upper) / (10 ** token0_decimals)
            usd_per_L_volatile = (sqrt_price_current - sqrt_price_lower) / (10 ** token0_decimals * current_price) if current_price > 0 else 0
            usd_per_L = usd_per_L_stable + usd_per_L_volatile
            return int(usd_amount / usd_per_L) if usd_per_L > 0 else 0


def calc_usd_from_liquidity(
    tick_lower: int,
    tick_upper: int,
    liquidity: int,
    current_price: float,
    token0: str,
    token1: str,
    t0_dec: int,
    t1_dec: int,
    cur_tick: int | None = None,
) -> float | None:
    """Calculate USD value of a position from on-chain liquidity + ticks.

    When cur_tick is provided (from on-chain slot0), uses it directly to avoid
    the precision loss of price→tick float roundtrip.
    Returns None on calculation error.
    """
    from config import is_stablecoin

    if liquidity <= 0 or current_price <= 0:
        return 0.0

    t0_is_stable = is_stablecoin(token0)
    invert = t0_is_stable  # token0 is stablecoin → price is inverted

    if cur_tick is not None:
        sqrt_cur = 1.0001 ** (cur_tick / 2)
    else:
        dec_offset = t1_dec - t0_dec
        if invert:
            pool_price = 1.0 / current_price * (10 ** dec_offset)
        else:
            pool_price = current_price * (10 ** dec_offset)
        if pool_price <= 0:
            return 0.0
        derived_tick = math.log(pool_price) / math.log(1.0001)
        sqrt_cur = 1.0001 ** (derived_tick / 2)

    sqrt_lower = 1.0001 ** (tick_lower / 2)
    sqrt_upper = 1.0001 ** (tick_upper / 2)

    result = calculate_amounts(sqrt_cur, sqrt_lower, sqrt_upper, liquidity)
    amt0_human = result.amount0 / (10 ** t0_dec)
    amt1_human = result.amount1 / (10 ** t1_dec)

    if invert:
        usd_val = amt0_human + amt1_human * current_price
    else:
        usd_val = amt1_human + amt0_human * current_price

    return round(usd_val, 4) if usd_val > 0 else 0.0
