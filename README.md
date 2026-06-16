# WETH/USDC Liquidity Bot (Base · Aerodrome Slipstream)

Automated concentrated liquidity bot for Base chain. Mints LP positions on Aerodrome Slipstream, stakes them for AERO rewards, and rebalances on price deviation.

## Setup

```bash
git clone <repo> && cd liquidity-bot-base
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env   # fill PRIVATE_KEY, WALLET_ADDRESS
python main.py
```

## Usage

| Command | Action |
|---|---|
| `python main.py` | Start bot |
| `python main.py --dry-run` | Override DRY_RUN=true |
| `python main.py --log-level DEBUG` | Verbose logging |
| `skip` + Enter | Skip sleep, start next cycle now |

## How It Works

Each cycle (every `SLEEP_INTERVAL` seconds):

1. **Check position** — auto-discover via ERC721 (checks PositionManager + Gauge for staked NFTs)
2. **Fetch pool state** — `slot0()` → sqrt price, tick, fee growth
3. **Staking** — `gauge.deposit()` after mint, `gauge.withdraw()` before remove
4. **Rebalance** — if price outside `[LOWER_BOUND_PCT, UPPER_BOUND_PCT]`: collect fees, remove liq, swap to ratio, mint new position, stake
5. **Add funds** — if wallet value > $0.20 and price in range: wrap ETH, swap to ratio, `increaseLiquidity`
6. **Compound fees** — if unclaimed fees ≥ `FEE_COMPOUND_THRESHOLD_USD`: collect → `increaseLiquidity`

## Key Config

| Var | Default | Description |
|---|---|---|
| `DRY_RUN` | `true` | Set false to send real txs |
| `LOWER_BOUND_PCT` | `0.99` | Lower bound as % of price |
| `UPPER_BOUND_PCT` | `1.01` | Upper bound as % of price |
| `FEE_TIER` | `50` | 0.05% Aerodrome Slipstream |
| `FEE_COMPOUND_THRESHOLD_USD` | `1.0` | Min fee value to compound |
| `DROP_THRESHOLD` | `0.98` | Emergency close if price drops 2% below lower bound |

## Contracts (Base)

| Contract | Address |
|---|---|
| WETH | `0x4200000000000000000000000000000000000006` |
| USDC | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` |
| Pool (WETH/USDC 0.05%) | `0x3FE04A59Ebd38cF06080a6F60a98D124eb59392A` |
| Position Manager | `0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53` |
| Swap Router | `0x698Cb2b6dd822994581fEa6eA4Fc755d1363A92F` |
| Gauge (staking) | `0xA0B61fdB9f1FB9b917Fe38b49427Fd4D87472D28` |

## Security

- `.env` is gitignored — never commit it
- Use a dedicated wallet with limited funds
- Start with `DRY_RUN=true` and check `liqbot.log`
