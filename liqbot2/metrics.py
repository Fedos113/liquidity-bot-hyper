import math
from datetime import datetime, timezone

from liqbot2.db import get_first_snapshot, get_snapshot_value_at, get_tx_fees_total

Q96 = 2 ** 96


def compute_position_amounts(liquidity, tick_lower, tick_upper, sqrt_price_x96):
    sp = sqrt_price_x96 / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)

    if sp <= spl:
        return int(liquidity * (spu - spl) / (spl * spu)), 0
    if sp >= spu:
        return 0, int(liquidity * (spu - spl))
    return int(liquidity * (spu - sp) / (sp * spu)), int(liquidity * (sp - spl))


def position_value_usd(amount0, amount1, token0_is_hype, current_price, dec0, dec1):
    if token0_is_hype:
        return amount0 * current_price / (10 ** dec0) + amount1 / (10 ** dec1)
    return amount0 / (10 ** dec0) + amount1 * current_price / (10 ** dec1)


def il_simple(price_now, price_entry):
    if price_entry <= 0:
        return 0.0
    r = price_now / price_entry
    return 2 * math.sqrt(r) / (1 + r) - 1


async def compute_all(db, pos_data, hype_bal, usdc_bal, current_price, sqrt_price_x96,
                      token0_is_hype, dec0, dec1, current_tick, wallet_address,
                      tick_lower, tick_upper):
    now = int(datetime.now(timezone.utc).timestamp())

    has_position = pos_data is not None and pos_data.get("liquidity", 0) > 0 and tick_lower is not None and tick_upper is not None

    if has_position:
        am0, am1 = compute_position_amounts(
            pos_data["liquidity"], tick_lower, tick_upper, sqrt_price_x96,
        )
        pos_val = position_value_usd(am0, am1, token0_is_hype, current_price, dec0, dec1)
    else:
        pos_val = 0.0

    wallet_val = position_value_usd(hype_bal, usdc_bal, token0_is_hype, current_price, dec0, dec1)
    portfolio_val = pos_val + wallet_val

    first = await get_first_snapshot(db, require_liquidity=has_position)
    tx_fees_wei = await get_tx_fees_total(db)
    tx_fees_usd = tx_fees_wei / 1e18 * current_price if tx_fees_wei else 0

    pnl_all = None
    pnl_24h = None
    pnl_7d = None
    il = None
    price_entry = current_price

    if first:
        price_entry = first.get("price") or current_price
        val_24h = await get_snapshot_value_at(db, now - 86400)
        val_7d = await get_snapshot_value_at(db, now - 604800)

        if first["portfolio_value_usd"] > 0:
            pnl_all = portfolio_val - first["portfolio_value_usd"]

        if val_24h is not None and val_24h > 0:
            pnl_24h = portfolio_val - val_24h

        if val_7d is not None and val_7d > 0:
            pnl_7d = portfolio_val - val_7d

    if has_position:
        il_pct = il_simple(current_price, price_entry)
        if il_pct != -1:
            il = pos_val * il_pct / (1 + il_pct)
        else:
            il = -pos_val

    liq = pos_data["liquidity"] if pos_data else 0
    await db.execute(
        "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now, hype_bal, usdc_bal, liq, tick_lower, tick_upper,
         current_tick, portfolio_val, current_price),
    )
    await db.commit()

    return {
        "wallet": wallet_address,
        "position_value": round(pos_val, 2),
        "wallet_value": round(wallet_val, 2),
        "portfolio_value": round(portfolio_val, 2),
        "current_price": round(current_price, 6),
        "pnl": {
            "24h": round(pnl_24h, 2) if pnl_24h is not None else None,
            "7d": round(pnl_7d, 2) if pnl_7d is not None else None,
            "all": round(pnl_all, 2) if pnl_all is not None else None,
        },
        "il": round(il, 2) if il is not None else None,
        "total_fees_usd": round(tx_fees_usd, 2),
    }
