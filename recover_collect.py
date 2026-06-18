import logging, time, os
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("recover")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET = Web3.to_checksum_address(os.getenv("WALLET_ADDRESS"))
PM = Web3.to_checksum_address(os.getenv("POSITION_MANAGER_ADDRESS"))
HYPE = Web3.to_checksum_address(os.getenv("HYPE_ADDRESS"))
USDC = Web3.to_checksum_address(os.getenv("USDC_ADDRESS"))
RPC_URL = "https://rpc.hyperliquid.xyz/evm"

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 30}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
assert w3.is_connected()
account = w3.eth.account.from_key(PRIVATE_KEY)

COLLECT_ABI = [{"inputs":[{"components":[{"name":"tokenId","type":"uint256"},{"name":"recipient","type":"address"},{"name":"amount0Max","type":"uint128"},{"name":"amount1Max","type":"uint128"}],"name":"params","type":"tuple"}],"name":"collect","outputs":[{"name":"amount0","type":"uint256"},{"name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"}]
POSITIONS_ABI = [{"inputs":[{"name":"tokenId","type":"uint256"}],"name":"positions","outputs":[{"type":"uint96"},{"type":"address"},{"type":"address"},{"type":"address"},{"type":"uint24"},{"type":"int24"},{"type":"int24"},{"type":"uint128"},{"type":"uint256"},{"type":"uint256"},{"type":"uint128"},{"type":"uint128"}],"stateMutability":"view","type":"function"}]

pm = w3.eth.contract(address=PM, abi=COLLECT_ABI + POSITIONS_ABI)

TOKEN_IDS_TO_CHECK = [499135, 498000, 499000]

log.info(f"Wallet: {WALLET[:10]}...{WALLET[-4:]}")
log.info(f"HYPE balance: {w3.from_wei(w3.eth.get_balance(WALLET), 'ether')}")

# Check and collect from position 499135
for tid in TOKEN_IDS_TO_CHECK:
    try:
        p = pm.functions.positions(tid).call()
        liq = p[7]
        owed0 = p[10]
        owed1 = p[11]
        if owed0 > 0 or owed1 > 0:
            log.info(f"Token {tid}: liquidity={liq}, owed0={owed0} ({owed0/1e18:.6f} wHYPE), owed1={owed1} ({owed1/1e6:.6f} USDC)")
        else:
            log.info(f"Token {tid}: no fees owed, skipping")
            continue
    except Exception as e:
        log.info(f"Token {tid}: error reading")
        continue

    # Build collect tx
    collect_params = {
        "tokenId": tid,
        "recipient": WALLET,
        "amount0Max": 2**128 - 1,
        "amount1Max": 2**128 - 1,
    }

    tx = pm.functions.collect(collect_params).build_transaction({
        "from": WALLET,
        "nonce": w3.eth.get_transaction_count(WALLET),
        "gas": 150000,
        "maxFeePerGas": w3.to_wei("2", "gwei"),
        "maxPriorityFeePerGas": w3.to_wei("0.1", "gwei"),
        "chainId": 999,
    })

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    log.info(f"Collecting fees from token {tid}... tx: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    log.info(f"Collected! Used {receipt['gasUsed']} gas. Status: {'ok' if receipt['status'] == 1 else 'FAILED'}")

    time.sleep(2)

# Final balances
log.info(f"\n--- Final balances ---")
log.info(f"Native HYPE: {w3.from_wei(w3.eth.get_balance(WALLET), 'ether')}")

whype = w3.eth.contract(address=HYPE, abi=[{"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}])
whype_bal = whype.functions.balanceOf(WALLET).call()
log.info(f"wHYPE: {whype_bal / 1e18}")

usdc = w3.eth.contract(address=USDC, abi=[{"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}])
usdc_bal = usdc.functions.balanceOf(WALLET).call()
log.info(f"USDC: {usdc_bal / 1e6}")
