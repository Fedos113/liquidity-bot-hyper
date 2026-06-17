# RPC Optimisation Report

## Summary of changes made to `bot.py` and supporting files

---

## 1. Pool Static Data Cache (`_get_pool_cache`)

**File:** `src/bot.py`

A thread-safe cache that stores pool metadata (`token0`, `token1`, `fee`, `tickSpacing`, `token0_is_hype`, `dec0`, `dec1`) after the first RPC call. All subsequent cycles reuse the cached values.

**Before:** Every cycle called `token0()`, `token1()`, `fee()`, `tickSpacing()`, and `get_token_order()` — 5 RPC calls per cycle.

**After:** 5 RPC calls **once**, then 0 for the lifetime of the process.

---

## 2. Multicall3 Batch Reads (`_multicall_price_and_balances`)

**File:** `src/bot.py`

Added `get_multicall3()` in `src/provider.py` that probes for the canonical Multicall3 contract (`0xcA11bde05977b3631167028862bE2a173976CA11`) on HyperEVM. If deployed, `_multicall_price_and_balances` batches `slot0` + `balanceOf(HYPE)` + `balanceOf(USDC)` into a single `aggregate3` call (1 HTTP round-trip instead of 3).

**Before:** 3 separate RPC calls: `slot0()`, `hype.balanceOf()`, `usdc.balanceOf()`.

**After:** 1 multicall (if Multicall3 available), otherwise 3 calls (unchanged fallback).

---

## 3. Fee Growth Multicall (`_multicall_fee_growth_and_ticks`)

**File:** `src/bot.py`

Batches the 5 RPC calls needed for fee computation (`feeGrowthGlobal0X128`, `feeGrowthGlobal1X128`, `slot0`, `ticks(lower)`, `ticks(upper)`) into one multicall.

**Before:** 5 separate RPC calls scattered across `get_unclaimed_fees`.

**After:** 1 multicall (if available), otherwise 5 calls (co-located in one function).

---

## 4. Merged Secondary Cycle (`_secondary_cycle`)

**Files:** `src/bot.py`, `src/config.py`

Replaced `_upward_cycle` and `_downward_cycle` threads with a single `_secondary_cycle` thread. Logic:
- If `pool_opened` (global flag) is `True`: fetch price via multicall, check upper bound + surge threshold → trigger `upward_inner_event`, check lower bound + drop threshold → trigger `downward_inner_event`.
- Uses `SECONDARY_INNER` (default 300s) as the sleep interval.
- `UPWARD_CYCLE_INTERVAL` and `DOWNWARD_CYCLE_INTERVAL` are **deprecated**.

**Before:** 2 separate threads each making 5+ RPC calls per tick (token0, token1, fee, slot0, position).

**After:** 1 thread making 2 RPC calls per tick (slot0+balances in multicall, position details from cache).

---

## 5. `TX_INTER_SLEEP` Between Consecutive Transactions

**File:** `src/bot.py`, `src/config.py`

Added mandatory `sleep(config.TX_INTER_SLEEP)` between every consecutive on-chain write inside inner cycles and the main cycle. This prevents nonce collisions when back-to-back writes (`collect_fees → remove_liquidity → collect_fees → swap`) are submitted without waiting for a receipt gap.

**Default:** 3 seconds. Configurable via `TX_INTER_SLEEP` in `.env`.

---

## 6. `pool_opened` Global Flag

**File:** `src/bot.py`

Added `pool_opened` boolean that tracks whether there is an active position with liquidity. Set to `True` when a position is found/created, `False` when closed. Used by `_secondary_cycle` to skip RPC reads when no position exists.

---

## 7. Inline Fee Computation

**File:** `src/bot.py`

Added `_compute_unclaimed_fees(pos, fg0, fg1, current_tick, lower_tick, upper_tick)` that computes unclaimed fees locally from pre-fetched data. The main cycle uses `_multicall_fee_growth_and_ticks` + `_compute_unclaimed_fees` instead of calling `get_unclaimed_fees`, reducing fee-related RPC calls from 5 to 1 (or 5 if multicall unavailable, but still co-located).

---

## 8. `cached_slot0` Parameter in `get_unclaimed_fees`

**File:** `src/position_manager.py`

Added optional `cached_slot0` parameter. When provided, skips the internal `slot0()` RPC call. Preserved for backward compatibility with external callers.

---

## 9. `pool_fee` Parameter in `create_position`, `rebalance`, `add_to_position`

**File:** `src/position_manager.py`

Added optional `pool_fee` parameter to `create_position`, `rebalance`, and `add_to_position`. When non-zero (from pool cache), skips the internal `fee()` RPC call. Saves 1 call per trigger in the main cycle and inner cycles.

---

## RPC Call Reduction Table

| Location | Before (calls/cycle) | After (calls/cycle) | Savings |
|---|---|---|---|
| Pool static data (token0, token1, fee, tickSpacing, token order) | 5 per cycle | 5 **once**, then 0 | ~5 calls/cycle saved |
| Main cycle: price + balances | 3 (slot0, hype.bal, usdc.bal) | 1 (multicall) or 3 | Up to 2 calls/cycle saved |
| Fee growth check | 5 (fg0, fg1, slot0, tick×2) | 1 (multicall) or 5 | Up to 4 calls/cycle saved |
| Secondary watcher: price check | 5 (token0, token1, fee, slot0, position) | 2 (slot0+balances via multicall, position from cache) | 3 calls/tick saved |
| Secondary: token0/token1 per tick | 2 | 0 (cached) | 2 calls/tick saved |
| Inner cycles: fee call inside swap | 1 per trigger | 0 (from cache) | 1 per trigger saved |
| **Total per main cycle** | **13+** | **3–7** (depending on multicall availability) | **~6–10 per cycle** |

---

## Files Modified

| File | Changes |
|---|---|
| `src/bot.py` | Pool cache, multicall helpers, merged secondary cycle, `TX_INTER_SLEEP`, `pool_opened` flag, inline fee computation |
| `src/config.py` | Added `SECONDARY_INNER`, `TX_INTER_SLEEP`, deprecation warnings for old cycle intervals |
| `src/provider.py` | Added Multicall3 detection and `get_multicall3()` helper |
| `src/position_manager.py` | Added `cached_slot0` to `get_unclaimed_fees`, `pool_fee` to `create_position`/`rebalance`/`add_to_position` |
| `.env.example` | Added `SECONDARY_INNER`, `TX_INTER_SLEEP`; deprecated `UPWARD_CYCLE_INTERVAL`, `DOWNWARD_CYCLE_INTERVAL` |
