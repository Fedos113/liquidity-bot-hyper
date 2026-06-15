import logging
from typing import Optional, Tuple

from web3 import Web3
from web3.types import TxReceipt

from src.config import config
from src.provider import with_retry, get_account
from src.math_utils import get_tick_spacing, get_token_order, calculate_bounds, get_price_from_sqrt_price

logger = logging.getLogger("liqbot")


def _record_tx_fee(tx_hash: str, fee_wei: int):
    try:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).resolve().parent.parent / "liqbot2" / "data" / "history.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO tx_fees (ts, tx_hash, fee_wei, description) VALUES (?, ?, ?, ?)",
                (int(__import__('time').time()), tx_hash, fee_wei, "tx"),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


def send_transaction(w3: Web3, tx: dict, dry_run: bool = False) -> Optional[TxReceipt]:
    if dry_run:
        logger.info(f"[DRY-RUN] Would send tx: from={tx['from']} nonce={tx['nonce']}")
        return None

    account = get_account(w3)
    signed_tx = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    logger.info(f"Tx sent: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt["status"] == 1:
        logger.info(f"Tx confirmed: {tx_hash.hex()} (gas used: {receipt['gasUsed']})")
        _record_tx_fee(tx_hash.hex(), receipt["gasUsed"] * tx.get("gasPrice", 0))
    else:
        revert_reason = ""
        try:
            w3.eth.call(tx, block_identifier=receipt["blockNumber"])
        except Exception as e:
            revert_reason = str(e)
        msg = f"Transaction reverted: {tx_hash.hex()}"
        if revert_reason:
            msg += f". Reason: {revert_reason}"
        logger.error(msg)
        raise ValueError(msg)

    return receipt


def build_deadline(w3: Web3, seconds_ahead: int = 600) -> int:
    return w3.eth.get_block("latest")["timestamp"] + seconds_ahead


def build_tx_params(w3: Web3, gas: int) -> dict:
    account = get_account(w3)
    return {
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
    }


@with_retry(max_retries=3, base_delay=2)
def get_current_price(w3: Web3, pool_contract) -> Tuple[float, int, int]:
    slot0 = pool_contract.functions.slot0().call()
    sqrt_price_x96 = slot0[0]
    current_tick = slot0[1]

    token0_is_hype, dec0, dec1 = get_token_order(pool_contract, config.HYPE_ADDRESS)
    invert = not token0_is_hype
    price = get_price_from_sqrt_price(sqrt_price_x96, dec0, dec1, invert)

    return price, current_tick, sqrt_price_x96


@with_retry(max_retries=3, base_delay=2)
def get_position_details(w3: Web3, position_manager, token_id: int) -> Optional[dict]:
    try:
        pos = position_manager.functions.positions(token_id).call()
        return {
            "nonce": pos[0],
            "operator": pos[1],
            "token0": pos[2],
            "token1": pos[3],
            "fee": pos[4],
            "tickLower": pos[5],
            "tickUpper": pos[6],
            "liquidity": pos[7],
            "amount0": pos[8],
            "amount1": pos[9],
            "amount0Collect": pos[10],
            "amount1Collect": pos[11],
        }
    except Exception as e:
        logger.warning(f"Could not fetch position {token_id}: {e}")
        return None


@with_retry(max_retries=3, base_delay=2)
def get_token_balances(w3: Web3) -> Tuple[int, int]:
    from src.provider import get_hype_contract, get_usdc_contract
    hype = get_hype_contract(w3)
    usdc = get_usdc_contract(w3)
    address = Web3.to_checksum_address(config.WALLET_ADDRESS)
    hype_bal = hype.functions.balanceOf(address).call()
    usdc_bal = usdc.functions.balanceOf(address).call()
    return hype_bal, usdc_bal


@with_retry(max_retries=3, base_delay=2)
def approve_token(w3: Web3, token_contract, spender: str, amount: int, dry_run: bool = False) -> None:
    account = get_account(w3)
    spender_addr = Web3.to_checksum_address(spender)
    current_allowance = token_contract.functions.allowance(account.address, spender_addr).call()
    if current_allowance >= amount:
        return

    logger.info(f"Approving {spender} for {amount}")
    tx = token_contract.functions.approve(spender_addr, amount).build_transaction(
        build_tx_params(w3, 100_000)
    )
    send_transaction(w3, tx, dry_run)


@with_retry(max_retries=3, base_delay=2)
def collect_fees(
    w3: Web3, position_manager, token_id: int, dry_run: bool = False
) -> Tuple[Optional[int], Optional[int]]:
    account = get_account(w3)
    deadline = build_deadline(w3)

    bal_before_0, bal_before_1 = get_token_balances(w3)

    tx = position_manager.functions.collect({
        "tokenId": token_id,
        "recipient": account.address,
        "amount0Max": 2 ** 128 - 1,
        "amount1Max": 2 ** 128 - 1,
    }).build_transaction(
        build_tx_params(w3, 200_000)
    )

    receipt = send_transaction(w3, tx, dry_run)
    if receipt:
        bal_after_0, bal_after_1 = get_token_balances(w3)
        amount0 = bal_after_0 - bal_before_0
        amount1 = bal_after_1 - bal_before_1
        logger.info(f"Collected fees: amount0={amount0}, amount1={amount1}")
        return amount0, amount1
    return None, None


@with_retry(max_retries=3, base_delay=2)
def remove_liquidity(
    w3: Web3, position_manager, token_id: int, liquidity: int, dry_run: bool = False
) -> Tuple[Optional[int], Optional[int]]:
    deadline = build_deadline(w3)

    bal_before_0, bal_before_1 = get_token_balances(w3)

    tx = position_manager.functions.decreaseLiquidity({
        "tokenId": token_id,
        "liquidity": liquidity,
        "amount0Min": 0,
        "amount1Min": 0,
        "deadline": deadline,
    }).build_transaction(
        build_tx_params(w3, 300_000)
    )

    receipt = send_transaction(w3, tx, dry_run)
    if receipt:
        bal_after_0, bal_after_1 = get_token_balances(w3)
        amount0 = bal_after_0 - bal_before_0
        amount1 = bal_after_1 - bal_before_1
        logger.info(f"Removed liquidity: amount0={amount0}, amount1={amount1}")
        return amount0, amount1
    return None, None


@with_retry(max_retries=3, base_delay=2)
def mint_position(
    w3: Web3,
    position_manager,
    pool_contract,
    tick_lower: int,
    tick_upper: int,
    amount0_desired: int,
    amount1_desired: int,
    fee_tier: int,
    dry_run: bool = False,
) -> Optional[int]:
    account = get_account(w3)
    deadline = build_deadline(w3)

    token0_addr = pool_contract.functions.token0().call()
    token1_addr = pool_contract.functions.token1().call()

    logger.info(
        f"Minting position: tickLower={tick_lower}, tickUpper={tick_upper}, "
        f"amount0={amount0_desired}, amount1={amount1_desired}"
    )

    mint_slippage = config.SLIPPAGE_TOLERANCE * 10
    amount0_min = int(amount0_desired * (1 - mint_slippage))
    amount1_min = int(amount1_desired * (1 - mint_slippage))
    tx = position_manager.functions.mint({
        "token0": token0_addr,
        "token1": token1_addr,
        "fee": fee_tier,
        "tickLower": tick_lower,
        "tickUpper": tick_upper,
        "amount0Desired": amount0_desired,
        "amount1Desired": amount1_desired,
        "amount0Min": amount0_min,
        "amount1Min": amount1_min,
        "recipient": account.address,
        "deadline": deadline,
    }).build_transaction(
        build_tx_params(w3, 500_000)
    )

    receipt = send_transaction(w3, tx, dry_run)
    if receipt:
        for log in receipt.get("logs", []):
            try:
                event = position_manager.events.Transfer().process_log(log)
                if event.args.to.lower() == account.address.lower():
                    return event.args.tokenId
            except Exception:
                pass
    return None


@with_retry(max_retries=3, base_delay=2)
def increase_liquidity(
    w3: Web3,
    position_manager,
    token_id: int,
    amount0_desired: int,
    amount1_desired: int,
    dry_run: bool = False,
) -> bool:
    if amount0_desired == 0 and amount1_desired == 0:
        return True

    deadline = build_deadline(w3)
    slippage = config.SLIPPAGE_TOLERANCE
    amount0_min = int(amount0_desired * (1 - slippage))
    amount1_min = int(amount1_desired * (1 - slippage))

    tx = position_manager.functions.increaseLiquidity({
        "tokenId": token_id,
        "amount0Desired": amount0_desired,
        "amount1Desired": amount1_desired,
        "amount0Min": amount0_min,
        "amount1Min": amount1_min,
        "deadline": deadline,
    }).build_transaction(
        build_tx_params(w3, 300_000)
    )

    receipt = send_transaction(w3, tx, dry_run)
    return receipt is not None and receipt["status"] == 1


def rebalance(
    w3: Web3,
    position_manager,
    pool_contract,
    token_id: int,
    current_price: float,
    dry_run: bool = False,
) -> Optional[int]:
    token0_is_hype, dec0, dec1 = get_token_order(pool_contract, config.HYPE_ADDRESS)
    tick_spacing = get_tick_spacing(pool_contract)
    invert = not token0_is_hype

    logger.info("=== Starting rebalance ===")

    pos = get_position_details(w3, position_manager, token_id)
    if not pos:
        logger.error("Cannot rebalance: position not found")
        return None

    logger.info(
        f"Old position: tickLower={pos['tickLower']}, "
        f"tickUpper={pos['tickUpper']}, liquidity={pos['liquidity']}"
    )

    if pos["liquidity"] > 0:
        logger.info("Step 1: Collecting fees...")
        collect_fees(w3, position_manager, token_id, dry_run)

        logger.info("Step 2: Removing liquidity...")
        remove_liquidity(w3, position_manager, token_id, pos["liquidity"], dry_run)
    else:
        logger.info("Position has no liquidity, skipping removal")

    logger.info("Step 3: Collecting remaining fees...")
    collect_fees(w3, position_manager, token_id, dry_run)

    logger.info("Step 4: Balancing tokens...")
    balance_tokens(w3, pool_contract, dry_run)

    logger.info("Step 5: Calculating new bounds...")
    tick_lower, tick_upper, actual_lower, actual_upper = calculate_bounds(
        current_price, config.LOWER_BOUND_PCT, config.UPPER_BOUND_PCT,
        dec0, dec1, tick_spacing, invert,
    )
    logger.info(
        f"New bounds: lower={actual_lower:.4f} (tick {tick_lower}), "
        f"upper={actual_upper:.4f} (tick {tick_upper})"
    )

    hype_bal, usdc_bal = get_token_balances(w3)
    logger.info(
        f"Wallet: HYPE={hype_bal / 10**config.HYPE_DECIMALS:.4f}, "
        f"USDC={usdc_bal / 10**config.USDC_DECIMALS:.6f}"
    )

    slot0 = pool_contract.functions.slot0().call()
    sqrt_price_x96 = slot0[0]
    from src.math_utils import calculate_token_amounts
    import math

    if token0_is_hype:
        raw0, raw1 = hype_bal, usdc_bal
    else:
        raw0, raw1 = usdc_bal, hype_bal

    if raw0 == 0 and raw1 == 0:
        logger.warning("No tokens available to mint new position")
        return None

    pool_fee = pool_contract.functions.fee().call()

    # Pre-mint: iteratively swap to match the pool's required ratio for near-full utilization
    Q96 = 2 ** 96
    sp = sqrt_price_x96 / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)
    target_ratio = sp * spu * (sp - spl) / (spu - sp)  # raw1 / raw0 for full utilization

    for iteration in range(5):
        current_ratio = raw1 / raw0 if raw0 > 0 else float('inf')
        deviation = abs(math.log(current_ratio / target_ratio)) if current_ratio > 0 and target_ratio > 0 else 1.0
        if deviation < 0.01:
            break

        if current_ratio > target_ratio:
            excess_usd = (raw1 - target_ratio * raw0) / 10**config.USDC_DECIMALS if token0_is_hype else (raw1 - target_ratio * raw0) / 10**config.HYPE_DECIMALS * current_price
        else:
            excess_usd = (raw0 - raw1 / target_ratio) * current_price / 10**config.HYPE_DECIMALS if token0_is_hype else (raw0 - raw1 / target_ratio) / 10**config.USDC_DECIMALS

        if excess_usd < 2.0:
            break

        swap_frac = min(0.50, 10.0 / max(excess_usd, 1.0))
        if current_ratio > target_ratio:
            excess_raw1 = raw1 - int(target_ratio * raw0)
            swap_raw = int(excess_raw1 * swap_frac * 0.95)
            if swap_raw >= 1000:
                t_in = config.USDC_ADDRESS if token0_is_hype else config.HYPE_ADDRESS
                t_out = config.HYPE_ADDRESS if token0_is_hype else config.USDC_ADDRESS
                logger.info(f"Pre-mint swap iter {iteration}: token1 -> token0, ~${excess_usd:.1f} excess")
                approve_token(w3, get_hype_or_usdc(w3, not token0_is_hype),
                              config.SWAP_ROUTER_ADDRESS, swap_raw, dry_run)
                swap_exact_input_single(w3, t_in, t_out, pool_fee, swap_raw, dry_run)
        else:
            excess_raw0 = raw0 - int(raw1 / target_ratio) if target_ratio > 0 else raw0
            swap_raw = int(excess_raw0 * swap_frac * 0.95)
            if swap_raw >= 1000:
                t_in = config.HYPE_ADDRESS if token0_is_hype else config.USDC_ADDRESS
                t_out = config.USDC_ADDRESS if token0_is_hype else config.HYPE_ADDRESS
                logger.info(f"Pre-mint swap iter {iteration}: token0 -> token1, ~${excess_usd:.1f} excess")
                approve_token(w3, get_hype_or_usdc(w3, token0_is_hype),
                              config.SWAP_ROUTER_ADDRESS, swap_raw, dry_run)
                swap_exact_input_single(w3, t_in, t_out, pool_fee, swap_raw, dry_run)

        hype_bal, usdc_bal = get_token_balances(w3)
        raw0, raw1 = (hype_bal, usdc_bal) if token0_is_hype else (usdc_bal, hype_bal)

    am0_opt, am1_opt = calculate_token_amounts(raw0, raw1, sqrt_price_x96, tick_lower, tick_upper)
    amount0_desired = max(am0_opt, 1)
    amount1_desired = max(am1_opt, 1)
    logger.info(f"Optimal amounts: {amount0_desired} t0, {amount1_desired} t1")

    logger.info("Step 6: Approving tokens...")
    hype_con = get_hype_or_usdc(w3, True)
    usdc_con = get_hype_or_usdc(w3, False)
    pm_addr = config.POSITION_MANAGER_ADDRESS

    if token0_is_hype:
        approve_token(w3, hype_con, pm_addr, amount0_desired, dry_run)
        approve_token(w3, usdc_con, pm_addr, amount1_desired, dry_run)
    else:
        approve_token(w3, usdc_con, pm_addr, amount0_desired, dry_run)
        approve_token(w3, hype_con, pm_addr, amount1_desired, dry_run)

    logger.info(f"Step 7: Minting new position (fee tier: {pool_fee})...")
    new_token_id = mint_position(
        w3, position_manager, pool_contract,
        tick_lower, tick_upper, amount0_desired, amount1_desired,
        pool_fee, dry_run,
    )

    if new_token_id is None and not dry_run:
        logger.warning("Could not determine new tokenId")
        return token_id

    result = new_token_id or token_id

    # Post-mint: swap leftover and add to position via increase_liquidity
    try:
        slot0 = pool_contract.functions.slot0().call()
        sqrt_price_x96 = slot0[0]
        hype_bal, usdc_bal = get_token_balances(w3)
        raw_now0, raw_now1 = (hype_bal, usdc_bal) if token0_is_hype else (usdc_bal, hype_bal)

        leftover0_usd = (raw_now0 / 10**config.HYPE_DECIMALS) * current_price if token0_is_hype else raw_now0 / 10**config.USDC_DECIMALS
        leftover1_usd = (raw_now1 / 10**config.HYPE_DECIMALS) * current_price if not token0_is_hype else raw_now1 / 10**config.USDC_DECIMALS

        if leftover0_usd < 2.0 and leftover1_usd < 2.0:
            logger.info(f"Unused ~${leftover0_usd + leftover1_usd:.2f} < $2, skipping top-up")
        elif leftover0_usd > 2.0 or leftover1_usd > 2.0:
            logger.info(f"Unused ~${max(leftover0_usd, leftover1_usd):.1f}, swapping leftover and adding to position {result}")

            if token0_is_hype:
                token0_name, token1_name = "HYPE", "USDC"
            else:
                token0_name, token1_name = "USDC", "HYPE"

            if leftover0_usd > leftover1_usd:
                amount_in = int(raw_now0 * 0.92)
                if amount_in >= 1000:
                    t_in = config.HYPE_ADDRESS if token0_is_hype else config.USDC_ADDRESS
                    t_out = config.USDC_ADDRESS if token0_is_hype else config.HYPE_ADDRESS
                    logger.info(f"Swapping ${leftover0_usd:.1f} excess {token0_name} -> {token1_name}")
                    approve_token(w3, get_hype_or_usdc(w3, token0_is_hype),
                                  config.SWAP_ROUTER_ADDRESS, amount_in, dry_run)
                    swap_exact_input_single(w3, t_in, t_out, pool_fee, amount_in, dry_run)
            else:
                amount_in = int(raw_now1 * 0.92)
                if amount_in >= 1000:
                    t_in = config.USDC_ADDRESS if token0_is_hype else config.HYPE_ADDRESS
                    t_out = config.HYPE_ADDRESS if token0_is_hype else config.USDC_ADDRESS
                    logger.info(f"Swapping ${leftover1_usd:.1f} excess {token1_name} -> {token0_name}")
                    approve_token(w3, get_hype_or_usdc(w3, not token0_is_hype),
                                  config.SWAP_ROUTER_ADDRESS, amount_in, dry_run)
                    swap_exact_input_single(w3, t_in, t_out, pool_fee, amount_in, dry_run)

            hype_bal, usdc_bal = get_token_balances(w3)
            add0_raw, add1_raw = (hype_bal, usdc_bal) if token0_is_hype else (usdc_bal, hype_bal)
            add0, add1 = calculate_token_amounts(add0_raw, add1_raw, sqrt_price_x96, tick_lower, tick_upper)
            add0 = max(1, add0)
            add1 = max(1, add1)
            if add0 > 1 or add1 > 1:
                logger.info(f"Adding liquidity: {add0} t0, {add1} t1")
                approve_token(w3, get_hype_or_usdc(w3, token0_is_hype),
                              config.POSITION_MANAGER_ADDRESS, add0, dry_run)
                approve_token(w3, get_hype_or_usdc(w3, not token0_is_hype),
                              config.POSITION_MANAGER_ADDRESS, add1, dry_run)
                increase_liquidity(w3, position_manager, result, add0, add1, dry_run)
    except Exception as e:
        logger.warning(f"Top-up failed (non-critical): {e}")

    logger.info(f"=== Rebalance complete. Token ID: {result} ===")
    return result


def create_position(
    w3: Web3,
    position_manager,
    pool_contract,
    current_price: float,
    dry_run: bool = False,
) -> Optional[int]:
    token0_is_hype, dec0, dec1 = get_token_order(pool_contract, config.HYPE_ADDRESS)
    tick_spacing = get_tick_spacing(pool_contract)
    invert = not token0_is_hype

    logger.info("=== Creating new position ===")

    logger.info("Step 0: Balancing tokens...")
    balance_tokens(w3, pool_contract, dry_run)

    logger.info("Step 1: Calculating bounds...")
    tick_lower, tick_upper, actual_lower, actual_upper = calculate_bounds(
        current_price, config.LOWER_BOUND_PCT, config.UPPER_BOUND_PCT,
        dec0, dec1, tick_spacing, invert,
    )
    logger.info(
        f"New bounds: lower={actual_lower:.4f} (tick {tick_lower}), "
        f"upper={actual_upper:.4f} (tick {tick_upper})"
    )

    hype_bal, usdc_bal = get_token_balances(w3)
    logger.info(
        f"Wallet: HYPE={hype_bal / 10**config.HYPE_DECIMALS:.4f}, "
        f"USDC={usdc_bal / 10**config.USDC_DECIMALS:.6f}"
    )

    slot0 = pool_contract.functions.slot0().call()
    sqrt_price_x96 = slot0[0]
    from src.math_utils import calculate_token_amounts

    if token0_is_hype:
        raw0, raw1 = hype_bal, usdc_bal
    else:
        raw0, raw1 = usdc_bal, hype_bal

    if raw0 == 0 and raw1 == 0:
        logger.warning("No tokens available to create position")
        return None

    pool_fee = pool_contract.functions.fee().call()
    import math
    Q96 = 2 ** 96
    sp = sqrt_price_x96 / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)
    target_ratio = sp * spu * (sp - spl) / (spu - sp)

    for iteration in range(5):
        current_ratio = raw1 / raw0 if raw0 > 0 else float('inf')
        deviation = abs(math.log(current_ratio / target_ratio)) if current_ratio > 0 and target_ratio > 0 else 1.0
        if deviation < 0.01:
            break

        if current_ratio > target_ratio:
            excess_usd = (raw1 - target_ratio * raw0) / 10**config.USDC_DECIMALS if token0_is_hype else (raw1 - target_ratio * raw0) / 10**config.HYPE_DECIMALS * current_price
        else:
            excess_usd = (raw0 - raw1 / target_ratio) * current_price / 10**config.HYPE_DECIMALS if token0_is_hype else (raw0 - raw1 / target_ratio) / 10**config.USDC_DECIMALS

        if excess_usd < 2.0:
            break

        swap_frac = min(0.50, 10.0 / max(excess_usd, 1.0))
        if current_ratio > target_ratio:
            excess_raw1 = raw1 - int(target_ratio * raw0)
            swap_raw = int(excess_raw1 * swap_frac * 0.95)
            if swap_raw >= 1000:
                t_in = config.USDC_ADDRESS if token0_is_hype else config.HYPE_ADDRESS
                t_out = config.HYPE_ADDRESS if token0_is_hype else config.USDC_ADDRESS
                logger.info(f"Pre-mint swap iter {iteration}: token1 -> token0, ~${excess_usd:.1f} excess")
                approve_token(w3, get_hype_or_usdc(w3, not token0_is_hype),
                              config.SWAP_ROUTER_ADDRESS, swap_raw, dry_run)
                swap_exact_input_single(w3, t_in, t_out, pool_fee, swap_raw, dry_run)
        else:
            excess_raw0 = raw0 - int(raw1 / target_ratio) if target_ratio > 0 else raw0
            swap_raw = int(excess_raw0 * swap_frac * 0.95)
            if swap_raw >= 1000:
                t_in = config.HYPE_ADDRESS if token0_is_hype else config.USDC_ADDRESS
                t_out = config.USDC_ADDRESS if token0_is_hype else config.HYPE_ADDRESS
                logger.info(f"Pre-mint swap iter {iteration}: token0 -> token1, ~${excess_usd:.1f} excess")
                approve_token(w3, get_hype_or_usdc(w3, token0_is_hype),
                              config.SWAP_ROUTER_ADDRESS, swap_raw, dry_run)
                swap_exact_input_single(w3, t_in, t_out, pool_fee, swap_raw, dry_run)

        hype_bal, usdc_bal = get_token_balances(w3)
        raw0, raw1 = (hype_bal, usdc_bal) if token0_is_hype else (usdc_bal, hype_bal)

    am0_opt, am1_opt = calculate_token_amounts(raw0, raw1, sqrt_price_x96, tick_lower, tick_upper)
    amount0_desired = max(am0_opt, 1)
    amount1_desired = max(am1_opt, 1)
    logger.info(f"Optimal amounts: {amount0_desired} t0, {amount1_desired} t1")

    logger.info("Step 2: Approving tokens...")
    hype_con = get_hype_or_usdc(w3, True)
    usdc_con = get_hype_or_usdc(w3, False)
    pm_addr = config.POSITION_MANAGER_ADDRESS

    if token0_is_hype:
        approve_token(w3, hype_con, pm_addr, amount0_desired, dry_run)
        approve_token(w3, usdc_con, pm_addr, amount1_desired, dry_run)
    else:
        approve_token(w3, usdc_con, pm_addr, amount0_desired, dry_run)
        approve_token(w3, hype_con, pm_addr, amount1_desired, dry_run)

    logger.info(f"Step 3: Minting position (fee tier: {pool_fee})...")
    new_token_id = mint_position(
        w3, position_manager, pool_contract,
        tick_lower, tick_upper, amount0_desired, amount1_desired,
        pool_fee, dry_run,
    )

    if new_token_id:
        logger.info(f"=== Position created. Token ID: {new_token_id} ===")
    else:
        logger.warning("Position creation failed or could not determine tokenId")

    return new_token_id


def wrap_hype(w3: Web3, amount: int, dry_run: bool = False) -> bool:
    if amount <= 0:
        return True
    from src.provider import get_whype_contract
    whype = get_whype_contract(w3)
    logger.info(f"Wrapping {amount / 1e18:.4f} HYPE to wHYPE")
    tx = whype.functions.deposit().build_transaction({
        **build_tx_params(w3, 100_000),
        "value": amount,
    })
    receipt = send_transaction(w3, tx, dry_run)
    return receipt is not None and receipt["status"] == 1


@with_retry(max_retries=3, base_delay=2)
def swap_exact_input_single(
    w3: Web3,
    token_in: str,
    token_out: str,
    fee: int,
    amount_in: int,
    dry_run: bool = False,
) -> Optional[int]:
    if amount_in <= 0:
        return 0
    from src.provider import get_swap_router_contract
    router = get_swap_router_contract(w3)
    account = get_account(w3)
    deadline = build_deadline(w3)

    logger.info(f"Swapping {amount_in / 1e18:.4f} tokenIn for tokenOut via fee={fee}")

    tx = router.functions.exactInputSingle({
        "tokenIn": Web3.to_checksum_address(token_in),
        "tokenOut": Web3.to_checksum_address(token_out),
        "fee": fee,
        "recipient": account.address,
        "deadline": deadline,
        "amountIn": amount_in,
        "amountOutMinimum": 0,
        "sqrtPriceLimitX96": 0,
    }).build_transaction(build_tx_params(w3, 300_000))

    receipt = send_transaction(w3, tx, dry_run)
    return None


def balance_tokens(
    w3: Web3,
    pool_contract,
    dry_run: bool = False,
):
    account = get_account(w3)
    native_balance = w3.eth.get_balance(account.address)

    # Wrap native HYPE to wHYPE, keeping small gas reserve
    gas_reserve_wei = int(0.01 * 1e18)
    wrap_amount = native_balance - gas_reserve_wei
    if wrap_amount > 0:
        wrap_hype(w3, wrap_amount, dry_run)

    hype_bal, usdc_bal = get_token_balances(w3)
    logger.info(
        f"Balances: wHYPE={hype_bal / 10**config.HYPE_DECIMALS:.4f}, "
        f"USDC={usdc_bal / 10**config.USDC_DECIMALS:.6f}"
    )

    if hype_bal == 0 and usdc_bal == 0:
        logger.warning("No wHYPE or USDC available after wrapping")
        return

    # Simple 50/50 value balancing
    if hype_bal > 0 and usdc_bal == 0:
        slot0 = pool_contract.functions.slot0().call()
        tick = slot0[1]
        from src.provider import get_hype_contract
        from src.math_utils import tick_to_price, get_token_order
        token0_is_hype, dec0, dec1 = get_token_order(pool_contract, config.HYPE_ADDRESS)
        invert = not token0_is_hype
        price = tick_to_price(tick, dec0, dec1, invert)

        half_value_in_hype = hype_bal // 2
        logger.info(f"Swapping half wHYPE (~{half_value_in_hype / 1e18:.4f}) for USDC")

        fee = pool_contract.functions.fee().call()
        approve_token(
            w3, get_hype_contract(w3),
            Web3.to_checksum_address(config.SWAP_ROUTER_ADDRESS),
            half_value_in_hype, dry_run,
        )
        swap_exact_input_single(
            w3, config.HYPE_ADDRESS, config.USDC_ADDRESS,
            fee, half_value_in_hype, dry_run,
        )
    elif usdc_bal > 0 and hype_bal == 0:
        slot0 = pool_contract.functions.slot0().call()
        tick = slot0[1]
        from src.math_utils import tick_to_price, get_token_order
        token0_is_hype, dec0, dec1 = get_token_order(pool_contract, config.HYPE_ADDRESS)
        invert = not token0_is_hype
        price = tick_to_price(tick, dec0, dec1, invert)

        half_value_in_usdc = usdc_bal // 2
        logger.info(f"Swapping half USDC (~${half_value_in_usdc / 1e6:.2f}) for wHYPE")

        fee = pool_contract.functions.fee().call()
        approve_token(
            w3, get_hype_or_usdc(w3, False),
            Web3.to_checksum_address(config.SWAP_ROUTER_ADDRESS),
            half_value_in_usdc, dry_run,
        )
        swap_exact_input_single(
            w3, config.USDC_ADDRESS, config.HYPE_ADDRESS,
            fee, half_value_in_usdc, dry_run,
        )
    else:
        logger.info("Both tokens present, skipping swap")


def get_hype_or_usdc(w3: Web3, is_hype: bool):
    from src.provider import get_hype_contract, get_usdc_contract
    return get_hype_contract(w3) if is_hype else get_usdc_contract(w3)
