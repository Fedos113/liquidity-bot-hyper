import logging
from functools import wraps
from typing import Any, Callable
from time import sleep

from web3 import Web3
from web3.exceptions import Web3Exception
from web3.middleware import ExtraDataToPOAMiddleware

from src.config import config
from src.constants import POOL_ABI, ERC20_ABI, POSITION_MANAGER_ABI

logger = logging.getLogger("liqbot")


def with_retry(max_retries: int = 5, base_delay: int = 1):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (Web3Exception, ConnectionError, TimeoutError) as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"RPC error: {e}. Retry {attempt + 1}/{max_retries} in {delay}s")
                        sleep(delay)
                    else:
                        logger.error(f"RPC error after {max_retries} retries: {e}")
            raise last_exc
        return wrapper
    return decorator


def get_web3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(config.RPC_URL, request_kwargs={"timeout": 30}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC: {config.RPC_URL}")
    logger.info(f"Connected to HyperEVM. Chain ID: {w3.eth.chain_id}")
    return w3


def get_pool_contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.POOL_ADDRESS), abi=POOL_ABI)


def get_position_manager_contract(w3: Web3):
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.POSITION_MANAGER_ADDRESS), abi=POSITION_MANAGER_ABI
    )


def get_erc20_contract(w3: Web3, address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)


def get_hype_contract(w3: Web3):
    return get_erc20_contract(w3, config.HYPE_ADDRESS)


def get_usdc_contract(w3: Web3):
    return get_erc20_contract(w3, config.USDC_ADDRESS)


def get_account(w3: Web3):
    return w3.eth.account.from_key(config.PRIVATE_KEY)


def estimate_gas(w3: Web3, tx: dict) -> int:
    try:
        estimated = w3.eth.estimate_gas(tx)
        return int(estimated * 1.2)
    except Exception:
        return 500_000
