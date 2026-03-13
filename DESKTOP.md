# Desktop — BNB Liquidity Ladder (PyQt6)

## Цель

Десктопное приложение для создания bid-ask лестниц ликвидности на Uniswap/PancakeSwap V3/V4.
Это **оригинальный** проект — веб-версия и бот портированы из него.

- Интерактивный калькулятор позиций с визуализацией
- Создание лестницы на блокчейне (V3/V4: pool init + batch mint в 1 TX через PositionManager.multicall)
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
| Тесты | pytest (1307+ тестов, ~80% покрытие src/) |
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
│   │       ├── position_manager.py    # V4 PositionManager (modifyLiquidities, initPool+mint)
│   │       └── subgraph.py            # Uniswap V4 GraphQL API
│   │
│   └── multicall/
│       └── batcher.py                 # PM.multicall (batch create+mint/close в 1 TX)
│
├── ui/                                # ИНТЕРФЕЙС (PyQt6)
│   ├── main_window.py                 # MainWindow (4 таба, меню, mutex, worker cleanup)
│   ├── dashboard_tab.py               # Tab 0: Dashboard (PnL stats, chart, active pairs)
│   ├── create_tab.py                  # Tab 1: Создание позиций (LoadPool, CreateLadder workers)
│   ├── manage_tab.py                  # Tab 2: Управление (Load, Scan, Close, Swap workers)
│   ├── calculator_tab.py              # Tab 3: Preview лестницы (без блокчейна)
│   ├── settings_dialog.py             # Настройки (RPC, gas, slippage, тема)
│   ├── password_dialog.py             # Мастер-пароль (шифрование ключа)
│   ├── swap_preview_dialog.py         # Preview свопов (KyberSwap → V3 → V2)
│   ├── widgets/
│   │   ├── position_table.py          # Таблица позиций (сортировка, USD, %)
│   │   └── price_chart.py             # Визуализация позиций (бары, текущая цена)
│   └── styles/
│       └── dark_theme.qss             # Dark theme
│
├── src/storage/                       # ХРАНИЛИЩЕ
│   ├── __init__.py
│   └── pnl_store.py                   # SQLite: trades + open_positions (~/.bnb_ladder/pnl.db)
│
├── tests/                             # ТЕСТЫ (1312 штук)
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
│   ├── Tab 0: DashboardTab      — Portfolio overview (PnL, chart, pairs)
│   ├── Tab 1: CreateTab         — Создание позиций (blockchain)
│   ├── Tab 2: ManageTab         — Управление позициями
│   └── Tab 3: CalculatorTab     — Preview (offline)
│
├── QMutex: _provider_mutex      — Защита provider при tab switch
├── Auto chain switch             — Dashboard pair click → переключение сети
└── Worker cleanup: closeEvent() → _cleanup_workers()
```

### Signal Flow

```
CreateTab.positions_created(ids) ──→ MainWindow ──→ ManageTab.add_positions()
ManageTab.trade_recorded()       ──→ MainWindow ──→ DashboardTab.refresh()
ManageTab.positions_updated()    ──→ MainWindow ──→ DashboardTab.update_positions_data()
DashboardTab.pair_clicked(ids, protocol, chain_id)
                                 ──→ MainWindow ──→ auto-switch chain + ManageTab.load_positions
SettingsDialog.accepted ──→ MainWindow ──→ all tabs.reload_settings()
QTabWidget.currentChanged ──→ MainWindow._on_tab_changed() [mutex-protected]
```

### Storage (SQLite — ~/.bnb_ladder/pnl.db)

```
trades             — Closed trade records (PnL, win/loss stats)
open_positions     — Active positions (persist across app restarts)
```

**Жизненный цикл позиции:**
1. Создана в CreateTab → token_ids → ManageTab.add_positions()
2. Загружена воркерами → positions_data[tid] с protocol, chain_id
3. Сохранена в SQLite (`save_open_positions_bulk`) после загрузки всех воркеров
4. Отображена в Dashboard (из SQLite при restart, из памяти в текущей сессии)
5. Закрыта → `remove_open_positions(token_ids)` + `save_trade(record)`
6. Удалена из Dashboard (автоматически при refresh или вручную кнопкой x)

### Worker Threads (QThread)

| Worker | Tab | Назначение |
|--------|-----|-----------|
| LoadPoolWorker | Create | Загрузка пула (BatchRPC: token0/1/fee/slot0) |
| CreateLadderWorker | Create | V3 batch mint (Multicall3) |
| CreateLadderWorkerV4 | Create | V4 ladder (modifyLiquidities) |
| LoadPositionWorker | Manage | Загрузка позиции по token_id |
| ScanWalletWorker | Manage | Сканирование всех позиций кошелька |
| ClosePositionWorker | Manage | Закрытие позиций (decrease + collect + burn) |
| BatchCloseWorker | Manage | Batch-close V4 позиций в 1 TX (modifyLiquidities) |
| SwapWorker | Manage | Своп через DexSwap (auto-sell после закрытия) |
| QuoteWorker | SwapDialog | Получение котировок для preview |

**ManageTab — ключевые паттерны:**
- `PriceProgressDelegate` — кастомный QStyledItemDelegate для Range Progress колонки (gradient bar + triangle marker)
- `QTimer` debounce (200ms) — `_flush_table_updates()` для пакетного обновления таблицы (при загрузке 50+ позиций)
- `_positions_mutex` (QMutex) — защита `positions_data` dict при конкурентных worker'ах
- `_row_index` dict — O(1) маппинг token_id → row для быстрого обновления
- PnL: `current_value + fees_earned - initial_investment`

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

**Сети и протоколы:**

| Сеть | Chain ID | V3 DEXes | V4 DEXes |
|------|----------|----------|----------|
| BNB Chain | 56 | PancakeSwap V3, Uniswap V3 | Uniswap V4, PancakeSwap V4 (Infinity) |
| Base | 8453 | Uniswap V3, PancakeSwap V3 | Uniswap V4 |
| Ethereum | 1 | Uniswap V3, PancakeSwap V3 | Uniswap V4 |
| BNB Testnet | 97 | PancakeSwap V3 | — |

> **Примечание:** `get_tokens_for_chain(1)` возвращает `TOKENS_BNB` — для Ethereum нет отдельного набора токенов.

**Два реестра стабильных токенов:**

- `STABLECOINS: Dict[str, int]` — адрес → decimals (для расчёта ликвидности, определения invert_price)
- `STABLE_TOKENS: Dict[str, str]` — адрес → символ (расширенный: включает WBNB, WETH — токены которые НЕ нужно продавать при auto-sell)

**STABLECOINS — реестр decimals:**
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
- `is_stablecoin(address)` — проверка адреса в STABLECOINS
- `is_stable_token(address)` — проверка в STABLE_TOKENS (включает WBNB/WETH)
- `get_stablecoin_decimals(address)` — decimals (default 18)
- `detect_v3_dex_by_pool(w3, pool_addr, chain_id)` — определение DEX по factory
- `get_chain_config(chain_id)` → ChainConfig
- `get_v3_dex_config(dex_name, chain_id)` → V3DexConfig

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
- Чтение пула: StateView (Uniswap V4) vs прямой PoolManager (PancakeSwap V4)

**V4 Contract Addresses** (из `src/contracts/v4/constants.py`):

| Сеть | Protocol | PoolManager | PositionManager | StateView |
|------|----------|-------------|-----------------|-----------|
| BNB (56) | Uniswap | `0x28e2ea09...` | `0x7a4a5c91...` | `0xd13dd3d6...` |
| BNB (56) | PancakeSwap | `0xa0FfB9c1...` | `0x55f4c8ab...` | — (Vault: `0x238a3588...`) |
| ETH (1) | Uniswap | `0x00000000...4444c5` | `0xbd216513...` | — |
| BASE (8453) | Uniswap | `0x498581ff...` | `0x7c5f5a4b...` | `0xa3c0c9b6...` |

**Permit2 Addresses:**
- Uniswap: `0x000000000022D473030F116dDEE9F6B43aC78BA3` (все сети)
- PancakeSwap: `0x31c2F6fcFf4F8759b3Bd5Bf0e1084A055615c768`

**V4 Action Codes** (action-based encoding в `modifyLiquidities`):
```
0x02 MINT_POSITION      — создание позиции
0x00 INCREASE_LIQUIDITY  — увеличение ликвидности
0x01 DECREASE_LIQUIDITY  — уменьшение ликвидности
0x03 BURN_POSITION       — удаление позиции
0x0d SETTLE_PAIR         — расчёт токен-пары
0x11 TAKE_PAIR           — вывод токен-пары
0x12 CLOSE_CURRENCY      — закрытие валюты
```

**V4 Position Info (packed bytes32):**
Позиция хранится как packed bytes32, тики извлекаются через 3 fallback layout:
1. Стандартный Uniswap: bits 8-32 = tickLower, 32-56 = tickUpper
2. Альтернативный: bits 24-48, 48-72
3. Top-down: bits 232-256, 208-232
Валидация: MIN_TICK ≤ tl < tu ≤ MAX_TICK, оба кратны tick_spacing.

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
   a. Detect pool (or batch createAndInitializePoolIfNecessary / initializePool)
   b. Approve tokens (ERC20 для V3, Permit2 для V4)
   c. Compute positions → MintParams[]
   d. V3: batch [createPool +] mint через PositionManager.multicall
      V4: [initializePool +] modifyLiquidities через PM.multicall
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
| storage/pnl_store.py | ~90% | test_dashboard.py |
| ui/dashboard_tab.py | ~70% | test_dashboard.py |
| UI layer (other) | 4-16% | Минимальные |
| okx_dex.py | 0% | — |
| **Итого** | **~80%** | **1307+ тестов** |

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

### 7. V4 Pool State — StateView vs PoolManager

Uniswap V4 читает пул через **StateView** контракт (отдельный адрес `state_view` в constants).
PancakeSwap V4 читает напрямую из **CLPoolManager** (нет StateView).
`V4PoolManager.get_pool_state()` автоматически выбирает метод по `self.protocol`.

### 8. V4 Position Info — 3 layout fallback

`getPoolAndPositionInfo(tokenId)` возвращает packed bytes32. Тики извлекаются через `_extract_ticks()`:
- Layout 1 (стандарт Uniswap): bits 8-32, 32-56
- Layout 2: bits 24-48, 48-72
- Layout 3 (top-down): bits 232-256, 208-232
- Валидация: MIN_TICK ≤ tl < tu ≤ MAX_TICK + alignment к tick_spacing
- Если все 3 layout провалились → запрос к Uniswap GraphQL API

### 9. EIP-1559 gas pricing

Все TX используют EIP-1559 с fallback на legacy:
```python
try:
    max_priority_fee = w3.eth.max_priority_fee
    base_fee = w3.eth.get_block('latest')['baseFeePerGas']
    # maxFeePerGas = baseFee * 2 + maxPriorityFee
except:
    # Fallback на legacy gasPrice
```
`GasEstimator` умножает estimate на `gas_multiplier` (из настроек, default 1.3).

### 10. Nonce pattern (tx_sent flag)

Все 26+ мест отправки TX используют флаг `tx_sent`:
```python
tx_sent = False
try:
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    tx_sent = True
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    nonce_manager.confirm_transaction(nonce)  # ВСЕГДА после receipt (даже revert)
except:
    if tx_sent:
        nonce_manager.confirm_transaction(nonce)  # TX ушла → nonce consumed
    else:
        nonce_manager.release_nonce(nonce)         # TX не ушла → переиспользовать
```

### 11. Worker lifecycle

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
| PM.multicall batch | Batch mint/close ✓ | Есть ✓ | Есть ✓ |
| Tests | 1307+ (80%) ✓ | Нет тестов ⚠️ | Нет тестов ⚠️ |

---

## Stablecoin Logic Map

Все точки зависимости от стейблкоинов:

| Файл | Использование |
|------|-------------|
| `config.py` | `STABLECOINS` dict, `STABLE_TOKENS` dict, `is_stablecoin()`, `is_stable_token()`, `get_stablecoin_decimals()` |
| `ui/create_tab.py` | `_should_invert_price()`, decimal offset, pool loading |
| `ui/manage_tab.py` | USD value calculation, position display |
| `ui/dashboard_tab.py` | USD value calculation for active pairs |
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
- [x] BaseException handlers в workers (emit signals, not swallow)
- [x] threading.excepthook для uncaught
- [x] Live settings reload
- [x] _load_pool_worker safe cleanup перед перезаписью
- [x] _on_finished deferred deleteLater (через finished signal)
- [x] advanced_tab wait() перед deleteLater (вкладка удалена)
- [x] _cleanup_workers покрывает все worker типы
- [x] _row_index очищается в _clear_list/_remove_selected
- [x] SwapPreviewDialog останавливает QuoteWorker при Cancel
- [x] Session close() для KyberSwap/OKX/DexSwap

### Тесты
- [x] 1312 тестов, все passing
- [x] Math coverage 99-100%
- [x] Contract coverage 100% (V3 PM, pool factory)
- [ ] UI layer coverage 4-16% — **НИЗКОЕ**
- [ ] okx_dex.py coverage 0% — **НЕ ТЕСТИРОВАНО**

### Исправлено в Audit v3 (2026-02-28)
- [x] **V4 liquidity invert_price** — `distribution.py` использовал human prices (invert=True) для `calculate_liquidity_from_usd` → неправильное определение направления позиции → недофинансированные V4 позиции на BNB (invert_price=True). Fix: всегда raw prices (invert=False) для liquidity calc.
- [x] **H1: V4 pool state query до создания** — `v4_liquidity_provider.py` запрашивал pool state даже при auto_create_pool=True (пул ещё не существует). Fix: `if not pool_created:`.
- [x] **H2: pool_factory decimal swap** — `pool_factory.py` `create_and_initialize_pool()` не менял decimals/price при реордеринге токенов в `create_pool()`. Fix: сравнение адресов, swap при необходимости.
- [x] **L9+Worker reload** — Lambda late-binding fix вызывал `deleteLater` → C++ объект удалён, Python ref остаётся. Второй load pool → RuntimeError. Fix: `_on_load_pool_worker_done()` + try/except RuntimeError.

### Известные нефиксированные баги

**CRITICAL:**
- [ ] **C1: PCS V4 PoolKey format** — `v4/abis.py` PANCAKE_V4_POSITION_MANAGER_ABI определяет initializePool/positions с Uniswap-style PoolKey (c0,c1,fee,tickSpacing,hooks). PCS V4 ожидает (c0,c1,hooks,poolManager,fee,parameters). Неверный function selector → revert. Также encode_mint_position кодирует PoolKey в Uniswap-формате для PCS V4 actions. Создание пулов PCS V4, минт позиций, чтение позиций — всё сломано.

**HIGH:**
- [ ] **H1: PCS V3 swap ABI mismatch** — `dex_swap.py` использует Uniswap ABI (7-field struct + multicall с deadline), PCS SmartRouter ожидает 8-field struct + multicall без deadline. V3 свапы на BSC ревертятся. Замаскировано auto-режимом (V3 = последний fallback). Исправлено в web версии (v22 #4), НЕ портировано в десктоп.
- [ ] **H2: V3 close_positions теряет fees при liquidity=0** — `liquidity_provider.py:978` пропускает позиции с liquidity==0, но у них могут быть uncollected fees (tokensOwed0/tokensOwed1 > 0).

**MEDIUM:**
- [ ] **M1: V4 close_position не burn-ит NFT** — `build_close_position_payload(burn=False)` по умолчанию. Orphan NFT + dust lock.
- [ ] **M2: Batcher EIP-1559 maxFeePerGas** — `batcher.py:530` использует `gas_price*2` вместо `baseFee*2+priorityFee`. Сейчас не триггерится.
- [x] **M3: advanced_tab** — вкладка удалена (функционал перенесён в Create tab).
- [x] **M4: BaseException handlers** — 8 workers без BaseException handler → UI freeze. **ИСПРАВЛЕНО 2026-02-28.**
- [ ] **M5: _pending_private_key не зерится** — manage_tab.py: приватный ключ остаётся в памяти после swap.
- [ ] **M6: SwapPreviewDialog QThread segfault** — deleteLater + ref drop при timeout QuoteWorker.wait().
- [ ] **M8: _secure_zero на immutable bytes** — `crypto.py` вызывает ctypes.memset на `bytes` (CPython хак, может сломаться в 3.12+)
- [ ] **M9: V3 in-range filter tick=0** — liquidity_provider.py:660 читает slot0 из неинициализированного пула → tick=0 → фильтрует все позиции.
- [ ] **M10: GraphQL injection** — `subgraph.py` интерполирует pool_id в запрос без валидации
- [ ] **M11: decrypt_key str не зерируется** — crypto.py:238 возвращает immutable str с приватным ключом.

**LOW:**
- [ ] **L1: calculate_liquidity returns None** — `liquidity0 or liquidity1` = None когда liquidity0=0
- [ ] **L2: negative tick_spacing OverflowError** — `to_pancake_tuple()` использует `1<<256` вместо `1<<24`
- [ ] **L3: sell_tokens_after_close session leak** — dex_swap.py: swapper.close() не вызовется при exception (нет try/finally)
- [ ] **L4: OKX session never closed** — okx_dex.py sell_tokens_after_close не вызывает swapper.close()
- [ ] **L5: OKX sell_tokens без NonceManager** — nonce collision при multi-token sell
- [ ] **L6: KyberSwap slippage floor 3%** — десктоп 3%, веб 0.5%
- [ ] **L7: V3 pool init по upper bound** — config.current_price = верхняя граница, не реальная цена
- [ ] **L8: Missing chainId** — transaction params без chainId (defense-in-depth)
- [x] **L9: create_tab lambda late binding** — _load_pool_worker.finished lambda → worker reload crash. **ИСПРАВЛЕНО 2026-02-28 (audit v3).**

---

## Wallet Scanning (обнаружение позиций)

**V3:**
1. `balanceOf(wallet)` → count, `tokenOfOwnerByIndex(wallet, i)` (ERC721Enumerable)
2. Fallback: Transfer event scan + `ownerOf()` проверка

**V4:**
1. ERC721Enumerable (если поддерживается)
2. Fallback: BSCScan/Basescan API `tokennfttx`
3. Fallback: Transfer event scan с chunked RPC (блоки по 1000-200)

---

## V3 vs V4 Comparison Table

| Аспект | V3 | V4 |
|--------|----|----|
| Пулы | Отдельные контракты + Factory | Singleton PoolManager |
| Минт | `PositionManager.mint()` | Action encoding → `modifyLiquidities()` |
| Апрувы | `ERC20.approve(PositionManager)` | `ERC20→Permit2→PositionManager` |
| Комиссии | Фиксированные: 100, 500, 2500, 3000, 10000 | Кастомные 0-100% (hundredths of bip) |
| Tick spacing | По таблице FEE→SPACING | `fee_percent × 200` |
| Батчинг | PM.multicall (create pool + mint) | PM.multicall (initPool + modifyLiq) |
| Чтение пула | `Pool.slot0()` | StateView (Uni) / PoolManager (PCS) |
| Pool ID | Нет (адрес пула) | `keccak256(PoolKey)` — разный формат для Uni и PCS |
| Позиции | Полный struct | Packed bytes32 (3 fallback layout) |
| Закрытие | decrease + collect + burn (3 calls) | DECREASE + BURN + TAKE_PAIR (packed) |
