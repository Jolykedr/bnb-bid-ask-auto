# Desktop Audit — 2026-03-31

Консолидированный аудит всех Python-файлов в `src/`, `ui/`, `config.py`.
Объединяет Audit v3 (2026-02-28), Audit v4 (2026-03-13) и Audit v5 (2026-03-31).
Каждый баг перепроверен по текущему коду на 2026-03-31.

---

## Сводная таблица

| Severity | Исправлено | Не исправлено | Ложные |
|----------|-----------|---------------|--------|
| **Critical** | 0 | 2 | 1 |
| **High** | 4 | 3 | 0 |
| **Medium** | 8 | 13 | 4 |
| **Low** | 5 | 15 | 1 |
| **Итого** | **17** | **33** | **6** |

---

# ✅ ИСПРАВЛЕННЫЕ (14)

### H1 ✅ PCS V3 swap ABI mismatch (FIXED)
**Было:** `dex_swap.py` использовал Uniswap ABI (7-field struct + multicall с deadline). PCS SmartRouter ожидает 8-field struct + multicall без deadline.
**Сейчас:** 7-field `ExactInputSingleParams` (без deadline) + `multicall(deadline, [data])` — совпадает с PCS SmartRouter.

### H6 ✅ CreateTab — `_dying_workers` + `_safe_cleanup_worker` (FIXED 2026-03-31)
**Файл:** `ui/create_tab.py`, `ui/main_window.py`
**Было:** 6+ воркеров без safe cleanup, `_on_finished` использовал `w.finished.connect(w.deleteLater)` — Python ref терялся → GC segfault.
**Стало:** `_dying_workers` list + `_safe_cleanup_worker()` метод. `_on_finished` использует `_safe_cleanup_worker(w)`. `MainWindow.closeEvent` чистит все воркеры включая `_search_pool_worker`, `_ref_price_worker`, `_dying_workers`.

### H7 ✅ KyberSwap минимальный slippage 3% → 0.1% (FIXED 2026-03-31)
**Файл:** `src/dex_swap.py:1231`
**Было:** `slippage_bips = max(int(slippage * 100), 300)` — floor 3%, MEV-боты извлекали ~2.5%.
**Стало:** `slippage_bips = max(int(slippage * 100), 10)` — floor 0.1%.

### H8 ✅ Operator precedence в `_calc_received_from_math` (FIXED 2026-03-31)
**Файл:** `ui/manage_tab.py:3306`
**Было:** `usd = a0 + a1 * (1 / raw_price) if raw_price > 0 else a0` — удвоение a0.
**Стало:** `usd = (a0 + a1 * (1 / raw_price)) if raw_price > 0 else a0`

### M3 ✅ advanced_tab удалён (FIXED 2026-02-28)
### M4 ✅ BaseException handlers добавлены (FIXED 2026-02-28)
### M6 ✅ SwapPreviewDialog QThread segfault (FIXED)
### M9 ✅ V3 in-range filter tick=0 (FIXED)

### M15 ✅ PCS V4 ABI — `getPoolAndPositionInfo` и `getPositionLiquidity` добавлены
**Файл:** `src/contracts/v4/abis.py:267, 288`

### M21 ✅ DexSwap session — закрывается в `sell_tokens_for_stablecoin`
**Файл:** `src/dex_swap.py:1762`

### M25 ✅ 9 мест чтения `positions_data` без mutex (FIXED 2026-03-13 + 2026-03-31)
**Файл:** `ui/manage_tab.py` — все доступы к `positions_data` обёрнуты в `QMutexLocker`.

### M29 ✅ `save_open_positions_bulk` crash при string liquidity (FIXED 2026-03-31)
**Файл:** `src/storage/pnl_store.py:362`
**Стало:** `int(pos.get('liquidity', 0)) <= 0`

### L17 ✅ `_parse_mint_events` — logging вместо silent pass (FIXED 2026-03-31)
**Файл:** `src/contracts/position_manager.py:291, 306`
**Было:** `except Exception: pass` — ошибки парсинга полностью проглатывались.
**Стало:** `except Exception as e: logger.warning(...)` — fallback chain сохранён, ошибки логируются.

### L33 ✅ `calc_usd_from_liquidity` возвращает `0.0` вместо `None` (FIXED 2026-03-31)
**Файл:** `src/math/liquidity.py:367, 382, 398`
**Было:** `return None` при невалидных данных или $0 позициях — ломало `sum()`.
**Стало:** `return 0.0` — все 3 caller-а используют `if usd:` (truthiness), поведение идентично.

---

# ❌ НЕ ИСПРАВЛЕННЫЕ (33)

## CRITICAL (2)

### C1: PCS V4 PoolKey format — все PCS V4 операции сломаны
**Файл:** `src/contracts/v4/abis.py:425-444`, `src/v4_liquidity_provider.py:1840`

`PANCAKE_V4_POSITION_MANAGER_ABI` определяет `initializePool` с Uniswap-style 5-field PoolKey `(c0, c1, fee, tickSpacing, hooks)`. PCS V4 CLPositionManager ожидает 6-field `(c0, c1, hooks, poolManager, fee, parameters)`.

`to_pancake_tuple()` существует в `pool_manager.py:40-59`, но **не вызывается** — строка 1840 использует `pool_key.to_tuple()`.

**Результат:** Создание пулов PCS V4, минт позиций, чтение — всё revert.
НОРМ
### C2: Hardcoded Ankr API key в config.py
**Файл:** `config.py:153`
```python
rpc_url="https://rpc.ankr.com/base/167737...d40d"
```
API-ключ в исходном коде. Утечка кода → злоупотребление, лимит → BASE-операции сломаны.
НОРМ
---

## HIGH (3)

### ~~H3: `calculate_liquidity_from_usd` — raw pool-space цены на BASE~~ → FIXED 2026-05-03 (см. блок «Исправлено math parity»)
### H4: `self.worker` перезаписывается без cleanup — segfault
**Файл:** `ui/manage_tab.py:2895, 3146`
`BatchCloseWorker` / `ClosePositionsWorker` присваиваются без `_safe_cleanup_worker`. Двойной клик → старый QThread GC'd → `QThread::~QThread()` abort.
**Практический риск:** низкий — кнопки блокируются перед стартом воркера.

### H5: `_swap_worker` перезаписывается без cleanup — segfault
**Файл:** `ui/manage_tab.py:3545`
Аналог H4 для `_swap_worker`.
**Практический риск:** низкий — кнопки блокируются.

---

## MEDIUM (13)

### H2: V3 `close_positions` теряет fees при liquidity=0
**Файл:** `src/liquidity_provider.py:948-959`
```python
if liquidity > 0:
    self.batcher.add_close_position_calls(...)
```
Позиции с `liquidity == 0` пропускаются целиком — uncollected fees теряются.
НОРМ
### H10: PancakeV4Actions — непроверенные action codes (shipped TODO)
**Файл:** `src/contracts/v4/abis.py:553-574`
```python
# TODO: Verify PancakeSwap V4 action codes if needed
class PancakeV4Actions:
    MINT_POSITION = 0x02  # копия Uniswap V4
```
Класс **реально используется** (`v4/position_manager.py:18,82`). Если коды PCS V4 отличаются → все PCS V4 операции revert + потеря gas.
НОРМ
### M17: `_pending_private_key` не обнуляется после swap/close
**Файл:** `ui/manage_tab.py`
Нет `self._pending_private_key = None` в `_on_swap_finished`, `_on_batch_close_finished`, `_on_close_finished`. Ключ в памяти на всё время жизни приложения.
НОРМ
### M18: SwapWorker не обнуляет `self.private_key`
**Файл:** `ui/manage_tab.py:1163-1231`
Нет `finally: self.private_key = None`. Ключ живёт до GC.
НОРМ
### M10: GraphQL injection — pool_id без валидации
**Файл:** `src/contracts/v4/subgraph.py:52-53`
`pool_id` конкатенируется в GraphQL query без санитизации.

### M22: `decrypt_key` возвращает immutable string
**Файл:** `src/crypto.py:238`
`plaintext_bytes.decode('utf-8')` → immutable `str`. `_secure_zero` не может обнулить.
НОРМ
### M12: `calculate_liquidity` возвращает `None` вместо `0`
**Файл:** `src/math/liquidity.py:174`
```python
return liquidity0 or liquidity1  # 0 or None → None → TypeError crash
```
НОРМ
### M13: `close_positions` (V3) crash при всех позициях с liquidity=0
**Файл:** `src/liquidity_provider.py:963`
Пустой `batcher.execute()` → `ValueError("No calls to execute")`.
НОРМ
### M14: V4 `close_positions` пропускает zero-liquidity позиции (fee loss)
**Файл:** `src/v4_liquidity_provider.py:1945-1953`
Аналог H2 для V4.
НОРМ
### M19: Proxy password + OKX ключи в plaintext QSettings
**Файл:** `ui/settings_dialog.py:445-456`
OKX API key/secret/passphrase, proxy password хранятся в plaintext Windows Registry.
НОРМ
### M20: OKX session не закрывается в `sell_tokens_after_close`
**Файл:** `src/okx_dex.py:651-699`
`OKXDexSwap` создаётся, но `swapper.close()` не вызывается → TCP leak.
НОРМ
### M23: OKX `execute_swap` — post-swap quote для USD value
**Файл:** `src/okx_dex.py:576-579`
После confirmed свопа делает **новый** `get_quote` → цена сдвинулась → неточный PnL. При API failure → `to_amount_usd = 0`.
НОРМ
### M26: `_collect_worker` перезаписывается без cleanup
**Файл:** `ui/manage_tab.py:2982`
Аналог H4 для `_collect_worker`. Двойной клик → segfault.
**Практический риск:** низкий — кнопки блокируются.

### M27: `verify_password` — leak расшифрованного ключа
**Файл:** `src/crypto.py:255-270`
Функция только проверяет пароль, но расшифровывает ключ целиком в immutable `str` и дропает ссылку. Ключ в heap до GC.
НОРМ
### M31: V4 `close_position` не burn-ит NFT
**Файл:** `src/contracts/v4/position_manager.py:884-934`
`build_close_position_payload(burn=False)` по умолчанию. Пустые NFT копятся → скан замедляется.
НОРМ
---

## LOW (15)

### L20: `to_pancake_tuple` OverflowError при отрицательном tick_spacing
**Файл:** `src/contracts/v4/pool_manager.py:50`
`((1 << 256) + ts) << 16` → 272 bits → `OverflowError` в `to_bytes(32, 'big')`. Нужен mask `& ((1 << 256) - 1)`.
НОРМ
### L3: `sell_tokens_after_close` session leak (dex_swap.py)
**Файл:** `src/dex_swap.py` — `swapper.close()` не вызывается при exception (нет try/finally).
НОРМ
### L4: OKX session never closed
**Файл:** `src/okx_dex.py` — `sell_tokens_after_close` не вызывает `swapper.close()`.
НОРМ
### L5: OKX `sell_tokens` без NonceManager
**Файл:** `src/okx_dex.py` — nonce collision при multi-token sell.
НОРМ
### L7: V3 pool init по upper bound (by design)
**Файл:** `src/liquidity_provider.py:46-58`
`config.current_price` = верхняя граница, не реальная рыночная цена. Документировано, но naming misleading.
НОРМ
### L10: `calculate_liquidity` crash при current == lower/upper
**Файл:** `src/math/liquidity.py:170` — деление на ноль.
НОРМ
### L11: `price_to_tick` float off-by-one ~27% тиков
**Файл:** `src/math/ticks.py:83` — `math.floor(math.log(price) / math.log(1.0001))`.
НОРМ
### L13: `math.sqrt` вместо `Decimal.sqrt` в `price_to_sqrt_price_x96`
**Файл:** `src/math/ticks.py:151`
НОРМ
### L14: Нет валидации отрицательных amount в liquidity функциях
**Файл:** `src/math/liquidity.py`
НОРМ
### L15: Batcher — нет EIP-1559 auto-detect, `maxFeePerGas = gas_price*2`
**Файл:** `src/multicall/batcher.py:528-532`
НОРМ
### L16: Approve/mint — hardcoded legacy `gasPrice`
**Файл:** `src/contracts/position_manager.py:144, 341`
НОРМ
### L18: Transfer event scan ограничен 100K блоков (~3.5 дней BSC)
**Файл:** `src/contracts/position_manager.py:573`
НОРМ
### L19: Двусторонняя лестница молча отбрасывает позиции выше текущей цены
**Файл:** `src/liquidity_provider.py:663-686`
НОРМ
### L21: `V4LadderConfig.base_token_amount` — мёртвое поле
**Файл:** `src/v4_liquidity_provider.py:130` — нигде не читается.
Мёртвый код
### L22: `try_all_sources_with_web3` — 4 мёртвых параметра
**Файл:** `src/contracts/v4/subgraph.py:191-198`
Мёртвый код
### L23: Ethereum (chain_id=1) использует `TOKENS_BNB`
**Файл:** `config.py:365`
НОРМ
### L26: `_update_timer` не останавливается при закрытии окна
**Файл:** `ui/manage_tab.py` — QTimer может сработать на уничтоженном виджете.
НОРМ
### L27: `_on_scan_position_found` обходит batch-update
**Файл:** `ui/manage_tab.py:2066` — вызывает `_update_table_row` напрямую вместо `_pending_updates`.

### L29: `_parse_actual_output` — `data="0x"` → ValueError
**Файл:** `src/dex_swap.py:431` — `int("0x", 16)` crash.
НОРМ
### L30: `_parse_actual_output` берёт MAX transfer вместо SUM
**Файл:** `src/dex_swap.py:433-434` — при split-route свопах второй transfer игнорируется.

### L34: API fallback hardcodes `hooks=0x0`
**Файл:** `src/contracts/v4/position_manager.py:266` — для пулов с hooks → неверный pool_id → revert.
НОРМ
### L35: `pnl_store._get_conn()` DDL на каждый вызов
**Файл:** `src/storage/pnl_store.py` — `CREATE TABLE IF NOT EXISTS` × 4 + `PRAGMA journal_mode=WAL` на каждый read.
НОРМ
---

# 🆕 НОВЫЙ БАГ (найден через тест)

### N1: `check_approvals` — TypeError если BatchRPC вернёт не-tuple
**Файл:** `src/v4_liquidity_provider.py:1033`
**Тест:** `tests/test_v4_provider_extended.py::TestCheckApprovals::test_check_approvals_all_approved` — **FAILS**
**Ошибка:** `TypeError: 'int' object is not subscriptable`
```python
quote_permit2_data = p2_results[0] if p2_results[0] is not None else (0, 0, 0)
# Guard защищает только от None. Если декодер вернёт int → crash
quote_permit2_data[0]  # TypeError
```
**Фикс:**
```python
raw = p2_results[0]
quote_permit2_data = raw if isinstance(raw, (list, tuple)) and len(raw) >= 3 else (0, 0, 0)
```

---

# ✅ ИСПРАВЛЕНО (UX cleanup, 2026-05-03)

### Wallet scan UI скрыт (FIXED 2026-05-03)
**Файл:** `ui/manage_tab.py:1380-1395`
**Было:** Кнопка "🔍 Scan Wallet" + protocol selector выполняли trade-off с RPC лимитами (3-stage fallback: ERC721Enumerable → Etherscan/BSCScan API → Transfer event scan по 20k блокам). Transfer fallback на ETH давал ~67h окно — старые позиции пропадали.
**Стало:** `scan_btn.hide()` + `scan_protocol_combo.hide()`. Token_ids автоматически:
- Сохраняются в SQLite через `positions_created` signal из CreateTab → ManageTab.
- Persist между запусками через `open_positions` table.
- Дашборд PnL показывает все active ladders без необходимости scan.
**Worker и handler `_scan_wallet` оставлены как dead code** (легко вернуть, не удалены).
**Безопасность:** Не задеты другие пути — Load by ID и Refresh All работают как раньше. Регрессий 0.

### PCS V4 disabled в protocol dropdown для chain_id != 56 (FIXED 2026-05-03)
**Файл:** `ui/create_tab.py:1658-1697` (новые `_set_protocol_safe` + `_update_protocol_options`)
**Было:** Dropdown содержал 4 опции (PCS V3, PCS V4, Uni V3, Uni V4) для всех сетей. На ETH/Base выбор PCS V4 → `ValueError: No V4 addresses found for chain X and protocol pancakeswap` в `V4PoolManager.__init__` (ловилось try/except, но UX плохой).
**Стало:**
- `_update_protocol_options()` — disable item "PancakeSwap V4" через Qt model().item().setFlags() для chain_id != 56.
- Если selected = PCS V4 при переключении на ETH/Base → авто-switch на Uniswap V4.
- Вызывается из `_on_network_changed` и при инициализации (после default `setCurrentIndex(3)`).
- Helper `_set_protocol_safe(idx, fallback=3)` для двух мест в pool loading flow (line 3273, 3316), где код мог программно set PCS V4 после auto-detect — теперь redirects на Uniswap V4 если PCS V4 disabled.
**PCS V4 (Infinity)** реально существует только на BNB Chain (никогда не деплоился на ETH/Base).

# ✅ ИСПРАВЛЕНО (Ethereum chain support, 2026-05-03)

### Ethereum (chain_id=1) полностью функционален (FIXED 2026-05-03)
**Было:** desktop принимал ETH в network dropdown, но `state_view` для V4 не задан → V4 read падал; `TOKENS_ETH` не существовал → UI показывал WBNB/CAKE на Ethereum; `get_token('WETH', 1)` бросал "Tokens not configured for chain_id".
**Стало:**
- `src/contracts/v4/constants.py:43` — добавлен `state_view="0x7fFE42C4a5DEeA5b0feC41C94C136Cf115597227"` для Uniswap V4 на ETH (StateView для slot0 / fees reads).
- `config.py` — добавлен `TOKENS_ETH` dict (WETH 18, USDC 6, USDT 6, DAI 18, WBTC 8) с правильными checksummed адресами и decimals.
- `config.py:get_tokens_for_chain(1)` теперь возвращает `TOKENS_ETH` (раньше fallback на BNB).
- `config.py:get_token(symbol, 1)` — добавлена ветка для ETH.
- `ui/create_tab.py:_get_current_tokens/_rebuild_token_combos` — ETH-ветка с defaults `WETH/USDT` и token1 list `[USDT, USDC, DAI, WETH]`.
- 3 теста обновлены (config + 2× v4_constants), которые ассертили старое broken-поведение.
**Источник:** Port from web v9060f21 (commit "fix: Ethereum V4 support — StateView address, ETH tokens, Codex search").
**Регрессии нет:** 1284 passed (+1 новый ETH-тест), 4 N1 те же.

# ✅ ИСПРАВЛЕНО (math parity с web, 2026-05-03)

### H3 ✅ `calculate_liquidity_from_usd` — decimal_factor для mixed-decimal (FIXED 2026-05-03)
**Файл:** `src/math/liquidity.py:303-377`
**Было:** `usd / avg_price` без компенсации decimals → ASK позиции на BASE (USDC 6 / volatile 18) с ошибкой ~10^12×, недофинансированные.
**Стало:** `decimal_factor = 10^(t0_dec - t1_dec)`, `volatile_usd = current_price * decimal_factor`. Также:
- `avg_price` заменён на `current_price` (фикс F238 из web)
- In-range ветка переписана на прямой L из `usd_per_L_stable + usd_per_L_volatile` (раньше merge с below использовал stablecoin only — недосчёт L для mixed-decimal in-range)
- `calculate_amounts` boundary `<`/`>` → `<=`/`>=` (граничный случай sqrt_c == sqrt_l/u не падает с ValueError)
**Эффект:** 18/18 пары не затронуты (decimal_factor=1, no-op). Mixed-decimal (BASE/ETH USDC) — ASK и in-range теперь корректны.
**Источник:** Port from web `bnb-web` v59 + F238 + F239.

### `solve_distribution` + Method B (width-aware USD) (FIXED 2026-05-03)
**Файл:** `src/math/distribution.py:69-110, 269-294, 309-322, 333-336`
**Было:** `total_ticks / n_positions` floor-aligned по spacing. Последняя позиция могла быть в 2x+ шире (получала весь остаток).
**Стало:**
- `solve_distribution(a, b)` solver: первые (n-1) позиций ширины `x`, последняя `y`, ограничение `1 ≤ y ≤ 1.35·x`. Если решения нет → n уменьшается до тех пор, пока не найдётся.
- Method B: веса распределения умножаются на `(width / avg_width)` → density USD/tick сохраняет bid-ask shape независимо от вариативной ширины последней позиции. Когда все ширины равны → no-op (обратная совместимость).
- Layout через `cumulative_ticks` вместо `i * ticks_per_position`.
- `BidAskPosition` расширена полями `amount0/amount1/side` (default-значения — обратная совместимость с позиционными вызовами в тестах).
- 3 теста обновлены под новую семантику: `n_positions` = upper bound, может уменьшиться под 1.35× cap; первые (n-1) ширин равны, последняя в [1, 1.35×].
**Источник:** Port from web v59 (math rewrite, 450-config stress test, 787/787 webtests).
**Регрессии нет:** baseline 1283 passed → after-fix 1283 passed (4 N1 фейла те же, не связаны).

# ✅ ИСПРАВЛЕНО (audit v7, 2026-04-03)

### N2 ✅ `_auto_remove_closed_positions` удаляла неправильные строки таблицы (FIXED)
**Файл:** `ui/manage_tab.py:2376-2382`
**Было:** `removeRow(row)` вызывался с включённой сортировкой. `_row_index` содержал stale row numbers после Qt sort → удалялись чужие строки.
**Стало:** Новый helper `_remove_rows_by_token_ids()` — отключает сортировку, верифицирует token_id в ячейке перед удалением, fallback O(n) scan если индекс stale.

### N3 ✅ `_record_closed_trade` — та же проблема с удалением строк (FIXED)
**Файл:** `ui/manage_tab.py:3529-3534`
**Стало:** Использует тот же `_remove_rows_by_token_ids()`.

### N4 ✅ V3 pool load с нестандартным fee переключал на PancakeSwap V4 (FIXED)
**Файл:** `ui/create_tab.py:3320`
**Было:** `self.protocol_combo.setCurrentIndex(1)` — hardcoded index 1 = PancakeSwap V4. При fee=100 (0.01%, не в fee_map) → протокол переключался на PCS V4 вместо V3.
**Стало:** `self.protocol_combo.setCurrentIndex(v3_idx)` — корректный V3 индекс (0=PCS V3 или 2=Uni V3).

---

# 🚫 ЛОЖНЫЕ (6)

### L8 → FALSE: Missing `chainId` в transaction params
`build_transaction()` кеширует `eth_chainId` один раз через web3.py middleware. При батчинге через Multicall3 — одна TX = один RPC. Не проблема.

### L12 → FALSE: `print_distribution` coverage string — корректная логика
### M16 → FALSE: Allowance verification — `approve_token` проверяет `current_allowance >= amount`
### M24 → FALSE: OKX `value:0` для non-native — теоретический concern
### M28 → FALSE: `calc_usd_from_liquidity` dec_offset — токены всегда в pool order
### M30 → FALSE: `run_ui.py` setOrganizationName — `check_license_gui` не использует QSettings
### C3 → FALSE: V4 `amount_max` — Permit2 cap, контракт берёт только нужное

---

## Приоритеты для фикса

### Немедленно (потеря средств / crash)
1. **C1** — PCS V4 PoolKey format (все PCS V4 операции broken)
2. **C2** — убрать API ключ из кода
3. **H3** — volatile token conversion на BASE
4. **M12** — `None` вместо `0` (crash)
5. **H2, M13, M14** — close crash / fee loss для zero-liq позиций
6. **H10** — PCS V4 action codes (verify or disable)
7. **N1** — check_approvals TypeError

### Безопасность
8. **M17/M18** — private key не обнуляется
9. **M19** — credentials в plaintext
10. **M22** — immutable string для ключа
11. **M27** — verify_password leak

### Улучшения
12. **M20, M23** — session leak, post-swap quote
13. **M31** — NFT burn
14. **L15/L16** — EIP-1559 gas
15. Остальные LOW — по возможности
