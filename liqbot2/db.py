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


async def get_snapshots(db):
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
