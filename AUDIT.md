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

### H3: `calculate_liquidity_from_usd` — raw pool-space цены на BASE
**Файл:** `src/math/liquidity.py:315, 337`
```python
avg_price = (price_lower + price_upper) / 2
amount0_in_tokens = usd_amount / avg_price
```
На BASE (USDC 6 dec / volatile 18 dec) raw цена ~10^12x от реальной → volatile-amount в 10^10 раз меньше. Позиции выше текущей цены будут с ~0 ликвидностью.
**Затрагивает:** только BASE chain, только позиции ВЫШЕ текущей цены.
НОРМ
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
