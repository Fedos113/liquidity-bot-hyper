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
)
from src.position_manager import (
    get_current_price,
    get_position_details,
    rebalance,
    create_position,
    collect_fees,
    increase_liquidity,
    get_token_balances,
    approve_token,
    get_hype_or_usdc,
    wrap_hype,
)

logger = logging.getLogger("liqbot")

running = True


def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received, stopping after current cycle...")
    running = False


def run_bot():
    global running
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

    while running:
        cycle_start = time()
        logger.info("=" * 50)
        logger.info(f"Cycle start: {cycle_start}")

        try:
            w3 = get_web3()
            pool = get_pool_contract(w3)
            pm = get_position_manager_contract(w3)

            current_price, current_tick, sqrt_price_x96 = get_current_price(w3, pool)
            logger.info(f"Current price: {current_price:.6f} USDC/HYPE")
            logger.info(f"Current tick: {current_tick}")

            token0_is_hype, dec0, dec1 = get_token_order(pool, config.HYPE_ADDRESS)
            tick_spacing = get_tick_spacing(pool)
            invert = not token0_is_hype

            pos = None
            if token_id > 0:
                pos = get_position_details(w3, pm, token_id)

            if pos and pos["liquidity"] > 0:
                lower_price = tick_to_price(pos["tickLower"], dec0, dec1, invert)
                upper_price = tick_to_price(pos["tickUpper"], dec0, dec1, invert)

                dist_lower = ((current_price - lower_price) / lower_price) * 100
                dist_upper = ((current_price - upper_price) / upper_price) * 100

                logger.info(
                    f"Position: [{lower_price:.4f} - {upper_price:.4f}] USDC/HYPE"
                )
                logger.info(f"Distance to lower: {dist_lower:.2f}%")
                logger.info(f"Distance to upper: {dist_upper:.2f}%")
                logger.info(f"Liquidity: {pos['liquidity']}")
                logger.info(
                    f"Unclaimed fees: {pos['amount0Collect'] / 10**dec0:.6f} t0, "
                    f"{pos['amount1Collect'] / 10**dec1:.6f} t1"
                )

                hype_bal, usdc_bal = get_token_balances(w3)
                total_value = calculate_usdc_value(
                    current_price,
                    usdc_bal / 10 ** config.USDC_DECIMALS,
                    hype_bal / 10 ** config.HYPE_DECIMALS,
                )
                logger.info(f"Total value: ~${total_value:.2f}")

                in_range = lower_price <= current_price <= upper_price
                if in_range:
                    logger.info("Status: IN RANGE - Position active and in range.")

                    fee_val0 = pos["amount0Collect"] / 10 ** dec0
                    fee_val1 = pos["amount1Collect"] / 10 ** dec1
                    fee_val_usd = calculate_usdc_value(
                        current_price,
                        fee_val1 if not token0_is_hype else 0,
                        fee_val0 if token0_is_hype else 0,
                    )
                    if fee_val0 > 0 or fee_val1 > 0:
                        logger.info(f"Fees value: ~${fee_val_usd:.2f}")

                    if fee_val_usd >= config.FEE_COMPOUND_THRESHOLD_USD:
                        logger.info(
                            f"Fees above ${config.FEE_COMPOUND_THRESHOLD_USD}, compounding..."
                        )
                        am0, am1 = collect_fees(w3, pm, token_id, dry_run)
                        if (am0 or 0) > 0 or (am1 or 0) > 0:
                            approve_token(
                                w3, get_hype_or_usdc(w3, token0_is_hype),
                                config.POSITION_MANAGER_ADDRESS, am0 or 0, dry_run,
                            )
                            approve_token(
                                w3, get_hype_or_usdc(w3, not token0_is_hype),
                                config.POSITION_MANAGER_ADDRESS, am1 or 0, dry_run,
                            )
                            increase_liquidity(w3, pm, token_id, am0 or 0, am1 or 0, dry_run)
                    else:
                        logger.info(
                            f"Fees (${fee_val_usd:.2f}) below ${config.FEE_COMPOUND_THRESHOLD_USD}"
                        )
                else:
                    direction = "below" if current_price < lower_price else "above"
                    logger.warning(f"Status: OUT OF RANGE - Price is {direction}")
                    new_id = rebalance(w3, pm, pool, token_id, current_price, dry_run)
                    if new_id is not None and new_id != token_id:
                        token_id = new_id
                        config.TOKEN_ID = new_id
                        logger.info(f"Updated token ID to {token_id}")
            elif token_id > 0 and pos is not None:
                logger.warning("Position exists but has no liquidity. Rebalancing...")
                new_id = rebalance(w3, pm, pool, token_id, current_price, dry_run)
                if new_id is not None and new_id != token_id:
                    token_id = new_id
                    config.TOKEN_ID = new_id
            else:
                if token_id == 0:
                    logger.info("No existing position. Attempting auto-creation...")
                    new_id = create_position(w3, pm, pool, current_price, dry_run)
                    if new_id is not None:
                        token_id = new_id
                        config.TOKEN_ID = new_id
                        logger.info(f"Auto-created position with token ID {token_id}")
                else:
                    logger.warning(f"Position {token_id} not found on-chain")

        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)

        elapsed = time() - cycle_start
        remaining = config.SLEEP_INTERVAL - elapsed
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
                if not running or skip_flag.is_set():
                    break
                sleep(1)
            if skip_flag.is_set():
                logger.info("Skip received, starting next cycle...")

    logger.info("Bot stopped.")
