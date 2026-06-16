import os
import aiosqlite

DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "history.db")


async def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                ts INTEGER PRIMARY KEY,
                hype_bal INTEGER,
                usdc_bal INTEGER,
                liquidity INTEGER,
                tick_lower INTEGER,
                tick_upper INTEGER,
                current_tick INTEGER,
                portfolio_value_usd REAL,
                price REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tx_fees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                tx_hash TEXT,
                fee_wei INTEGER,
                description TEXT
            )
        """)
        try:
            await db.execute("ALTER TABLE snapshots ADD COLUMN price REAL")
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                type TEXT,
                usd_value REAL,
                description TEXT
            )
        """)
        await db.commit()


async def insert_snapshot(db, ts, hype_bal, usdc_bal, liquidity, tick_lower, tick_upper, current_tick, portfolio_value_usd, price):
    await db.execute(
        "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, hype_bal, usdc_bal, liquidity, tick_lower, tick_upper, current_tick, portfolio_value_usd, price),
    )
    await db.commit()


async def insert_tx_fee(db, ts, tx_hash, fee_wei, description):
    await db.execute(
        "INSERT INTO tx_fees (ts, tx_hash, fee_wei, description) VALUES (?, ?, ?, ?)",
        (ts, tx_hash, fee_wei, description),
    )
    await db.commit()


async def get_snapshots(db, cutoff=None):
    if cutoff:
        cursor = await db.execute("SELECT ts, portfolio_value_usd FROM snapshots WHERE ts >= ? ORDER BY ts", (cutoff,))
    else:
        cursor = await db.execute("SELECT ts, portfolio_value_usd FROM snapshots ORDER BY ts")
    rows = await cursor.fetchall()
    return [{"ts": r[0], "value": r[1]} for r in rows]


async def get_first_snapshot(db, require_liquidity=False):
    if require_liquidity:
        cursor = await db.execute("SELECT * FROM snapshots WHERE liquidity > 0 ORDER BY ts ASC LIMIT 1")
    else:
        cursor = await db.execute("SELECT * FROM snapshots ORDER BY ts ASC LIMIT 1")
    row = await cursor.fetchone()
    if row:
        return {
            "ts": row[0], "hype_bal": row[1], "usdc_bal": row[2],
            "liquidity": row[3], "tick_lower": row[4], "tick_upper": row[5],
            "current_tick": row[6], "portfolio_value_usd": row[7],
            "price": row[8] if len(row) > 8 else None,
        }
    return None


async def get_snapshot_value_at(db, ts):
    cursor = await db.execute(
        "SELECT portfolio_value_usd FROM snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
        (ts,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def get_tx_fees_total(db):
    cursor = await db.execute("SELECT COALESCE(SUM(fee_wei), 0) FROM tx_fees")
    row = await cursor.fetchone()
    return row[0]


async def get_snapshot_at(db, ts):
    cursor = await db.execute(
        "SELECT ts, portfolio_value_usd FROM snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
        (ts,),
    )
    row = await cursor.fetchone()
    return {"ts": row[0], "value": row[1]} if row else None


async def insert_deposit(db, ts, type_, usd_value, description=""):
    await db.execute(
        "INSERT INTO deposits (ts, type, usd_value, description) VALUES (?, ?, ?, ?)",
        (ts, type_, usd_value, description),
    )
    await db.commit()


async def get_net_deposits_before(db, ts):
    cursor = await db.execute(
        "SELECT COALESCE(SUM(CASE WHEN type='deposit' THEN usd_value ELSE -usd_value END), 0) FROM deposits WHERE ts < ?",
        (ts,),
    )
    row = await cursor.fetchone()
    return row[0]


async def get_net_deposits_batch(db, timestamps):
    if not timestamps:
        return {}
    cursor = await db.execute("SELECT ts, type, usd_value FROM deposits ORDER BY ts")
    deposits = await cursor.fetchall()
    result = {}
    cum = 0.0
    dep_idx = 0
    for ts in sorted(timestamps):
        while dep_idx < len(deposits) and deposits[dep_idx][0] < ts:
            if deposits[dep_idx][1] == "deposit":
                cum += deposits[dep_idx][2]
            else:
                cum -= deposits[dep_idx][2]
            dep_idx += 1
        result[ts] = cum
    return result
