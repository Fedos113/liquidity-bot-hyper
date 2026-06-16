#!/usr/bin/env python3
"""
backtest.py -- Simulate the HYPE/USDC liquidity bot over historical price data.

Simulates every price point at hourly granularity with per-step fee compounding.

Usage:
  python backtest.py --initial-hype 10 --initial-usdc 200
  python backtest.py --csv prices.csv
  python backtest.py --days 300 --output results.csv
"""

import argparse
import csv
import logging
import math
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, ".")

from src.math_utils import (
    calculate_bounds,
    position_value_usd,
    tick_to_price,
)
from src.constants import Q96, TICK_SPACINGS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backtest")

# -- Bot constants (mirrors src/config.py defaults) -------------------------

BOT_LOWER_BOUND_PCT = 0.96
BOT_UPPER_BOUND_PCT = 1.06
BOT_FEE_TIER = 3000
BOT_TICK_SPACING = TICK_SPACINGS[BOT_FEE_TIER]
BOT_HYPE_DROP_THRESHOLD = 0.98
BOT_SECONDARY_INTERVAL = 600
BOT_SLEEP_INTERVAL = 3600
BOT_HYPE_DECIMALS = 18
BOT_USDC_DECIMALS = 6
BOT_SWAP_FEE = 0.003
BOT_WALLET_MIN = 0.2

# -- Helpers ----------------------------------------------------------------

Q128 = 1 << 128


def sqrt_price_x96_from_price(price: float) -> int:
    """Compute sqrt_price_x96 for a USDC/HYPE price. (token0=HYPE, token1=USDC)"""
    raw_price = price * (10 ** (BOT_USDC_DECIMALS - BOT_HYPE_DECIMALS))
    return int(math.sqrt(raw_price) * Q96)


def price_to_tick(price: float) -> int:
    """Convert USDC/HYPE price to un-snapped Uniswap tick."""
    p = price * (10 ** (BOT_USDC_DECIMALS - BOT_HYPE_DECIMALS))
    return int(math.log(p) / math.log(1.0001))


def position_amounts(
    liquidity: int, tick_lower: int, tick_upper: int, sqrt_p: int,
) -> tuple[int, int]:
    """Return (raw_amount0, raw_amount1) in position at a given sqrt price."""
    sp = sqrt_p / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)
    if sp <= spl:
        return int(liquidity * (spu - spl) / (spl * spu)), 0
    elif sp >= spu:
        return 0, int(liquidity * (spu - spl))
    a0 = int(liquidity * (spu - sp) / (sp * spu))
    a1 = int(liquidity * (sp - spl))
    return a0, a1


def liquidity_from_amounts(
    amount0_raw: int, amount1_raw: int,
    sqrt_p: int, tick_lower: int, tick_upper: int,
) -> int:
    """Max deployable liquidity from raw token amounts."""
    sp = sqrt_p / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)
    if sp <= spl:
        return int(amount0_raw * spl * spu / (spu - spl))
    elif sp >= spu:
        return int(amount1_raw / (spu - spl))
    l0 = int(amount0_raw * sp * spu / (spu - sp))
    l1 = int(amount1_raw / (sp - spl))
    return min(l0, l1)


def simulate_swap(amount_human: float, token_in_is_hype: bool, price: float) -> float:
    """0.3% fee swap. Returns amount_out in human units."""
    after_fee = amount_human * (1.0 - BOT_SWAP_FEE)
    return after_fee * price if token_in_is_hype else after_fee / price


# -- Data fetching ----------------------------------------------------------

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
HYPE_COIN_ID = "hyperliquid"
CHUNK_DAYS = 90  # max range for hourly data from CoinGecko


def fetch_coin_gecko(days: int = 300) -> list[tuple[int, float]]:
    """Fetch hourly price data by splitting into 90-day chunks.

    Returns [(unix_ts, price), ...] with hourly granularity over the full range.
    """
    import requests

    now = int(time.time())
    chunk_sec = CHUNK_DAYS * 86400
    all_prices: list[tuple[int, float]] = []
    end_ts = now
    remaining_days = days

    while remaining_days > 0:
        chunk = min(remaining_days, CHUNK_DAYS)
        start_ts = end_ts - chunk * 86400

        url = f"{COINGECKO_BASE}/coins/{HYPE_COIN_ID}/market_chart/range"
        params = {"vs_currency": "usd", "from": start_ts, "to": end_ts}
        logger.info(f"Fetching chunk {datetime.fromtimestamp(start_ts).date()} -> "
                     f"{datetime.fromtimestamp(end_ts).date()}")

        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("prices", [])
        all_prices.extend([(int(e[0]) // 1000, e[1]) for e in raw])

        remaining_days -= chunk
        end_ts = start_ts
        if remaining_days > 0:
            time.sleep(2)

    all_prices.sort(key=lambda x: x[0])
    seen: set[int] = set()
    deduped: list[tuple[int, float]] = []
    for ts, p in all_prices:
        if ts not in seen:
            seen.add(ts)
            deduped.append((ts, p))
    return deduped


def load_csv(path: str) -> list[tuple[int, float]]:
    """Load CSV with columns: timestamp,price (or timestamp,open,high,low,close)."""
    rows: list[tuple[int, float]] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        ncols = len(headers) if headers else 0
        for row in reader:
            if len(row) < 2:
                continue
            ts = int(row[0])
            price = float(row[4]) if ncols >= 5 else float(row[1])
            rows.append((ts, price))
    return rows


# -- Position state ---------------------------------------------------------

class Position:
    __slots__ = ("liquidity", "tick_lower", "tick_upper",
                  "fee_growth_0_last", "fee_growth_1_last",
                  "tokens_owed_0", "tokens_owed_1")

    def __init__(self, liquidity: int, tl: int, tu: int):
        self.liquidity = liquidity
        self.tick_lower = tl
        self.tick_upper = tu
        self.fee_growth_0_last = 0
        self.fee_growth_1_last = 0
        self.tokens_owed_0 = 0
        self.tokens_owed_1 = 0


# -- Backtest engine --------------------------------------------------------

class Backtest:
    def __init__(self, initial_hype: float, initial_usdc: float,
                 hourly_fee_rate: float):
        self.hype = initial_hype
        self.usdc = initial_usdc
        self.initial_hype = initial_hype
        self.initial_usdc = initial_usdc
        self.hourly_fee_rate = hourly_fee_rate

        self.pos: Optional[Position] = None
        self.token0_is_hype = True
        self.invert = not self.token0_is_hype

        # Fee tracking
        self._fee_growth_global_0 = 0
        self._fee_growth_global_1 = 0
        self.fees_collected_usd = 0.0

        # Stats
        self.rebalances = 0
        self.adds = 0
        self.swaps = 0
        self.secondary_triggers = 0
        self.history: list[tuple[int, float, float, float, float, float]] = []

    # -- helpers -----------------------------------------------------------

    def _wallet_val(self, price: float) -> float:
        return self.hype * price + self.usdc

    def _pos_val(self, price: float, sqrt_p: int) -> float:
        if self.pos is None:
            return 0.0
        return position_value_usd(
            self.pos.liquidity, self.pos.tick_lower, self.pos.tick_upper,
            sqrt_p, self.token0_is_hype, price,
            BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS,
        )

    def _total_val(self, price: float, sqrt_p: int) -> float:
        return self._wallet_val(price) + self._pos_val(price, sqrt_p)

    # -- fee accrual with compounding --------------------------------------

    def _compound_fees(self, price: float):
        """Accrue fees for the current step and compound into the position."""
        if self.pos is None or self.pos.liquidity == 0:
            return

        tick = price_to_tick(price)
        in_range = self.pos.tick_lower <= tick < self.pos.tick_upper
        if not in_range:
            return

        sqrt_p = sqrt_price_x96_from_price(price)
        pos_val = self._pos_val(price, sqrt_p)
        fee_usd = pos_val * self.hourly_fee_rate
        if fee_usd <= 0:
            return

        dec0, dec1 = BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS
        spl = math.sqrt(1.0001 ** self.pos.tick_lower)
        spu = math.sqrt(1.0001 ** self.pos.tick_upper)
        sp = sqrt_p / Q96

        val_per_liq_0 = (spu - sp) / (sp * spu) * price / (10 ** dec0)
        val_per_liq_1 = (sp - spl) / (10 ** dec1)
        total_vpl = val_per_liq_0 + val_per_liq_1
        if total_vpl <= 0:
            return

        # Split fee USD into token amounts
        fee_tok0_usd = fee_usd * val_per_liq_0 / total_vpl
        fee_tok1_usd = fee_usd * val_per_liq_1 / total_vpl
        fee0_raw = int(fee_tok0_usd / price * (10 ** dec0)) if fee_tok0_usd > 0 else 0
        fee1_raw = int(fee_tok1_usd * (10 ** dec1)) if fee_tok1_usd > 0 else 0

        if fee0_raw <= 0 and fee1_raw <= 0:
            return

        # Calculate how much new liquidity the fee tokens provide
        add_liq = liquidity_from_amounts(
            fee0_raw, fee1_raw, sqrt_p,
            self.pos.tick_lower, self.pos.tick_upper,
        )
        if add_liq <= 0:
            return

        # Compound: add liquidity to the position (fees are now part of the position)
        self.pos.liquidity += add_liq
        self.fees_collected_usd += fee_usd

        # Sync the fee-growth trackers so these fees are NOT collected again on removal
        Q128 = 1 << 128
        dg0 = int(fee0_raw * Q128 / (self.pos.liquidity - add_liq)) if (fee0_raw > 0 and self.pos.liquidity > add_liq) else 0
        dg1 = int(fee1_raw * Q128 / (self.pos.liquidity - add_liq)) if (fee1_raw > 0 and self.pos.liquidity > add_liq) else 0
        self._fee_growth_global_0 += dg0
        self._fee_growth_global_1 += dg1
        self.pos.fee_growth_0_last = self._fee_growth_global_0
        self.pos.fee_growth_1_last = self._fee_growth_global_1

    # -- core actions ------------------------------------------------------

    def _optimize_ratio(self, price: float, tick_lower: Optional[int] = None, tick_upper: Optional[int] = None):
        """Mirror bot's _optimize_ratio.

        Uses self.pos ticks if available, otherwise explicit tick_lower/tick_upper.
        """
        if self.pos is not None:
            tl = self.pos.tick_lower
            tu = self.pos.tick_upper
        elif tick_lower is not None and tick_upper is not None:
            tl, tu = tick_lower, tick_upper
        else:
            return

        sqrt_p = sqrt_price_x96_from_price(price)
        sp = sqrt_p / Q96
        p = sp * sp
        spl = math.sqrt(1.0001 ** tl)
        spu = math.sqrt(1.0001 ** tu)
        target_ratio = sp * spu * (sp - spl) / (spu - sp)

        if self.token0_is_hype:
            raw0 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))
            raw1 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
        else:
            raw0 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
            raw1 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))

        if raw0 <= 0 or raw1 <= 0 or target_ratio <= 0:
            return

        current_ratio = raw1 / raw0
        deviation = abs(math.log(current_ratio / target_ratio))
        if deviation < 0.01:
            return

        if current_ratio > target_ratio:
            num = raw1 - int(target_ratio * raw0)
            denom = 1.0 + target_ratio / p
            swap_raw = max(0, int(num / denom * 0.99))
            if swap_raw >= 1000:
                dec_swap = BOT_USDC_DECIMALS if not self.token0_is_hype else BOT_HYPE_DECIMALS
                amt = swap_raw / (10 ** dec_swap)
                if self.token0_is_hype:
                    if amt > self.usdc:
                        amt = self.usdc * 0.99
                    out = simulate_swap(amt, False, price)
                    self.usdc -= amt
                    self.hype += out
                else:
                    if amt > self.hype:
                        amt = self.hype * 0.99
                    out = simulate_swap(amt, True, price)
                    self.hype -= amt
                    self.usdc += out
                if amt >= 0.001:
                    self.swaps += 1
        else:
            num = int(target_ratio * raw0) - raw1
            denom = p + target_ratio
            swap_raw = max(0, int(num / denom * 0.99))
            if swap_raw >= 1000:
                dec_swap = BOT_HYPE_DECIMALS if self.token0_is_hype else BOT_USDC_DECIMALS
                amt = swap_raw / (10 ** dec_swap)
                if self.token0_is_hype:
                    if amt > self.hype:
                        amt = self.hype * 0.99
                    out = simulate_swap(amt, True, price)
                    self.hype -= amt
                    self.usdc += out
                else:
                    if amt > self.usdc:
                        amt = self.usdc * 0.99
                    out = simulate_swap(amt, False, price)
                    self.usdc -= amt
                    self.hype += out
                if amt >= 0.001:
                    self.swaps += 1

    def _create_position(self, price: float):
        """Mirror bot's create_position."""
        if self.hype <= 0 and self.usdc <= 0:
            return

        snapped_lower, snapped_upper, _, _ = calculate_bounds(
            price, BOT_LOWER_BOUND_PCT, BOT_UPPER_BOUND_PCT,
            BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS, BOT_TICK_SPACING, self.invert,
        )

        self._optimize_ratio(price, snapped_lower, snapped_upper)

        sqrt_p = sqrt_price_x96_from_price(price)
        if self.token0_is_hype:
            raw0 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))
            raw1 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
        else:
            raw0 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
            raw1 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))

        liq = liquidity_from_amounts(raw0, raw1, sqrt_p, snapped_lower, snapped_upper)
        if liq <= 0:
            return

        a0, a1 = position_amounts(liq, snapped_lower, snapped_upper, sqrt_p)
        if self.token0_is_hype:
            self.hype -= a0 / (10 ** BOT_HYPE_DECIMALS)
            self.usdc -= a1 / (10 ** BOT_USDC_DECIMALS)
        else:
            self.usdc -= a0 / (10 ** BOT_USDC_DECIMALS)
            self.hype -= a1 / (10 ** BOT_HYPE_DECIMALS)

        self.pos = Position(liq, snapped_lower, snapped_upper)

    def _remove_position(self, price: float):
        """Remove all liquidity and return tokens + fees to wallet."""
        if self.pos is None:
            return

        sqrt_p = sqrt_price_x96_from_price(price)
        a0, a1 = position_amounts(
            self.pos.liquidity, self.pos.tick_lower, self.pos.tick_upper, sqrt_p,
        )

        # Collect outstanding fees
        d0 = self._fee_growth_global_0 - self.pos.fee_growth_0_last
        d1 = self._fee_growth_global_1 - self.pos.fee_growth_1_last
        fee0 = self.pos.tokens_owed_0 + (self.pos.liquidity * d0) // Q128
        fee1 = self.pos.tokens_owed_1 + (self.pos.liquidity * d1) // Q128
        a0 += fee0
        a1 += fee1

        if self.token0_is_hype:
            self.hype += a0 / (10 ** BOT_HYPE_DECIMALS)
            self.usdc += a1 / (10 ** BOT_USDC_DECIMALS)
        else:
            self.usdc += a0 / (10 ** BOT_USDC_DECIMALS)
            self.hype += a1 / (10 ** BOT_HYPE_DECIMALS)

        fee_val = (fee0 / (10 ** BOT_HYPE_DECIMALS) * price if self.token0_is_hype
                   else fee1 / (10 ** BOT_HYPE_DECIMALS) * price)
        fee_val += (fee1 / (10 ** BOT_USDC_DECIMALS) if self.token0_is_hype
                    else fee0 / (10 ** BOT_USDC_DECIMALS))
        if fee_val > 0.001:
            self.fees_collected_usd += fee_val

        self.pos = None

    def _add_to_position(self, price: float):
        """Mirror bot's add_to_position."""
        if self.pos is None:
            return
        if self._wallet_val(price) < BOT_WALLET_MIN:
            return

        self._optimize_ratio(price)

        sqrt_p = sqrt_price_x96_from_price(price)
        if self.token0_is_hype:
            raw0 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))
            raw1 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
        else:
            raw0 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
            raw1 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))

        add_liq = liquidity_from_amounts(
            raw0, raw1, sqrt_p, self.pos.tick_lower, self.pos.tick_upper,
        )
        if add_liq <= 0:
            return

        a0, a1 = position_amounts(add_liq, self.pos.tick_lower, self.pos.tick_upper, sqrt_p)
        if self.token0_is_hype:
            self.hype -= a0 / (10 ** BOT_HYPE_DECIMALS)
            self.usdc -= a1 / (10 ** BOT_USDC_DECIMALS)
        else:
            self.usdc -= a0 / (10 ** BOT_USDC_DECIMALS)
            self.hype -= a1 / (10 ** BOT_HYPE_DECIMALS)

        self.pos.liquidity += add_liq
        self.adds += 1

    # -- main simulation ---------------------------------------------------

    def run(self, prices: list[tuple[int, float]]) -> dict[str, Any]:
        if not prices:
            raise ValueError("No price data")

        initial_price = prices[0][1]
        initial_total = self._wallet_val(initial_price)
        last_cycle_ts: Optional[int] = None
        last_secondary_ts: Optional[int] = None

        total_steps = len(prices)
        log_every = max(1, total_steps // 100)  # log ~100 lines

        for idx, (ts, price) in enumerate(prices):
            if idx == 0:
                sqrt_p = sqrt_price_x96_from_price(price)
                self.history.append((ts, price, self.hype, self.usdc,
                                     self.pos.liquidity if self.pos else 0,
                                     self._total_val(price, sqrt_p)))
                continue

            # 1. Fee compounding (happens every step when in range)
            self._compound_fees(price)

            tick = price_to_tick(price)

            # 2. Secondary cycle (anti-IL)
            if (last_secondary_ts is None
                or (ts - last_secondary_ts) >= BOT_SECONDARY_INTERVAL):
                last_secondary_ts = ts
                if self.pos is not None:
                    lower_p = tick_to_price(
                        self.pos.tick_lower, BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS, self.invert,
                    )
                    trigger = lower_p * BOT_HYPE_DROP_THRESHOLD
                    if price < trigger:
                        logger.info(
                            f"[{datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')}] "
                            f"ANTI-IL price=${price:.4f} < ${trigger:.4f}"
                        )
                        self._remove_position(price)
                        if self.hype > 0:
                            out = simulate_swap(self.hype, True, price)
                            logger.info(f"  Swapped {self.hype:.4f} HYPE -> {out:.4f} USDC")
                            self.usdc += out
                            self.hype = 0
                            self.swaps += 1
                        self.secondary_triggers += 1

            # 3. Main cycle
            if (last_cycle_ts is None
                or (ts - last_cycle_ts) >= BOT_SLEEP_INTERVAL):
                last_cycle_ts = ts

                if self.pos is not None:
                    in_range = self.pos.tick_lower <= tick < self.pos.tick_upper

                    if not in_range:
                        if idx % log_every == 0:
                            logger.info(
                                f"[{datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')}] "
                                f"Out of range (tick={tick}), rebalancing"
                            )
                        self._remove_position(price)
                        self.rebalances += 1
                        self._create_position(price)
                    else:
                        self._add_to_position(price)

                        sqrt_p = sqrt_price_x96_from_price(price)
                        pos_val = self._pos_val(price, sqrt_p)
                        if pos_val <= 1.0:
                            self._remove_position(price)
                            self._create_position(price)
                else:
                    self._create_position(price)

            # Record every step for full granularity
            sqrt_p = sqrt_price_x96_from_price(price)
            self.history.append((ts, price, self.hype, self.usdc,
                                 self.pos.liquidity if self.pos else 0,
                                 self._total_val(price, sqrt_p)))

        # Final valuation: close any open position
        final_price = prices[-1][1]
        if self.pos:
            self._remove_position(final_price)
        final_total = self._wallet_val(final_price)

        hodl_hype = initial_total / initial_price
        hodl_val = hodl_hype * final_price

        return {
            "initial_hype": self.initial_hype,
            "initial_usdc": self.initial_usdc,
            "initial_total": initial_total,
            "initial_price": initial_price,
            "final_price": final_price,
            "final_hype": self.hype,
            "final_usdc": self.usdc,
            "final_total": final_total,
            "hodl_total": hodl_val,
            "return_pct": (final_total / initial_total - 1) * 100,
            "hodl_return_pct": (hodl_val / initial_total - 1) * 100,
            "outperformance_pct": (final_total / hodl_val - 1) * 100,
            "fees_earned": self.fees_collected_usd,
            "rebalances": self.rebalances,
            "adds": self.adds,
            "swaps": self.swaps,
            "secondary_triggers": self.secondary_triggers,
            "steps": len(self.history),
            "history": self.history,
        }


# -- CLI --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HYPE/USDC Liquidity Bot Backtest")
    parser.add_argument("--initial-hype", type=float, default=10.0)
    parser.add_argument("--initial-usdc", type=float, default=200.0)
    parser.add_argument("--hourly-fee-rate", type=float, default=0.01,
                        help="Hourly fee as fraction of position value (0.01 = 1%%/hr)")
    parser.add_argument("--csv", type=str, default=None,
                        help="CSV file with price data (timestamp,close or OHLC)")
    parser.add_argument("--days", type=int, default=300,
                        help="Days of CoinGecko data (fetched in hourly chunks)")
    parser.add_argument("--output", type=str, default=None,
                        help="Save full step-by-step history to CSV")
    args = parser.parse_args()

    if args.csv:
        prices = load_csv(args.csv)
    else:
        prices = fetch_coin_gecko(args.days)

    if not prices:
        logger.error("No price data loaded")
        sys.exit(1)

    logger.info(f"Loaded {len(prices)} price points, "
                f"range: {datetime.fromtimestamp(prices[0][0]).date()} -> "
                f"{datetime.fromtimestamp(prices[-1][0]).date()}")

    bt = Backtest(args.initial_hype, args.initial_usdc, args.hourly_fee_rate)
    result = bt.run(prices)

    sep = "=" * 60
    print()
    print(sep)
    print("  BACKTEST RESULTS")
    print(sep)
    print(f"  Data points:      {result['steps']}")
    print(f"  Initial capital:  {result['initial_hype']:.4f} HYPE + "
          f"{result['initial_usdc']:.2f} USDC = ${result['initial_total']:.2f}")
    print(f"  Initial price:    ${result['initial_price']:.4f}")
    print(f"  Final price:      ${result['final_price']:.4f}")
    print(f"  Price change:     {result['final_price']/result['initial_price']*100-100:+.2f}%")
    print(f"  Hourly fee rate:  {args.hourly_fee_rate*100:.2f}%/hr (compounds every step)")
    print()
    print(f"  -- Portfolio --")
    print(f"  Final HYPE:       {result['final_hype']:.4f}")
    print(f"  Final USDC:       {result['final_usdc']:.2f}")
    print(f"  Final total:      ${result['final_total']:.2f}")
    print(f"  Return:           {result['return_pct']:+.2f}%")
    print()
    print(f"  -- Buy & Hold --")
    print(f"  HODL value:       ${result['hodl_total']:.2f}")
    print(f"  HODL return:      {result['hodl_return_pct']:+.2f}%")
    print(f"  Outperformance:   {result['outperformance_pct']:+.2f}%")
    print()
    print(f"  -- Activity --")
    print(f"  Rebalances:       {result['rebalances']}")
    print(f"  Adds:             {result['adds']}")
    print(f"  Swaps:            {result['swaps']}")
    print(f"  Anti-IL triggers: {result['secondary_triggers']}")
    print(f"  Fees earned:      ${result['fees_earned']:.2f}")
    print(sep)

    if args.output:
        with open(args.output, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "price", "hype_bal", "usdc_bal",
                         "position_liquidity", "total_value_usd"])
            for row in result["history"]:
                w.writerow(row)
        logger.info(f"Full {result['steps']}-row history saved to {args.output}")


if __name__ == "__main__":
    main()
