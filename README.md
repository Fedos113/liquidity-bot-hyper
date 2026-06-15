
# Liquidity Bot for Hyperliquid (HYPE/USDC)

An automated concentrated liquidity provision bot for HyperEVM, managing a HYPE/USDC position with automatic rebalancing. Includes a web dashboard for real-time monitoring.

## ⚠️ Disclaimer
This bot interacts with real funds on the blockchain. **Use at your own risk.** Always test with `DRY_RUN=true` before deploying real capital. Never share your `.env` file or private keys.

---

## Quick Local Setup

### 1. Clone and Install
```bash
git clone https://github.com/Fedos113/liquidity-bot-hyper.git
cd liquidity-bot-hyper
python3 -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
```

Edit `.env` with your RPC URL, private key, wallet address, and pool addresses. Key parameters:

| Variable | Default | Description |
|---|---|---|
| `TOKEN_ID` | `0` | `0` = auto-discover first active position on wallet |
| `DRY_RUN` | `true` | Set `false` to execute real transactions |
| `LOWER_BOUND_PCT` | `0.96` | Lower bound as fraction of current price |
| `UPPER_BOUND_PCT` | `1.06` | Upper bound as fraction of current price |
| `SLEEP_INTERVAL` | `3600` | Seconds between cycles (1h default) |
| `SLIPPAGE_TOLERANCE` | `0.005` | 0.5% slippage tolerance |

### 3. Start the Bot
```bash
python main.py
```

The bot auto-starts the dashboard (Docker). Open **http://localhost:8000**.

While the bot is sleeping, type **`skip` + Enter** to jump to the next cycle immediately.

### 4. Start Dashboard Standalone
```bash
docker compose -f liqbot2/docker-compose.yml up -d --build
```

---

## Web Dashboard

### Features
- **Wallet & Position Value** — HYPE/USDC split + total portfolio
- **Current Price** — live HYPE price from the pool
- **P&L** — 24h, 7d, all-time in dollars (green/red)
- **Impermanent Loss** — difference between LP value and HODL value (red = loss)
- **Total Tx Fees** — cumulative gas costs in USD (from bot transactions only)
- Manual **REFRESH** button — no auto-polling

### Architecture
```
bot (main.py) ─── saves token_id & tx fees ──┐
                                              ▼
dashboard (liqbot2/) ←─ reads shared SQLite DB ←─ chain RPC
├── main.py       FastAPI endpoints (/refresh, /, /chart)
├── db.py         SQLite schema + async helpers
├── metrics.py    Position value, IL, PNL computations
├── index.html    Dark-themed UI (Chart.js)
├── Dockerfile    python:3.12-slim
└── docker-compose.yml
```

---

## How the Bot Works

Each cycle (every `SLEEP_INTERVAL` seconds):

1. **Check position** — auto-discover via ERC721 `balanceOf`/`tokenOfOwnerByIndex`, filter for active liquidity (`liquidity > 0`). Falls back to `TOKEN_ID` from env.
2. **Fetch pool state** — `slot0` (sqrt price, tick), token balances, fee growth
3. **Collect fees** — harvest accrued HYPE/USDC from the position
4. **Decide rebalance** — compare current price to bounds; rebalance if outside
5. **Pre-mint ratio loop** — iteratively swap the excess token to match the pool's HYPE/USDC ratio within 1%
6. **Mint / increase liquidity** — create or add to the position
7. **Post-mint top-up** — swap leftover imbalance, increase liquidity again
8. **Record snapshot** — save wallet balances, position data, price to SQLite
9. **Skip support** — listens for `skip` + Enter during sleep to restart cycle

---

## Key Design Decisions
- Pre-mint iterative swap (instead of post-mint): avoids `increase_liquidity` slippage checks
- Auto-discovery scans all wallet NFTs for matching pool + active liquidity; burns are ignored
- PNL baseline skips zero-liquidity snapshots (wallet-only) when a position exists
- Dashboard reads price from a dedicated `price` column (not recomputed from tick) for correct IL math
- Tx fees in gas token (HYPE) converted to USD using live pool price

---

## Configuration

**Swap / Rebalance behavior:** The `swap_frac` is capped at `min(0.50, 10.0 / excess_usd)` to avoid overshooting. Each pre-mint swap moves only the **excess** token amount (above the target ratio), not the entire balance.

**Bounds convention:** `LOWER_BOUND_PCT=0.96` means the position's lower tick is set at 96% of the current price. The bot rebalances when the price exits this range.

---

## Security Best Practices
- Never commit your `.env` file (`.gitignore` already excludes it)
- Use a dedicated wallet with only the funds you intend to provide
- Keep your `PRIVATE_KEY` secure
- Monitor `liqbot.log` and the dashboard regularly

---

## License
Proprietary. See [LICENSE](LICENSE) for details.
