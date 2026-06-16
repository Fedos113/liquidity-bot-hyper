import math
from datetime import datetime, timezone

from liqbot2.db import get_first_snapshot, get_snapshot_at, get_net_deposits_before

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

    net_dep_now = await get_net_deposits_before(db, now)
    adjusted_now = portfolio_val - net_dep_now

    first = await get_first_snapshot(db, require_liquidity=has_position)

    pnl_all = None
    pnl_24h = None
    pnl_7d = None

    if first:
        net_dep_first = await get_net_deposits_before(db, first["ts"])
        adjusted_first = first["portfolio_value_usd"] - net_dep_first

        if first["portfolio_value_usd"] > 0:
            pnl_all = adjusted_now - adjusted_first

        snap_24h = await get_snapshot_at(db, now - 86400)
        if snap_24h and snap_24h["value"] > 0:
            net_dep_24h = await get_net_deposits_before(db, snap_24h["ts"])
            adjusted_24h = snap_24h["value"] - net_dep_24h
            pnl_24h = adjusted_now - adjusted_24h

        snap_7d = await get_snapshot_at(db, now - 604800)
        if snap_7d and snap_7d["value"] > 0:
            net_dep_7d = await get_net_deposits_before(db, snap_7d["ts"])
            adjusted_7d = snap_7d["value"] - net_dep_7d
            pnl_7d = adjusted_now - adjusted_7d

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
    }
