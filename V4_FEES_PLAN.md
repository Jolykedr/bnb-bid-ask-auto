# Plan: Port V4 Fee Calculation from Web to Desktop

## Problem

V4 positions show `0.000000 / 0.0000` fees because V4 doesn't store `tokensOwed` in the position struct (unlike V3). The web version computes fees off-chain from fee growth accumulators — desktop needs the same.

## What Already Exists in Desktop

| Component | Status |
|-----------|--------|
| StateView ABI (`getFeeGrowthGlobals`, `getTickInfo`, `getPositionInfo`) | DONE — `src/contracts/v4/abis.py` lines 37-81 |
| PoolManager ABI (`getFeeGrowthGlobals`, `getPoolTickInfo`, `getPositionInfo`) | DONE — `src/contracts/v4/abis.py` lines 174-218 |
| StateView address (Uniswap V4 BSC) | DONE — `src/contracts/v4/constants.py:35` = `0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4` |
| PancakeSwap V4 — no StateView, uses PoolManager directly | DONE — falls back correctly |
| Read batching (`BatchRPC.add_call()`) | DONE — `src/utils.py:519` — supports arbitrary target + calldata + decoder |
| UI reads `tokens_owed0/1` from position dict | DONE — `ui/manage_tab.py:2556` |
| V4Position dataclass | Only has: token_id, pool_key, tick_lower, tick_upper, liquidity. **No fee fields** |

## What's Missing (4 pieces)

### Piece 1: `_compute_position_id()` — pure function

**Web source:** `backend/api/v4_positions.py:3421-3437`

**Logic:** `positionId = keccak256(abi.encodePacked(owner, tickLower, tickUpper, salt))`
- `owner` = PositionManager contract address (NOT wallet!) — 20 bytes
- `tickLower` = signed int24 → 3 bytes big-endian (two's complement)
- `tickUpper` = signed int24 → 3 bytes big-endian (two's complement)
- `salt` = `token_id.to_bytes(32, 'big')` — NFT token ID as bytes32

Packed: 20 + 3 + 3 + 32 = 58 bytes → keccak256 → bytes32

**Where to add:** `src/contracts/v4/pool_manager.py` — new standalone function

---

### Piece 2: `_calculate_unclaimed_fees()` — pure math function

**Web source:** `backend/api/v4_positions.py:3440-3485`

```python
def _calculate_unclaimed_fees(
    fee_growth_global0, fee_growth_global1,
    fg_outside0_lower, fg_outside1_lower,
    fg_outside0_upper, fg_outside1_upper,
    fg_inside0_last, fg_inside1_last,
    liquidity, current_tick, tick_lower, tick_upper,
) -> tuple[int, int]:
    MOD = 1 << 256
    Q128 = 1 << 128

    # feeGrowthBelow
    if current_tick >= tick_lower:
        fg_below0, fg_below1 = fg_outside0_lower, fg_outside1_lower
    else:
        fg_below0 = (fee_growth_global0 - fg_outside0_lower) % MOD
        fg_below1 = (fee_growth_global1 - fg_outside1_lower) % MOD

    # feeGrowthAbove
    if current_tick < tick_upper:
        fg_above0, fg_above1 = fg_outside0_upper, fg_outside1_upper
    else:
        fg_above0 = (fee_growth_global0 - fg_outside0_upper) % MOD
        fg_above1 = (fee_growth_global1 - fg_outside1_upper) % MOD

    # feeGrowthInside
    fg_inside0 = (fee_growth_global0 - fg_below0 - fg_above0) % MOD
    fg_inside1 = (fee_growth_global1 - fg_below1 - fg_above1) % MOD

    # Unclaimed fees (raw wei)
    fees0 = ((fg_inside0 - fg_inside0_last) % MOD) * liquidity // Q128
    fees1 = ((fg_inside1 - fg_inside1_last) % MOD) * liquidity // Q128
    return fees0, fees1
```

**Where to add:** `src/contracts/v4/pool_manager.py` — new standalone function. Zero dependencies, pure math.

---

### Piece 3: Batch fee reading via `BatchRPC`

**Web source:** `backend/api/v4_positions.py:3576-3678`

**New function:** `get_v4_unclaimed_fees(w3, positions, chain_id, protocol)` in `src/contracts/v4/pool_manager.py`

**Steps:**
1. Group positions by pool_key `(c0, c1, fee, tick_spacing, hooks)`
2. Pick fee contract: Uniswap → StateView; PancakeSwap → PoolManager
3. Build BatchRPC calls per pool group:
   - 1x `getFeeGrowthGlobals(poolId)` → decode `(uint256, uint256)`
   - 1x `getSlot0(poolId)` → decode current tick (signed int24 at offset)
   - Nx `getTickInfo(poolId, tick)` for unique ticks → decode `feeGrowthOutside0X128, feeGrowthOutside1X128` (offsets +64, +96)
   - Nx `getPositionInfo(poolId, positionId)` per position → decode `(liquidity, feeGrowthInside0LastX128, feeGrowthInside1LastX128)`
4. Execute batch (single RPC call)
5. For each position, call `_calculate_unclaimed_fees()`
6. Return `{token_id: (fees0_raw, fees1_raw)}`

**Tick info function name:** `getTickInfo` for Uniswap, `getPoolTickInfo` for PancakeSwap.

**Deduplication:** Unique ticks per pool group via `set()`.

**positionId:** Use `_compute_position_id(pm_address, tick_lower, tick_upper, token_id)`.

---

### Piece 4: Integration into position loading

**File:** `src/contracts/v4/position_manager.py` — method `get_all_positions()`

**Current code (line 1824):** `'tokens_owed0': 0, 'tokens_owed1': 0`

**Change:** After loading all positions, call `get_v4_unclaimed_fees()` in a single batch, then populate `tokens_owed0/1` in each position dict.

**Also update:** `V4Position` dataclass to optionally carry fee data (or just set it in the dict before returning).

---

## Execution Order

```
Step 1: Add _compute_position_id() to pool_manager.py          [~15 lines]
Step 2: Add _calculate_unclaimed_fees() to pool_manager.py      [~30 lines, copy from web]
Step 3: Add get_v4_unclaimed_fees() to pool_manager.py          [~80 lines, uses BatchRPC]
Step 4: Call get_v4_unclaimed_fees() from position_manager.py   [~10 lines]
Step 5: Test with real positions on BSC                         [manual verification]
```

## Protocol Differences (Uniswap V4 vs PancakeSwap V4)

| | Uniswap V4 | PancakeSwap V4 |
|---|---|---|
| Fee contract | StateView (`constants.state_view`) | PoolManager (`constants.pool_manager`) |
| Tick info function | `getTickInfo(poolId, tick)` | `getPoolTickInfo(poolId, tick)` |
| Pool ID hash | `keccak256(c0, c1, fee, tickSpacing, hooks)` | `keccak256(c0, c1, hooks, poolManager, fee, parameters)` |
| Fee math | Identical | Identical |

Pool ID computation is already implemented in `pool_manager.py:compute_pool_id()` — reuse it.

## Files to Modify

| File | Changes |
|------|---------|
| `src/contracts/v4/pool_manager.py` | Add `_compute_position_id`, `_calculate_unclaimed_fees`, `get_v4_unclaimed_fees` |
| `src/contracts/v4/position_manager.py` | Call `get_v4_unclaimed_fees` in `get_all_positions()`, populate tokens_owed |
| `src/utils.py` (optional) | Add convenience `BatchRPC.add_v4_fee_calls()` if wanted |

## Files NOT to Modify

- `src/contracts/v4/abis.py` — ABIs already complete
- `src/contracts/v4/constants.py` — addresses already present
- `ui/manage_tab.py` — already reads `tokens_owed0/1` from dict

## Testing

1. Use token IDs 397093-397099 (Uniswap V4, BSC, wallet `0xEAA45...`)
2. Compare desktop fees with web version output
3. Verify PancakeSwap V4 with existing PCS positions if available

## Estimated Scope

~135 lines of new code (pure Python, no UI changes). 1 new batch RPC call per position load. Web version reference: `backend/api/v4_positions.py:3421-3843`.
