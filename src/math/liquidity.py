"""
Uniswap V3 Liquidity Mathematics

Формулы из whitepaper:
- L = amount0 * (sqrt(upper) * sqrt(lower)) / (sqrt(upper) - sqrt(lower))
- L = amount1 / (sqrt(upper) - sqrt(lower))

Когда текущая цена в диапазоне:
- L = amount0 * (sqrt(upper) * sqrt(current)) / (sqrt(upper) - sqrt(current))
- L = amount1 / (sqrt(current) - sqrt(lower))
"""

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

    # Случай 1: текущая цена ниже диапазона
    if sqrt_price_current < sqrt_price_lower:
        amount0 = calculate_amount0_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)

    # Случай 2: текущая цена выше диапазона
    elif sqrt_price_current > sqrt_price_upper:
        amount1 = calculate_amount1_for_liquidity(sqrt_price_lower, sqrt_price_upper, liquidity)

    # Случай 3: текущая цена в диапазоне
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

    if token1_is_stable:
        # token1 = стейблкоин, token0 = volatile
        if position_below or (not position_above):
            # Позиция НИЖЕ или В ДИАПАЗОНЕ: используем stablecoin (token1)
            amount1 = usd_to_wei(usd_amount, token1_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount1=amount1
            )
        else:
            # Позиция ВЫШЕ: используем volatile (token0)
            # Конвертируем USD в token0: amount = usd / price
            avg_price = (price_lower + price_upper) / 2
            amount0_in_tokens = usd_amount / avg_price
            amount0 = usd_to_wei(amount0_in_tokens, token0_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount0=amount0
            )
    else:
        # token0 = стейблкоин, token1 = volatile
        if position_above or (not position_below):
            # Позиция ВЫШЕ или В ДИАПАЗОНЕ: используем stablecoin (token0)
            amount0 = usd_to_wei(usd_amount, token0_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount0=amount0
            )
        else:
            # Позиция НИЖЕ: используем volatile (token1)
            avg_price = (price_lower + price_upper) / 2
            amount1_in_tokens = usd_amount / avg_price
            amount1 = usd_to_wei(amount1_in_tokens, token1_decimals)
            return calculate_liquidity(
                sqrt_price_current=sqrt_price_current,
                sqrt_price_lower=sqrt_price_lower,
                sqrt_price_upper=sqrt_price_upper,
                amount1=amount1
            )
