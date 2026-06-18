import logging
from functools import wraps
from typing import Any, Callable, Optional

from web3 import Web3
from web3.exceptions import Web3Exception
from web3.middleware import ExtraDataToPOAMiddleware

from src.config import config
from src.constants import POOL_ABI, ERC20_ABI, POSITION_MANAGER_ABI, WHYPE_ABI, SWAP_ROUTER_ABI

logger = logging.getLogger("liqbot")


def sanitize_err(msg: str) -> str:
    """Redact any configured RPC URL (which may contain API keys) from an error message."""
    for p in rpc_manager.providers:
        if p.url and p.url in msg:
            msg = msg.replace(p.url, f"{p.name} [URL REDACTED]")
    return msg

PUBLIC_RPC_URL = "https://rpc.hyperliquid.xyz/evm"
MULTICALL3_ADDRESS = "0x0000000000000000000000000000000000000999"
MULTICALL3_ABI = [
    {
        "inputs": [{"components": [{"name": "target", "type": "address"}, {"name": "allowFailure", "type": "bool"}, {"name": "callData", "type": "bytes"}], "name": "calls", "type": "tuple[]"}],
        "name": "aggregate3",
        "outputs": [{"components": [{"name": "success", "type": "bool"}, {"name": "returnData", "type": "bytes"}], "name": "returnData", "type": "tuple[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]


class RPCProvider:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self.active = True
        self.web3: Optional[Web3] = None

    def connect(self) -> bool:
        try:
            w3 = Web3(Web3.HTTPProvider(self.url, request_kwargs={"timeout": 30}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if w3.is_connected():
                logger.info(f"Connected to {self.name}")
                self.web3 = w3
                return True
            logger.warning(f"{self.name} endpoint not reachable")
        except Exception as e:
            logger.warning(f"{self.name} connection failed: {sanitize_err(str(e))}")
        self.active = False
        return False


class RPCManager:
    SLOT_ORDER = ["HypeRPC (public)", "HypeRPC (API)", "Alchemy", "dRPC", "Chainstack"]

    def __init__(self):
        self.providers: list[RPCProvider] = []
        self._build_providers()

    def _build_providers(self):
        # Public HypeRPC (no API key) — always first in priority
        self.providers.append(RPCProvider("HypeRPC (public)", PUBLIC_RPC_URL))
        if config.HYPE_RPC_API_KEY:
            self.providers.append(RPCProvider(
                "HypeRPC (API)",
                f"https://evmrpc-eu.hyperpc.app/{config.HYPE_RPC_API_KEY}?apikey={config.HYPE_RPC_API_KEY}",
            ))
        if config.ALCHEMY_API_KEY:
            self.providers.append(RPCProvider(
                "Alchemy",
                f"https://hyperliquid-mainnet.g.alchemy.com/v2/{config.ALCHEMY_API_KEY}",
            ))
        if config.DRPC_API_KEY:
            self.providers.append(RPCProvider(
                "dRPC",
                f"https://lb.drpc.live/hyperliquid/{config.DRPC_API_KEY}",
            ))
        if config.CHAINSTACK_ENDPOINT:
            self.providers.append(RPCProvider("Chainstack", config.CHAINSTACK_ENDPOINT))
        # Only add fallback if different from public endpoint
        if config.RPC_URL.rstrip("/").lower() != PUBLIC_RPC_URL.rstrip("/").lower():
            self.providers.append(RPCProvider("HyperEVM (fallback)", config.RPC_URL))

    def get_active(self) -> list[RPCProvider]:
        return [p for p in self.providers if p.active]

    def get_active_names(self) -> list[str]:
        return [p.name for p in self.get_active()]

    def get_web3(self) -> Web3:
        for p in self.get_active():
            if p.web3:
                return p.web3
        for p in self.get_active():
            if p.connect():
                return p.web3
        raise ConnectionError("No active RPC providers available")

    def get_web3_for_slot(self, slot: int) -> Web3:
        active = self.get_active()
        if not active:
            raise ConnectionError("No active RPC providers available")
        idx = min(slot, len(active) - 1)
        p = active[idx]
        if not p.web3:
            p.connect()
        if p.web3:
            return p.web3
        return self.get_web3()

    def get_web3_for_swap(self) -> Web3:
        priority = ["HypeRPC (public)", "HypeRPC (API)", "Alchemy", "dRPC", "Chainstack"]
        for name in priority:
            for p in self.providers:
                if p.name == name and p.active:
                    if not p.web3:
                        p.connect()
                    if p.web3:
                        return p.web3
        return self.get_web3()

    def get_web3_for_name(self, keyword: str) -> Web3:
        for p in self.providers:
            if keyword.lower() in p.name.lower() and p.active:
                if not p.web3:
                    p.connect()
                if p.web3:
                    return p.web3
        return self.get_web3()

    def on_error(self, failed_w3: Web3) -> Web3:
        for p in self.providers:
            if p.web3 is failed_w3:
                p.active = False
                p.web3 = None
                logger.warning(f"RPC provider {p.name} disabled (error)")
                break
        next_w3 = self.get_web3()
        logger.info(f"RPC switched to next active provider")
        return next_w3

    def test_all(self):
        for p in self.providers:
            if p.active:
                ok = p.connect()
                if not ok:
                    logger.warning(f"RPC provider {p.name} marked inactive")
            else:
                logger.info(f"RPC provider {p.name} skipped (inactive)")

    def get_summary(self) -> list[dict]:
        return [
            {"name": p.name, "active": p.active, "connected": p.web3 is not None}
            for p in self.providers
        ]


rpc_manager = RPCManager()

_mc3_available = None


def get_multicall3(w3: Web3) -> Optional[Any]:
    global _mc3_available
    if _mc3_available is None:
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(MULTICALL3_ADDRESS))
            _mc3_available = code != b""
        except Exception:
            _mc3_available = False
    if _mc3_available:
        return w3.eth.contract(
            address=Web3.to_checksum_address(MULTICALL3_ADDRESS), abi=MULTICALL3_ABI
        )
    return None


def with_retry(max_retries: int = 1, base_delay: int = 0):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (Web3Exception, ConnectionError, TimeoutError) as e:
                    last_exc = e
                    safe = sanitize_err(str(e))
                    if attempt < max_retries - 1:
                        logger.warning(f"RPC error: {safe}. Immediate retry {attempt + 1}/{max_retries}")
                    else:
                        logger.warning(f"RPC error: {safe}")
            raise last_exc
        return wrapper
    return decorator


def get_web3() -> Web3:
    return rpc_manager.get_web3()


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


def get_whype_contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.HYPE_ADDRESS), abi=WHYPE_ABI)


def get_swap_router_contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.SWAP_ROUTER_ADDRESS), abi=SWAP_ROUTER_ABI)


def get_account(w3: Web3):
    return w3.eth.account.from_key(config.PRIVATE_KEY)


def estimate_gas(w3: Web3, tx: dict) -> int:
    try:
        estimated = w3.eth.estimate_gas(tx)
        return int(estimated * 1.2)
    except Exception:
        return 500_000
