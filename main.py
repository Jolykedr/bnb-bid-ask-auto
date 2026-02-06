"""
Uniswap/PancakeSwap V3 Bid-Ask Liquidity Ladder

Стратегия "лесенка на покупку":
- Минимум ликвидности около текущей цены
- Максимум ликвидности на нижних уровнях
- Чем ниже падает цена - тем больше покупаем
"""

import os
import sys
from dotenv import load_dotenv

# === ПРОВЕРКА ЛИЦЕНЗИИ ===
from licensing import LicenseChecker, find_license_file, LicenseError

def check_license_on_startup():
    """Проверка лицензии при запуске."""
    license_path = find_license_file([
        "license.lic",
        os.path.join(os.path.dirname(__file__), "license.lic"),
    ])

    if not license_path:
        print("=" * 60)
        print("ФАЙЛ ЛИЦЕНЗИИ НЕ НАЙДЕН")
        print("=" * 60)
        print("Поместите файл license.lic в папку с программой.")
        print("Для получения лицензии свяжитесь с разработчиком.")
        print("=" * 60)
        sys.exit(1)

    try:
        checker = LicenseChecker()
        checker.verify_or_exit(license_path, show_info=True)
    except LicenseError as e:
        print(f"Ошибка лицензии: {e}")
        sys.exit(1)

from src.math.distribution import (
    calculate_bid_ask_distribution,
    calculate_bid_ask_from_percent,
    print_distribution,
    DistributionType
)
from src.liquidity_provider import LiquidityProvider, LiquidityLadderConfig
from config import BNB_CHAIN, TOKENS_BNB, FEE_TIERS

load_dotenv()


def interactive_calculator():
    """
    Интерактивный калькулятор bid-ask лесенки.
    Позволяет настроить все параметры.
    """
    print("\n" + "=" * 70)
    print("BID-ASK LADDER CALCULATOR")
    print("=" * 70)

    # Текущая цена
    while True:
        try:
            current_price = float(input("\nТекущая цена токена ($): "))
            if current_price > 0:
                break
            print("Цена должна быть > 0")
        except ValueError:
            print("Введите число")

    # Способ задания диапазона
    print("\nКак задать диапазон?")
    print("1. В процентах от текущей цены (рекомендуется)")
    print("2. В абсолютных ценах")

    range_choice = input("Выбор (1/2): ").strip()

    if range_choice == "1":
        # Проценты
        print(f"\nТекущая цена: ${current_price}")
        print("Введите диапазон в % (отрицательные = ниже текущей цены)")
        print("Пример: от -5% до -50% покроет диапазон от $570 до $300 при цене $600")

        while True:
            try:
                percent_from = float(input("\nНачало диапазона (%, например -5): "))
                percent_to = float(input("Конец диапазона (%, например -50): "))

                # Проверяем что это имеет смысл
                upper = current_price * (1 + max(percent_from, percent_to) / 100)
                lower = current_price * (1 + min(percent_from, percent_to) / 100)

                if lower > 0 and lower < upper:
                    print(f"\nДиапазон: ${lower:.2f} - ${upper:.2f}")
                    break
                print("Некорректный диапазон")
            except ValueError:
                print("Введите число")

        use_percent = True
    else:
        # Абсолютные цены
        while True:
            try:
                upper = float(input("\nВерхняя граница ($): "))
                lower = float(input("Нижняя граница ($): "))
                if lower > 0 and lower < upper <= current_price:
                    break
                print("Некорректный диапазон (должно быть: 0 < lower < upper <= current_price)")
            except ValueError:
                print("Введите число")

        use_percent = False

    # Количество позиций
    while True:
        try:
            n_positions = int(input("\nКоличество позиций (1-20): "))
            if 1 <= n_positions <= 20:
                break
            print("Выберите от 1 до 20")
        except ValueError:
            print("Введите целое число")

    # Сумма
    while True:
        try:
            total_usd = float(input("\nОбщая сумма ($): "))
            if total_usd > 0:
                break
            print("Сумма должна быть > 0")
        except ValueError:
            print("Введите число")

    # Тип распределения
    print("\nТип распределения ликвидности:")
    print("1. linear     - линейное (1,2,3,4...)")
    print("2. quadratic  - квадратичное (1,4,9,16...) - более агрессивное")
    print("3. exponential - экспоненциальное")
    print("4. fibonacci  - по Фибоначчи (1,1,2,3,5,8...)")

    dist_map = {"1": "linear", "2": "quadratic", "3": "exponential", "4": "fibonacci"}
    dist_choice = input("Выбор (1-4) [1]: ").strip() or "1"
    distribution_type = dist_map.get(dist_choice, "linear")

    # Fee tier
    print("\nFee tier пула:")
    print("1. 0.05% (500)  - стабильные пары")
    print("2. 0.25% (2500) - PancakeSwap стандарт")
    print("3. 0.30% (3000) - Uniswap стандарт")
    print("4. 1.00% (10000) - волатильные пары")

    fee_map = {"1": 500, "2": 2500, "3": 3000, "4": 10000}
    fee_choice = input("Выбор (1-4) [2]: ").strip() or "2"
    fee_tier = fee_map.get(fee_choice, 2500)

    # Расчёт
    print("\n" + "=" * 70)
    print("РЕЗУЛЬТАТ")
    print("=" * 70)

    if use_percent:
        positions = calculate_bid_ask_from_percent(
            current_price=current_price,
            percent_from=percent_from,
            percent_to=percent_to,
            total_usd=total_usd,
            n_positions=n_positions,
            fee_tier=fee_tier,
            distribution_type=distribution_type
        )
    else:
        positions = calculate_bid_ask_distribution(
            current_price=upper,  # Upper boundary of the range, NOT actual current price
            lower_price=lower,
            total_usd=total_usd,
            n_positions=n_positions,
            fee_tier=fee_tier,
            distribution_type=distribution_type
        )

    print_distribution(positions, current_price=current_price)

    # Повторить?
    again = input("\nПересчитать с другими параметрами? (y/n): ")
    if again.lower() == "y":
        interactive_calculator()


def quick_examples():
    """Быстрые примеры с разными настройками."""
    current = 600.0

    print("\n" + "=" * 70)
    print("QUICK EXAMPLES")
    print("=" * 70)

    examples = [
        {"name": "Conservative", "from": -5, "to": -25, "positions": 5, "dist": "linear"},
        {"name": "Moderate", "from": -10, "to": -40, "positions": 7, "dist": "linear"},
        {"name": "Aggressive", "from": -10, "to": -60, "positions": 10, "dist": "quadratic"},
        {"name": "Deep buyer", "from": -20, "to": -70, "positions": 8, "dist": "fibonacci"},
    ]

    for ex in examples:
        print(f"\n>>> {ex['name']}: {ex['from']}% to {ex['to']}%, {ex['positions']} positions, {ex['dist']}")
        positions = calculate_bid_ask_from_percent(
            current_price=current,
            percent_from=ex["from"],
            percent_to=ex["to"],
            total_usd=1000,
            n_positions=ex["positions"],
            fee_tier=2500,
            distribution_type=ex["dist"]
        )
        print_distribution(positions, current_price=current)
        input("\nНажми Enter для следующего примера...")


def create_ladder_interactive():
    """Интерактивное создание реальной лесенки."""
    print("\n" + "=" * 70)
    print("CREATE REAL LIQUIDITY LADDER")
    print("=" * 70)

    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        print("\nERROR: PRIVATE_KEY not found in .env file")
        print("Create .env file with: PRIVATE_KEY=0x...")
        return

    provider = LiquidityProvider(
        rpc_url=BNB_CHAIN.rpc_url,
        private_key=private_key,
        position_manager_address=BNB_CHAIN.position_manager
    )

    print(f"\nAccount: {provider.account.address}")

    # Баланс
    usdt_balance = provider.get_token_balance(TOKENS_BNB["USDT"].address)
    print(f"USDT Balance: {provider.format_amount(usdt_balance)}")

    # Интерактивный ввод параметров
    print("\n--- Настройка параметров ---")

    current_price = float(input("Текущая цена токена ($): "))
    percent_from = float(input("Начало диапазона (%): "))
    percent_to = float(input("Конец диапазона (%): "))
    n_positions = int(input("Количество позиций: "))
    total_usd = float(input("Общая сумма ($): "))

    # Расчёт цен
    upper_price = current_price * (1 + max(percent_from, percent_to) / 100)
    lower_price = current_price * (1 + min(percent_from, percent_to) / 100)

    config = LiquidityLadderConfig(
        current_price=upper_price,
        lower_price=lower_price,
        total_usd=total_usd,
        n_positions=n_positions,
        token0=TOKENS_BNB["WBNB"].address,
        token1=TOKENS_BNB["USDT"].address,
        fee_tier=2500,
        distribution_type="linear"
    )

    # Preview
    print("\n--- Preview ---")
    positions = provider.preview_ladder(config)
    print_distribution(positions, current_price=current_price)

    # Подтверждение
    confirm = input("\nСоздать эти позиции? (yes/no): ")
    if confirm.lower() != "yes":
        print("Отменено")
        return

    # Создание
    print("\nСоздание позиций...")
    result = provider.create_ladder(config)

    if result.success:
        print(f"\n SUCCESS!")
        print(f"TX: {result.tx_hash}")
        print(f"Gas used: {result.gas_used}")
        if result.token_ids:
            print(f"Token IDs: {result.token_ids}")
    else:
        print(f"\n FAILED: {result.error}")


def main():
    """Главная функция."""
    # Проверка лицензии при запуске
    check_license_on_startup()

    print("""
 ____  _     _        _         _      _       _     _
| __ )(_) __| |      / \   ___| | __ | |     / \   __| | __| | ___ _ __
|  _ \| |/ _` |____ / _ \ / __| |/ / | |    / _ \ / _` |/ _` |/ _ \ '__|
| |_) | | (_| |____/ ___ \\\\__ \   <  | |___/ ___ \ (_| | (_| |  __/ |
|____/|_|\__,_|   /_/   \_\___/_|\_\ |_____/_/   \_\__,_|\__,_|\___|_|

    Uniswap/PancakeSwap V3 Liquidity Ladder Tool
    """)

    print("Выбери действие:")
    print("1. Интерактивный калькулятор (рекомендуется)")
    print("2. Быстрые примеры")
    print("3. Создать реальную лесенку (требует PRIVATE_KEY)")
    print("4. Выход")

    choice = input("\nВыбор (1-4): ").strip()

    if choice == "1":
        interactive_calculator()
    elif choice == "2":
        quick_examples()
    elif choice == "3":
        create_ladder_interactive()
    elif choice == "4":
        print("Выход")
    else:
        print("Неверный выбор")


if __name__ == "__main__":
    main()
