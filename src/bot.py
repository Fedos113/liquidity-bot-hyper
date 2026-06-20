import logging
import signal
import threading
from datetime import datetime
from time import sleep, time
from typing import Optional

from web3 import Web3
from web3.exceptions import Web3Exception

from src.config import config
from src.constants import HYPE_DECIMALS, USDC_DECIMALS
from src.provider import (
    get_web3,
    get_pool_contract,
    get_position_manager_contract,
    get_hype_contract,
    get_usdc_contract,
    rpc_manager,
    sanitize_err,
)
from src.math_utils import (
    get_price_from_sqrt_price,
    tick_to_price,
    calculate_usdc_value,
    position_value_usd,
)
from src.position_manager import (
    get_position_details,
    add_to_position,
    create_position,
    collect_fees,
    remove_liquidity,
    increase_liquidity,
    get_token_balances,
    approve_token,
    get_hype_or_usdc,
    swap_exact_input_single,
    TxFeeExceeded,
    set_hype_price,
)

logger = logging.getLogger("liqbot")

running = True
tx_lock = threading.Lock()
token_id_ref = [0]
upper_threshold_event = threading.Event()
downward_trigger_event = threading.Event()
downward_inner_event = threading.Event()
upward_inner_event = threading.Event()

_pool_cache = {}
_pool_cache_lock = threading.Lock()
pool_opened = False
_cached_price = 0.0
_cached_lower_price = 0.0
_cached_upper_price = 0.0
_cached_drop_trigger = 0.0
_cached_surge_trigger = 0.0


class ChartLogger:
    def __init__(self, path: str = "chart.log"):
        self.path = path
        self.initial_balance: Optional[float] = None
        self.initial_price: Optional[float] = None
        self.last_balance: Optional[float] = None
        self.last_price: Optional[float] = None
        self._initialized = False

    def _ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _pnl(self, current_balance: float) -> str:
        if self.initial_balance and self.initial_balance > 0:
            pct = (current_balance / self.initial_balance - 1) * 100
            return f"{pct:+.2f}%"
        return "+0.00%"

    def initial_pool(self, price: float, lower: float, upper: float,
                     tp_threshold: float, sl_threshold: float, total_balance: float):
        self.initial_price = price
        self.initial_balance = total_balance
        self.last_price = price
        self.last_balance = total_balance
        self._initialized = True
        with open(self.path, "a") as f:
            f.write("=" * 124 + "\n")
            f.write(f"{self._ts()}: Initial Pool created at {price:.2f}: "
                    f"{lower:.4f} - {upper:.4f} ; "
                    f"TP: {tp_threshold:.2f} SL: {sl_threshold:.2f}; "
                    f"Initial balance: ${total_balance:.2f}\n")

    def tp_triggered(self, price: float, total_balance: float):
        self.last_price = price
        self.last_balance = total_balance
        pnl = self._pnl(total_balance)
        with open(self.path, "a") as f:
            f.write(f"{self._ts()}: TP triggered PNL: {pnl}: "
                    f"USDC swapped to HYPE at {price:.2f} ; "
                    f"Balance: ${total_balance:.2f}\n")

    def pool_created(self, price: float, lower: float, upper: float,
                     tp_threshold: float, sl_threshold: float, total_balance: float):
        self.last_price = price
        self.last_balance = total_balance
        pnl = self._pnl(total_balance)
        with open(self.path, "a") as f:
            f.write(f"{self._ts()}: Pool created at {price:.2f}, PNL: {pnl}: "
                    f"{lower:.4f} - {upper:.4f} ; "
                    f"TP: {tp_threshold:.2f} SL: {sl_threshold:.2f}; "
                    f"Balance: ${total_balance:.2f}\n")

    def sl_triggered(self, price: float, total_balance: float):
        self.last_price = price
        self.last_balance = total_balance
        pnl = self._pnl(total_balance)
        with open(self.path, "a") as f:
            f.write(f"{self._ts()}: SL triggered, PNL: {pnl}: "
                    f"HYPE swapped to USDC at {price:.2f}; "
                    f"Balance: ${total_balance:.2f}\n")

    def bot_stopped(self, price: float, total_balance: float):
        if not self._initialized:
            return
        pnl = self._pnl(total_balance)
        with open(self.path, "a") as f:
            f.write(f"{self._ts()}: Bot stopped at {price:.2f}; "
                    f"PNL: {pnl}; "
                    f"Balance: ${total_balance:.2f}\n")
            f.write("=" * 124 + "\n")


chart_logger = ChartLogger()
_logged_initial_pool = False


def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received, stopping after current cycle...")
    running = False


def _get_pool_cache(w3, pool):
    global _pool_cache
    pool_addr = pool.address.lower()
    with _pool_cache_lock:
        if pool_addr in _pool_cache:
            return _pool_cache[pool_addr]
        token0 = pool.functions.token0().call()
        token1 = pool.functions.token1().call()
        fee = pool.functions.fee().call()
        tick_spacing = pool.functions.tickSpacing().call()
        token0_is_hype = token0.lower() == config.HYPE_ADDRESS.lower()
        dec0 = HYPE_DECIMALS if token0_is_hype else USDC_DECIMALS
        dec1 = HYPE_DECIMALS if not token0_is_hype else USDC_DECIMALS
        _pool_cache[pool_addr] = {
            "token0": token0, "token1": token1, "fee": fee,
            "tick_spacing": tick_spacing, "token0_is_hype": token0_is_hype,
            "dec0": dec0, "dec1": dec1,
        }
        logger.info(f"Cached pool: token0_is_hype={token0_is_hype}, fee={fee}")
        return _pool_cache[pool_addr]


def _multicall_price_and_balances(w3, pool):
    from src.provider import get_multicall3, get_hype_contract, get_usdc_contract
    mc3 = get_multicall3(w3)
    cache = _get_pool_cache(w3, pool)
    if mc3:
        try:
            hype_con = get_hype_contract(w3)
            usdc_con = get_usdc_contract(w3)
            account = Web3.to_checksum_address(config.WALLET_ADDRESS)
            calls = [
                (Web3.to_checksum_address(pool.address), False, pool.encodeABI(fn_name="slot0")),
                (Web3.to_checksum_address(hype_con.address), False, hype_con.encodeABI(fn_name="balanceOf", args=[account])),
                (Web3.to_checksum_address(usdc_con.address), False, usdc_con.encodeABI(fn_name="balanceOf", args=[account])),
            ]
            results = mc3.functions.aggregate3(calls).call()
            if not all(r[0] for r in results):
                raise ValueError("MC3 partial failure, falling back")
            slot0_r = pool.decode_function_result("slot0", results[0][1])
            sqrt_price_x96 = slot0_r[0]
            current_tick = slot0_r[1]
            hype_bal = int.from_bytes(results[1][1], 'big')
            usdc_bal = int.from_bytes(results[2][1], 'big')
        except Exception:
            slot0 = pool.functions.slot0().call()
            sqrt_price_x96, current_tick = slot0[0], slot0[1]
            hype_bal, usdc_bal = get_token_balances(w3)
    else:
        slot0 = pool.functions.slot0().call()
        sqrt_price_x96, current_tick = slot0[0], slot0[1]
        hype_bal, usdc_bal = get_token_balances(w3)
    invert = not cache["token0_is_hype"]
    price = get_price_from_sqrt_price(sqrt_price_x96, cache["dec0"], cache["dec1"], invert)
    return price, current_tick, sqrt_price_x96, hype_bal, usdc_bal


def _multicall_fee_growth_and_ticks(w3, pool, pos):
    from src.provider import get_multicall3
    mc3 = get_multicall3(w3)
    if mc3:
        try:
            calls = [
                (Web3.to_checksum_address(pool.address), False, pool.encodeABI(fn_name="feeGrowthGlobal0X128")),
                (Web3.to_checksum_address(pool.address), False, pool.encodeABI(fn_name="feeGrowthGlobal1X128")),
                (Web3.to_checksum_address(pool.address), False, pool.encodeABI(fn_name="slot0")),
                (Web3.to_checksum_address(pool.address), False, pool.encodeABI(fn_name="ticks", args=[pos["tickLower"]])),
                (Web3.to_checksum_address(pool.address), False, pool.encodeABI(fn_name="ticks", args=[pos["tickUpper"]])),
            ]
            results = mc3.functions.aggregate3(calls).call()
            if not all(r[0] for r in results):
                raise ValueError("MC3 partial failure, falling back")
            fg0 = int.from_bytes(results[0][1], 'big')
            fg1 = int.from_bytes(results[1][1], 'big')
            slot0_r = pool.decode_function_result("slot0", results[2][1])
            current_tick = slot0_r[1]
            lower_tick = pool.decode_function_result("ticks", results[3][1])
            upper_tick = pool.decode_function_result("ticks", results[4][1])
            return fg0, fg1, current_tick, lower_tick, upper_tick
        except Exception:
            pass
    fg0 = pool.functions.feeGrowthGlobal0X128().call()
    fg1 = pool.functions.feeGrowthGlobal1X128().call()
    slot0 = pool.functions.slot0().call()
    current_tick = slot0[1]
    lower_tick = pool.functions.ticks(pos["tickLower"]).call()
    upper_tick = pool.functions.ticks(pos["tickUpper"]).call()
    return fg0, fg1, current_tick, lower_tick, upper_tick


def _compute_unclaimed_fees(pos, fg0, fg1, current_tick, lower_tick, upper_tick):
    tick_lower = pos["tickLower"]
    tick_upper = pos["tickUpper"]
    liquidity = pos["liquidity"]
    fg0_last = pos["feeGrowthInside0LastX128"]
    fg1_last = pos["feeGrowthInside1LastX128"]
    owed0 = pos["tokensOwed0"]
    owed1 = pos["tokensOwed1"]
    if liquidity == 0:
        return owed0, owed1
    below0 = lower_tick[2] if current_tick >= tick_lower else fg0 - lower_tick[2]
    below1 = lower_tick[3] if current_tick >= tick_lower else fg1 - lower_tick[3]
    above0 = upper_tick[2] if current_tick < tick_upper else fg0 - upper_tick[2]
    above1 = upper_tick[3] if current_tick < tick_upper else fg1 - upper_tick[3]
    inside0 = fg0 - below0 - above0
    inside1 = fg1 - below1 - above1
    Q128 = 1 << 128
    u0 = owed0 + (liquidity * (inside0 - fg0_last)) // Q128
    u1 = owed1 + (liquidity * (inside1 - fg1_last)) // Q128
    return u0, u1


def _close_pool_3rpc(cache, tid, pos, dry_run):
    """Close position: collect fees, remove liquidity, collect principal — priority gas for speed."""
    try:
        w3_slot0 = rpc_manager.get_web3_for_slot(0)
        w3_slot1 = rpc_manager.get_web3_for_slot(1)
        w3_slot2 = rpc_manager.get_web3_for_slot(2)

        pm_slot0 = get_position_manager_contract(w3_slot0)
        pm_slot1 = get_position_manager_contract(w3_slot1)
        pm_slot2 = get_position_manager_contract(w3_slot2)

        pool_slot0 = get_pool_contract(w3_slot0)

        current_price, _, _, _, _ = _multicall_price_and_balances(w3_slot0, pool_slot0)
        set_hype_price(current_price)

        collect_fees(w3_slot0, pm_slot0, tid, dry_run, priority=True)
        sleep(config.TX_INTER_SLEEP)

        remove_liquidity(w3_slot1, pm_slot1, tid, pos["liquidity"], dry_run, priority=True)
        sleep(config.TX_INTER_SLEEP)

        collect_fees(w3_slot2, pm_slot2, tid, dry_run, priority=True)

        return current_price
    except (Web3Exception, ConnectionError, TimeoutError, ValueError):
        raise


def _handle_rpc_error(e, failed_w3):
    try:
        rpc_manager.on_error(failed_w3)
    except Exception:
        pass


def _secondary_cycle():
    global running, tx_lock, pool_opened, _cached_price, _cached_lower_price, _cached_upper_price, _cached_drop_trigger, _cached_surge_trigger

    logger.info(f"[SECONDARY] Initial delay {config.SECONDARY_INNER}s before first cycle")
    for _ in range(config.SECONDARY_INNER):
        if not running:
            return
        sleep(1)

    while running:
        cycle_start = time()
        logger.info("[SECONDARY] Cycle start")

        if tx_lock.locked():
            logger.info("[SECONDARY] Main cycle active, skipping")
            elapsed = time() - cycle_start
            remaining = config.SECONDARY_INNER - elapsed
            for _ in range(int(max(remaining, 0))):
                if not running:
                    break
                sleep(1)
            continue

        if not pool_opened:
            logger.info("[SECONDARY] Pool not opened, skipping")
            elapsed = time() - cycle_start
            remaining = config.SECONDARY_INNER - elapsed
            for _ in range(int(max(remaining, 0))):
                if not running:
                    break
                sleep(1)
            continue

        w3 = get_web3()
        pool = get_pool_contract(w3)
        for attempt in range(len(rpc_manager.get_active())):
            try:
                fresh_price, _, _, _, _ = _multicall_price_and_balances(w3, pool)
                _cached_price = fresh_price
                set_hype_price(fresh_price)
                break
            except (Web3Exception, ConnectionError, TimeoutError) as e:
                logger.warning(f"[SECONDARY] Price fetch failed on RPC attempt {attempt+1}: {sanitize_err(str(e))}")
                if attempt < len(rpc_manager.get_active()) - 1:
                    w3 = rpc_manager.on_error(w3)
                    pool = get_pool_contract(w3)
                else:
                    raise
            except Exception as e:
                logger.warning(f"[SECONDARY] Price fetch failed: {sanitize_err(str(e))}")
                raise

        try:
            price = _cached_price
            lower = _cached_lower_price
            upper = _cached_upper_price
            drop = _cached_drop_trigger
            surge = _cached_surge_trigger

            if price <= 0 or lower <= 0 or upper <= 0:
                logger.info("[SECONDARY] Cached bounds not ready yet, skipping")
                elapsed = time() - cycle_start
                remaining = config.SECONDARY_INNER - elapsed
                for _ in range(int(max(remaining, 0))):
                    if not running:
                        break
                    sleep(1)
                continue

            logger.info(
                f"[SECONDARY] Price=${price:.4f} "
                f"lower=${lower:.4f} upper=${upper:.4f} "
                f"drop=${drop:.4f} surge=${surge:.4f}"
            )

            if price > surge:
                logger.warning(
                    f"[SECONDARY] Price ${price:.4f} surged above upper bound, "
                    f"triggering upward inner cycle..."
                )
                upward_inner_event.set()
            elif price < drop:
                logger.warning(
                    f"[SECONDARY] Price ${price:.4f} dropped below lower bound, "
                    f"triggering downward inner cycle..."
                )
                downward_inner_event.set()
                downward_trigger_event.set()
            else:
                logger.info("[SECONDARY] Price within bounds, no action")

        except Exception as e:
            logger.warning(f"[SECONDARY] Error: {sanitize_err(str(e))}")

        elapsed = time() - cycle_start
        remaining = config.SECONDARY_INNER - elapsed
        for _ in range(int(max(remaining, 0))):
            if not running:
                break
            sleep(1)


def _downward_inner_cycle():
    global running, tx_lock, token_id_ref, pool_opened

    while running:
        downward_inner_event.wait()
        if not running:
            return

        logger.info("[DOWNWARD-INNER] Inner cycle triggered: closing position and swapping to USDC")

        while running:
            cycle_start = time()
            success = False

            w3 = None
            try:
                w3 = get_web3()
                pool = get_pool_contract(w3)
                pm = get_position_manager_contract(w3)
                cache = _get_pool_cache(w3, pool)

                tid = token_id_ref[0]
                pos = None
                if tid > 0:
                    pos = get_position_details(w3, pm, tid)
                if not pos or pos["liquidity"] == 0:
                    new_id, pos = _auto_discover_position(w3, pm, cache["token0"], cache["token1"])
                    if new_id is not None:
                        token_id_ref[0] = new_id
                        tid = new_id

                if tid > 0 and pos and pos["liquidity"] > 0:
                    current_price = _close_pool_3rpc( cache, tid, pos, config.DRY_RUN)
                    set_hype_price(current_price)
                else:
                    current_price, _, _, _, _ = _multicall_price_and_balances(w3, pool)
                    set_hype_price(current_price)

                with tx_lock:
                    hype_bal, usdc_bal = get_token_balances(w3)
                    if hype_bal > 0:
                        swap_w3 = rpc_manager.get_web3_for_swap()
                        swap_pool = get_pool_contract(swap_w3)
                        swap_cache = _get_pool_cache(swap_w3, swap_pool)
                        amt_out = swap_exact_input_single(
                            swap_w3, config.HYPE_ADDRESS, config.USDC_ADDRESS,
                            swap_cache["fee"], hype_bal, config.DRY_RUN, priority=True,
                        )
                        if amt_out is not None:
                            final_usdc = (usdc_bal + amt_out) / 10 ** USDC_DECIMALS
                            chart_logger.sl_triggered(current_price, final_usdc)
                        logger.info("[DOWNWARD-INNER] All wHYPE swapped to USDC")

                    pool_opened = False
                    success = True
                    logger.info("[DOWNWARD-INNER] Position closed and swapped successfully")

            except TxFeeExceeded as e:
                logger.warning(f"[DOWNWARD-INNER] {e}, retrying in {config.DOWNWARD_INNER_CYCLE_INTERVAL}s")
            except (Web3Exception, ConnectionError, TimeoutError) as e:
                logger.warning(f"[DOWNWARD-INNER] RPC error: {sanitize_err(str(e))}, cycling provider")
                if w3 is not None:
                    _handle_rpc_error(e, w3)
            except Exception as e:
                logger.warning(f"[DOWNWARD-INNER] Error: {sanitize_err(str(e))}, retrying in {config.DOWNWARD_INNER_CYCLE_INTERVAL}s")

            if success:
                downward_inner_event.clear()
                break

            elapsed = time() - cycle_start
            remaining = config.DOWNWARD_INNER_CYCLE_INTERVAL - elapsed
            for _ in range(int(max(remaining, 0))):
                if not running:
                    break
                sleep(1)


def _upward_inner_cycle():
    global running, tx_lock, token_id_ref, pool_opened

    while running:
        upward_inner_event.wait()
        if not running:
            return

        logger.info("[UPWARD-INNER] Inner cycle triggered: closing position and swapping USDC to HYPE")

        while running:
            cycle_start = time()
            success = False

            w3 = None
            try:
                w3 = get_web3()
                pool = get_pool_contract(w3)
                pm = get_position_manager_contract(w3)
                cache = _get_pool_cache(w3, pool)

                tid = token_id_ref[0]
                pos = None
                if tid > 0:
                    pos = get_position_details(w3, pm, tid)
                if not pos or pos["liquidity"] == 0:
                    new_id, pos = _auto_discover_position(w3, pm, cache["token0"], cache["token1"])
                    if new_id is not None:
                        token_id_ref[0] = new_id
                        tid = new_id

                if tid > 0 and pos and pos["liquidity"] > 0:
                    current_price = _close_pool_3rpc( cache, tid, pos, config.DRY_RUN)
                    set_hype_price(current_price)
                else:
                    current_price, _, _, _, _ = _multicall_price_and_balances(w3, pool)
                    set_hype_price(current_price)

                with tx_lock:
                    if config.TP_AGGRESSIVE:
                        hype_bal, usdc_bal = get_token_balances(w3)
                        if usdc_bal > 0:
                            swap_w3 = rpc_manager.get_web3_for_swap()
                            swap_pool = get_pool_contract(swap_w3)
                            swap_cache = _get_pool_cache(swap_w3, swap_pool)
                            amt_out = swap_exact_input_single(
                                swap_w3, config.USDC_ADDRESS, config.HYPE_ADDRESS,
                                swap_cache["fee"], usdc_bal, config.DRY_RUN, priority=True,
                            )
                            if amt_out is not None:
                                final_hype = (hype_bal + amt_out) / 10 ** HYPE_DECIMALS
                                total = final_hype * current_price
                                chart_logger.tp_triggered(current_price, total)
                            logger.info("[UPWARD-INNER] All USDC swapped to HYPE")
                    else:
                        logger.info("[UPWARD-INNER] TP_AGGRESSIVE=false, skipping swap")

                    pool_opened = False
                    success = True
                    logger.info("[UPWARD-INNER] Position closed and swapped successfully")

            except TxFeeExceeded as e:
                logger.warning(f"[UPWARD-INNER] {e}, retrying in {config.UPWARD_INNER_CYCLE_INTERVAL}s")
            except (Web3Exception, ConnectionError, TimeoutError) as e:
                logger.warning(f"[UPWARD-INNER] RPC error: {sanitize_err(str(e))}, cycling provider")
                if w3 is not None:
                    _handle_rpc_error(e, w3)
            except Exception as e:
                logger.warning(f"[UPWARD-INNER] Error: {sanitize_err(str(e))}, retrying in {config.UPWARD_INNER_CYCLE_INTERVAL}s")

            if success:
                upward_inner_event.clear()
                logger.info(f"[UPWARD-INNER] Waiting {config.UPWARD_DELAY}s before main cycle...")
                for _ in range(config.UPWARD_DELAY):
                    if not running:
                        break
                    sleep(1)
                upper_threshold_event.set()
                break

            elapsed = time() - cycle_start
            remaining = config.UPWARD_INNER_CYCLE_INTERVAL - elapsed
            for _ in range(int(max(remaining, 0))):
                if not running:
                    break
                sleep(1)


def run_bot():
    global running, token_id_ref, pool_opened, _logged_initial_pool, _cached_price, _cached_lower_price, _cached_upper_price, _cached_drop_trigger, _cached_surge_trigger
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config.validate()
    token_id = config.TOKEN_ID
    dry_run = config.DRY_RUN

    if not dry_run:
        for attempt in range(3):
            w3 = None
            try:
                w3 = get_web3()
                hype = get_hype_contract(w3)
                usdc = get_usdc_contract(w3)
                max_approve = 2 ** 256 - 1
                approve_token(w3, hype, config.SWAP_ROUTER_ADDRESS, max_approve, dry_run)
                approve_token(w3, usdc, config.SWAP_ROUTER_ADDRESS, max_approve, dry_run)
                break
            except (Web3Exception, ConnectionError, TimeoutError) as e:
                logger.warning(f"Startup approval failed: {sanitize_err(str(e))}, rotating provider")
                if w3 is not None:
                    rpc_manager.on_error(w3)
                if attempt >= 2:
                    logger.error("Startup approvals failed after 3 attempts, continuing...")

    if dry_run:
        logger.info("=" * 50)
        logger.info("DRY-RUN MODE: No transactions will be sent")
        logger.info("=" * 50)

    logger.info("Starting HYPE/USDC Liquidity Bot (optimised)")
    logger.info(
        f"SLEEP_INTERVAL={config.SLEEP_INTERVAL}s, "
        f"LOWER={config.LOWER_BOUND_PCT}, UPPER={config.UPPER_BOUND_PCT}"
    )
    logger.info(
        f"Secondary cycle: interval={config.SECONDARY_INNER}s, "
        f"drop={config.HYPE_DROP_THRESHOLD}, surge={config.HYPE_UPPER_THRESHOLD}"
    )
    logger.info(f"TX_INTER_SLEEP={config.TX_INTER_SLEEP}s, PRIORITY_FEE_MULTIPLIER={config.PRIORITY_FEE_MULTIPLIER}x")
    logger.info(f"MIN_WALLET_USD={config.MIN_WALLET_USD}")

    token_id_ref[0] = token_id
    secondary_thread = threading.Thread(target=_secondary_cycle, daemon=True)
    downward_inner_thread = threading.Thread(target=_downward_inner_cycle, daemon=True)
    upward_inner_thread = threading.Thread(target=_upward_inner_cycle, daemon=True)
    secondary_thread.start()
    downward_inner_thread.start()
    upward_inner_thread.start()

    while running:
        cycle_error = False
        position_minted = False
        cycle_start = time()
        logger.info("=" * 50)
        logger.info(f"Cycle start: {cycle_start}")

        if downward_trigger_event.is_set():
            logger.info("Downward trigger detected, sleeping 1h before next cycle...")
            downward_trigger_event.clear()
            for _ in range(config.DOWNWARD_COOLDOWN):
                if not running:
                    break
                sleep(1)
            continue

        w3 = None
        try:
            w3 = get_web3()
            pool = get_pool_contract(w3)
            pm = get_position_manager_contract(w3)
            cache = _get_pool_cache(w3, pool)

            current_price, current_tick, sqrt_price_x96, hype_bal, usdc_bal = _multicall_price_and_balances(w3, pool)
            logger.info(f"Current price: {current_price:.6f} USDC/HYPE")
            logger.info(f"Current tick: {current_tick}")

            invert = not cache["token0_is_hype"]

            pos = None
            if token_id > 0:
                pos = get_position_details(w3, pm, token_id)
            if not pos or pos["liquidity"] == 0:
                new_id, pos = _auto_discover_position(w3, pm, cache["token0"], cache["token1"])
                if new_id is not None and new_id != token_id:
                    token_id = new_id
                    token_id_ref[0] = new_id
                    config.TOKEN_ID = new_id

            set_hype_price(current_price)
            try:
                with tx_lock:
                    if pos and pos["liquidity"] > 0:
                        pool_opened = True
                        lower_price = tick_to_price(pos["tickLower"], cache["dec0"], cache["dec1"], invert)
                        upper_price = tick_to_price(pos["tickUpper"], cache["dec0"], cache["dec1"], invert)
                        logger.info(f"Position: [{lower_price:.4f} - {upper_price:.4f}] USDC/HYPE")
                        logger.info(f"Liquidity: {pos['liquidity']}")

                        pos_val = position_value_usd(
                            pos["liquidity"], pos["tickLower"], pos["tickUpper"],
                            sqrt_price_x96, cache["token0_is_hype"], current_price,
                            cache["dec0"], cache["dec1"],
                        )
                        logger.info(f"Position value: ~${pos_val:.2f}")

                        wallet_val = calculate_usdc_value(
                            current_price,
                            usdc_bal / 10 ** USDC_DECIMALS,
                            hype_bal / 10 ** HYPE_DECIMALS,
                        )
                        logger.info(f"Wallet value: ~${wallet_val:.2f}")

                        if not _logged_initial_pool:
                            target_lower = current_price * config.LOWER_BOUND_PCT
                            target_upper = current_price * config.UPPER_BOUND_PCT
                            tp = target_upper * config.HYPE_UPPER_THRESHOLD
                            sl = target_lower * config.HYPE_DROP_THRESHOLD
                            chart_logger.initial_pool(current_price, target_lower, target_upper, tp, sl, wallet_val + pos_val)
                            _logged_initial_pool = True
                        _cached_price = current_price
                        _cached_lower_price = lower_price
                        _cached_upper_price = upper_price
                        _cached_drop_trigger = lower_price * config.HYPE_DROP_THRESHOLD
                        _cached_surge_trigger = upper_price * config.HYPE_UPPER_THRESHOLD

                        in_range = lower_price <= current_price <= upper_price

                        if pos_val > 1.0:
                            if not in_range:
                                direction = "below" if current_price < lower_price else "above"
                                logger.warning(f"Position out of bounds ({direction}), closing and recreating...")
                                _close_pool_3rpc( cache, token_id, pos, dry_run)
                                new_id = create_position(
                                    w3, pm, pool, current_price, dry_run,
                                    pool_fee=cache["fee"],
                                    token0=cache["token0"], token1=cache["token1"],
                                    token0_is_hype=cache["token0_is_hype"],
                                )
                                if new_id is not None:
                                    token_id = new_id
                                    token_id_ref[0] = new_id
                                    config.TOKEN_ID = new_id
                                    position_minted = True
                                    pool_opened = True
                                    target_lower = current_price * config.LOWER_BOUND_PCT
                                    target_upper = current_price * config.UPPER_BOUND_PCT
                                    tp = target_upper * config.HYPE_UPPER_THRESHOLD
                                    sl = target_lower * config.HYPE_DROP_THRESHOLD
                                    if not _logged_initial_pool:
                                        chart_logger.initial_pool(current_price, target_lower, target_upper, tp, sl, wallet_val + pos_val)
                                        _logged_initial_pool = True
                                    else:
                                        chart_logger.pool_created(current_price, target_lower, target_upper, tp, sl, wallet_val + pos_val)
                                    logger.info(f"Created new position ID {token_id}")
                                    _cached_price = current_price
                                    _cached_lower_price = target_lower
                                    _cached_upper_price = target_upper
                                    _cached_drop_trigger = sl
                                    _cached_surge_trigger = tp
                                else:
                                    pool_opened = False
                            else:
                                logger.info("Position in range and active.")

                                if wallet_val > config.MIN_WALLET_USD:
                                    logger.info(f"Wallet ${wallet_val:.2f} > ${config.MIN_WALLET_USD}, adding funds...")
                                    add_to_position(rpc_manager.get_web3_for_swap(), pm, pool, token_id, current_price, pos, dry_run, pool_fee=cache["fee"])
                                else:
                                    logger.info(f"Wallet ${wallet_val:.2f} <= ${config.MIN_WALLET_USD}, checking fees...")

                                fg0, fg1, cur_tick, lower_tick, upper_tick = _multicall_fee_growth_and_ticks(w3, pool, pos)
                                fee_owed_0, fee_owed_1 = _compute_unclaimed_fees(pos, fg0, fg1, cur_tick, lower_tick, upper_tick)
                                logger.info(
                                    f"Unclaimed fees: {fee_owed_0 / 10**cache['dec0']:.6f} t0, "
                                    f"{fee_owed_1 / 10**cache['dec1']:.6f} t1"
                                )
                                fee_val_usd = calculate_usdc_value(
                                    current_price,
                                    (fee_owed_1 / 10**cache['dec1']) if not cache["token0_is_hype"] else 0,
                                    (fee_owed_0 / 10**cache['dec0']) if cache["token0_is_hype"] else 0,
                                )
                                if fee_val_usd >= config.FEE_COMPOUND_THRESHOLD_USD:
                                    logger.info(f"Fees ~${fee_val_usd:.2f} above threshold, compounding...")
                                    am0, am1 = collect_fees(w3, pm, token_id, dry_run)
                                    if (am0 or 0) > 0 or (am1 or 0) > 0:
                                        sleep(config.TX_INTER_SLEEP)
                                        approve_token(w3, get_hype_or_usdc(w3, cache["token0_is_hype"]),
                                                      config.POSITION_MANAGER_ADDRESS, am0 or 0, dry_run)
                                        approve_token(w3, get_hype_or_usdc(w3, not cache["token0_is_hype"]),
                                                      config.POSITION_MANAGER_ADDRESS, am1 or 0, dry_run)
                                        sleep(config.TX_INTER_SLEEP)
                                        increase_liquidity(w3, pm, token_id, am0 or 0, am1 or 0, dry_run)
                        else:
                            logger.warning(f"Position value ${pos_val:.2f} <= $1, closing and recreating...")
                            _close_pool_3rpc( cache, token_id, pos, dry_run)
                            new_id = create_position(
                                w3, pm, pool, current_price, dry_run,
                                pool_fee=cache["fee"],
                                token0=cache["token0"], token1=cache["token1"],
                                token0_is_hype=cache["token0_is_hype"],
                            )
                            if new_id is not None:
                                token_id = new_id
                                token_id_ref[0] = new_id
                                config.TOKEN_ID = new_id
                                position_minted = True
                                pool_opened = True
                                target_lower = current_price * config.LOWER_BOUND_PCT
                                target_upper = current_price * config.UPPER_BOUND_PCT
                                tp = target_upper * config.HYPE_UPPER_THRESHOLD
                                sl = target_lower * config.HYPE_DROP_THRESHOLD
                                if not _logged_initial_pool:
                                    chart_logger.initial_pool(current_price, target_lower, target_upper, tp, sl, wallet_val + pos_val)
                                    _logged_initial_pool = True
                                else:
                                    chart_logger.pool_created(current_price, target_lower, target_upper, tp, sl, wallet_val + pos_val)
                                logger.info(f"Created new position ID {token_id}")
                                _cached_price = current_price
                                _cached_lower_price = target_lower
                                _cached_upper_price = target_upper
                                _cached_drop_trigger = sl
                                _cached_surge_trigger = tp
                            else:
                                pool_opened = False
                    else:
                        pool_opened = False
                        logger.info("No active position found. Creating new position...")
                        new_id = create_position(
                            w3, pm, pool, current_price, dry_run,
                            pool_fee=cache["fee"],
                            token0=cache["token0"], token1=cache["token1"],
                            token0_is_hype=cache["token0_is_hype"],
                        )
                        if new_id is not None:
                            token_id = new_id
                            token_id_ref[0] = new_id
                            position_minted = True
                            pool_opened = True
                            fresh_hype, fresh_usdc = get_token_balances(w3)
                            wallet_val = calculate_usdc_value(current_price, fresh_usdc / 10**USDC_DECIMALS, fresh_hype / 10**HYPE_DECIMALS)
                            new_pos = get_position_details(w3, pm, new_id)
                            if new_pos:
                                pos_val = position_value_usd(
                                    new_pos["liquidity"], new_pos["tickLower"], new_pos["tickUpper"],
                                    sqrt_price_x96, cache["token0_is_hype"], current_price,
                                    cache["dec0"], cache["dec1"],
                                )
                            else:
                                pos_val = 0
                            total_val = wallet_val + pos_val
                            target_lower = current_price * config.LOWER_BOUND_PCT
                            target_upper = current_price * config.UPPER_BOUND_PCT
                            tp = target_upper * config.HYPE_UPPER_THRESHOLD
                            sl = target_lower * config.HYPE_DROP_THRESHOLD
                            if not _logged_initial_pool:
                                chart_logger.initial_pool(current_price, target_lower, target_upper, tp, sl, total_val)
                                _logged_initial_pool = True
                            else:
                                chart_logger.pool_created(current_price, target_lower, target_upper, tp, sl, total_val)
                        config.TOKEN_ID = new_id
                        logger.info(f"Created new position ID {token_id}")
                        _cached_price = current_price
                        _cached_lower_price = target_lower
                        _cached_upper_price = target_upper
                        _cached_drop_trigger = sl
                        _cached_surge_trigger = tp
            except TxFeeExceeded as e:
                logger.warning(f"{e}, skipping to next cycle with 60s delay")
                cycle_error = True

        except (Web3Exception, ConnectionError, TimeoutError) as e:
            logger.warning(f"RPC error in main cycle: {sanitize_err(str(e))}, rotating provider")
            if w3 is not None:
                _handle_rpc_error(e, w3)
            sleep(5)
            continue
        except Exception as e:
            logger.warning(f"Cycle error: {sanitize_err(str(e))}")
            cycle_error = True

        elapsed = time() - cycle_start
        if cycle_error:
            remaining = 10
        elif position_minted:
            remaining = 30
        else:
            remaining = config.SLEEP_INTERVAL - elapsed
        cycle_error = False
        if remaining > 0 and running:
            logger.info(f"Cycle complete. Sleeping for {remaining:.0f}s...")
            for _ in range(int(remaining)):
                if not running or upper_threshold_event.is_set():
                    break
                sleep(1)
            if upper_threshold_event.is_set():
                logger.info("Upper threshold triggered by secondary, starting next cycle...")
                upper_threshold_event.clear()

    try:
        w3 = get_web3()
        pool = get_pool_contract(w3)
        final_price, _, _, final_hype, final_usdc = _multicall_price_and_balances(w3, pool)
        wallet_val = calculate_usdc_value(final_price, final_usdc / 10 ** USDC_DECIMALS, final_hype / 10 ** HYPE_DECIMALS)
        chart_logger.bot_stopped(final_price, wallet_val)
    except Exception:
        if chart_logger.last_balance is not None and chart_logger.last_price is not None:
            chart_logger.bot_stopped(chart_logger.last_price, chart_logger.last_balance)

    logger.info("Bot stopped.")


def _auto_discover_position(w3, pm, pool_t0, pool_t1):
    from src.provider import get_account
    account = get_account(w3)
    _ERC721_ABI = [
        {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
        {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "index", "type": "uint256"}], "name": "tokenOfOwnerByIndex", "outputs": [{"name": "tokenId", "type": "uint256"}], "type": "function"},
    ]
    erc721 = w3.eth.contract(address=pm.address, abi=_ERC721_ABI)
    try:
        balance = erc721.functions.balanceOf(account.address).call()
        for i in range(balance):
            tid = erc721.functions.tokenOfOwnerByIndex(account.address, i).call()
            try:
                raw = pm.functions.positions(tid).call()
                if raw[2].lower() == pool_t0.lower() and raw[3].lower() == pool_t1.lower() and raw[7] > 0:
                    pos = get_position_details(w3, pm, tid)
                    logger.info(f"Auto-discovered position {tid} with liquidity {raw[7]}")
                    return tid, pos
            except Exception:
                pass
    except Exception:
        pass
    return None, None
