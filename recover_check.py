import logging
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("recover")

WALLET = Web3.to_checksum_address(os.getenv("WALLET_ADDRESS"))
POOL = Web3.to_checksum_address(os.getenv("POOL_ADDRESS"))
PM = Web3.to_checksum_address(os.getenv("POSITION_MANAGER_ADDRESS"))
HYPE = Web3.to_checksum_address(os.getenv("HYPE_ADDRESS"))
USDC = Web3.to_checksum_address(os.getenv("USDC_ADDRESS"))

RPC_URL = "https://rpc.hyperliquid.xyz/evm"

w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 30}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
assert w3.is_connected(), "Not connected"

log.info(f"Chain ID: {w3.eth.chain_id}")
log.info(f"Wallet: {WALLET[:10]}...{WALLET[-4:]}")
log.info(f"Block: {w3.eth.block_number}")

# Native HYPE balance
hype_bal = w3.eth.get_balance(WALLET)
log.info(f"Native HYPE: {w3.from_wei(hype_bal, 'ether')} HYPE")

# wHYPE balance
whype = w3.eth.contract(address=HYPE, abi=[
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"}
])
whype_dec = whype.functions.decimals().call()
whype_bal = whype.functions.balanceOf(WALLET).call()
log.info(f"wHYPE: {whype_bal / 10**whype_dec}")

# USDC balance
usdc = w3.eth.contract(address=USDC, abi=[
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"type":"uint8"}],"stateMutability":"view","type":"function"}
])
usdc_dec = usdc.functions.decimals().call()
usdc_bal = usdc.functions.balanceOf(WALLET).call()
log.info(f"USDC: {usdc_bal / 10**usdc_dec}")

# Check all NFTs
pm = w3.eth.contract(address=PM, abi=[
    {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"positions","outputs":[{"type":"uint96"},{"type":"address"},{"type":"address"},{"type":"address"},{"type":"uint24"},{"type":"int24"},{"type":"int24"},{"type":"uint128"},{"type":"uint256"},{"type":"uint256"},{"type":"uint128"},{"type":"uint128"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"index","type":"uint256"}],"name":"tokenOfOwnerByIndex","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}
])

nft_count = pm.functions.balanceOf(WALLET).call()
log.info(f"NFTs owned by wallet: {nft_count}")

total_owed0 = 0
total_owed1 = 0

log.info(f"\n--- All NFTs owned ---")
for i in range(nft_count):
    tid = pm.functions.tokenOfOwnerByIndex(WALLET, i).call()
    try:
        p = pm.functions.positions(tid).call()
        liq = p[7]
        owed0 = p[10] / 10**whype_dec
        owed1 = p[11] / 10**usdc_dec
        total_owed0 += owed0
        total_owed1 += owed1
        if owed0 > 0.0001 or owed1 > 0.001:
            log.info(f"  Token {tid}: liquidity={liq}, owed0={owed0:.6f} wHYPE, owed1={owed1:.6f} USDC  *** HAS VALUE ***")
        else:
            log.info(f"  Token {tid}: liquidity={liq}, owed0={owed0:.6f} wHYPE, owed1={owed1:.6f} USDC")
    except:
        log.info(f"  Token {tid}: error reading")

log.info(f"\n=== TOTALS ===")
log.info(f"Total unclaimed wHYPE fees: {total_owed0:.6f}")
log.info(f"Total unclaimed USDC fees: {total_owed1:.6f}")
hypothetical_price = 70  # USDC per wHYPE
log.info(f"Total estimated value: ${total_owed0 * hypothetical_price + total_owed1:.2f}")
