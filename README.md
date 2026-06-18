
# Liquidity Bot for Hyperliquid (HYPE/USDC)

Automated concentrated liquidity provision bot for HyperEVM. Manages a Uniswap V3-style HYPE/USDC position with automatic rebalancing, fee compounding, and anti-IL protection.

---

## ⚠️ Disclaimer

This bot interacts with **real funds** on the blockchain. **Use at your own risk.**
- Always test with `DRY_RUN=true` before deploying real capital
- Use a dedicated wallet with limited funds
- Never share your `.env` file or private keys
- Monitor `liqbot.log` regularly

---

## Quick Start

### 1. Clone and Install

```bash
git clone https://github.com/Fedos113/liquidity-bot-hyper.git
cd liquidity-bot-hyper
```

**Create virtual environment:**

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
```

```bash
python -m venv venv
venv\Scripts\activate     # Windows PowerShell
```

**Install dependencies:**

```bash
pip install -r requirements.txt
```

---

### 2. Create Configuration

```bash
cp .env.example .env
```

Edit `.env` with your details (see [Configuration Guide](#configuration-guide) below).

**Minimal working `.env`:**

```bash
RPC_URL=https://rpc.hyperliquid.xyz/evm
PRIVATE_KEY=0xabc123...your_private_key
WALLET_ADDRESS=0xabc123...your_wallet
POOL_ADDRESS=0x6c9A33E3b592C0d65B3Ba59355d5Be0d38259285
POSITION_MANAGER_ADDRESS=0xeaD19AE861c29bBb2101E834922B2FEee69B9091
HYPE_ADDRESS=0x5555555555555555555555555555555555555555
USDC_ADDRESS=0xb88339cb7199b77e23db6e890353e22632ba630f
DRY_RUN=true
```

---

### 3. Run the Bot

```bash
python main.py
```

**Override dry-run mode at runtime:**

```bash
python main.py --no-dry-run
```

**Override log level:**

```bash
python main.py --log-level DEBUG
```

**Combine both:**

```bash
python main.py --no-dry-run --log-level DEBUG
```

---

## Configuration Guide

### `.env.example` Sections

#### Required

| Variable | Description |
|---|---|
| `RPC_URL` | HyperEVM RPC endpoint (fallback) |
| `PRIVATE_KEY` | Wallet private key (0x-prefixed) |
| `WALLET_ADDRESS` | Wallet address (0x-prefixed) |
| `POOL_ADDRESS` | HYPE/USDC Uniswap pool |
| `POSITION_MANAGER_ADDRESS` | Nonfungible position manager |
| `HYPE_ADDRESS` | wHYPE token contract |
| `USDC_ADDRESS` | USDC token contract |

#### RPC Providers (provider rotation)

| Variable | What to put | How the bot uses it |
|---|---|---|
| `HYPE_RPC_API_KEY` | API key only | Constructs `https://evmrpc-eu.hyperpc.app/<KEY>?apikey=<KEY>` |
| `CHAINSTACK_ENDPOINT` | Full HTTPS URL | Used as-is (e.g. `https://.../evm` or `.../nanoreth`) |
| `ALCHEMY_API_KEY` | API key only | Constructs `https://hyperliquid-mainnet.g.alchemy.com/v2/<KEY>` |
| `DRPC_API_KEY` | API key only | Constructs `https://lb.drpc.live/hyperliquid/<KEY>` |

The bot auto-detects which keys are provided and builds a rotation chain: **HypeRPC → Chainstack → Alchemy → dRPC → HyperEVM fallback**. If a provider's quota is exceeded, it is permanently disabled for the session. On any RPC error, the bot immediately switches to the next active provider.

#### Optional

| Variable | Default | Description |
|---|---|---|
| `TOKEN_ID` | `0` | 0 = auto-discover first active position |
| `DRY_RUN` | `true` | Set `false` to execute real transactions |
| `LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |

#### Bot Parameters

| Variable | Default | Description |
|---|---|---|
| `LOWER_BOUND_PCT` | `0.99` | Lower bound as % of current price |
| `UPPER_BOUND_PCT` | `1.01` | Upper bound as % of current price |
| `SLEEP_INTERVAL` | `3600` | Main cycle interval (seconds) |
| `TX_INTER_SLEEP` | `1` | Pause between consecutive transactions |
| `SLIPPAGE_TOLERANCE` | `0.005` | Max slippage (0.5%) |
| `FEE_TIER` | `500` | Pool fee tier (500 = 0.05%) |
| `FEE_COMPOUND_THRESHOLD_USD` | `5.0` | Auto-compound if fees exceed this |
| `MIN_WALLET_USD` | `0.2` | Minimum wallet value to add funds |
| `UNTOUCHABLE_HYPE` | `0.02` | Native HYPE reserved for gas fees |
| `PRIORITY_FEE_MULTIPLIER` | `1.5` | Gas price multiplier for urgent operations |

#### Secondary Cycle (Price Watcher)

| Variable | Default | Description |
|---|---|---|
| `SECONDARY_INNER` | `300` | Interval between price checks |

#### Downward Protection (Anti-IL)

| Variable | Default | Description |
|---|---|---|
| `HYPE_DROP_THRESHOLD` | `0.98` | Close if price drops below threshold |
| `DOWNWARD_COOLDOWN` | `3600` | Cooldown before next main cycle |
| `DOWNWARD_INNER_CYCLE_INTERVAL` | `300` | Retry interval for close+swap |

#### Upward Protection (Surge)

| Variable | Default | Description |
|---|---|---|
| `HYPE_UPPER_THRESHOLD` | `1.02` | Trigger if price surges above threshold |
| `UPWARD_INNER_CYCLE_INTERVAL` | `180` | Retry interval for close+swap |
| `UPWARD_DELAY` | `60` | Wait before main cycle after surge |

---

## How the Bot Works

### Slippage Protection

Non-priority swaps (ratio optimization during rebalance/mint) use `amountOutMinimum = expected_output × (1 − SLIPPAGE_TOLERANCE)`, estimated from the pool's current `slot0` state (2 RPC calls). If the swap reverts due to slippage, it retries after 10s with a fresh price estimate.

Priority swaps (emergency close+swap in inner cycles) bypass slippage protection for speed — execution certainty is prioritized over best price.

### Main Cycle (every `SLEEP_INTERVAL` seconds)

1. **Connect & cache** — pick active RPC provider, create contracts, cache pool static data (token0, token1, fee, tickSpacing — 5 RPC calls, done **once**)
2. **Read state** — batch `slot0` + both token balances in 1 multicall (or 3 sequential calls if multicall3 unavailable)
3. **Auto-discover position** — scan wallet NFTs for matching pool with active liquidity
4. **Evaluate position** — compute position value, wallet value, check if price is in range
5. **Close & recreate** — if out of bounds or position value <= $1: use `_close_pool_3rpc` (3 RPCs on 3 providers: HypeRPC → Chainstack → HypeRPC), then mint new position
6. **Add funds** — if wallet > `MIN_WALLET_USD`, add to position
7. **Compound fees** — if unclaimed fees > `FEE_COMPOUND_THRESHOLD_USD`, collect and reinvest
8. **Sleep** — listen for skip/upper-threshold wake during sleep

### Secondary Cycle (every `SECONDARY_INNER` seconds)

A single merged watcher thread replacing the old `_upward_cycle` and `_downward_cycle` threads:
- If `pool_opened = True`: fetch price via multicall
- If price > `upper_bound × HYPE_UPPER_THRESHOLD`: trigger upward inner
- If price < `lower_bound × HYPE_DROP_THRESHOLD`: trigger downward inner

### Inner Cycles (upward / downward)

Triggered by price threshold breaches:
1. Close position using **3 different RPC slots**: collect_fees (HypeRPC) → remove_liquidity (Chainstack) → collect_fees (HypeRPC)
2. Swap all proceeds to one side (HYPE for upward, USDC for downward) using **HypeRPC** (or next active)
3. Set `pool_opened = False`, notify main cycle

### 3-RPC Rotation Pattern

Consecutive on-chain writes use different RPC providers to distribute load:

| Step | Provider | Action |
|---|---|---|
| 1 | HypeRPC (slot 0) | Multicall read + collect_fees |
| 2 | Chainstack (slot 1) | remove_liquidity |
| 3 | HypeRPC (slot 0) | collect_fees again |

Swap operations always try HypeRPC first, then fall back: Chainstack → Alchemy → dRPC → HyperEVM.

---

## RPC Provider Priority

On startup, `main.py` tests every configured provider and logs status:

```
RPC providers: 4/5 active
  HypeRPC: active, connected
  Chainstack: active, connected
  Alchemy: inactive, disconnected
  dRPC: active, connected
  HyperEVM (fallback): active, connected
```

If a provider's **quota is exceeded** during runtime, it is flagged inactive for the rest of the session. All RPC errors trigger an immediate switch to the next active provider.

---

## Commands Reference

### Run bot

```bash
python main.py
```

### Run with real transactions

```bash
python main.py --no-dry-run
```

### Run with debug logging

```bash
python main.py --log-level DEBUG
```

### Skip remaining sleep

While bot is sleeping, type `skip` + Enter to jump to the next cycle.

### Check logs

```bash
tail -f liqbot.log
```

### Backtest

```bash
python backtest.py --initial-hype 10 --initial-usdc 200 --days 90
```

```bash
python backtest.py --csv prices.csv --output backtest_results.csv
```

---

## Potential Errors & Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `No active RPC providers available` | All API keys invalid or quota exhausted | Check your API keys, verify endpoints |
| `Missing required .env variables` | `.env` not configured | Copy `.env.example` to `.env` and fill in values |
| `Transaction reverted` | Slippage too tight or insufficient balance | Increase `SLIPPAGE_TOLERANCE`, check wallet |
| Bot stuck at "Sleeping for Xs" | Position value too small or no transaction needed | Wait for next cycle or type `skip` |
| High gas fees | `MAX_TX_FEE_USD` exceeded | Increase `MAX_TX_FEE_USD` in `position_manager.py` |
| Fee not compounding | Collected fees below threshold | Lower `FEE_COMPOUND_THRESHOLD_USD` |
| Position keeps recreating | Price fluctuating around bounds | Widen `LOWER_BOUND_PCT` / `UPPER_BOUND_PCT` |

### RPC Error Recovery

The bot automatically handles:
- **Rate limiting** (`-32005`): waits 60s, retries with backoff
- **Connection timeout**: cycles to next provider immediately
- **Quota exceeded**: disables provider for the session
- **All providers down**: raises `ConnectionError`, retries next cycle

---

## Architecture Overview

```
                   ┌─────────────────────────────────┐
                   │           main.py                │
                   │  test_all() → run_bot()          │
                   └──────────┬──────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
    ┌──────────────────┐ ┌──────────┐ ┌──────────────┐
    │  _secondary_cycle│ │Main Cycle│ │Inner Cycles  │
    │  (merged watcher)│ │ (tx_lock)│ │(up/down)     │
    └────────┬─────────┘ └────┬─────┘ └──────┬───────┘
             │                │               │
             ▼                ▼               ▼
    ┌─────────────────────────────────────────────┐
    │              RPCManager                      │
    │  HypeRPC → Chainstack → Alchemy → dRPC → EVM│
    └─────────────────────────────────────────────┘
```

---

## Key Design Decisions

- **3-slot RPC rotation** for consecutive writes — distributes load, avoids rate limits
- **Pool cache** — token0/token1/fee/tickSpacing fetched once, reused forever
- **Multicall3 batching** — `slot0` + 2× `balanceOf` in 1 RPC call
- **Merged secondary cycle** — one thread instead of two, 50% fewer watcher RPCs
- **`TX_INTER_SLEEP`** — prevents nonce collisions between consecutive transactions
- **`_handle_rpc_error`** with provider cycling — immediate recovery without waiting for retry timeout
- **Quota tracking** — permanently disables exhausted providers mid-session

---

## Security Best Practices

- Never commit `.env` (excluded via `.gitignore`)
- Use a **dedicated wallet** with only the funds you intend to provide
- Always start with `DRY_RUN=true`
- Monitor `liqbot.log` for unexpected behavior
- Keep your `PRIVATE_KEY` secure — it signs every transaction

---

## License

Proprietary. See [LICENSE](LICENSE) for details.
