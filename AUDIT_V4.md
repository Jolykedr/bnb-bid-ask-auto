# Desktop Audit v4 — 2026-02-28 (обновлено 2026-03-13)

Полный аудит всех Python-файлов в `src/`, `ui/`, `config.py`.
Найдено **36 новых багов** (не пересекаются с ранее известными C1, H1-H2, M1-M11, L1-L9).

> **Обновление 2026-03-13:** L24, L25, L28 исправлены (QMutex). Добавлен BalanceFetchWorker (async balance). NonceManager gap re-sync. V4 PM возвращает receipt.

---

## CRITICAL (1)

### C2: Hardcoded Ankr API key в config.py
**Файл:** `config.py:153`
**Статус:** unfixed

```python
rpc_url="https://rpc.ankr.com/base/"
```

API-ключ Ankr захардкожен в исходном коде. Любой с доступом к репозиторию может использовать/злоупотребить ключом. При достижении лимита — все BASE-операции перестанут работать.

**Фикс:** Вынести в `.env` или `QSettings`, загружать через `os.getenv("ANKR_API_KEY")`.

---

## HIGH (5)

### H3: `calculate_liquidity_from_usd` — конвертация volatile токена через raw цены (BASE chain)
**Файл:** `src/math/liquidity.py:314-316, 336-338`
**Статус:** unfixed

После фикса `invert_price` (audit v3), в `calculate_liquidity_from_usd` теперь приходят raw pool-space цены. Строки:

```python
avg_price = (price_lower + price_upper) / 2
amount0_in_tokens = usd_amount / avg_price
```

На BASE chain (USDC 6 dec, volatile 18 dec) raw цена содержит decimal offset (~1e11 вместо ~10). Результат: volatile-token amount в **10^10 раз меньше** чем нужно. Ликвидность позиций выше текущей цены будет ~0.

**Затрагивает:** Только BASE chain, только позиции ВЫШЕ текущей цены в двусторонних лестницах. BNB chain (18/18) не затронут.

**Фикс:** Передавать human-readable цену отдельным параметром или обратить decimal scaling перед конвертацией volatile token.

---

### H4: `self.worker` перезаписывается без cleanup — segfault
**Файл:** `ui/manage_tab.py:2474, 2585`
**Статус:** unfixed

```python
# Строка 2474
self.worker = BatchCloseWorker(...)

# Строка 2585
self.worker = ClosePositionsWorker(...)
```

Не проверяют, жив ли предыдущий worker. При двойном клике — старый QThread GC-уничтожается → `QThread::~QThread()` abort. Метод `_safe_cleanup_worker` есть (строка 1455), но **не вызывается** перед присвоением.

**Фикс:**
```python
if self.worker is not None:
    self._safe_cleanup_worker(self.worker)
    self.worker = None
```

---

### H5: `_swap_worker` перезаписывается без cleanup — segfault
**Файл:** `ui/manage_tab.py:2786`
**Статус:** unfixed

```python
self._swap_worker = SwapWorker(...)
```

Аналогично H4: без проверки предыдущего worker. Атрибут даже не инициализируется в `__init__`.

**Фикс:**
```python
if hasattr(self, '_swap_worker') and self._swap_worker is not None:
    self._safe_cleanup_worker(self._swap_worker)
    self._swap_worker = None
```

---

### H6: CreateTab — нет `_safe_cleanup_worker` / `_dying_workers`
**Файл:** `ui/create_tab.py:2065, 2170`
**Статус:** unfixed

CreateTab полностью отсутствует паттерн `_dying_workers` / `_safe_cleanup_worker`. При повторном создании ладера до завершения предыдущего — старый worker теряет Python-ссылку, GC собирает его → segfault.

**Фикс:** Добавить `_dying_workers = []` и `_safe_cleanup_worker()` в CreateTab по аналогии с ManageTab. Перед `self.worker = ...` вызывать cleanup.

---

### H7: KyberSwap минимальный slippage 3% — MEV-атака
**Файл:** `src/dex_swap.py:1231`
**Статус:** unfixed

```python
slippage_bips = max(int(slippage * 100), 300)  # minimum 3% for volatile tokens
```

Пол в 300 bips (3%) означает, что даже если пользователь задал 0.5% slippage, свап выполнится с 3%. MEV-боты гарантированно извлекут ~2.5% от суммы каждого свапа.

**Фикс:** Убрать `max(..., 300)`, использовать пользовательский slippage как есть. Или снизить до `max(..., 50)` (0.5%).

---

## MEDIUM (11)

### M12: `calculate_liquidity` возвращает `None` вместо `0`
**Файл:** `src/math/liquidity.py:173`
**Статус:** unfixed

```python
return liquidity0 or liquidity1
```

Когда `liquidity0=0`: `0 or None` → `None`. Далее `int(None)` → `TypeError` crash.

**Фикс:**
```python
if liquidity0 is not None:
    return liquidity0
return liquidity1
```

---

### M13: `close_positions` (V3) crash при всех позициях с liquidity=0
**Файл:** `src/liquidity_provider.py:988`
**Статус:** unfixed

Все позиции с `liquidity==0` пропускаются (строка 978) → `batcher.execute()` с пустым списком → `ValueError("No calls to execute")`. Пользователь видит "close failed" без объяснения.

**Фикс:** Проверить `if len(self.batcher) == 0: return (None, True, "No positions with liquidity")` перед `execute()`. Также добавить collect-only путь для zero-liquidity позиций (связано с H2).

---

### M14: V4 `close_positions` тоже пропускает zero-liquidity позиции
**Файл:** `src/v4_liquidity_provider.py:2039-2047`
**Статус:** unfixed

```python
if position.liquidity > 0:
    positions_to_close.append({...})
else:
    logger.warning(f"Position {token_id} has zero liquidity, skipping")
```

Аналог H2 для V4: позиции с нулевой ликвидностью пропускаются, некобранные fees теряются. V4 поддерживает `DECREASE_LIQUIDITY` с `liquidityDelta=0` для сбора fees.

**Фикс:** Для позиций с `liquidity==0` добавить `DECREASE_LIQUIDITY(liquidityDelta=0)` + `TAKE_PAIR` для сбора накопленных комиссий.

---

### M15: PCS V4 ABI отсутствуют `getPoolAndPositionInfo`, `getPositionLiquidity`
**Файл:** `src/contracts/v4/abis.py:331-432`
**Статус:** unfixed

В `PANCAKE_V4_POSITION_MANAGER_ABI` нет этих двух функций, которые есть на реальном контракте CLPositionManager. `get_position()` делает цепочку fallback-ов с `ABIFunctionNotFound` → лишние RPC-вызовы и потенциальные decode failures.

**Фикс:** Добавить в ABI:
- `getPoolAndPositionInfo(uint256) returns (PoolKey, PositionInfo)`
- `getPositionLiquidity(uint256) returns (uint128)`

---

### M16: Allowance verification — только лог, без проверки
**Файл:** `src/liquidity_provider.py:724-733`
**Статус:** unfixed

```python
current_allowance = token_contract.functions.allowance(...).call()
logger.info(f"Current allowance: {current_allowance}")
# ← Нет проверки current_allowance >= needed
```

После `check_and_approve_tokens` читается `allowance` и логируется, но **не проверяется** `allowance >= needed`. При RPC-лаге — mint revert, потеря газа.

**Фикс:** `if current_allowance < total_amount: raise Exception("Insufficient allowance after approval")`.

---

### M17: `_pending_private_key` никогда не обнуляется после свапа
**Файл:** `ui/manage_tab.py:2803-2852`
**Статус:** unfixed

Приватный ключ остаётся в `self._pending_private_key` на всё время жизни приложения. Нигде в `_on_swap_finished`, `_on_batch_close_finished`, `_on_close_finished` не обнуляется.

**Фикс:** Добавить `self._pending_private_key = None` в начале `_on_swap_finished` (после строки 2808) и в `_on_batch_close_finished` / `_on_close_finished`.

---

### M18: SwapWorker не обнуляет `self.private_key`
**Файл:** `ui/manage_tab.py:852, 860-928`
**Статус:** unfixed

Нет `finally: self.private_key = None`. Ключ живёт в памяти worker'а до GC. Сравни с `CreateLadderWorkerV4` (create_tab.py:543) где есть `finally: self.private_key = None`.

**Фикс:** Добавить `finally` блок в `run()`:
```python
finally:
    self.private_key = None
```

---

### M19: Proxy password + OKX ключи в plaintext QSettings
**Файл:** `ui/settings_dialog.py:452-457`
**Статус:** unfixed

```python
self.settings.setValue("proxy/password", self.proxy_pass_input.text())
self.settings.setValue("okx/api_key", self.okx_api_key_input.text())
self.settings.setValue("okx/secret_key", self.okx_secret_input.text())
self.settings.setValue("okx/passphrase", self.okx_passphrase_input.text())
```

Proxy-пароль, OKX API key/secret/passphrase хранятся в plaintext в Windows Registry (`HKEY_CURRENT_USER\Software\BNBLiquidityLadder\Settings`). Приложение уже имеет AES-256 шифрование для wallet ключей, но эти credentials его обходят.

**Фикс:** Шифровать через существующий AES-256-GCM механизм (crypto.py) или Windows DPAPI.

---

### M20: OKX session не закрывается в `sell_tokens_after_close`
**Файл:** `src/okx_dex.py` ~line 699
**Статус:** unfixed

HTTP-сессия OKX не закрывается в `sell_tokens_after_close`. Утечка TCP-соединений.

**Фикс:** Добавить `self.session.close()` в `finally` блок или использовать context manager.

---

### M21: DexSwap/Kyber session leak при exception
**Файл:** `src/dex_swap.py` ~lines 1709-1762
**Статус:** unfixed

При исключении в `swap()` сессии DexSwap и KyberSwapClient не закрываются через `close()`.

**Фикс:** Обернуть в `try/finally` с вызовом `self.close()`, или использовать context manager pattern.

---

### M22: `crypto.py` — расшифрованный ключ возвращается как immutable string
**Файл:** `src/crypto.py` ~line 238
**Статус:** unfixed

`decrypt_key()` возвращает `str` (immutable в Python). `_secure_zero` не может обнулить immutable string через ctypes. Приватный ключ остаётся в памяти Python до сборки мусора.

**Фикс:** Возвращать `bytearray` (mutable) вместо `str`, обнулять после использования.

---

## LOW (19)

### L10: `calculate_liquidity` crash при current == lower / upper
**Файл:** `src/math/liquidity.py:149-169`
**Статус:** unfixed

Когда `sqrt_price_current == sqrt_price_lower`, код входит в case 3 (in-range) и вызывает `calculate_liquidity_for_amount1(sqrt_price_lower, sqrt_price_current, ...)` где `lower == current` → `ValueError`. Uniswap V3 трактует `current == lower` как "below range".

**Практика:** Очень редко — требует точного совпадения float.

---

### L11: `price_to_tick` float off-by-one ~27% тиков
**Файл:** `src/math/ticks.py:83`
**Статус:** unfixed

```python
tick = math.floor(math.log(price) / math.log(1.0001))
```

Float-арифметика даёт ошибку в ~27% тиков (±1 tick = 0.01%). Использование `Decimal` с 50-знаковой точностью устранило бы все ошибки.

**Практика:** Поглощается tick spacing alignment в большинстве случаев.

---

### L12: `print_distribution` неверное покрытие для двусторонних распределений
**Файл:** `src/math/distribution.py:580-584`
**Статус:** unfixed

```python
coverage = f"${positions[-1].price_lower:.2f} - ${positions[0].price_upper:.2f}"
```

Для двусторонних распределений `positions[0]` не имеет наивысшую цену. Отображается перевёрнутый/неполный диапазон.

**Влияние:** Только логирование, не влияет на расчёты.

---

### L13: `math.sqrt` вместо `Decimal.sqrt` в `price_to_sqrt_price_x96`
**Файл:** `src/math/ticks.py:151`
**Статус:** unfixed

```python
sqrt_price = math.sqrt(price)
sqrt_price_x96 = int(sqrt_price * Q96)
```

Float `math.sqrt` (~15 значащих цифр) вместо `Decimal.sqrt` (50 цифр). Пренебрежимая ошибка для практических значений.

---

### L14: Нет валидации отрицательных amount в liquidity функциях
**Файл:** `src/math/liquidity.py` (функции `usd_to_wei`, `calculate_liquidity_for_amount0/1`)
**Статус:** unfixed

Нет проверки `amount >= 0`. `usd_to_wei(-100, 18)` вернёт `-10^20`. Все текущие вызовы валидируют upstream.

---

### L15: Batcher всегда legacy `gasPrice`, нет EIP-1559 auto-detect
**Файл:** `src/multicall/batcher.py:528-532`
**Статус:** unfixed

```python
if max_priority_fee:
    tx_params['maxPriorityFeePerGas'] = max_priority_fee
    tx_params['maxFeePerGas'] = self.w3.eth.gas_price * 2  # Неверная формула
else:
    tx_params['gasPrice'] = gas_price or self.w3.eth.gas_price
```

Нет auto-detect EIP-1559. Формула `maxFeePerGas` неверна: должно быть `baseFee * 2 + maxPriorityFee`. Сравни с `pool_factory._get_gas_params()` (строка 359) где правильная логика.

---

### L16: Approve/mint hardcoded legacy `gasPrice`
**Файл:** `src/contracts/position_manager.py:144, 341`; `src/liquidity_provider.py:479`
**Статус:** unfixed

Все approval и mint транзакции используют `'gasPrice': self.w3.eth.gas_price`. Suboptimal на EIP-1559 цепочках (Ethereum, Base).

---

### L17: `_parse_mint_events` проглатывает все ошибки парсинга
**Файл:** `src/contracts/position_manager.py:281-307`
**Статус:** unfixed

```python
except Exception:
    pass  # Все ошибки проглочены
```

Два `except Exception: pass` блока. При успешном минте — `token_id` может быть потерян и позиция станет неотслеживаемой.

---

### L18: Transfer event scan ограничен 100K блоков
**Файл:** `src/contracts/position_manager.py:542-543`
**Статус:** unfixed

```python
from_block = max(0, current_block - 100000)
```

~3.5 дня на BSC. Позиции старше этого срока не найдутся через fallback-путь (ERC721Enumerable обычно работает, но при его отсутствии — проблема).

---

### L19: Двусторонняя лестница молча отбрасывает позиции выше текущей цены
**Файл:** `src/liquidity_provider.py:677-703`
**Статус:** unfixed

При использовании `calculate_two_sided_distribution` позиции выше текущей цены молча фильтруются (требуют volatile token, но код работает только со stablecoin). Нет предупреждения пользователю.

---

### L20: `to_pancake_tuple` OverflowError при отрицательном tick_spacing
**Файл:** `src/contracts/v4/pool_manager.py:49-51`
**Статус:** unfixed

```python
params_int = ((1 << 256) + ts) << 16
params_int.to_bytes(32, 'big')  # OverflowError — 272 bits
```

Нужен mask: `& ((1 << 256) - 1)`. Практически tick_spacing всегда > 0.

---

### L21: `V4LadderConfig.base_token_amount` — мёртвый API
**Файл:** `src/v4_liquidity_provider.py:130`
**Статус:** unfixed

Поле `base_token_amount: float = None` объявлено в dataclass, но нигде не читается. В `create_ladder` (строка 1796) создаётся локальная переменная с тем же именем, полностью перекрывая config field. Пользователи, устанавливающие это поле, не получат ожидаемого эффекта.

---

### L22: `try_all_sources_with_web3` — 4 мёртвых параметра
**Файл:** `src/contracts/v4/subgraph.py:191-198`
**Статус:** unfixed

Параметры `w3`, `api_key`, `rpc_url`, `bscscan_api_key` объявлены но не используются в теле функции.

---

### L23: Ethereum (chain_id=1) использует `TOKENS_BNB`
**Файл:** `config.py:365`
**Статус:** unfixed

```python
1: TOKENS_BNB,   # Ethereum — используем BNB tokens пока нет TOKENS_ETH
```

Неправильные адреса токенов для Ethereum mainnet. Любая операция с токенами на Ethereum будет использовать BNB-адреса.

---

### L24: `_on_scan_position_found` пишет `positions_data` без QMutex
**Файл:** `ui/manage_tab.py:1674`
**Статус:** ✅ FIXED (2026-03-13)

`self.positions_data[token_id] = position` теперь обёрнут в `QMutexLocker`. Также добавлен mutex в `_save_positions`, `_persist_open_positions`, `_update_token_ids_input`, `_scan_wallet`, `_update_summary_bar`, `_update_buttons`, `_batch_close_all`, `_clear_all_positions`, `add_positions`, и передаётся `dict(self.positions_data)` snapshot в workers.

---

### L25: `_remove_selected` модифицирует `positions_data` без QMutex
**Файл:** `ui/manage_tab.py:2331-2333`
**Статус:** ✅ FIXED (2026-03-13)

Удаление из `positions_data` теперь обёрнуто в mutex. Snapshot передаётся для rebuild таблицы.

---

### L26: `_update_timer` не останавливается при закрытии окна
**Файл:** `ui/manage_tab.py:966-969`
**Статус:** unfixed

QTimer `_update_timer` (200ms, single-shot) не останавливается в `MainWindow._cleanup_workers()`. Может сработать на частично уничтоженном виджете.

---

### L27: `_on_scan_position_found` обходит batch-update
**Файл:** `ui/manage_tab.py:1678`
**Статус:** unfixed

Вызывает `_update_table_row()` напрямую вместо очереди `_pending_updates` + `_flush_table_updates`. При скане десятков позиций — O(n²) table operations + мерцание UI.

**Фикс:**
```python
self._pending_updates[token_id] = position
if not self._update_timer.isActive():
    self._update_timer.start()
```

---

### L28: `_batch_close_all` читает `positions_data` без QMutex
**Файл:** `ui/manage_tab.py:2390-2393`
**Статус:** ✅ FIXED (2026-03-13)

Теперь используется `QMutexLocker` + snapshot. Также `_batch_collect_fees` и `ClosePositionsWorker` получают `dict(self.positions_data)` вместо ссылки.

---

## Сводная таблица

| Severity | Новых (v4) | Ранее известных | Всего | Исправлено |
|----------|-----------|----------------|-------|-----------|
| **Critical** | 1 (C2) | 1 (C1) | **2** | 0 |
| **High** | 5 (H3-H7) | 2 (H1-H2) | **7** | 0 |
| **Medium** | 11 (M12-M22) | 11 (M1-M11) | **22** | 0 |
| **Low** | 19 (L10-L28) | 9 (L1-L9) | **28** | 3 (L24,L25,L28) |
| **Итого** | **36** | **23** | **59** | **3** |

## Приоритеты для фикса

### Немедленно (потеря средств / crash)
1. **C2** — убрать API ключ из кода
2. **H3** — volatile token conversion на BASE (позиции с ~0 ликвидностью)
3. **H4-H6** — segfault при двойном клике (worker cleanup)
4. **H7** — 3% slippage floor (MEV)
5. **M12** — `None` вместо `0` (crash)
6. **M13-M14** — close crash / fee loss для zero-liq позиций

### Важно (безопасность)
7. **M17-M18** — private key не обнуляется
8. **M19** — credentials в plaintext
9. **M22** — immutable string для приватного ключа

### Улучшения
10. **M15-M16** — ABI / allowance verification
11. **M20-M21** — session leaks
12. **L15-L16** — EIP-1559 gas
13. Остальные LOW — по возможности
