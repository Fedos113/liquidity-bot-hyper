import logging, time, os
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("recover")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET = Web3.to_checksum_address(os.getenv("WALLET_ADDRESS"))
HYPE = Web3.to_checksum_address(os.getenv("HYPE_ADDRESS"))
RPC_URL = "https://rpc.hyperliquid.xyz/evm"

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 30}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
assert w3.is_connected()
account = w3.eth.account.from_key(PRIVATE_KEY)

# Check wHYPE balance
whype = w3.eth.contract(address=HYPE, abi=[
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"}
])

bal = whype.functions.balanceOf(WALLET).call()
log.info(f"wHYPE balance: {bal / 1e18}")

if bal == 0:
    log.info("Nothing to unwrap")
    exit()

# Unwrap all wHYPE to native HYPE
tx = whype.functions.withdraw(bal).build_transaction({
    "from": WALLET,
    "nonce": w3.eth.get_transaction_count(WALLET),
    "gas": 100000,
    "maxFeePerGas": w3.to_wei("2", "gwei"),
    "maxPriorityFeePerGas": w3.to_wei("0.1", "gwei"),
    "chainId": 999,
})
signed = account.sign_transaction(tx)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
log.info(f"Unwrapping {bal/1e18} wHYPE... tx: {tx_hash.hex()}")
receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
log.info(f"Done! Gas: {receipt['gasUsed']}, status: {'ok' if receipt['status'] else 'FAILED'}")

# Final
hype_bal = w3.eth.get_balance(WALLET)
usdc = w3.eth.contract(address=Web3.to_checksum_address(os.getenv("USDC_ADDRESS")), abi=[{"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}])
whype_bal = whype.functions.balanceOf(WALLET).call()
usdc_bal = usdc.functions.balanceOf(WALLET).call()

log.info(f"\n=== FINAL WALLET ===")
log.info(f"Native HYPE: {w3.from_wei(hype_bal, 'ether')}")
log.info(f"wHYPE: {whype_bal / 1e18}")
log.info(f"USDC: {usdc_bal / 1e6}")
