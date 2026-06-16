# Liquidity Bot — Pool Creation, Swaps & Position Management (Full Process)

This document traces every step of the **HYPE/USDC Concentrated Liquidity Bot** on **HyperEVM** (Hyperliquid's EVM chain, Chain ID 999). It covers the complete lifecycle: initial pool interaction, position minting, rebalancing, swaps, fee collection, and compounding.

---

## Table of Contents

1. [Architecture & Smart Contracts](#1-architecture--smart-contracts)
2. [Startup Flow](#2-startup-flow)
3. [The Main Bot Cycle](#3-the-main-bot-cycle)
4. [Position Creation (Full Detail)](#4-position-creation-full-detail)
5. [Rebalance Flow](#5-rebalance-flow)
6. [Swap Mechanism](#6-swap-mechanism)
7. [Fee Collection & Compounding](#7-fee-collection--compounding)
 8. [Balance Tokens](#8-balance-tokens)
 9. [Optimize Token Ratio (_optimize_ratio)](#9-optimize-token-ratio-_optimize_ratio)
10. [Post-Mint Top-Up](#10-post-mint-top-up)
11. [Dashboard & Metrics](#11-dashboard--metrics)
12. [Real Execution Log Traces](#12-real-execution-log-traces)
13. [Error Scenarios Observed](#13-error-scenarios-observed)
14. [Glossary](#14-glossary)

---

## 1. Architecture & Smart Contracts

### Contracts

| Contract | Address | Role |
|---|---|---|
| **HYPE/USDC Pool** | `0x6c9A33E3b592C0d65B3Ba59355d5Be0d38259285` | Uniswap V3-style concentrated liquidity pool |
| **NonfungiblePositionManager** | `0xeaD19AE861c29bBb2101E834922B2FEee69B9091` | Manages NFT positions (mint, collect, increase/decrease liquidity) |
| **Swap Router** | `0x1EbDFC75FfE3ba3de61E7138a3E8706aC841Af9B` | Executes exact-input single-hop swaps |
| **wHYPE** | `0x5555555555555555555555555555555555555555` | Wrapped HYPE (native token), 18 decimals |
| **USDC** | `0xb88339cb7199b77e23db6e890353e22632ba630f` | USD Coin, 6 decimals |

### Tokens

- **wHYPE**: 18 decimals, token0 in the pool
- **USDC**: 6 decimals, token1 in the pool
- Pool fee tier: **500** (0.05%) — note: the `.env` has `FEE_TIER=3000` but the pool itself uses 0.05%
- **Tick spacing**: 60 (for 0.05% fee tier)

### Key Parameters (from `.env`)

| Parameter | Value | Meaning |
|---|---|---|
| `LOWER_BOUND_PCT` | `0.97` | Lower bound = 97% of current price |
| `UPPER_BOUND_PCT` | `1.04` | Upper bound = 104% of current price |
| `SLEEP_INTERVAL` | `600` | Seconds between bot cycles |
| `SLIPPAGE_TOLERANCE` | `0.005` | 0.5% slippage for increaseLiquidity |
| `FEE_COMPOUND_THRESHOLD_USD` | `5.0` | Compound fees when >= $5.00 |
| `DRY_RUN` | `false` | Real transactions |

### Token Order in Pool

The pool stores its tokens as:
- **token0 = wHYPE** (because `0x55...55` < `0xb8...0f` alphabetically)
- **token1 = USDC**

When `token0_is_hype = True`, the price formula inverts to show USDC/HYPE.

---

## 2. Startup Flow

### Entry Point: `main.py`

```python
python main.py [--dry-run] [--log-level DEBUG|INFO|WARNING|ERROR]
```

1. `main.py` parses CLI args and loads `Config` from `.env` (via `src/config.py`)
2. Calls `setup_logging()` — outputs to both stdout and `liqbot.log`
3. Invokes `run_bot()` from `src/bot.py`

### Bot Initialization: `src/bot.py:run_bot()`

```
  START
    ├─ Validate config (check required vars, 0x prefix)
    ├─ Register SIGINT/SIGTERM handlers
    ├─ Log SLEEP_INTERVAL, LOWER, UPPER bounds
    └─ Enter main loop
```

---

## 3. The Main Bot Cycle

Each cycle executes these steps sequentially:

### Step-by-Step Loop (simplified)

```
while running:
  1. Connect to HyperEVM via Web3
  2. Get pool and position manager contract instances
  3. Read slot0() → sqrtPriceX96, currentTick
  4. Compute current_price from sqrtPriceX96
  5. Determine token order (which is token0/token1)
  6. Fetch position:
     a. Try config.TOKEN_ID (from .env)
     b. If not found/liquidity=0 → auto_discover (scan wallet NFTs)
  7. Decision Tree:

     ┌── Position EXISTS + has liquidity (> 0)
     │    ├── Position VALUE > $1?
     │    │    ├── IN RANGE (lower <= price <= upper)?
     │    │    │    ├── Wallet > $0.20? → add_to_position()
     │    │    │    └── Wallet <= $0.20? → check fees
     │    │    │         └── Fees >= $5.00? → compound (collect + increaseLiquidity)
     │    │    └── OUT OF RANGE (above or below)?
     │    │         → collect_fees() → remove_liquidity() → collect_fees() → create_position()
     │    └── Position value <= $1?
     │         → collect_fees() → remove_liquidity() → collect_fees() → create_position()
     │
     └── Position NOT FOUND or liquidity=0
          → create_position()

  8. Sleep for SLEEP_INTERVAL seconds (type 'skip' to bypass)
```

### Auto-Discovery (`_auto_discover_position`)

When the saved `TOKEN_ID` points to a position with zero liquidity or an invalid position:

1. Create an ERC721 interface to the PositionManager contract
2. Call `balanceOf(wallet)` to get NFT count
3. For each index i, call `tokenOfOwnerByIndex(wallet, i)`
4. Check if token's pool matches (token0/token1) AND liquidity > 0
5. Return the first valid position found

---

## 4. Position Creation (Full Detail)

Called when no position exists or after removing an out-of-range position.

### `create_position()` in `src/position_manager.py:540-619`

```
Step 0: Balance Tokens (balance_tokens)
  ├── Check native HYPE balance
  ├── Compute wrap_amount = native_balance - 0.01 HYPE (gas reserve)
  ├── If wrap_amount > 0: wrap native HYPE → wHYPE (call wHYPE.deposit())
  ├── Read wHYPE and USDC balances
  └── (No swap here — ratio optimization happens in Step 1b)

Step 1: Calculate Bounds
  ├── lower_price = current_price * LOWER_BOUND_PCT (0.97)
  ├── upper_price = current_price * UPPER_BOUND_PCT (1.04)
  ├── Convert prices to ticks via price_to_tick()
  │   └── snappend to tick_spacing (60)
  ├── Ensure tick_lower < tick_upper
  └── Convert back to actual prices for logging

Step 1b: Optimize Token Ratio (_optimize_ratio)
  ├── Compute target_ratio = sp * spu * (sp - spl) / (spu - sp)
  │   where sp = sqrt(current_price), spl/spu = sqrt(lower/upper tick prices)
  ├── Compare current token ratio (raw1/raw0) to target
  ├── If deviation < 1% → return (optimal ratio achieved)
  ├── If deviation >= 1%:
  │   ├── Compute excess_raw of the overrepresented token
  │   ├── Swap 95% of excess via exactInputSingle (approve → swap)
  │   └── Re-read balances once after the swap
  └── (Single swap, not iterative — the 95% fraction avoids overshooting)

Step 2: Calculate Optimal Amounts
  └── calculate_token_amounts(raw0, raw1, sqrt_price_x96, tick_lower, tick_upper)
      └── Returns amount0, amount1 for max liquidity at the given price range

Step 3: Approve Tokens
  ├── Approve wHYPE → PositionManager for amount0_desired
  └── Approve USDC → PositionManager for amount1_desired

Step 4: Mint Position
  ├── Build MintParams struct:
  │   ├── token0, token1, fee
  │   ├── tickLower, tickUpper
  │   ├── amount0Desired, amount1Desired
  │   ├── amount0Min = amount0Desired * (1 - 5%)  (5x slippage)
  │   ├── amount1Min = amount1Desired * (1 - 5%)
  │   ├── recipient = wallet
  │   └── deadline = block.timestamp + 600s
  ├── Sign and send transaction
  ├── Wait for receipt
  └── Parse Transfer event logs → extract new tokenId
```

### The MintParams Struct (from POSITION_MANAGER_ABI)

```solidity
struct MintParams {
    address token0;
    address token1;
    uint24 fee;
    int24 tickLower;
    int24 tickUpper;
    uint256 amount0Desired;
    uint256 amount1Desired;
    uint256 amount0Min;
    uint256 amount1Min;
    address recipient;
    uint256 deadline;
}
```

### Real Example (from logs: Token ID 495097)

```
Current price: 67.445738 USDC/HYPE
Current tick: -234620...-233620 (range)
Wallet: HYPE=0.3650, USDC=24.513423
Pre-mint: both tokens present, ratio acceptable
amount0=365000000000000000 (0.365 wHYPE), amount1=17276396 (17.28 USDC)
Mint tx sent, confirmed, new Token ID: 495097
```

---

## 5. Rebalance Flow

Called when the position is out of range (price moved outside bounds).

### `rebalance()` in `src/position_manager.py:379-537`

```
=== Starting Rebalance ===

Step 1: Collect Fees (before removal)
  ├── Read balances before
  ├── Call positionManager.collect({tokenId, recipient, amount0Max=MAX, amount1Max=MAX})
  ├── Read balances after → delta = collected amounts
  └── Log collected amounts

Step 2: Remove Liquidity
  ├── Call positionManager.decreaseLiquidity({tokenId, liquidity, amount0Min=0, amount1Min=0})
  ├── Read balances after
  └── Log removed amounts (amount0, amount1 returned to wallet)

Step 3: Collect Fees Again (after removal)
  └── Same as Step 1 (catches any residual fees)

Step 4: Balance Tokens
  ├── Wrap any native HYPE (keep 0.01 HYPE for gas)
  └── (No swap — ratio optimization handled in Step 5b)

Step 5: Calculate New Bounds
  └── Same as create_position Step 1

Step 5b: Optimize Token Ratio
  └── Same as create_position Step 1b (_optimize_ratio, single swap of 95%)

Step 6: Calculate Optimal Amounts
  └── Same as create_position Step 2

Step 7: Approve Tokens
  └── Same as create_position Step 3

Step 8: Mint New Position
  └── Same as create_position Step 4

Step 9 (Post-Mint): Top-Up
  ├── Read leftover balances after mint
  ├── If leftover < $2 total → skip
  ├── If one token excess > $2:
  │   ├── Swap 92% of excess for the other token
  │   └── Calculate new optimal amounts
  ├── If add0 > 1 or add1 > 1:
  │   ├── Approve tokens → PositionManager
  │   └── increase_liquidity() with remaining funds
  └── (Non-critical: failure doesn't stop rebalance)

=== Rebalance Complete ===
```

### Real Rebalance Example (Token ID 495097 → 495183)

```
Old position: tickLower=-234620, tickUpper=-233620, liquidity=0
(Previous mint failed, position has 0 liquidity, skipping removal)
Step 3: Collect fees → 0, 0 (nothing to collect)
Step 4: Both tokens present (wHYPE=1.5012, USDC=101.404659), skipping swap
Step 5: New bounds = lower=64.8576 (tick -234600), upper=71.6067 (tick -233610)
Optimal amounts: 1501160787968863488 t0, 69502681 t1
Mint tx: tickLower=-234600, tickUpper=-233610
→ New Token ID: 495183 (gas used: 438303)
```

---

## 6. Swap Mechanism

### `swap_exact_input_single()` in `src/position_manager.py:636-667`

Swaps an exact amount of one token for another via the Swap Router.

```
swap_exact_input_single(token_in, token_out, fee, amount_in):
  1. Validate amount_in > 0
  2. Get SwapRouter contract instance
  3. Get wallet account, compute deadline (now + 600s)
  4. Build ExactInputSingleParams:
     {
       tokenIn:         checksummed token_in address
       tokenOut:        checksummed token_out address
       fee:             pool fee tier (e.g. 500)
       recipient:       wallet address
       deadline:        block.timestamp + 600
       amountIn:        amount_in (raw units)
       amountOutMinimum: 0 (no minimum)
       sqrtPriceLimitX96: 0 (no limit)
     }
  5. Build transaction (gas=300000)
  6. Sign and send via send_transaction()
  7. Wait for receipt
  8. Return None (no amountOut tracking, just success/fail)
```

### ExactInputSingleParams Struct

```solidity
struct ExactInputSingleParams {
    address tokenIn;
    address tokenOut;
    uint24 fee;
    address recipient;
    uint256 deadline;
    uint256 amountIn;
    uint256 amountOutMinimum;
    uint160 sqrtPriceLimitX96;
}
```

### Where Swaps Happen

Swaps occur in these contexts:

| Context | Direction | Purpose |
|---|---|---|
| **_optimize_ratio()** | Excess token → other token | Correct wallet ratio to match pool's optimal ratio before minting |
| **Post-mint top-up** | Excess leftover → other token | Deploy remaining capital into the position (92% of excess) |
| **add_to_position()** | Excess token → other token | Correct ratio before adding liquidity to existing position |

### Real Swap Examples

**Pre-mint ratio correction (single swap, 95% of excess):**
```
Ratio deviation: token0→token1, ~$24.2 excess
  → Approve SwapRouter for 0.3111 wHYPE
  → Swap 0.3111 wHYPE → USDC (95% of excess)
  → Final amounts: 1.479 wHYPE, 79.75 USDC
```

---

## 7. Fee Collection & Compounding

### `collect_fees()` in `src/position_manager.py:187-212`

```python
collect_fees(w3, position_manager, token_id):
  1. Read wHYPE + USDC balances (before)
  2. Build CollectParams:
     {
       tokenId:     token_id
       recipient:   wallet address
       amount0Max:  2^128 - 1 (max uint128)
       amount1Max:  2^128 - 1
     }
  3. Call positionManager.collect()
  4. Read balances (after)
  5. amount0 = after_0 - before_0
  6. amount1 = after_1 - before_1
  7. Return (amount0, amount1)
```

### `get_unclaimed_fees()` in `src/position_manager.py:110-158`

Computes unclaimed fees without sending a transaction, using:

```
feeGrowthInside = feeGrowthGlobal - feeGrowthBelow - feeGrowthAbove

Where:
  - feeGrowthGlobal is read from pool.feeGrowthGlobal0X128/1X128
  - feeGrowthBelow is read from pool.ticks(tickLower)[2 or 3]
  - feeGrowthAbove is read from pool.ticks(tickUpper)[2 or 3]

unclaimed = tokensOwed + (liquidity * (currentInside - lastInside)) / 2^128
```

### Fee Compounding Logic in `bot.py`

```
if fee_val_usd >= FEE_COMPOUND_THRESHOLD_USD ($5.00):
  am0, am1 = collect_fees(w3, pm, token_id)
  if any collected:
    approve tokens → PositionManager
    increase_liquidity(w3, pm, token_id, am0, am1)
```

### Real Fee Check (from log)

```
Position: [66.0355 - 70.7526] USDC/HYPE
Liquidity: 634478177113048
Unclaimed fees: 0.000000 t0, 0.000000 t1
Fees ($0.00) below $5.0
```

---

## 8. Balance Tokens

### `balance_tokens()` in `src/position_manager.py:670-691`

Called at the start of `create_position()` and as Step 4 of `rebalance()`.

```
balance_tokens(w3, dry_run):
  1. Read native HYPE balance
  2. Compute wrap_amount = native_balance - 0.01 HYPE (gas reserve)
  3. If wrap_amount > 0: wrap_hype(wrap_amount)
     └─ Calls wHYPE.deposit() with value = wrap_amount (payable function)
  4. Read wHYPE and USDC balances
  5. Log balances
  └── (No swap — the _optimize_ratio function handles ratio correction)
```

### Why No Swap Here?

The old 50/50 split was removed because `_optimize_ratio()` (Step 1b/5b) handles the precise ratio needed for full capital utilization. Wrapping native HYPE is the only essential preparatory step; the ratio correction happens right before minting with the actual pool math.

---

## 9. Optimize Token Ratio (_optimize_ratio)

This is a critical optimization used in both `create_position()` and `rebalance()`. It ensures the wallet's token ratio matches what the pool needs for full capital utilization at the chosen tick range.

### `_optimize_ratio()` in `src/position_manager.py:692-751`

### The Math

```
sp  = sqrt(current price)              = sqrtPriceX96 / 2^96
spl = sqrt(price at tick_lower)        = sqrt(1.0001^tickLower)
spu = sqrt(price at tick_upper)        = sqrt(1.0001^tickUpper)

target_ratio = sp * spu * (sp - spl) / (spu - sp)
             = raw1 / raw0  (optimal ratio for full utilization)

current_ratio = raw1 / raw0  (wallet's current ratio)

deviation = |ln(current_ratio / target_ratio)|
```

### The Algorithm

```
Compute target_ratio from tick bounds and current price
Compute current_ratio from wallet balances
Compute deviation

If deviation < 0.01 (1%):  → return (already optimal)

Determine which token is overrepresented:
  If current_ratio > target_ratio → excess token1
  If current_ratio < target_ratio → excess token0

Compute excess_raw of the overrepresented token
swap_raw = int(excess_raw * 0.95)

If swap_raw >= 1000:
  approve → SwapRouter
  swap_exact_input_single(excess_token → other_token, swap_raw)

Re-read balances once
```

### Single Swap, Not Iterative

The old iterative approach was simplified to a single swap of 95% of the excess raw amount. Swapping 95% instead of 100% accounts for slippage without needing multiple iterations. The `$2` minimum check was also removed — swaps proceed as long as `swap_raw >= 1000` (a dust threshold in raw units).

### Real Example

```
Ratio deviation: token0->token1
  → Approve SwapRouter for 0.3111 wHYPE
  → Swap 0.3111 wHYPE → USDC (95% of excess)
  → Final amounts: 1.479 wHYPE, 79.75 USDC
```

---

## 10. Post-Mint Top-Up

After minting a position, there's often a small leftover balance due to rounding. The bot tries to deploy this too.

### In `rebalance()` lines 483-534

```
1. Read wallet balances after mint
2. Compute leftover0_usd and leftover1_usd
3. If both < $2: → skip ("Unused ~$X < $2")
4. If one > $2:
   ├── Swap 92% of the excess token for the other
   ├── Calculate new optimal amounts with remaining funds
   ├── If amounts meaningful (> 1):
   │   ├── Approve → PositionManager
   │   └── increase_liquidity()
   └── (Wrapped in try/except — non-critical)
```

### Real Example (Token ID 495242)

```
After mint, leftover ~$24.5 (mostly USDC)
→ Swap $24.5 excess USDC → HYPE
→ Adding liquidity: 0.0375 wHYPE + 1.96 USDC
→ But increaseLiquidity reverted with "Price slippage check"
→ Top-up failed (non-critical, rebalance still successful)
```

---

## 11. Dashboard & Metrics

The dashboard is a FastAPI app (`liqbot2/main.py`) serving:

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | — | Serves `index.html` (dark-themed Chart.js dashboard) |
| `POST /refresh` | — | Reads live chain data, computes metrics, stores snapshot |
| `GET /chart` | — | Returns historical portfolio values as JSON |

### Metrics Computed (`liqbot2/metrics.py`)

- **Position value**: Compute token amounts from liquidity + sqrtPrice, value in USD
- **Wallet value**: wHYPE balance * price + USDC balance
- **Portfolio value**: Position value + Wallet value
- **PnL (24h/7d/All)**: Difference from historical snapshots
- **Impermanent Loss**: `IL = 2*sqrt(r)/(1+r) - 1` where `r = price_now / price_entry`
- **Total tx fees**: Sum of `fee_wei` from `tx_fees` table, converted to USD

### SQLite Schema

```sql
CREATE TABLE snapshots (
    ts INTEGER PRIMARY KEY,
    hype_bal INTEGER,
    usdc_bal INTEGER,
    liquidity INTEGER,
    tick_lower INTEGER,
    tick_upper INTEGER,
    current_tick INTEGER,
    portfolio_value_usd REAL,
    price REAL
);

CREATE TABLE tx_fees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    tx_hash TEXT,
    fee_wei INTEGER,
    description TEXT
);
```

---

## 12. Real Execution Log Traces

### Full Successful Position Creation (Token ID 495097)

```
17:23:20 Cycle start
17:24:28 Cycle start
17:24:32 Current price: 67.260143 USDC/HYPE
17:24:32 Current tick: -234229
17:24:35 No active position → create_position()
17:28:58 Cycle start
17:29:02 Current price: 67.417202 USDC/HYPE
17:29:08 New bounds: lower=64.7280 (tick -234620), upper=71.4637 (tick -233630)
17:29:08 Wallet: HYPE=0.0000, USDC=0.0000 → No tokens (position already minted but we see the cycle)
```

### First Successful Mint (after several retries)

```
17:40:46 Cycle start
17:40:48 Current price: 67.193678 USDC/HYPE
17:40:50 Wrapping 0.7300 HYPE to wHYPE
17:40:52 Tx: 6b2da2... (wrap)
17:40:56 Swapping half wHYPE (~0.3650) for USDC
17:40:58 Approve SwapRouter for 365000000000000000
17:40:58 Tx: d0b190... (approve)
17:41:00 Swap 0.3650 wHYPE → USDC via fee=500
17:41:01 Tx: 5c014f... (swap)
17:41:01 New bounds: lower=64.5341 (tick -234650), upper=71.2496 (tick -233660)
17:41:02 Approve PositionManager for 365000000000000000
17:41:04 Tx: 073a6f... (approve t0)
17:41:05 Approve PositionManager for 24513423
17:41:06 Tx: 30a0dc... (approve t1)
17:41:08 Minting: tickLower=-234650, tickUpper=-233660
            amount0=365000000000000000, amount1=24513423
17:41:09 Tx: fb13b7... (mint)
**→ REVERTED: "Price slippage check"**
```

### Successful Mint after adjusting tick bounds

```
17:43:18 Cycle start
17:43:21 Current price: 67.345832
17:43:24 Both tokens present, skipping swap
17:43:24 New bounds: lower=64.6633 (tick -234630), upper=71.3922 (tick -233640)
17:43:27 Minting: tickLower=-234630, tickUpper=-233640
            amount0=365000000000000000, amount1=17112340
17:43:29 Tx: 5ca386... (mint)
**→ REVERTED: "Price slippage check"**
```

### Finally Successful

```
17:46:49 Cycle start
17:46:51 Current price: 67.445738
17:46:55 Both tokens present, skipping swap
17:46:55 New bounds: lower=64.7280 (tick -234620), upper=71.5352 (tick -233620)
17:47:00 Minting: tickLower=-234620, tickUpper=-233620
            amount0=365000000000000000, amount1=17276396
17:47:02 Tx: f15bb6... (mint confirmed)
```
**→ Position created. Token ID: 495097**

### Successful Rebalance Chain (IDs 495183 → 495187 → 495203)

```
ID 495183: mint tickLower=-234600, tickUpper=-233610, am0=1.501wHYPE, am1=69.50USDC
    → gas: 438303
ID 495187: mint tickLower=-234560, tickUpper=-233570, am0=19.03USDC(as t0), am1=1
    → gas: 438105
ID 495203: mint tickLower=-234410, tickUpper=-233710, am0=1.263wHYPE, am1=66.83USDC
    → gas: 438443
```

### Full Rebalance with Pre-Mint Swaps (ID 495219)

```
19:05:00 Cycle start
19:05:02 Current price: 67.838619
19:05:14 Balances: wHYPE=1.4422, USDC=105.389955
19:05:14 New bounds: lower=65.7719 (tick -234460), upper=70.5407 (tick -233760)
19:05:16 Swapping excess token1 ($27.64)
19:05:18 Tx: 3eb45b... (swap)
19:05:19 Swapping excess token0 ($23.11)
19:05:21 Approve SwapRouter for 0.3111 wHYPE
19:05:23 Swap 0.3111 wHYPE → USDC
19:05:24 Final amounts: 1.479wHYPE, 79.75USDC
19:05:32 Mint tx confirmed → Token ID: 495219 (gas: 429113)
```

---

## 13. Error Scenarios Observed

### 1. Price Slippage Check (mint)

```
Reason: "Price slippage check"
```
Happens when the price moves between computing amounts and sending the mint tx. The bot uses 5x the configured slippage (0.5% * 10 = 5%) for mint, but rapid price moves can still exceed this.

**Impact**: Mint reverts; wallet keeps tokens. Next cycle retries with updated price bounds.

### 2. Price Slippage Check (increaseLiquidity)

```
Reason: "Price slippage check"
```
Happens in post-mint top-up when `increaseLiquidity` checks slippage. Wrapped in try/except — non-critical, rebalance still succeeds.

### 3. RPC Rate Limiting

```
{'code': -32005, 'message': 'rate limited'}
```
HyperEVM RPC rate-limits the bot during rapid cycles. The `with_retry` decorator retries 3 times with exponential backoff (2s, 4s), but if the rate limit persists, the cycle fails.

### 4. RPC Invalid Block Height

```
{'code': -32603, 'message': 'invalid block height: 37905512'}
```
Ephemeral RPC inconsistency — retry succeeds on the next attempt.

### 5. Connection Reset

```
ConnectionResetError(10054, 'An existing connection was forcibly closed by the remote host')
```
Network-level interruption. The `with_retry` decorator handles this.

### 6. Execution Reverted (no data) — Amount Too Small

```
Reason: "execution reverted" (no data)
```
When wallet has almost no tokens (< $1), the calculated amounts (e.g., `amount0=2298`, `amount1=1`) are too small for the pool to process. These positions are worth ~$0.16 and consistently fail to mint.

---

## 14. Glossary

| Term | Definition |
|---|---|
| **Tick** | A unit of price granularity in Uniswap V3. Each tick represents a 0.01% price step (1.0001^tick). |
| **Tick spacing** | The minimum distance between initialized ticks. For 0.05% pools = 60 ticks. |
| **sqrtPriceX96** | The square root of the price stored as a Q64.96 fixed-point number (multiplied by 2^96). |
| **slot0** | The pool's "slot 0" storage struct containing sqrtPriceX96, tick, and observation state. |
| **Liquidity** | The virtual liquidity (L) in the constant product formula L^2 = x * y, adjusted for the price range. |
| **Q96** | 2^96, the fixed-point scaling factor for sqrtPriceX96. |
| **NonfungiblePositionManager** | Uniswap V3 contract that manages NFT positions — each position is an ERC-721 token. |
| **Concentrated Liquidity** | Liquidity provision within a specific price range, earning fees only when the price is in range. |
| **Auto-discovery** | Scanning the wallet's ERC-721 tokens to find active position NFTs. |
| **Pre-mint swap (_optimize_ratio)** | Single swap of 95% of the excess token before minting, correcting the wallet ratio to match the pool's optimal ratio. |
| **Post-mint top-up** | Swapping any leftover tokens after minting and adding them via increaseLiquidity. |

---

*Documented from the live `liquidity-bot-hyper` codebase and `liqbot.log` execution traces on HyperEVM (Chain ID 999). Last updated: June 16, 2026.*
