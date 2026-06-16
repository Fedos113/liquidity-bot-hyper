# Secondary Protection Cycle — Implementation Plan

## Goal

Add an independent secondary monitoring cycle that runs every **10 minutes** alongside the existing main pool-creation cycle (unchanged). If a Uniswap V3 position is open and the HYPE price drops **>2% below the position's lower bound**, the secondary cycle closes the position and swaps all wHYPE → USDC as a protective measure against impermanent loss.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Concurrency model | **Daemon thread** inside the same process | Simplest; no IPC, no extra process, shares `running` flag naturally |
| Nonce safety | **Global `threading.Lock`** wrapping all tx-sending paths | Prevents nonce conflicts if both cycles try to send txs simultaneously |
| Web3 instances | Each thread creates **its own `Web3`** connection | Avoids connection-pool concurrency issues |
| Token ID sharing | **Thread-safe list `[token_id]`** as a mutable reference | Both threads can read/write the current token ID atomically |

## Files to Modify

| File | Change |
|---|---|
| `src/config.py` | Add 2 new config vars |
| `src/bot.py` | Add secondary cycle thread function; launch it in `run_bot()`; add global `tx_lock`; wrap main cycle's critical section with lock |
| `src/provider.py` | Export `get_swap_router_contract` (already exists), no changes needed |

No changes to `src/position_manager.py`, `src/math_utils.py`, `src/constants.py`, `main.py`.

## Step-by-Step Implementation

### 1. New Config Variables (`src/config.py`)

Add after existing env vars:

```
SECONDARY_CYCLE_INTERVAL: int = int(os.getenv("SECONDARY_CYCLE_INTERVAL", "600"))
HYPE_DROP_THRESHOLD: float = float(os.getenv("HYPE_DROP_THRESHOLD", "0.98"))
```

- `SECONDARY_CYCLE_INTERVAL` = 60 seconds (1 min)
- `HYPE_DROP_THRESHOLD` = 0.98 (price < lower_bound × 0.98 triggers close)

Also add to `.env.example` with defaults.

### 2. Global Transaction Lock (`src/bot.py`)

Add near the existing `running` global:

```
tx_lock = threading.Lock()
token_id_ref = [0]  # mutable container shared between threads
```

### 3. Secondary Cycle Function (`src/bot.py`)

New function `_secondary_cycle()`:

```
def _secondary_cycle():
    global running, tx_lock, token_id_ref

    while running:
        cycle_start = time()
        logger.info("[SECONDARY] Cycle start")

        try:
            # Own Web3 connection
            w3 = get_web3()
            pool = get_pool_contract(w3)
            pm = get_position_manager_contract(w3)
            pool_t0 = pool.functions.token0().call()
            pool_t1 = pool.functions.token1().call()

            # Resolve token ID (shared ref or auto-discover)
            tid = token_id_ref[0]
            pos = None
            if tid > 0:
                pos = get_position_details(w3, pm, tid)
            if not pos or pos["liquidity"] == 0:
                new_id, pos = _auto_discover_position(w3, pm, pool_t0, pool_t1)
                if new_id is not None:
                    token_id_ref[0] = new_id
                    tid = new_id

            if pos and pos["liquidity"] > 0:
                current_price, current_tick, _ = get_current_price(w3, pool)
                token0_is_hype, dec0, dec1 = get_token_order(pool, config.HYPE_ADDRESS)
                invert = not token0_is_hype
                lower_price = tick_to_price(pos["tickLower"], dec0, dec1, invert)
                trigger_price = lower_price * config.HYPE_DROP_THRESHOLD

                logger.info(
                    f"[SECONDARY] Price=${current_price:.4f} "
                    f"lower=${lower_price:.4f} trigger=${trigger_price:.4f}"
                )

                if current_price < trigger_price:
                    logger.warning(
                        f"[SECONDARY] Price ${current_price:.4f} dropped >2% "
                        f"below lower bound ${lower_price:.4f}. Closing position..."
                    )
                    with tx_lock:
                        collect_fees(w3, pm, tid, config.DRY_RUN)
                        remove_liquidity(w3, pm, tid, pos["liquidity"], config.DRY_RUN)
                        collect_fees(w3, pm, tid, config.DRY_RUN)

                        # Swap all wHYPE → USDC
                        hype_bal, usdc_bal = get_token_balances(w3)
                        if hype_bal > 0:
                            pool_fee = pool.functions.fee().call()
                            approve_token(
                                w3, get_hype_or_usdc(w3, True),
                                config.SWAP_ROUTER_ADDRESS, hype_bal, config.DRY_RUN,
                            )
                            swap_exact_input_single(
                                w3, config.HYPE_ADDRESS, config.USDC_ADDRESS,
                                pool_fee, hype_bal, config.DRY_RUN,
                            )
                            logger.info("[SECONDARY] Position closed, all wHYPE swapped to USDC")
                else:
                    logger.info("[SECONDARY] Price within threshold, no action")
            else:
                logger.info("[SECONDARY] No active position")

        except Exception as e:
            logger.warning(f"[SECONDARY] Cycle error: {e}")

        # Sleep with early-exit on shutdown
        elapsed = time() - cycle_start
        remaining = config.SECONDARY_CYCLE_INTERVAL - elapsed
        for _ in range(int(max(remaining, 0))):
            if not running:
                break
            sleep(1)
```

### 4. Launch Thread in `run_bot()` (`src/bot.py`)

Near the start of `run_bot()`, after setting up signal handlers and before the main `while running:` loop:

```
token_id_ref[0] = token_id
secondary_thread = threading.Thread(target=_secondary_cycle, daemon=True)
secondary_thread.start()
logger.info(f"Secondary protection cycle started (interval={config.SECONDARY_CYCLE_INTERVAL}s, drop_threshold={config.HYPE_DROP_THRESHOLD})")
```

Also update `token_id_ref[0]` whenever the main cycle changes `token_id` (4 locations in the existing code).

### 5. Wrap Main Cycle Critical Section with Lock

In the main cycle's `try` block, wrap the decision/action section with `tx_lock`:

```
with tx_lock:
    # existing main cycle logic unchanged
    if pos and pos["liquidity"] > 0:
        ...
    else:
        ...
```

This ensures the secondary cycle never sends transactions while the main cycle is mid-operation.

## No Changes to

| Area | Reason |
|---|---|
| `src/position_manager.py` | All on-chain functions are reused as-is |
| `src/math_utils.py` | Price/tick conversions unchanged |
| `src/constants.py` | ABIs unchanged |
| `src/provider.py` | All contract getters already exist |
| `main.py` | Entry point unchanged |
| Main cycle logic/structure | Only wrapped in `tx_lock`; no behavioral change |
| Main cycle sleep timing | Unchanged (still `SLEEP_INTERVAL`, still stdin skip) |

## Thread Safety Summary

| Concern | Mitigation |
|---|---|
| Nonce collision | `tx_lock` ensures only one thread builds/sends transactions at a time |
| Web3 connection reuse | Each thread creates its own `Web3` instance |
| Token ID staleness | Shared mutable `token_id_ref` updated by both threads; secondary re-discovers if stale |
| Shutdown | Both threads check `running` flag; secondary is a daemon thread |
| Logging | `logging` module is thread-safe; prefix `[SECONDARY]` for clarity |

## Risk Assessment

- **False positive trigger**: price may briefly dip below threshold and recover. The secondary cycle will execute protective close + swap on the next 10-min check. Acceptable – better to protect capital.
- **Race on `token_id_ref`**: if main cycle creates a new position while secondary is running, it's safe because the secondary also auto-discovers if `pos` is stale.
- **Dry-run respect**: all transaction calls pass `config.DRY_RUN`, so dry-run mode works for the secondary cycle too.
- **Dashboard impact**: the dashboard reads `position_meta.json` and chain state; after secondary closes the position, the dashboard will show 0 liquidity, which is correct.
