#!/usr/bin/env python3
"""
backtest.py — Simulate the HYPE/USDC liquidity bot over historical price data.

Uses the bot's own math utilities and mirrors its exact decision logic.

Usage:
  python backtest.py --initial-hype 10 --initial-usdc 200 --fee-apr 0.15
  python backtest.py --csv prices.csv
  python backtest.py --days 300
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
    get_price_from_sqrt_price,
    position_value_usd,
    tick_to_price,
    price_to_tick,
)
from src.constants import Q96, TICK_SPACINGS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backtest")

BOT_LOWER_BOUND_PCT = 0.96
BOT_UPPER_BOUND_PCT = 1.06
BOT_FEE_TIER = 3000
BOT_TICK_SPACING = TICK_SPACINGS[BOT_FEE_TIER]
BOT_SLIPPAGE = 0.005
BOT_FEE_COMPOUND_THRESHOLD = 5.0
BOT_HYPE_DROP_THRESHOLD = 0.98
BOT_SECONDARY_INTERVAL = 600
BOT_SLEEP_INTERVAL = 3600
BOT_HYPE_DECIMALS = 18
BOT_USDC_DECIMALS = 6
BOT_SWAP_FEE = 0.003
BOT_WALLET_MIN = 0.2

# ── Price data ──────────────────────────────────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
HYPE_COIN_ID = "hyperliquid"


def fetch_coin_gecko(days: int = 300) -> list[tuple[int, float]]:
    """Fetch price data from CoinGecko market_chart. Returns [(unix_ts, price), ...].

    Uses market_chart endpoint which returns one point per ~5 min for 1 day
    or daily for longer ranges. All points are used to reconstruct close prices.
    """
    import requests
    url = f"{COINGECKO_BASE}/coins/{HYPE_COIN_ID}/market_chart"
    params = {"vs_currency": "usd", "days": str(days)}
    logger.info(f"Fetching {days}d market data from CoinGecko …")
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("prices", [])
    if not raw:
        raise ValueError("No price data in CoinGecko response")
    # raw entries: [timestamp_ms, price]
    return [(int(e[0]) // 1000, e[1]) for e in raw]


def load_csv(path: str) -> list[tuple[int, float]]:
    """Load CSV with columns: timestamp, price (or timestamp,open,high,low,close)."""
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


# ── Helpers ─────────────────────────────────────────────────────────────

def sqrt_price_x96_from_price(price: float, invert: bool) -> int:
    """Compute sqrt_price_x96 for a human-readable USDC/HYPE price."""
    p = 1.0 / price if invert else price
    raw_price = p * (10 ** (BOT_USDC_DECIMALS - BOT_HYPE_DECIMALS))
    return int(math.sqrt(raw_price) * Q96)


def position_amounts(
    liquidity: int, tick_lower: int, tick_upper: int, sqrt_p: int,
) -> tuple[int, int]:
    """Return (raw_amount0, raw_amount1) locked in position at a given sqrt price."""
    sp = sqrt_p / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)
    if sp <= spl:
        return int(liquidity * (spu - spl) / (spl * spu)), 0
    elif sp >= spu:
        return 0, int(liquidity * (spu - spl))
    else:
        a0 = int(liquidity * (spu - sp) / (sp * spu))
        a1 = int(liquidity * (sp - spl))
        return a0, a1


def liquidity_from_amounts(
    amount0_raw: int, amount1_raw: int,
    sqrt_p: int, tick_lower: int, tick_upper: int,
) -> int:
    """Compute max deployable liquidity given raw token amounts."""
    sp = sqrt_p / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)
    if sp <= spl:
        return int(amount0_raw * spl * spu / (spu - spl))
    elif sp >= spu:
        return int(amount1_raw / (spu - spl))
    else:
        l0 = int(amount0_raw * sp * spu / (spu - sp))
        l1 = int(amount1_raw / (sp - spl))
        return min(l0, l1)


def simulate_swap(amount_human: float, token_in_is_hype: bool, price: float) -> float:
    """Simulate a 0.3%-fee swap. Returns amount_out in human units."""
    after_fee = amount_human * (1.0 - BOT_SWAP_FEE)
    return after_fee * price if token_in_is_hype else after_fee / price


def price_to_tick_raw(price: float, invert: bool) -> int:
    """Convert human USDC/HYPE price to Uniswap tick (not snapped to spacing)."""
    p = 1.0 / price if invert else price
    raw = p * (10 ** (BOT_USDC_DECIMALS - BOT_HYPE_DECIMALS))
    return int(math.log(raw) / math.log(1.0001))


# ── State ──────────────────────────────────────────────────────────────

class Position:
    __slots__ = ("liquidity", "tick_lower", "tick_upper",
                  "fee_growth_0_last", "fee_growth_1_last",
                  "tokens_owed_0", "tokens_owed_1",
                  "creation_tick", "creation_price")

    def __init__(self, liquidity: int, tl: int, tu: int, tick: int, price: float):
        self.liquidity = liquidity
        self.tick_lower = tl
        self.tick_upper = tu
        self.fee_growth_0_last = 0
        self.fee_growth_1_last = 0
        self.tokens_owed_0 = 0
        self.tokens_owed_1 = 0
        self.creation_tick = tick
        self.creation_price = price


class Backtest:
    def __init__(self, initial_hype: float, initial_usdc: float,
                 hourly_fee_rate: float, dry_run: bool = False):
        self.hype = initial_hype        # human units (mutable during sim)
        self.usdc = initial_usdc
        self.initial_hype = initial_hype
        self.initial_usdc = initial_usdc
        self.hourly_fee_rate = hourly_fee_rate
        self.dry_run = dry_run

        self.pos: Optional[Position] = None
        self.token0_is_hype = True     # confirmed from pool config
        self.invert = not self.token0_is_hype  # False

        # simulated pool fee growth (per second, scaled by liquidity share)
        self._fee_growth_global_0 = 0
        self._fee_growth_global_1 = 0
        self._last_fee_ts: Optional[int] = None

        # counters
        self.rebalances = 0
        self.adds = 0
        self.swaps = 0
        self.secondary_triggers = 0
        self.fees_collected_usd = 0.0
        self.history: list[tuple[int, float, float, float, float, float]] = []

    # ── fee simulation ─────────────────────────────────────────────────
    def _accrue_fees(self, ts: int, price: float):
        """Accrue fees at hourly_fee_rate of position value per hour when in range."""
        if self._last_fee_ts is None:
            self._last_fee_ts = ts
            return
        if self.pos is None or self.pos.liquidity == 0:
            self._last_fee_ts = ts
            return

        dt = ts - self._last_fee_ts
        if dt <= 0:
            return

        tick = price_to_tick_raw(price, self.invert)
        in_range = self.pos.tick_lower <= tick < self.pos.tick_upper
        self._last_fee_ts = ts

        if not in_range:
            return

        sp = sqrt_price_x96_from_price(price, self.invert)
        pos_val = position_value_usd(
            self.pos.liquidity, self.pos.tick_lower, self.pos.tick_upper,
            sp, self.token0_is_hype, price, BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS,
        )
        fee_usd = pos_val * self.hourly_fee_rate * dt / 3600
        if fee_usd <= 0:
            return

        dec0, dec1 = BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS
        spl = math.sqrt(1.0001 ** self.pos.tick_lower)
        spu = math.sqrt(1.0001 ** self.pos.tick_upper)

        val_per_liq_0 = (spu - sp) / (sp * spu) * price / (10 ** dec0)
        val_per_liq_1 = (sp - spl) / (10 ** dec1)
        total_val_per_liq = val_per_liq_0 + val_per_liq_1

        if total_val_per_liq <= 0:
            return

        fee_liq_0 = fee_usd * val_per_liq_0 / total_val_per_liq
        fee_liq_1 = fee_usd * val_per_liq_1 / total_val_per_liq

        am0_per_liq = fee_liq_0 / price * (10 ** dec0) if fee_liq_0 > 0 else 0
        am1_per_liq = fee_liq_1 * (10 ** dec1) if fee_liq_1 > 0 else 0

        dg0 = int(am0_per_liq * (1 << 128) / max(self.pos.liquidity, 1)) if am0_per_liq > 0 else 0
        dg1 = int(am1_per_liq * (1 << 128) / max(self.pos.liquidity, 1)) if am1_per_liq > 0 else 0

        self._fee_growth_global_0 += dg0
        self._fee_growth_global_1 += dg1

    def _collect_fees(self, price: float) -> tuple[int, int]:
        """Collect accrued fees into wallet. Returns (hype_gained, usdc_gained) human."""
        if self.pos is None:
            return 0.0, 0.0

        Q128 = 1 << 128
        d0 = (self._fee_growth_global_0 - self.pos.fee_growth_0_last)
        d1 = (self._fee_growth_global_1 - self.pos.fee_growth_1_last)
        fee0_raw = self.pos.tokens_owed_0 + (self.pos.liquidity * d0) // Q128
        fee1_raw = self.pos.tokens_owed_1 + (self.pos.liquidity * d1) // Q128

        self.pos.fee_growth_0_last = self._fee_growth_global_0
        self.pos.fee_growth_1_last = self._fee_growth_global_1
        self.pos.tokens_owed_0 = 0
        self.pos.tokens_owed_1 = 0

        if self.token0_is_hype:
            hype_gain = fee0_raw / (10 ** BOT_HYPE_DECIMALS)
            usdc_gain = fee1_raw / (10 ** BOT_USDC_DECIMALS)
        else:
            hype_gain = fee1_raw / (10 ** BOT_HYPE_DECIMALS)
            usdc_gain = fee0_raw / (10 ** BOT_USDC_DECIMALS)

        val = hype_gain * price + usdc_gain
        self.fees_collected_usd += val
        if val > 0.001:
            logger.info(f"  Collected fees: {hype_gain:.6f} HYPE + {usdc_gain:.6f} USDC = ${val:.4f}")

        return hype_gain, usdc_gain

    def _snap_tick(self, tick: int) -> int:
        return round(tick / BOT_TICK_SPACING) * BOT_TICK_SPACING

    # ── core actions ───────────────────────────────────────────────────

    def _optimize_ratio(self, price: float) -> tuple[float, float]:
        """Mirror bot's _optimize_ratio. Swap excess token to balance position ratio.
        Returns (hype, usdc) after optimization."""
        if self.pos is None:
            return self.hype, self.usdc

        tick_lower = self.pos.tick_lower
        tick_upper = self.pos.tick_upper
        sqrt_p = sqrt_price_x96_from_price(price, self.invert)

        sp = sqrt_p / Q96
        p = sp * sp
        spl = math.sqrt(1.0001 ** tick_lower)
        spu = math.sqrt(1.0001 ** tick_upper)

        target_ratio = sp * spu * (sp - spl) / (spu - sp)

        if self.token0_is_hype:
            raw0 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))
            raw1 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
        else:
            raw0 = int(self.usdc * (10 ** BOT_USDC_DECIMALS))
            raw1 = int(self.hype * (10 ** BOT_HYPE_DECIMALS))

        if raw0 <= 0 or raw1 <= 0:
            return self.hype, self.usdc

        current_ratio = raw1 / raw0
        if current_ratio <= 0 or target_ratio <= 0:
            return self.hype, self.usdc

        deviation = abs(math.log(current_ratio / target_ratio))
        if deviation < 0.01:
            return self.hype, self.usdc

        if current_ratio > target_ratio:
            numerator = raw1 - int(target_ratio * raw0)
            denom = 1.0 + target_ratio / p
            swap_raw = max(0, int(numerator / denom * 0.99))
            if swap_raw >= 1000:
                dec_swap = BOT_USDC_DECIMALS if not self.token0_is_hype else BOT_HYPE_DECIMALS
                amt_human = swap_raw / (10 ** dec_swap)
                if self.token0_is_hype:
                    out = simulate_swap(amt_human, False, price)
                    self.usdc -= amt_human
                    self.hype += out
                else:
                    out = simulate_swap(amt_human, True, price)
                    self.hype -= amt_human
                    self.usdc += out
                self.swaps += 1
        else:
            numerator = int(target_ratio * raw0) - raw1
            denom = p + target_ratio
            swap_raw = max(0, int(numerator / denom * 0.99))
            if swap_raw >= 1000:
                dec_swap = BOT_HYPE_DECIMALS if self.token0_is_hype else BOT_USDC_DECIMALS
                amt_human = swap_raw / (10 ** dec_swap)
                if self.token0_is_hype:
                    out = simulate_swap(amt_human, True, price)
                    self.hype -= amt_human
                    self.usdc += out
                else:
                    out = simulate_swap(amt_human, False, price)
                    self.usdc -= amt_human
                    self.hype += out
                self.swaps += 1

        return self.hype, self.usdc

    def _create_position(self, price: float, ts: int = 0):
        """Mirror bot's create_position."""
        if self.hype <= 0 and self.usdc <= 0:
            return

        tick = price_to_tick_raw(price, self.invert)
        snapped_lower, snapped_upper, _, _ = calculate_bounds(
            price, BOT_LOWER_BOUND_PCT, BOT_UPPER_BOUND_PCT,
            BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS, BOT_TICK_SPACING, self.invert,
        )

        self._optimize_ratio(price)

        sqrt_p = sqrt_price_x96_from_price(price, self.invert)

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

        self.pos = Position(liq, snapped_lower, snapped_upper, tick, price)
        if ts > 0:
            self._last_fee_ts = ts
        logger.info(
            f"  Created position: liq={liq} range=[{snapped_lower},{snapped_upper}] "
            f"a0={a0} a1={a1}"
        )

    def _remove_position(self, price: float):
        """Remove all liquidity and return tokens to wallet."""
        if self.pos is None:
            return

        sqrt_p = sqrt_price_x96_from_price(price, self.invert)
        tick = price_to_tick_raw(price, self.invert)
        a0, a1 = position_amounts(
            self.pos.liquidity, self.pos.tick_lower, self.pos.tick_upper, sqrt_p,
        )

        # Include owed fees
        Q128 = 1 << 128
        d0 = (self._fee_growth_global_0 - self.pos.fee_growth_0_last)
        d1 = (self._fee_growth_global_1 - self.pos.fee_growth_1_last)
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

        wallet_val = self.hype * price + self.usdc
        if wallet_val < BOT_WALLET_MIN:
            return

        self._optimize_ratio(price)

        sqrt_p = sqrt_price_x96_from_price(price, self.invert)

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

        a0, a1 = position_amounts(
            add_liq, self.pos.tick_lower, self.pos.tick_upper, sqrt_p,
        )

        if self.token0_is_hype:
            self.hype -= a0 / (10 ** BOT_HYPE_DECIMALS)
            self.usdc -= a1 / (10 ** BOT_USDC_DECIMALS)
        else:
            self.usdc -= a0 / (10 ** BOT_USDC_DECIMALS)
            self.hype -= a1 / (10 ** BOT_HYPE_DECIMALS)

        self.pos.liquidity += add_liq
        self.adds += 1

    # ── main simulation ────────────────────────────────────────────────

    def run(self, prices: list[tuple[int, float]]) -> dict[str, Any]:
        initial_price = prices[0][1]
        initial_total = self.hype * initial_price + self.usdc

        last_cycle_ts: Optional[int] = None
        last_secondary_ts: Optional[int] = None

        for idx, (ts, price) in enumerate(prices):
            if idx == 0:
                self._last_fee_ts = ts
                self.history.append((ts, price, self.hype, self.usdc,
                                     self.pos.liquidity if self.pos else 0,
                                     self.hype * price + self.usdc))
                continue

            self._accrue_fees(ts, price)
            tick = price_to_tick_raw(price, self.invert)

            # ── secondary cycle (anti-IL) ─────────────────────────────
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

            # ── main cycle ────────────────────────────────────────────
            if (last_cycle_ts is None
                or (ts - last_cycle_ts) >= BOT_SLEEP_INTERVAL):
                last_cycle_ts = ts

                if self.pos is not None:
                    in_range = self.pos.tick_lower <= tick < self.pos.tick_upper

                    if not in_range:
                        logger.info(
                            f"[{datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')}] "
                            f"Out of range (tick={tick}), rebalancing"
                        )
                        self._remove_position(price)
                        self.rebalances += 1
                        self._create_position(price, ts)
                    else:
                        if self.hype * price + self.usdc > BOT_WALLET_MIN:
                            self._add_to_position(price)

                        pos_val = position_value_usd(
                            self.pos.liquidity, self.pos.tick_lower, self.pos.tick_upper,
                            sqrt_price_x96_from_price(price, self.invert),
                            self.token0_is_hype, price,
                            BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS,
                        )
                        if pos_val <= 1.0:
                            logger.info(f"  Position value ${pos_val:.2f} <= $1, recreating")
                            self._remove_position(price)
                            self._create_position(price, ts)
                else:
                    self._create_position(price, ts)

            # record every 6 hours
            if idx % 6 == 0:
                pos_liq = self.pos.liquidity if self.pos else 0
                total = self.hype * price + self.usdc
                if self.pos:
                    pv = position_value_usd(
                        self.pos.liquidity, self.pos.tick_lower, self.pos.tick_upper,
                        sqrt_price_x96_from_price(price, self.invert),
                        self.token0_is_hype, price,
                        BOT_HYPE_DECIMALS, BOT_USDC_DECIMALS,
                    )
                    total += pv
                self.history.append((ts, price, self.hype, self.usdc, pos_liq, total))

        final_price = prices[-1][1]
        if self.pos:
            self._remove_position(final_price)

        final_total = self.hype * final_price + self.usdc
        hodl_hype = initial_total / initial_price
        hodl_val = hodl_hype * final_price

        return {
            "initial_hype": self.initial_hype,
            "initial_usdc": self.initial_usdc,
            "initial_price": initial_price,
            "final_price": final_price,
            "final_hype": self.hype,
            "final_usdc": self.usdc,
            "final_total": final_total,
            "hodl_total": hodl_val,
            "initial_total": initial_total,
            "return_pct": (final_total / initial_total - 1) * 100,
            "hodl_return_pct": (hodl_val / initial_total - 1) * 100,
            "outperformance_pct": (final_total / hodl_val - 1) * 100,
            "fees_earned": self.fees_collected_usd,
            "rebalances": self.rebalances,
            "adds": self.adds,
            "swaps": self.swaps,
            "secondary_triggers": self.secondary_triggers,
            "history": self.history,
        }


# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HYPE/USDC Liquidity Bot Backtest")
    parser.add_argument("--initial-hype", type=float, default=10.0,
                        help="Initial wHYPE balance")
    parser.add_argument("--initial-usdc", type=float, default=200.0,
                        help="Initial USDC balance")
    parser.add_argument("--hourly-fee-rate", type=float, default=0.01,
                        help="Hourly fee rate as fraction of position value (0.01 = 1%%/hr)")
    parser.add_argument("--csv", type=str, default=None,
                        help="CSV file with price data (columns: timestamp,close "
                             "or timestamp,open,high,low,close)")
    parser.add_argument("--days", type=int, default=300,
                        help="Days of CoinGecko data to fetch")
    parser.add_argument("--output", type=str, default=None,
                        help="Save CSV history to file")
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

    # ── print report ───────────────────────────────────────────────────
    print()
    sep = "=" * 60
    print(sep)
    print("  BACKTEST RESULTS")
    print(sep)
    print(f"  Initial capital:  {result['initial_hype']:.4f} HYPE + "
          f"{result['initial_usdc']:.2f} USDC = ${result['initial_total']:.2f}")
    print(f"  Initial price:    ${result['initial_price']:.4f}")
    print(f"  Final price:      ${result['final_price']:.4f}")
    print(f"  Price change:     {result['final_price']/result['initial_price']*100-100:+.2f}%")
    print(f"  Hourly fee rate:  {args.hourly_fee_rate*100:.2f}%/hr")
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

    # ── save CSV ────────────────────────────────────────────────────────
    if args.output:
        with open(args.output, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "price", "hype_bal", "usdc_bal",
                         "position_liquidity", "total_value_usd"])
            for row in result["history"]:
                w.writerow(row)
        logger.info(f"History saved to {args.output}")


if __name__ == "__main__":
    main()
