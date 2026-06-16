import math
import logging
from typing import Tuple

from src.constants import Q96, TICK_SPACINGS

logger = logging.getLogger("liqbot")


def get_price_from_sqrt_price(sqrt_price_x96: int, token0_decimals: int, token1_decimals: int, invert: bool = False) -> float:
    raw_price = (sqrt_price_x96 / Q96) ** 2
    human_price = raw_price * (10 ** (token0_decimals - token1_decimals))
    return 1.0 / human_price if invert else human_price


def tick_to_price(tick: int, token0_decimals: int, token1_decimals: int, invert: bool = False) -> float:
    raw_price = 1.0001 ** tick
    human_price = raw_price * (10 ** (token0_decimals - token1_decimals))
    return 1.0 / human_price if invert else human_price


def price_to_tick(price: float, token0_decimals: int, token1_decimals: int, tick_spacing: int, invert: bool = False) -> int:
    if invert:
        price = 1.0 / price
    raw_price = price * (10 ** (token1_decimals - token0_decimals))
    tick = int(math.log(raw_price) / math.log(1.0001))
    snapped = round(tick / tick_spacing) * tick_spacing
    return snapped


def get_tick_spacing(pool_contract) -> int:
    try:
        fee = pool_contract.functions.fee().call()
        return TICK_SPACINGS.get(fee, 60)
    except Exception:
        try:
            return pool_contract.functions.tickSpacing().call()
        except Exception:
            return 60


def get_token_order(pool_contract, native_address: str) -> Tuple[bool, int, int]:
    token0 = pool_contract.functions.token0().call()
    token1 = pool_contract.functions.token1().call()
    token0_addr = token0.lower()
    token1_addr = token1.lower()
    native_lower = native_address.lower()
    token0_is_native = token0_addr == native_lower

    if not token0_is_native and token1_addr != native_lower:
        raise ValueError(f"Native token address {native_address} not found in pool. token0={token0}, token1={token1}")

    from src.config import config
    decimals0 = config.NATIVE_DECIMALS if token0_is_native else config.USDC_DECIMALS
    decimals1 = config.NATIVE_DECIMALS if not token0_is_native else config.USDC_DECIMALS
    return token0_is_native, decimals0, decimals1


def calculate_bounds(
    current_price: float,
    lower_pct: float,
    upper_pct: float,
    token0_decimals: int,
    token1_decimals: int,
    tick_spacing: int,
    invert: bool = False,
) -> Tuple[int, int, float, float]:
    lower_price = current_price * lower_pct
    upper_price = current_price * upper_pct

    tick_lower = price_to_tick(lower_price, token0_decimals, token1_decimals, tick_spacing, invert)
    tick_upper = price_to_tick(upper_price, token0_decimals, token1_decimals, tick_spacing, invert)

    if tick_lower >= tick_upper:
        tick_lower = tick_upper - tick_spacing

    actual_lower_price = tick_to_price(tick_lower, token0_decimals, token1_decimals, invert)
    actual_upper_price = tick_to_price(tick_upper, token0_decimals, token1_decimals, invert)

    return tick_lower, tick_upper, actual_lower_price, actual_upper_price


def calculate_token_amounts(
    amount0_desired: int,
    amount1_desired: int,
    sqrt_price_x96: int,
    tick_lower: int,
    tick_upper: int,
) -> Tuple[int, int]:
    sqrt_price = sqrt_price_x96 / Q96
    sqrt_price_lower = math.sqrt(1.0001 ** tick_lower)
    sqrt_price_upper = math.sqrt(1.0001 ** tick_upper)

    if sqrt_price <= sqrt_price_lower:
        amount0 = int(amount0_desired)
        amount1 = 0
    elif sqrt_price >= sqrt_price_upper:
        amount0 = 0
        amount1 = int(amount1_desired)
    else:
        liq_from_0 = amount0_desired * sqrt_price * sqrt_price_upper / (sqrt_price_upper - sqrt_price)
        liq_from_1 = amount1_desired / (sqrt_price - sqrt_price_lower)
        liquidity = min(liq_from_0, liq_from_1)

        amount0 = int(liquidity * (sqrt_price_upper - sqrt_price) / (sqrt_price * sqrt_price_upper))
        amount1 = int(liquidity * (sqrt_price - sqrt_price_lower))

    return amount0, amount1


def calculate_usdc_value(price: float, usdc_amount: float, hype_amount: float) -> float:
    return usdc_amount + hype_amount * price


def position_value_usd(liquidity, tick_lower, tick_upper, sqrt_price_x96, token0_is_hype, current_price, dec0, dec1):
    sp = sqrt_price_x96 / Q96
    spl = math.sqrt(1.0001 ** tick_lower)
    spu = math.sqrt(1.0001 ** tick_upper)

    if sp <= spl:
        am0 = int(liquidity * (spu - spl) / (spl * spu))
        am1 = 0
    elif sp >= spu:
        am0 = 0
        am1 = int(liquidity * (spu - spl))
    else:
        am0 = int(liquidity * (spu - sp) / (sp * spu))
        am1 = int(liquidity * (sp - spl))

    if token0_is_hype:
        return am0 * current_price / (10 ** dec0) + am1 / (10 ** dec1)
    return am0 / (10 ** dec0) + am1 * current_price / (10 ** dec1)
