import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
import aiosqlite

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config
from src.provider import get_web3, get_pool_contract, get_position_manager_contract, get_account
from src.math_utils import get_token_order, get_tick_spacing, get_price_from_sqrt_price
from src.position_manager import get_position_details, get_token_balances

load_dotenv()

from liqbot2.db import init_db, get_snapshots
from liqbot2.metrics import compute_all

DB_PATH = Path(__file__).resolve().parent / "data" / "history.db"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.db = await aiosqlite.connect(DB_PATH)
    app.state.db.row_factory = aiosqlite.Row
    yield
    await app.state.db.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index():
    index_path = Path(__file__).resolve().parent / "index.html"
    return FileResponse(str(index_path))


_ERC721_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "index", "type": "uint256"}], "name": "tokenOfOwnerByIndex", "outputs": [{"name": "tokenId", "type": "uint256"}], "type": "function"},
]


def _find_position(w3, pm, pool):
    account = get_account(w3)
    pool_t0 = pool.functions.token0().call().lower()
    pool_t1 = pool.functions.token1().call().lower()

    def _check(tid):
        try:
            raw = pm.functions.positions(tid).call()
            return raw[2].lower() == pool_t0 and raw[3].lower() == pool_t1 and raw[7] > 0
        except Exception:
            return False

    def _build(tid, raw):
        return tid, {
            "nonce": raw[0], "operator": raw[1],
            "token0": raw[2], "token1": raw[3],
            "fee": raw[4], "tickLower": raw[5],
            "tickUpper": raw[6], "liquidity": raw[7],
            "feeGrowthInside0LastX128": raw[8], "feeGrowthInside1LastX128": raw[9],
            "tokensOwed0": raw[10], "tokensOwed1": raw[11],
        }

    data_dir = Path(__file__).resolve().parent / "data"
    meta_file = data_dir / "position_meta.json"
    saved_tid = 0
    if meta_file.exists():
        try:
            import json
            meta = json.loads(meta_file.read_text())
            saved_tid = meta.get("token_id", 0)
        except Exception:
            pass
    if not saved_tid:
        tid_file = data_dir / "token_id.txt"
        if tid_file.exists():
            try:
                saved_tid = int(tid_file.read_text().strip())
            except Exception:
                pass
    if saved_tid > 0:
        try:
            raw = pm.functions.positions(saved_tid).call()
            if raw[2].lower() == pool_t0 and raw[3].lower() == pool_t1 and raw[7] > 0:
                return _build(saved_tid, raw)
        except Exception:
            pass

    erc721 = w3.eth.contract(address=pm.address, abi=_ERC721_ABI)
    try:
        balance = erc721.functions.balanceOf(account.address).call()
        for i in range(balance):
            tid = erc721.functions.tokenOfOwnerByIndex(account.address, i).call()
            if _check(tid):
                raw = pm.functions.positions(tid).call()
                return _build(tid, raw)
    except Exception:
        pass

    return None, None


@app.post("/refresh")
async def refresh():
    w3 = get_web3()
    pool = get_pool_contract(w3)
    pm = get_position_manager_contract(w3)

    slot0 = pool.functions.slot0().call()
    sqrt_price_x96 = slot0[0]
    current_tick = slot0[1]
    token0_is_hype, dec0, dec1 = get_token_order(pool, config.HYPE_ADDRESS)
    invert = not token0_is_hype
    current_price = get_price_from_sqrt_price(sqrt_price_x96, dec0, dec1, invert)

    token_id, pos = _find_position(w3, pm, pool)
    if pos is None and config.TOKEN_ID > 0:
        pos = get_position_details(w3, pm, config.TOKEN_ID)

    hype_bal, usdc_bal = get_token_balances(w3)
    tick_lower = pos["tickLower"] if pos and pos["liquidity"] > 0 else None
    tick_upper = pos["tickUpper"] if pos and pos["liquidity"] > 0 else None

    result = await compute_all(
        app.state.db, pos, hype_bal, usdc_bal, current_price, sqrt_price_x96,
        token0_is_hype, dec0, dec1, current_tick, config.WALLET_ADDRESS,
        tick_lower, tick_upper,
    )

    return result


@app.get("/chart")
async def chart():
    rows = await get_snapshots(app.state.db)
    return {"timestamps": [r["ts"] for r in rows], "values": [r["value"] for r in rows]}
