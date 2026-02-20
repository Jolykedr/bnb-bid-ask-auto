# Desktop — BNB Liquidity Ladder (PyQt6)

## Цель

Десктопное приложение для создания bid-ask лестниц ликвидности на Uniswap/PancakeSwap V3/V4.
Это **оригинальный** проект — веб-версия и бот портированы из него.

- Интерактивный калькулятор позиций с визуализацией
- Создание лестницы на блокчейне (V3: batch mint через Multicall3, V4: modifyLiquidities)
- Управление позициями (загрузка, закрытие, сбор комиссий, свопы)
- Поддержка BNB Chain, Base, Ethereum
- Лицензирование (Ed25519)

---

## Стек технологий

| Компонент | Технология |
|-----------|-----------|
| UI | PyQt6 6.4+ |
| Блокчейн | Web3.py 6+ |
| Шифрование | AES-256-GCM + PBKDF2 (600k итераций) |
| Математика | Python Decimal (50-digit precision) |
| Тесты | pytest (1308 тестов, ~80% покрытие src/) |
| Пакеты | uv (pyproject.toml + uv.lock) |
| DEX агрегатор | KyberSwap, OKX DEX |

---

## Архитектура

```
bnb/
├── run_ui.py                          # GUI точка входа (лицензия, exception handlers)
├── main.py                            # CLI точка входа (интерактивный калькулятор)
├── config.py                          # Сети, DEX-ы, токены, STABLECOINS
│
├── src/                               # БИЗНЕС-ЛОГИКА
│   ├── liquidity_provider.py          # V3 Provider (LiquidityLadderConfig, create_ladder)
│   ├── v4_liquidity_provider.py       # V4 Provider (Permit2, action encoding)
│   ├── dex_swap.py                    # Multi-DEX swap (V2/V3 + KyberSwap fallback)
│   ├── kyberswap.py                   # KyberSwap HTTP client
│   ├── okx_dex.py                     # OKX DEX API (HMAC auth)
│   ├── utils.py                       # NonceManager, DecimalsCache, GasEstimator, BatchRPC
│   ├── crypto.py                      # AES-256-GCM шифрование ключей
│   │
│   ├── math/                          # МАТЕМАТИКА
│   │   ├── ticks.py                   # price↔tick, compute_decimal_tick_offset, align_tick
│   │   ├── liquidity.py               # usd_to_wei, calculate_liquidity_from_usd, amounts
│   │   └── distribution.py            # bid-ask distribution (linear/quad/exp/fib)
│   │
│   ├── contracts/                     # СМАРТ-КОНТРАКТЫ
│   │   ├── abis.py                    # ABI: ERC20, PositionManager, Pool, Multicall3
│   │   ├── position_manager.py        # V3 NFT (mint, burn, collect, approve)
│   │   ├── pool_factory.py            # V3 Factory (create pool, get pool, slot0)
│   │   └── v4/
│   │       ├── constants.py           # V4Protocol enum, адреса, fee conversion
│   │       ├── abis.py               # V4 ABI (StateView, Actions)
│   │       ├── pool_manager.py        # PoolKey, pool state, pool_id (Uni vs PCS)
│   │       ├── position_manager.py    # V4 PositionManager (modifyLiquidities)
│   │       └── subgraph.py            # Uniswap V4 GraphQL API
│   │
│   └── multicall/
│       └── batcher.py                 # Multicall3 (batch mint до 7 позиций в 1 TX)
│
├── ui/                                # ИНТЕРФЕЙС (PyQt6)
│   ├── main_window.py                 # MainWindow (4 таба, меню, mutex, worker cleanup)
│   ├── calculator_tab.py              # Tab 0: Preview лестницы (без блокчейна)
│   ├── create_tab.py                  # Tab 1: Создание позиций (LoadPool, CreateLadder workers)
│   ├── manage_tab.py                  # Tab 2: Управление (Load, Scan, Close, Swap workers)
│   ├── advanced_tab.py                # Tab 3: Custom tokens, pool creation V3/V4
│   ├── settings_dialog.py             # Настройки (RPC, gas, slippage, тема)
│   ├── password_dialog.py             # Мастер-пароль (шифрование ключа)
│   ├── swap_preview_dialog.py         # Preview свопов (KyberSwap → V3 → V2)
│   ├── widgets/
│   │   ├── position_table.py          # Таблица позиций (сортировка, USD, %)
│   │   └── price_chart.py             # Визуализация позиций (бары, текущая цена)
│   └── styles/
│       └── dark_theme.qss             # Dark theme
│
├── tests/                             # ТЕСТЫ (1308 штук)
│   ├── test_ticks.py                  # 711 строк, tick math
│   ├── test_liquidity.py              # 1135 строк, liquidity formulas
│   ├── test_distribution.py           # 1091 строк, distribution + offset
│   ├── test_crypto.py                 # 376 строк, AES + PBKDF2
│   ├── test_dex_swap.py               # DEX swap (99% coverage)
│   ├── test_v4_provider.py            # V4 provider (93% coverage)
│   ├── test_position_manager_v3.py    # V3 PM (100% coverage)
│   ├── test_pool_factory.py           # Pool factory (100% coverage)
│   ├── test_subgraph.py               # GraphQL (100% coverage)
│   ├── test_bugfixes_feb12.py         # Regression тесты
│   └── ... (ещё 8 файлов)
│
├── debug/                             # DEBUG-скрипты (11 штук)
│   ├── debug_pool.py                  # V4 pool ID анализ
│   ├── debug_create.py                # Отладка создания позиций
│   ├── check_v3_pool_pcs.py           # PCS V3 slot0 (8 полей)
│   └── ...
│
└── licensing/                         # ЛИЦЕНЗИРОВАНИЕ
    ├── license_checker.py             # Ed25519 проверка подписи
    └── license_generator.py           # Генерация .lic файлов
```

---

## UI архитектура

### MainWindow — Центральное окно

```
MainWindow(QMainWindow)
├── Menu: File, Edit, View, Help
├── StatusBar: Connection + Network labels
├── QTabWidget:
│   ├── Tab 0: CalculatorTab     — Preview (offline)
│   ├── Tab 1: CreateTab         — Создание позиций (blockchain)
│   ├── Tab 2: ManageTab         — Управление позициями
│   └── Tab 3: AdvancedTab       — Custom tokens, pool creation
│
├── QMutex: _provider_mutex      — Защита provider при tab switch
└── Worker cleanup: closeEvent() → _cleanup_workers()
```

### Signal Flow

```
AdvancedTab.tokens_updated(list) ──→ MainWindow ──→ CreateTab.update_tokens()
CreateTab.positions_created(ids) ──→ MainWindow ──→ ManageTab.add_positions()
SettingsDialog.accepted ──→ MainWindow ──→ all tabs.reload_settings()
QTabWidget.currentChanged ──→ MainWindow._on_tab_changed() [mutex-protected]
```

### Worker Threads (QThread)

| Worker | Tab | Назначение |
|--------|-----|-----------|
| LoadPoolWorker | Create | Загрузка пула (BatchRPC: token0/1/fee/slot0) |
| CreateLadderWorker | Create | V3 batch mint (Multicall3) |
| CreateLadderWorkerV4 | Create | V4 ladder (modifyLiquidities) |
| LoadPositionWorker | Manage | Загрузка позиции по token_id |
| ScanWalletWorker | Manage | Сканирование всех позиций кошелька |
| ClosePositionWorker | Manage | Закрытие позиции (decrease + collect + burn) |
| SwapWorker | Manage | Своп через DexSwap |
| QuoteWorker | SwapDialog | Получение котировок для preview |
| LoadTokenWorker | Advanced | Загрузка информации о токене |
| CreatePoolWorker | Advanced | Создание V3 пула |
| CreateV4PoolWorker | Advanced | Создание V4 пула |

**Паттерн lifecycle:**
```python
worker = SomeWorker(params)
worker.progress.connect(self._on_progress)
worker.result.connect(self._on_result)
worker.finished.connect(lambda: worker.deleteLater())
worker.start()
# closeEvent: worker.quit() → worker.wait(5000) → worker.deleteLater()
```

---

## Ключевые модули

### config.py — Центральная конфигурация

**Сети:**
- BNB Chain (56): PancakeSwap V3, Uniswap V3
- Base (8453): Uniswap V3/V4, PancakeSwap V3
- Ethereum (1): Uniswap V3, PancakeSwap V3

**STABLECOINS — единый реестр:**
```python
STABLECOINS = {
    # BNB: все 18 decimals
    "0x55d398...": 18,  # USDT
    "0x8ac76a...": 18,  # USDC
    "0xe9e7ce...": 18,  # BUSD
    # Base: USDC/USDbC = 6 decimals!
    "0x833589...": 6,   # USDC
    "0xd9aaec...": 6,   # USDbC
    # ETH: USDC/USDT = 6 decimals!
    "0xa0b869...": 6,   # USDC
    "0xdac17f...": 6,   # USDT
}
```

**Функции:**
- `is_stablecoin(address)` — проверка адреса
- `get_stablecoin_decimals(address)` — decimals (default 18)
- `detect_v3_dex_by_pool(w3, pool_addr, chain_id)` — определение DEX по factory

---

### src/math/ticks.py — Tick-математика

```python
price_to_tick(price, invert=False)
tick_to_price(tick, invert=False)
align_tick_to_spacing(tick, spacing, round_down=True)
compute_decimal_tick_offset(addr0, dec0, addr1, dec1) → int
get_tick_spacing(fee, allow_custom=False) → int
```

**Decimal tick offset:**
```
offset = int(round((dec1 - dec0) * log(10) / log(1.0001)))
```
| Пара | dec0 | dec1 | offset |
|------|------|------|--------|
| BNB/USDT (BSC) | 18 | 18 | 0 |
| USDC/TOKEN (BASE) | 6 | 18 | +276324 |

---

### src/math/distribution.py — Bid-ask распределение

```python
calculate_bid_ask_distribution(
    current_price, lower_price, total_usd, n_positions,
    fee_tier=500, distribution_type="linear",
    invert_price=False, decimal_tick_offset=0,
    token0_decimals=18, token1_decimals=18,
    token1_is_stable=True
) → List[BidAskPosition]
```

**Типы распределения:**
| Тип | Веса (5 позиций) | Описание |
|-----|---------|---------|
| linear | 1,2,3,4,5 | Линейно больше к нижней цене |
| quadratic | 1,4,9,16,25 | Квадратично больше к нижней цене |
| exponential | 1.0, 1.5, 2.25, 3.38, 5.06 | Экспоненциально больше |
| fibonacci | 1,1,2,3,5 | Фибоначчи |

**Выход:** `BidAskPosition(index, tick_lower, tick_upper, price_lower, price_upper, usd_amount, percentage, liquidity)`

---

### src/liquidity_provider.py — V3 Provider

```python
class LiquidityLadderConfig:
    current_price: float      # Верхняя граница (= текущая цена)
    lower_price: float        # Нижняя граница
    token0: str               # Volatile token
    token1: str               # Stablecoin
    fee_tier: int             # 100, 500, 2500, 3000, 10000
    n_positions: int          # 1-50
    distribution_type: str    # linear/quadratic/exponential/fibonacci
    total_usd: float
    token0_decimals: int
    token1_decimals: int

class LiquidityProvider:
    def preview_ladder(config) → List[BidAskPosition]
    def create_ladder(config) → LadderResult  # batch mint via Multicall3
```

### src/v4_liquidity_provider.py — V4 Provider

```python
class V4LadderConfig:
    # Как V3, но:
    fee_percent: float        # 0-100% (не fee_tier)
    protocol: V4Protocol      # UNISWAP / PANCAKESWAP
    actual_current_price: float  # Реальная цена пула
    invert_price: bool

class V4LiquidityProvider:
    def preview_ladder(config) → List[BidAskPosition]
    def create_ladder(config) → LadderResult
```

**Отличия V4 от V3:**
- Permit2 approval flow (не ERC20.approve)
- Action-based encoding (не multicall mint)
- Custom fee tiers (0-100%, не только стандартные)
- Разные pool_id формулы для Uniswap vs PancakeSwap

---

### src/utils.py — Утилиты

**NonceManager** — thread-safe nonce tracking:
```python
nonce = nonce_manager.get_next_nonce()  # Аллокация
nonce_manager.confirm_transaction(nonce) # После receipt
nonce_manager.release_nonce(nonce)       # При ошибке (TX не отправлена)
```
Интегрирован в 23+ точках (8 src-файлов).

**DecimalsCache** — кеш decimals:
```python
decimals = cache.get_decimals(token_addr)  # Кеш или on-chain read
# Raises RuntimeError если RPC не ответил (НЕ silent fallback на 18)
```

**GasEstimator** — оценка gas:
```python
gas = estimator.estimate(contract_fn, from_addr, value)
# Fallback defaults: approve=60k, mint=500k, swap=300k
```

**BatchRPC** — batch JSON-RPC через Multicall3:
```python
batch = BatchRPC(w3, multicall3_addr)
batch.add_balance_of(token, wallet)
batch.add_decimals(token)
batch.add_pool_slot0(pool)
results = batch.execute()  # 1 RPC call вместо N
```

---

### src/crypto.py — Шифрование ключей

```python
encrypt_key(private_key_hex, password) → base64_string
decrypt_key(encrypted_base64, password) → private_key_hex
_secure_zero(bytearray_data) → None  # ctypes.memset
```

- AES-256-GCM + PBKDF2-SHA256 (600k итераций)
- Формат: `VERSION(1B) + SALT(16B) + NONCE(12B) + CIPHERTEXT + TAG(16B)`
- Поддержка `cryptography` и `PyCryptodome` backends
- Migration из старого Base64 формата

---

## Как работает создание лестницы

```
1. Пользователь подключает кошелёк (пароль → decrypt private key)
2. Загружает пул (LoadPoolWorker → BatchRPC → fee, tokens, decimals, price)
3. Вводит параметры: range, positions, USD, distribution
4. Preview (calculate_bid_ask_distribution → таблица + chart)
5. Create:
   a. Detect/create pool
   b. Approve tokens (ERC20 для V3, Permit2 для V4)
   c. Compute positions → MintParams[]
   d. V3: batch mint через Multicall3 (до 7 за TX)
      V4: modifyLiquidities (action-based encoding)
   e. Parse receipt → token_ids
6. Результат → ManageTab (positions_created signal)
```

---

## Тесты

```bash
# Запуск всех тестов
cd bnb
pytest tests/ -v

# С покрытием
pytest tests/ --cov=src --cov-report=term-missing
```

| Модуль | Покрытие | Тесты |
|--------|---------|-------|
| math/ticks.py | 99% | test_ticks.py (711 строк) |
| math/liquidity.py | 99-100% | test_liquidity.py (1135 строк) |
| math/distribution.py | 99-100% | test_distribution.py (1091 строк) |
| dex_swap.py | 99% | test_dex_swap.py |
| v4_liquidity_provider.py | 93% | test_v4_provider.py + extended |
| position_manager.py (V3) | 100% | test_position_manager_v3.py |
| pool_factory.py | 100% | test_pool_factory.py |
| subgraph.py | 100% | test_subgraph.py |
| crypto.py | ~95% | test_crypto.py (376 строк) |
| UI layer | 4-16% | Минимальные |
| okx_dex.py | 0% | — |
| **Итого** | **~80%** | **1308 тестов** |

---

## Как запустить

### GUI
```bash
cd bnb
uv sync                    # Установить зависимости
cp .env.example .env       # Создать .env
# Заполнить PRIVATE_KEY в .env (или ввести через UI)
uv run python run_ui.py
```

### CLI
```bash
uv run python main.py      # Интерактивный калькулятор
```

### Тесты
```bash
uv run pytest tests/ -v
```

---

## Known Pitfalls (для аудита)

### 1. Это ОРИГИНАЛ — веб и бот копируют отсюда

`bnb_src/` в web backend — это shim, который импортирует из `src/` этого проекта.
`bnb_snapshot/` в bot — это snapshot (копия) `src/`.
**Любой фикс здесь должен быть скопирован в web/bot**, и наоборот.

### 2. PancakeSwap V3 slot0 ABI

PCS V3 возвращает 8 полей (feeProtocol: uint32), Uniswap V3 — 7 (uint8).
Решение: raw `eth_call` с selector `0x3850c7bd` в:
- `src/contracts/pool_factory.py`
- `ui/create_tab.py` (2 места)
- `src/liquidity_provider.py`

### 3. V4 PoolKey различия

| Uniswap V4 | PancakeSwap V4 |
|------------|----------------|
| `(c0, c1, fee, tickSpacing, hooks)` | `(c0, c1, hooks, poolMgr, fee, params)` |

`_compute_pool_id()` в `pool_manager.py` обрабатывает оба. Неверный формат = неверный пул.

### 4. Single-token mint constraint

Позиция ниже тика = нужен только token1 (stablecoin).
Позиция выше тика = нужен только token0 (volatile).
Позиция **в диапазоне** тика = нужны ОБА токена.
При стратегии "только стейблкоин" — in-range позиции пропускаются.

### 5. amount0_max/amount1_max swap на BASE

На BASE (USDC=currency0) `above_tick` нужен token0 (=stablecoin), не volatile.
Код ранее предполагал above_tick=volatile — правильно на BNB, неверно на BASE.
**Исправлено:** assign amounts по protocol token index, не по stablecoin/volatile.

### 6. DecimalsCache raises RuntimeError

В отличие от бота (который логирует warning), desktop **бросает RuntimeError** при неудачном чтении decimals.
Это **правильное поведение** — silent fallback на 18 приводит к 10^12x ошибке для USDC 6 dec.

### 7. Worker lifecycle

- `deleteLater()` на finished
- `worker.wait(5000)` перед delete в closeEvent
- `BaseException` handlers в workers (не только Exception)
- `threading.excepthook` для неперехваченных
- `_cancel_load_workers()` disconnects signals перед удалением

---

## Расхождения с Web/Bot версиями

| Аспект | Desktop | Web Backend | Bot |
|--------|---------|------------|-----|
| Decimal error | RuntimeError ✓ | RuntimeError ✓ | Warning only ⚠️ |
| Gas price cap | Через GasEstimator | `_check_gas_price()` ✓ | Нет ⚠️ |
| Token normalize | LiquidityLadderConfig | `_normalize_token_order()` | Нет ⚠️ |
| KyberSwap thread | Однопоточный UI | Shared session ⚠️ | Thread-local ✓ |
| NonceManager | Полноценный ✓ | Нет (pending nonce) | Per-wallet lock ✓ |
| Multicall3 batch | До 7 mint/TX ✓ | Есть ✓ | Есть ✓ |
| Tests | 1308 (80%) ✓ | Нет тестов ⚠️ | Нет тестов ⚠️ |

---

## Stablecoin Logic Map

Все точки зависимости от стейблкоинов:

| Файл | Использование |
|------|-------------|
| `config.py` | `STABLECOINS` dict, `is_stablecoin()`, `get_stablecoin_decimals()` |
| `ui/create_tab.py` | `_should_invert_price()`, decimal offset, pool loading |
| `ui/manage_tab.py` | USD value calculation, position display |
| `ui/advanced_tab.py` | Swap token detection |
| `src/liquidity_provider.py` | Quote token detection, distribution params |
| `src/v4_liquidity_provider.py` | Quote token, invert_price, amount assignment |
| `src/dex_swap.py` | Sell target detection |
| `src/okx_dex.py` | Skip swap if already stablecoin |
| `src/math/distribution.py` | `token1_is_stable` → `usd_to_wei()` decimals |
| `src/math/liquidity.py` | `usd_to_wei()` — decimals определяют масштаб |

---

## Audit Checklist

### Корректность расчётов
- [x] `decimal_tick_offset` применяется везде (11 файлов)
- [x] `token1_is_stable` динамический (не hardcoded)
- [x] On-chain decimal read raises RuntimeError
- [x] `amount0_max/amount1_max` правильно для pool order (исправлено)
- [x] `invert_price` из pool address order
- [x] Stablecoin registry централизован (config.py)

### Безопасность
- [x] AES-256-GCM + PBKDF2 (600k итераций) для ключей
- [x] Secure zeroing (ctypes.memset)
- [x] Нет bare `except:` — все `except Exception:`
- [x] NonceManager предотвращает nonce конфликты
- [x] Gas estimation с fallback defaults
- [x] Лицензирование (Ed25519)

### UI
- [x] QMutex защищает provider sync
- [x] Worker cleanup в closeEvent
- [x] BaseException handlers в workers
- [x] threading.excepthook для uncaught
- [x] Live settings reload

### Тесты
- [x] 1308 тестов, все passing
- [x] Math coverage 99-100%
- [x] Contract coverage 100% (V3 PM, pool factory)
- [ ] UI layer coverage 4-16% — **НИЗКОЕ**
- [ ] okx_dex.py coverage 0% — **НЕ ТЕСТИРОВАНО**
