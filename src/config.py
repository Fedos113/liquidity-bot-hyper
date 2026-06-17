import os
import warnings
from dotenv import load_dotenv

load_dotenv()


class Config:
    RPC_URL: str = os.getenv("RPC_URL", "")
    PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
    WALLET_ADDRESS: str = os.getenv("WALLET_ADDRESS", "")
    POOL_ADDRESS: str = os.getenv("POOL_ADDRESS", "")
    POSITION_MANAGER_ADDRESS: str = os.getenv("POSITION_MANAGER_ADDRESS", "")
    HYPE_ADDRESS: str = os.getenv("HYPE_ADDRESS", "")
    USDC_ADDRESS: str = os.getenv("USDC_ADDRESS", "")
    SWAP_ROUTER_ADDRESS: str = os.getenv("SWAP_ROUTER_ADDRESS", "0x1EbDFC75FfE3ba3de61E7138a3E8706aC841Af9B")

    HYPE_RPC_API_KEY: str = os.getenv("HYPE_RPC_API_KEY", "")
    CHAINSTACK_ENDPOINT: str = os.getenv("CHAINSTACK_ENDPOINT", "")
    ALCHEMY_API_KEY: str = os.getenv("ALCHEMY_API_KEY", "")
    DRPC_API_KEY: str = os.getenv("DRPC_API_KEY", "")

    TOKEN_ID: int = int(os.getenv("TOKEN_ID", "0"))
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    LOWER_BOUND_PCT: float = float(os.getenv("LOWER_BOUND_PCT", "0.96"))
    UPPER_BOUND_PCT: float = float(os.getenv("UPPER_BOUND_PCT", "1.06"))
    SLEEP_INTERVAL: int = int(os.getenv("SLEEP_INTERVAL", "3600"))
    SLIPPAGE_TOLERANCE: float = float(os.getenv("SLIPPAGE_TOLERANCE", "0.005"))
    FEE_TIER: int = int(os.getenv("FEE_TIER", "3000"))
    FEE_COMPOUND_THRESHOLD_USD: float = float(os.getenv("FEE_COMPOUND_THRESHOLD_USD", "5.0"))
    MIN_WALLET_USD: float = float(os.getenv("MIN_WALLET_USD", "0.2"))

    # DEPRECATED: use SECONDARY_INNER instead
    DOWNWARD_CYCLE_INTERVAL: int = int(os.getenv("DOWNWARD_CYCLE_INTERVAL", "600"))
    UPWARD_CYCLE_INTERVAL: int = int(os.getenv("UPWARD_CYCLE_INTERVAL", "600"))
    HYPE_DROP_THRESHOLD: float = float(os.getenv("HYPE_DROP_THRESHOLD", "0.98"))
    DOWNWARD_COOLDOWN: int = int(os.getenv("DOWNWARD_COOLDOWN", "3600"))
    DOWNWARD_INNER_CYCLE_INTERVAL: int = int(os.getenv("DOWNWARD_INNER_CYCLE_INTERVAL", "300"))
    UPWARD_INNER_CYCLE_INTERVAL: int = int(os.getenv("UPWARD_INNER_CYCLE_INTERVAL", "180"))
    UPWARD_DELAY: int = int(os.getenv("UPWARD_DELAY", "60"))
    HYPE_UPPER_THRESHOLD: float = float(os.getenv("HYPE_UPPER_THRESHOLD", "1.02"))

    SECONDARY_INNER: int = int(os.getenv("SECONDARY_INNER", "300"))
    TX_INTER_SLEEP: int = int(os.getenv("TX_INTER_SLEEP", "3"))

    HYPE_DECIMALS: int = int(os.getenv("HYPE_DECIMALS", "18"))
    USDC_DECIMALS: int = int(os.getenv("USDC_DECIMALS", "6"))

    def validate(self):
        required = [
            ("RPC_URL", self.RPC_URL),
            ("PRIVATE_KEY", self.PRIVATE_KEY),
            ("WALLET_ADDRESS", self.WALLET_ADDRESS),
            ("POOL_ADDRESS", self.POOL_ADDRESS),
            ("POSITION_MANAGER_ADDRESS", self.POSITION_MANAGER_ADDRESS),
            ("HYPE_ADDRESS", self.HYPE_ADDRESS),
            ("USDC_ADDRESS", self.USDC_ADDRESS),
        ]
        missing = [name for name, val in required if not val]
        if missing:
            raise ValueError(f"Missing required .env variables: {', '.join(missing)}")
        if not self.WALLET_ADDRESS.startswith("0x"):
            raise ValueError("WALLET_ADDRESS must start with 0x")
        if not self.PRIVATE_KEY.startswith("0x"):
            raise ValueError("PRIVATE_KEY must start with 0x")

        if os.getenv("UPWARD_CYCLE_INTERVAL") or os.getenv("DOWNWARD_CYCLE_INTERVAL"):
            warnings.warn(
                "UPWARD_CYCLE_INTERVAL and DOWNWARD_CYCLE_INTERVAL are deprecated. "
                "Use SECONDARY_INNER instead.",
                DeprecationWarning,
            )


config = Config()
