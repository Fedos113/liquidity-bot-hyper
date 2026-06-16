import logging
import signal
import threading
from time import sleep, time

from src.config import config
from src.provider import get_web3, get_pool_contract, get_position_manager_contract
from src.math_utils import (
    get_tick_spacing,
    get_token_order,
    tick_to_price,
    calculate_usdc_value,
    calculate_bounds,
    position_value_usd,
)
from src.position_manager import (
    get_current_price,
    get_position_details,
    get_unclaimed_fees,
    add_to_position,
    rebalance,
    create_position,
    collect_fees,
    remove_liquidity,
    increase_liquidity,
    get_token_balances,
    approve_token,
    get_hype_or_usdc,
    wrap_hype,
    swap_exact_input_single,
    TxFeeExceeded,
    set_hype_price,
)

logger = logging.getLogger("liqbot")

running = True
tx_lock = threading.Lock()
token_id_ref = [0]
upper_threshold_event = threading.Event()


def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received, stopping after current cycle...")
    running = False


def _downward_cycle():
    global running, tx_lock, token_id_ref

    logger.info(f"[DOWNWARD] Initial delay {config.DOWNWARD_CYCLE_INTERVAL}s before first cycle")
    for _ in range(config.DOWNWARD_CYCLE_INTERVAL):
        if not running:
            return
        sleep(1)

    while running:
        cycle_start = time()
        logger.info("[DOWNWARD] Cycle start")

        if tx_lock.locked():
            logger.info("[DOWNWARD] Main cycle active, skipping RPC reads")
            elapsed = time() - cycle_start
            remaining = config.DOWNWARD_CYCLE_INTERVAL - elapsed
            for _ in range(int(max(remaining, 0))):
                if not running:
                    break
                sleep(1)
            continue

        try:
            w3 = get_web3()
            pool = get_pool_contract(w3)
            pm = get_position_manager_contract(w3)
            pool_t0 = pool.functions.token0().call()
            pool_t1 = pool.functions.token1().call()

            tid = token_id_ref[0]
            pos = None
            if tid > 0:
                pos = get_position_details(w3, pm, tid)
            if not pos or pos["liquidity"] == 0:
                new_id, pos = _auto_discover_position(w3, pm, pool_t0, pool_t1)
                if new_id is not None:
                    token_id_ref[0] = new_id
                    tid = new_id

            if pos and pos["liquidity"] > 0:
                current_price, current_tick, _ = get_current_price(w3, pool)
                token0_is_hype, dec0, dec1 = get_token_order(pool, config.HYPE_ADDRESS)
                invert = not token0_is_hype
                lower_price = tick_to_price(pos["tickLower"], dec0, dec1, invert)
                upper_price = tick_to_price(pos["tickUpper"], dec0, dec1, invert)
                trigger_price = lower_price * config.HYPE_DROP_THRESHOLD

                logger.info(
                    f"[DOWNWARD] Price=${current_price:.4f} "
                    f"lower=${lower_price:.4f} upper=${upper_price:.4f} "
                    f"drop_trigger=${trigger_price:.4f}"
                )

                if current_price < trigger_price:
                    logger.warning(
                        f"[DOWNWARD] Price ${current_price:.4f} dropped >{((1 - config.HYPE_DROP_THRESHOLD) * 100):.0f}% "
                        f"below lower bound ${lower_price:.4f}. Closing position..."
                    )
                    with tx_lock:
                        set_hype_price(current_price)
                        try:
                            collect_fees(w3, pm, tid, config.DRY_RUN)
                            remove_liquidity(w3, pm, tid, pos["liquidity"], config.DRY_RUN)
                            collect_fees(w3, pm, tid, config.DRY_RUN)

                            hype_bal, usdc_bal = get_token_balances(w3)
                            if hype_bal > 0:
                                pool_fee = pool.functions.fee().call()
                                approve_token(
                                    w3, get_hype_or_usdc(w3, True),
                                    config.SWAP_ROUTER_ADDRESS, hype_bal, config.DRY_RUN,
                                )
                                swap_exact_input_single(
                                    w3, config.HYPE_ADDRESS, config.USDC_ADDRESS,
                                    pool_fee, hype_bal, config.DRY_RUN,
                                )
                                logger.info("[DOWNWARD] Position closed, all wHYPE swapped to USDC")
                        except TxFeeExceeded as e:
                            logger.warning(f"[DOWNWARD] {e}, skipping to next cycle with 60s delay")
                            elapsed = time() - cycle_start
                            remaining = 60
                            for _ in range(int(max(remaining, 0))):
                                if not running:
                                    break
                                sleep(1)
                            continue
                else:
                    logger.info("[DOWNWARD] Price above drop threshold, no action")
            else:
                logger.info("[DOWNWARD] No active position")

        except Exception as e:
            logger.warning(f"[DOWNWARD] Cycle error: {e}")

        elapsed = time() - cycle_start
        remaining = config.DOWNWARD_CYCLE_INTERVAL - elapsed
        for _ in range(int(max(remaining, 0))):
            if not running:
                break
            sleep(1)


def _upward_cycle():
    global running, tx_lock, token_id_ref

    logger.info(f"[UPWARD] Initial delay {config.UPWARD_CYCLE_INTERVAL}s before first cycle")
    for _ in range(config.UPWARD_CYCLE_INTERVAL):
        if not running:
            return
        sleep(1)

    while running:
        cycle_start = time()
        logger.info("[UPWARD] Cycle start")

        if tx_lock.locked():
            logger.info("[UPWARD] Main cycle active, skipping RPC reads")
            elapsed = time() - cycle_start
            remaining = config.UPWARD_CYCLE_INTERVAL - elapsed
            for _ in range(int(max(remaining, 0))):
                if not running:
                    break
                sleep(1)
            continue

        try:
            w3 = get_web3()
            pool = get_pool_contract(w3)
            pm = get_position_manager_contract(w3)
            pool_t0 = pool.functions.token0().call()
            pool_t1 = pool.functions.token1().call()

            tid = token_id_ref[0]
            pos = None
            if tid > 0:
                pos = get_position_details(w3, pm, tid)
            if not pos or pos["liquidity"] == 0:
                new_id, pos = _auto_discover_position(w3, pm, pool_t0, pool_t1)
                if new_id is not None:
                    token_id_ref[0] = new_id
                    tid = new_id

            if pos and pos["liquidity"] > 0:
                current_price, current_tick, _ = get_current_price(w3, pool)
                token0_is_hype, dec0, dec1 = get_token_order(pool, config.HYPE_ADDRESS)
                invert = not token0_is_hype
                upper_price = tick_to_price(pos["tickUpper"], dec0, dec1, invert)
                upper_trigger_price = upper_price * config.HYPE_UPPER_THRESHOLD

                logger.info(
                    f"[UPWARD] Price=${current_price:.4f} "
                    f"upper=${upper_price:.4f} "
                    f"surge_trigger=${upper_trigger_price:.4f}"
                )

                if current_price > upper_trigger_price:
                    logger.warning(
                        f"[UPWARD] Price ${current_price:.4f} surged >{((config.HYPE_UPPER_THRESHOLD - 1) * 100):.0f}% "
                        f"above upper bound ${upper_price:.4f}. Signaling main cycle to skip sleep..."
                    )
                    upper_threshold_event.set()
                else:
                    logger.info("[UPWARD] Price below surge threshold, no action")
            else:
                logger.info("[UPWARD] No active position")

        except Exception as e:
            logger.warning(f"[UPWARD] Cycle error: {e}")

        elapsed = time() - cycle_start
        remaining = config.UPWARD_CYCLE_INTERVAL - elapsed
        for _ in range(int(max(remaining, 0))):
            if not running:
                break
            sleep(1)


def run_bot():
    global running, token_id_ref, upper_threshold_event
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config.validate()
    token_id = config.TOKEN_ID
    dry_run = config.DRY_RUN

    if dry_run:
        logger.info("=" * 50)
        logger.info("DRY-RUN MODE: No transactions will be sent")
        logger.info("=" * 50)

    logger.info("Starting HYPE/USDC Liquidity Bot")
    logger.info(
        f"SLEEP_INTERVAL={config.SLEEP_INTERVAL}s, "
        f"LOWER={config.LOWER_BOUND_PCT}, UPPER={config.UPPER_BOUND_PCT}"
    )
    logger.info(
        f"Downward protection cycle: interval={config.DOWNWARD_CYCLE_INTERVAL}s, "
        f"drop_threshold={config.HYPE_DROP_THRESHOLD}"
    )
    logger.info(
        f"Upward surge cycle: interval={config.UPWARD_CYCLE_INTERVAL}s, "
        f"upper_threshold={config.HYPE_UPPER_THRESHOLD}"
    )

    token_id_ref[0] = token_id
    downward_thread = threading.Thread(target=_downward_cycle, daemon=True)
    upward_thread = threading.Thread(target=_upward_cycle, daemon=True)
    downward_thread.start()
    upward_thread.start()

    while running:
        cycle_error = False
        position_minted = False
        cycle_start = time()
        logger.info("=" * 50)
        logger.info(f"Cycle start: {cycle_start}")

        try:
            w3 = get_web3()
            pool = get_pool_contract(w3)
            pm = get_position_manager_contract(w3)
            pool_t0 = pool.functions.token0().call()
            pool_t1 = pool.functions.token1().call()

            current_price, current_tick, sqrt_price_x96 = get_current_price(w3, pool)
            logger.info(f"Current price: {current_price:.6f} USDC/HYPE")
            logger.info(f"Current tick: {current_tick}")

            token0_is_hype, dec0, dec1 = get_token_order(pool, config.HYPE_ADDRESS)
            tick_spacing = get_tick_spacing(pool)
            invert = not token0_is_hype

            # Find active position
            pos = None
            if token_id > 0:
                pos = get_position_details(w3, pm, token_id)
            if not pos or pos["liquidity"] == 0:
                new_id, pos = _auto_discover_position(w3, pm, pool_t0, pool_t1)
                if new_id is not None and new_id != token_id:
                    token_id = new_id
                    token_id_ref[0] = new_id
                    config.TOKEN_ID = new_id

            set_hype_price(current_price)
            try:
                with tx_lock:
                    if pos and pos["liquidity"] > 0:
                        lower_price = tick_to_price(pos["tickLower"], dec0, dec1, invert)
                        upper_price = tick_to_price(pos["tickUpper"], dec0, dec1, invert)
                        logger.info(f"Position: [{lower_price:.4f} - {upper_price:.4f}] USDC/HYPE")
                        logger.info(f"Liquidity: {pos['liquidity']}")

                        pos_val = position_value_usd(
                            pos["liquidity"], pos["tickLower"], pos["tickUpper"],
                            sqrt_price_x96, token0_is_hype, current_price, dec0, dec1,
                        )
                        logger.info(f"Position value: ~${pos_val:.2f}")

                        hype_bal, usdc_bal = get_token_balances(w3)
                        wallet_val = calculate_usdc_value(
                            current_price,
                            usdc_bal / 10 ** config.USDC_DECIMALS,
                            hype_bal / 10 ** config.HYPE_DECIMALS,
                        )
                        logger.info(f"Wallet value: ~${wallet_val:.2f}")

                        in_range = lower_price <= current_price <= upper_price

                        if pos_val > 1.0:
                            if not in_range:
                                direction = "below" if current_price < lower_price else "above"
                                logger.warning(f"Position out of bounds ({direction}), closing and recreating...")
                                collect_fees(w3, pm, token_id, dry_run)
                                remove_liquidity(w3, pm, token_id, pos["liquidity"], dry_run)
                                collect_fees(w3, pm, token_id, dry_run)
                                new_id = create_position(w3, pm, pool, current_price, dry_run)
                                if new_id is not None:
                                    token_id = new_id
                                    token_id_ref[0] = new_id
                                    config.TOKEN_ID = new_id
                                    position_minted = True
                                    logger.info(f"Created new position ID {token_id}")
                            else:
                                logger.info("Position in range and active.")

                                if wallet_val > 0.2:
                                    logger.info(f"Wallet ${wallet_val:.2f} > $0.2, adding funds...")
                                    add_to_position(w3, pm, pool, token_id, current_price, pos, dry_run)
                                else:
                                    logger.info(f"Wallet ${wallet_val:.2f} <= $0.2, checking fees...")

                                fee_owed_0, fee_owed_1 = get_unclaimed_fees(w3, pool, pos)
                                logger.info(
                                    f"Unclaimed fees: {fee_owed_0 / 10**dec0:.6f} t0, "
                                    f"{fee_owed_1 / 10**dec1:.6f} t1"
                                )
                                fee_val_usd = calculate_usdc_value(
                                    current_price,
                                    (fee_owed_1 / 10**dec1) if not token0_is_hype else 0,
                                    (fee_owed_0 / 10**dec0) if token0_is_hype else 0,
                                )
                                if fee_val_usd >= config.FEE_COMPOUND_THRESHOLD_USD:
                                    logger.info(f"Fees ~${fee_val_usd:.2f} above threshold, compounding...")
                                    am0, am1 = collect_fees(w3, pm, token_id, dry_run)
                                    if (am0 or 0) > 0 or (am1 or 0) > 0:
                                        approve_token(w3, get_hype_or_usdc(w3, token0_is_hype),
                                                      config.POSITION_MANAGER_ADDRESS, am0 or 0, dry_run)
                                        approve_token(w3, get_hype_or_usdc(w3, not token0_is_hype),
                                                      config.POSITION_MANAGER_ADDRESS, am1 or 0, dry_run)
                                        increase_liquidity(w3, pm, token_id, am0 or 0, am1 or 0, dry_run)
                        else:
                            logger.warning(f"Position value ${pos_val:.2f} <= $1, closing and recreating...")
                            collect_fees(w3, pm, token_id, dry_run)
                            remove_liquidity(w3, pm, token_id, pos["liquidity"], dry_run)
                            collect_fees(w3, pm, token_id, dry_run)
                            new_id = create_position(w3, pm, pool, current_price, dry_run)
                            if new_id is not None:
                                token_id = new_id
                                token_id_ref[0] = new_id
                                config.TOKEN_ID = new_id
                                position_minted = True
                                logger.info(f"Created new position ID {token_id}")
                    else:
                        logger.info("No active position found. Creating new position...")
                        new_id = create_position(w3, pm, pool, current_price, dry_run)
                        if new_id is not None:
                            token_id = new_id
                            token_id_ref[0] = new_id
                            position_minted = True
                        config.TOKEN_ID = new_id
                        logger.info(f"Created position ID {token_id}")
            except TxFeeExceeded as e:
                logger.warning(f"{e}, skipping to next cycle with 60s delay")
                cycle_error = True

        except Exception as e:
            logger.warning(f"Cycle error, retrying in 60s: {e}")
            cycle_error = True

        elapsed = time() - cycle_start
        remaining = 60 if cycle_error or position_minted else config.SLEEP_INTERVAL - elapsed
        cycle_error = False
        if remaining > 0 and running:
            logger.info(f"Cycle complete. Sleeping for {remaining:.0f}s... (type 'skip' + Enter to start next cycle now)")
            import sys
            skip_flag = threading.Event()
            def stdin_listener():
                try:
                    while True:
                        line = sys.stdin.readline()
                        if not line:
                            break
                        if line.strip().lower() == "skip":
                            skip_flag.set()
                            break
                except (EOFError, OSError):
                    pass
            threading.Thread(target=stdin_listener, daemon=True).start()
            for _ in range(int(remaining)):
                if not running or skip_flag.is_set() or upper_threshold_event.is_set():
                    break
                sleep(1)
            if skip_flag.is_set():
                logger.info("Skip received, starting next cycle...")
            if upper_threshold_event.is_set():
                logger.info("Upper threshold triggered by secondary, starting next cycle...")
                upper_threshold_event.clear()

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
