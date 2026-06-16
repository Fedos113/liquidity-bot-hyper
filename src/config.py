import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    RPC_URL: str = os.getenv("RPC_URL", "")
    PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
    WALLET_ADDRESS: str = os.getenv("WALLET_ADDRESS", "")
    POOL_ADDRESS: str = os.getenv("POOL_ADDRESS", "")
    POSITION_MANAGER_ADDRESS: str = os.getenv("POSITION_MANAGER_ADDRESS", "")
    WETH_ADDRESS: str = os.getenv("WETH_ADDRESS", "")
    USDC_ADDRESS: str = os.getenv("USDC_ADDRESS", "")
    SWAP_ROUTER_ADDRESS: str = os.getenv("SWAP_ROUTER_ADDRESS", "0x698Cb2b6dd822994581fEa6eA4Fc755d1363A92F")
    GAUGE_ADDRESS: str = os.getenv("GAUGE_ADDRESS", "")

    TOKEN_ID: int = int(os.getenv("TOKEN_ID", "0"))
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    LOWER_BOUND_PCT: float = float(os.getenv("LOWER_BOUND_PCT", "0.99"))
    UPPER_BOUND_PCT: float = float(os.getenv("UPPER_BOUND_PCT", "1.01"))
    SLEEP_INTERVAL: int = int(os.getenv("SLEEP_INTERVAL", "3600"))
    SLIPPAGE_TOLERANCE: float = float(os.getenv("SLIPPAGE_TOLERANCE", "0.005"))
    FEE_TIER: int = int(os.getenv("FEE_TIER", "50"))
    FEE_COMPOUND_THRESHOLD_USD: float = float(os.getenv("FEE_COMPOUND_THRESHOLD_USD", "1.0"))

    SECONDARY_CYCLE_INTERVAL: int = int(os.getenv("SECONDARY_CYCLE_INTERVAL", "600"))
    DROP_THRESHOLD: float = float(os.getenv("DROP_THRESHOLD", "0.98"))

    NATIVE_DECIMALS: int = int(os.getenv("NATIVE_DECIMALS", "18"))
    USDC_DECIMALS: int = int(os.getenv("USDC_DECIMALS", "6"))

    def validate(self):
        required = [
            ("RPC_URL", self.RPC_URL),
            ("PRIVATE_KEY", self.PRIVATE_KEY),
            ("WALLET_ADDRESS", self.WALLET_ADDRESS),
            ("POOL_ADDRESS", self.POOL_ADDRESS),
            ("POSITION_MANAGER_ADDRESS", self.POSITION_MANAGER_ADDRESS),
            ("WETH_ADDRESS", self.WETH_ADDRESS),
            ("USDC_ADDRESS", self.USDC_ADDRESS),
        ]
        missing = [name for name, val in required if not val]
        if missing:
            raise ValueError(f"Missing required .env variables: {', '.join(missing)}")
        if not self.WALLET_ADDRESS.startswith("0x"):
            raise ValueError("WALLET_ADDRESS must start with 0x")
        if not self.PRIVATE_KEY.startswith("0x"):
            raise ValueError("PRIVATE_KEY must start with 0x")


config = Config()
