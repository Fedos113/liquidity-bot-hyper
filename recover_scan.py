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

POSITIONS_ABI = [{"inputs":[{"name":"tokenId","type":"uint256"}],"name":"positions","outputs":[{"type":"uint96"},{"type":"address"},{"type":"address"},{"type":"address"},{"type":"uint24"},{"type":"int24"},{"type":"int24"},{"type":"uint128"},{"type":"uint256"},{"type":"uint256"},{"type":"uint128"},{"type":"uint128"}],"stateMutability":"view","type":"function"}]
BALANCE_ABI = [{"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}]
TOKEN_ABI = [{"inputs":[{"name":"owner","type":"address"},{"name":"index","type":"uint256"}],"name":"tokenOfOwnerByIndex","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}]

pm = w3.eth.contract(address=PM, abi=POSITIONS_ABI + BALANCE_ABI + TOKEN_ABI)

nft_count = pm.functions.balanceOf(WALLET).call()
log.info(f"NFTs owned: {nft_count}")

# Scan ALL positions
tokens_to_collect = []
for i in range(nft_count):
    try:
        tid = pm.functions.tokenOfOwnerByIndex(WALLET, i).call()
        p = pm.functions.positions(tid).call()
        owed0 = p[10]
        owed1 = p[11]
        liq = p[7]
        if owed0 > 0 or owed1 > 0:
            val = owed0/1e18 * 70 + owed1/1e6
            log.info(f"Token {tid}: liquidity={liq}, owed0={owed0/1e18:.6f} wHYPE, owed1={owed1/1e6:.6f} USDC (≈${val:.2f}) *** COLLECT ***")
            tokens_to_collect.append(tid)
        time.sleep(0.2)
    except Exception as e:
        log.info(f"  Index {i}: error")
        time.sleep(1)

log.info(f"\nTokens to collect: {tokens_to_collect}")

if not tokens_to_collect:
    log.info("Nothing to collect.")
    log.info(f"Final HYPE: {w3.from_wei(w3.eth.get_balance(WALLET), 'ether')}")
    log.info(f"Final USDC: {w3.eth.contract(address=USDC, abi=[{'inputs':[{'name':'owner','type':'address'}],'name':'balanceOf','outputs':[{'type':'uint256'}],'stateMutability':'view','type':'function'}]).functions.balanceOf(WALLET).call() / 1e6}")
    exit()

# Collect from each
COLLECT_ABI = [{"inputs":[{"components":[{"name":"tokenId","type":"uint256"},{"name":"recipient","type":"address"},{"name":"amount0Max","type":"uint128"},{"name":"amount1Max","type":"uint128"}],"name":"params","type":"tuple"}],"name":"collect","outputs":[{"name":"amount0","type":"uint256"},{"name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"}]
pm_collect = w3.eth.contract(address=PM, abi=COLLECT_ABI)

for tid in tokens_to_collect:
    collect_params = {
        "tokenId": tid,
        "recipient": WALLET,
        "amount0Max": 2**128 - 1,
        "amount1Max": 2**128 - 1,
    }
    nonce = w3.eth.get_transaction_count(WALLET)
    tx = pm_collect.functions.collect(collect_params).build_transaction({
        "from": WALLET,
        "nonce": nonce,
        "gas": 150000,
        "maxFeePerGas": w3.to_wei("2", "gwei"),
        "maxPriorityFeePerGas": w3.to_wei("0.1", "gwei"),
        "chainId": 999,
    })
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    log.info(f"Collecting token {tid}... tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    log.info(f"  Gas: {receipt['gasUsed']}, status: {'ok' if receipt['status'] else 'FAILED'}")
    time.sleep(1)

# Show final balances
whype = w3.eth.contract(address=HYPE, abi=[{"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}])
usdc = w3.eth.contract(address=USDC, abi=[{"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}])
log.info(f"\n=== FINAL BALANCES ===")
log.info(f"Native HYPE: {w3.from_wei(w3.eth.get_balance(WALLET), 'ether')}")
log.info(f"wHYPE: {whype.functions.balanceOf(WALLET).call() / 1e18}")
log.info(f"USDC: {usdc.functions.balanceOf(WALLET).call() / 1e6}")
