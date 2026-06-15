# liqbot2 — Web Dashboard

## Stack
- **Backend**: Python + FastAPI + uvicorn, single `main.py`
- **DB**: SQLite via `aiosqlite`, single file `data/history.db`
- **Frontend**: Single HTML file served by FastAPI, vanilla JS + Chart.js (CDN)
- **Docker**: `python:3.12-slim`, single container, port 8000

## API Endpoints

### `POST /refresh`
Fetches live data from chain (web3 calls), returns full dashboard payload.

**Computation per metric:**
| Metric | Source | Algorithm |
|---|---|---|
| Position value | `position_manager.positions(tokenId)` → liquidity + ticks → `L * (1/√P - 1/√Pu)` or `L * (√P - √Pl)` | Single contract call |
| Bounds | `positions().tickLower/Upper` → convert to price | 2 ints, O(1) |
| Wallet address | Config constant | O(1) |
| Total tx fees | `SELECT SUM(fee_wei) FROM tx_fees` | SQL aggregate on `tx_fees` table |
| Impermanent loss | `(pos_value_now + withdrawn_fees) / hold_value - 1` where hold_value = initial_t0 * price_now + initial_t1 | See PNL section |
| PNL 24h | `snapshots` table: value at `now-86400` vs `now`; if no exact snapshot, interpolate from nearest two | Max 2 DB reads |
| PNL week | Same as 24h with `now-604800` | Max 2 DB reads |
| PNL all | Compare first snapshot token amounts vs current, valuing both at current price | 1 DB read (first row) |

**After computing metrics**, this endpoint also inserts a new snapshot:
```sql
INSERT INTO snapshots (ts, hype_bal, usdc_bal, liquidity, tick_lower, tick_upper, current_tick, portfolio_value_usd)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

### `GET /chart`
Returns `{timestamps: [...], values: [...]}` — all `portfolio_value_usd` from `snapshots` ordered by `ts`.

### `GET /`
Serves `index.html`.

## Database Schema

### `snapshots`
| Column | Type | Purpose |
|---|---|---|
| ts | INTEGER (epoch) | PK |
| hype_bal | INTEGER | wHYPE balance at snapshot |
| usdc_bal | INTEGER | USDC balance at snapshot |
| liquidity | INTEGER | Position liquidity |
| tick_lower | INTEGER | Lower tick |
| tick_upper | INTEGER | Upper tick |
| current_tick | INTEGER | Pool tick |
| portfolio_value_usd | REAL | Computed USD value |

### `tx_fees`
| Column | Type | Purpose |
|---|---|---|
| id | INTEGER PK | Auto |
| ts | INTEGER | When tx occurred |
| tx_hash | TEXT | Transaction hash |
| fee_wei | INTEGER | Gas cost in wei |
| description | TEXT | "mint", "swap", "collect", etc. |

`tx_fees` is populated by the existing bot (`src/position_manager.py:send_transaction`) via a new `insert_tx_fee` function call after each confirmed tx. This is the only bot change.

## PNL Calculation (detail)

```
withdrawn_fees_usd = SUM of (fees collected - fees reinvested) valued at historical prices
hold_value = initial_hype * hype_price_now + initial_usdc
pos_value = current position value in USD
pnl_all = (pos_value + wallet_balance + withdrawn_fees_usd) / hold_value - 1
```

- First snapshot = benchmark ("all" PNL baseline)
- 24h/week PNL: `snapshots` at `now - period` vs `now`; snapshots store `portfolio_value_usd` directly so it's `v_now / v_then - 1`

## Impermanent Loss (detail)

```
IL = pos_value / (pos_t0 * price_now / price_t0 + pos_t1) - 1
```

At initial position creation, store `pos_t0_amount = amount0_desired`, `pos_t1_amount = amount1_desired` in a `positions` table. On each refresh, compute what those amounts would be worth at current price vs what the position is actually worth (from `positions().liquidity` and tick math).

Simplified approximation used for chart: `IL = 2 * sqrt(r) / (1 + r) - 1` where `r = price_now / price_at_entry`. This is pure AMM IL formula accurate for concentrated positions when price moves within range; store `price_at_entry` in first snapshot.

## Frontend (`index.html`)

### Layout
```
┌──────────────────────────────────────────────┐
│  LIQBOT2                          [REFRESH]  │
├──────────────────────────────────────────────┤
│  Wallet: 0x1234...5678                        │
│  Position: $1,234.56    Bounds: +/- 4%       │
├──────────────────────────────────────────────┤
│  PNL: 24h +2.3%   7d -1.1%   ALL +8.7%      │
│  IL: -0.3%    Total Fees: $12.34              │
├──────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────┐ │
│  │         Portfolio Value Chart            │ │
│  │     ╱╲     ╱╲     ╱╲     ╱╲              │ │
│  │  ╱╲  ╱ ╲  ╱ ╲  ╱ ╲  ╱                  │ │
│  │ ╱  ╲╱   ╲╱   ╲╱   ╲╱                    │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

### Styling
- Dark bg `#0d1117`, cards `#161b22`, borders `#30363d`
- Accent: `#58a6ff` (blue), green PNL `#3fb950`, red PNL `#f85149`
- Font: system-ui, monospace for addresses
- Cards with subtle box-shadow and rounded `border-radius: 8px`
- Responsive: single column on mobile
- Chart: Chart.js line chart, gradient fill, no animation on load

### JS Flow
1. On page load: `fetch('/chart')` → render chart; `fetch('/refresh')` → render cards
2. On REFRESH click: `fetch('/refresh', {method: 'POST'})` → re-render cards + chart

## Docker

### `Dockerfile`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn aiosqlite web3 python-dotenv
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `requirements.txt`
```
fastapi
uvicorn
aiosqlite
web3
python-dotenv
```

### `docker-compose.yml`
```yaml
services:
  dashboard:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./.env:/app/.env
    restart: unless-stopped
```

## Bot Integration (one change)

In `send_transaction` after successful receipt, call:
```python
asyncio.run(insert_tx_fee(ts, tx_hash.hex(), receipt['gasUsed'] * tx['gasPrice'], "tx"))
```

Where `insert_tx_fee` is a new function in `db.py` shared with the dashboard. This populates `tx_fees` so Total Fees USD is accurate.

## File Structure

```
liqbot2/
├── main.py              # FastAPI app, endpoints
├── db.py                # SQLite init, insert/query helpers
├── metrics.py           # PNL, IL, position value computations
├── index.html           # Single-file frontend
├── Dockerfile
├── requirements.txt
└── docker-compose.yml
```

## Implementation Order
1. `db.py` — schema, `init_db`, `insert_snapshot`, `insert_tx_fee`, `get_snapshots`, `get_first_snapshot`, `get_tx_fees_total`
2. `metrics.py` — `compute_all(current_price, pos_data, hype_bal, usdc_bal)` → dict with all dashboard fields, reads from DB
3. `main.py` — FastAPI app, `/refresh` calls `compute_all`, `/chart` returns snapshots
4. `index.html` — dark theme, card layout, chart, refresh button
5. `Dockerfile` + `docker-compose.yml` — containerization
6. Bot integration — one-line change in `send_transaction` to write tx fees to DB
