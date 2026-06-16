# Base Chain Migration Plan

## Overview

Migrate the bot from **HyperEVM** (HYPE/USDC, Uniswap V3 concentrated liquidity) to **Base** (ETH/WETH-USDC, Aerodrome Slipstream concentrated liquidity + gauge staking).

**Core constraint:** Algorithm logic and decision tree remain identical. Only chain-specific plumbing changes: addresses, gas token, middleware, ABI additions (staking), and log/var naming.

---

## Current vs Target Architecture

| Layer | HyperEVM | Base |
|---|---|---|
| Chain ID | 999 | 8453 |
| RPC | `https://rpc.hyperliquid.xyz/evm` | `https://mainnet.base.org` |
| Gas token | HYPE (native, 18 dec) | ETH (native, 18 dec) |
| Wrapped native | wHYPE (`0x5555...5555`) | WETH (`0x4200...0006`) |
| Stablecoin | USDC (`0xb883...630f`) | USDC (`0x8335...2913`) |
| DEX | Uniswap V3 | Aerodrome Slipstream (same concentrated liquidity interface as V3) |
| Swap router | Uniswap V3 SwapRouter (`0x1EbD...Af9B`) | Aerodrome Slipstream SwapRouter (`0x698C...92F`) |
| Position manager | Uniswap V3 NonfungiblePositionManager | Aerodrome Slipstream NonfungiblePositionManager (`0xe1f8...b53`) |
| Pool | HYPE/USDC 0.05% (`0x6c9A...9285`) | WETH/USDC 0.05% (`0x3FE0...392A`) |
| Staking | None | Aerodrome CL Gauge (`0xA0B6...D28`) |
| Middleware | `ExtraDataToPOAMiddleware` | None needed |
| Gas reserve | 0.02 HYPE | **0.0014 ETH** |
| Fee tier | 500 | 50 |

---

## Contract Addresses (Base)

| Contract | Address | Found In |
|---|---|---|
| **WETH** | `0x4200000000000000000000000000000000000006` | WRAP tx |
| **USDC** | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` | SWAP tx |
| **Aerodrome Slipstream Pool** (WETH/USDC 0.05%) | `0x3FE04A59Ebd38cF06080a6F60a98D124eb59392A` | MINT tx (pool emitted Mint event) |
| **Aerodrome Slipstream Position Manager** | `0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53` | MINT/UNMINT tx |
| **Aerodrome Slipstream SwapRouter** | `0x698Cb2b6dd822994581fEa6eA4Fc755d1363A92F` | Aerodrome Slipstream docs |
| **Aerodrome Slipstream Factory** | `0xf8f2eB4940CFE7d13603DDDD87f123820Fc061Ef` | Pool deposit URL |
| **Aerodrome CL Gauge** (stake NFT for AERO rewards) | `0xA0B61fdB9f1FB9b917Fe38b49427Fd4D87472D28` | DEPOSIT/WITHDRAW tx |
| **Aerodrome CL GaugeFactory** | `0x385293CaE378C813F16f0C1334d774AdDDf56AbB` | Aerodrome docs |
| **AERO token** (reward token) | `0x940181a94A35A4569E4529A3CDfB74e38FD98631` | WITHDRAW tx (reward transfer) |

> Note: The SWAP tx the user sent used **Uniswap V4 Universal Router** (`0xFdf682...`) for the swap itself, but the **Aerodrome Slipstream SwapRouter** at `0x698Cb2b6dd822994581fEa6eA4Fc755d1363A92F` supports the standard `ISwapRouter.exactInputSingle` interface — same as the current bot's `SWAP_ROUTER_ABI`. This means minimal swap code changes.

---

## Configuration Changes (`.env`)

### Variable rename mapping

| Old Name (HyperEVM) | New Name (Base) | Reason |
|---|---|---|
| `HYPE_ADDRESS` | `WETH_ADDRESS` | Native token name change |
| `HYPE_DECIMALS` | `NATIVE_DECIMALS` (keep 18) | Generic name |
| `HYPE_DROP_THRESHOLD` | `DROP_THRESHOLD` | Generic name |
| — | `GAUGE_ADDRESS` | New staking contract |

### Updated values

| Variable | Old | New |
|---|---|---|
| `RPC_URL` | `https://rpc.hyperliquid.xyz/evm` | `https://mainnet.base.org` |
| `POOL_ADDRESS` | `0x6c9A...9285` | `0x3FE04A59Ebd38cF06080a6F60a98D124eb59392A` |
| `POSITION_MANAGER_ADDRESS` | `0xeaD1...9091` | `0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53` |
| `WETH_ADDRESS` (was `HYPE_ADDRESS`) | `0x5555...5555` | `0x4200000000000000000000000000000000000006` |
| `USDC_ADDRESS` | `0xb883...630f` | `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913` |
| `SWAP_ROUTER_ADDRESS` | `0x1EbD...Af9B` | `0x698Cb2b6dd822994581fEa6eA4Fc755d1363A92F` |
| `FEE_TIER` | `500` | `50` |
| `GAUGE_ADDRESS` | (none) | `0xA0B61fdB9f1FB9b917Fe38b49427Fd4D87472D28` |
| `GAS_RESERVE_ETH` (hardcoded) | `0.02 HYPE` | `0.0014 ETH` |

---

## File-by-File Changes

### 1. `src/config.py` — Config class

```
Rename:
  HYPE_ADDRESS      → WETH_ADDRESS
  HYPE_DECIMALS     → NATIVE_DECIMALS
  HYPE_DROP_THRESHOLD → DROP_THRESHOLD

Add:
  GAUGE_ADDRESS: str = os.getenv("GAUGE_ADDRESS", "")

Update:
  - Hardcoded default for SWAP_ROUTER_ADDRESS: "0x1EbD..." → "0x698C..."
  - validate(): check WETH_ADDRESS instead of HYPE_ADDRESS
  - validate(): add GAUGE_ADDRESS to required (or optional)
```

### 2. `src/constants.py` — ABIs and tick spacings

```
TICK_SPACINGS:
  Add: 50: 10   (Aerodrome Slipstream 0.05%; tick spacing matches Uniswap V3's fee/5 = 10)

WHYPE_ABI → rename to WETH_ABI (same ABI, just rename)

Add GAUGE_ABI for staking:
  [
    {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"deposit","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"user","type":"address"},{"indexed":true,"name":"tokenId","type":"uint256"},{"indexed":true,"name":"liquidityToStake","type":"uint128"}],"name":"Deposit","type":"event"},
    {"anonymous":false,"inputs":[{"indexed":true,"name":"user","type":"address"},{"indexed":true,"name":"tokenId","type":"uint256"},{"indexed":true,"name":"liquidityToStake","type":"uint128"}],"name":"Withdraw","type":"event"},
  ]
```

Note: The Aerodrome Slipstream Position Manager uses the **same ABI** as Uniswap V3 NonfungiblePositionManager. `POSITION_MANAGER_ABI`, `POOL_ABI`, `ERC20_ABI`, `SWAP_ROUTER_ABI` stay identical.

### 3. `src/provider.py` — Web3 connection + contract factories

```
Changes:
  - Remove ExtraDataToPOAMiddleware import and injection (line 9, 48)
  - Rename get_hype_contract()      → get_weth_contract()
  - Rename get_whype_contract()     → get_weth_contract() (consolidate, or keep as alias)
  - Rename get_hype_contract() callers → use get_weth_contract()
  - Update logger msg: "Connected to HyperEVM" → "Connected to Base. Chain ID: {w3.eth.chain_id}"
  - RATE_LIMIT_CODE may need adjustment based on RPC provider (Infura, Alchemy, etc.)
  - Add get_gauge_contract(w3) factory function
```

### 4. `src/position_manager.py` — All on-chain tx functions

```
Global constants:
  GAS_RESERVE_HYPE = 0.02  →  GAS_RESERVE_ETH = 0.0014
  _hype_price_usd           →  _native_price_usd
  set_hype_price()          →  set_native_price()

balance_tokens():
  - Wrap native ETH → WETH via WETH.deposit() payable
  - gas_reserve_wei = int(GAS_RESERVE_ETH * 1e18)
  - Log: "WETH" / "ETH" instead of "wHYPE" / "HYPE"

wrap_hype() → rename to wrap_eth() or wrap_native():
  - Uses WETH_ABI (renamed from WHYPE_ABI)
  - Log: "ETH" → "WETH"

send_transaction():
  - Log: "HYPE" → "ETH" in fee message

get_current_price():
  - get_token_order(pool, config.WETH_ADDRESS) instead of config.HYPE_ADDRESS

_optimize_ratio() / add_to_position() / rebalance() / create_position():
  - All config.HYPE_ADDRESS references → config.WETH_ADDRESS
  - All config.HYPE_DECIMALS references → config.NATIVE_DECIMALS
  - All token name strings: "HYPE" → "WETH" or "ETH"

get_hype_or_usdc() → rename to get_native_or_usdc():
  - is_hype param → is_native (keep same logic)

New functions:
  def stake_position(w3, gauge_contract, token_id: int, dry_run: bool = False):
      """Call gauge.deposit(token_id) to stake the LP NFT for AERO rewards."""
      tx = gauge_contract.functions.deposit(token_id).build_transaction(
          build_tx_params(w3, 200_000)
      )
      send_transaction(w3, tx, dry_run)

  def unstake_position(w3, gauge_contract, token_id: int, dry_run: bool = False):
      """Call gauge.withdraw(token_id) to unstake and claim AERO rewards."""
      tx = gauge_contract.functions.withdraw(token_id).build_transaction(
          build_tx_params(w3, 200_000)
      )
      send_transaction(w3, tx, dry_run)
```

### 5. `src/math_utils.py` — Math functions

```
get_token_order():
  - Parameter: hype_address: str → native_address: str
  - Variable: hype_lower → native_lower
  - Error msg: "HYPE address" → "Native token address"
  - config.HYPE_DECIMALS → config.NATIVE_DECIMALS

Rest of math: UNCHANGED (price math is protocol-agnostic)
```

### 6. `src/bot.py` — Main loop + secondary protection

```
run_bot():
  - Startup log: "HYPE/USDC Liquidity Bot" → "WETH/USDC Liquidity Bot"
  - All log messages referencing HYPE → ETH/WETH
  - Variable names: hype_bal → eth_bal (or native_bal)
  - set_hype_price(current_price) → set_native_price(current_price)

Position management flow — new staking steps added:
  After mint_position() / create_position():
    → stake_position(w3, gauge_contract, token_id, dry_run)

  Before remove_liquidity() in rebalance():
    → unstake_position(w3, gauge_contract, token_id, dry_run)

  In emergency close (secondary cycle):
    → unstake_position(w3, gauge_contract, token_id, dry_run)
    → then proceed with remove/swap as before

_secondary_cycle():
  - get_token_order(pool, config.WETH_ADDRESS)
  - Log msgs: "HYPE" → "ETH/WETH"
  - Swap: config.WETH_ADDRESS instead of config.HYPE_ADDRESS

_auto_discover_position():
  - UNCHANGED (uses ERC721 balanceOf/tokenOfOwnerByIndex)
  - Note: when NFT is staked in gauge, balanceOf returns 0 for the wallet
    → The gauge contract holds the NFT. Need to detect staked positions.
    → Resolution: token_id_ref tracks active token_id; on startup, check
      `gauge_contract.functions.tokenOfOwnerByIndex(wallet, 0)` or similar
      to find staked NFTs. OR simply skip auto-discovery of staked NFTs
      and rely on config.TOKEN_ID or persistent state.

  Auto-discover for staked positions:
    Add gauge_contract parameter; query gauge.balanceOf(wallet) for staked tokenIds:
      _ERC721_ABI on gauge_contract → gauge_contract.functions.balanceOf(account).call()
      gauge_contract.functions.tokenOfOwnerByIndex(account, i).call()
    This returns staked token IDs before withdrawal.

Update main loop to import gauge_contract:
  from src.provider import get_gauge_contract
  gauge = get_gauge_contract(w3)
```

### 7. `.env.example`

```
Apply all renaming; add GAUDE_ADDRESS; update all addresses.
```

### 8. `main.py`

```
CLI description: "HYPE/USDC" → "WETH/USDC"
```

---

## New Staking Lifecycle

The user's Base transactions demonstrate a staking loop that doesn't exist on HyperEVM:

```
1. MINT position → NFT received (tokenId = 1378654)
2. DEPOSIT tokenId → NFT sent to gauge, staked for AERO rewards
3. [time passes, fees + AERO accumulate]
4. WITHDRAW tokenId → NFT returned from gauge, AERO rewards claimed
5. UNMINT tokenId → decreaseLiquidity + collect + burn
```

**Integration into bot flow:**

```
create_position() or rebalance():
  Mint position
  → stake_position(gauge, tokenId)         [NEW STEP]

add_to_position():
  (no staking change; position is already staked)

rebalance():
  → unstake_position(gauge, tokenId)       [NEW STEP]
  → collect_fees()
  → remove_liquidity()
  → collect_fees()
  → balance_tokens()
  → mint new position
  → stake_position(gauge, newTokenId)      [NEW STEP]

Secondary protection close:
  → unstake_position(gauge, tokenId)       [NEW STEP]
  → collect_fees()
  → remove_liquidity()
  → collect_fees()
  → swap all to USDC
```

**Gauge ABI** (confirmed from DEPOSIT/WITHDRAW transactions):

```json
[
  {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"deposit","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[{"name":"owner","type":"address"},{"name":"index","type":"uint256"}],"name":"tokenOfOwnerByIndex","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
  {"anonymous":false,"inputs":[{"indexed":true,"name":"user","type":"address"},{"indexed":true,"name":"tokenId","type":"uint256"},{"indexed":true,"name":"liquidityToStake","type":"uint128"}],"name":"Deposit","type":"event"},
  {"anonymous":false,"inputs":[{"indexed":true,"name":"user","type":"address"},{"indexed":true,"name":"tokenId","type":"uint256"},{"indexed":true,"name":"liquidityToStake","type":"uint128"}],"name":"Withdraw","type":"event"}
]
```

The gauge contract at `0xA0B61fdB9f1FB9b917Fe38b49427Fd4D87472D28` behaves like an ERC721 holder. When tokenId is deposited:
- The Position Manager transfers the NFT to the gauge
- The gauge records ownership internally
- `gauge.balanceOf(wallet)` returns count of staked NFTs
- `gauge.tokenOfOwnerByIndex(wallet, i)` returns staked token IDs

This means `_auto_discover_position()` needs to check BOTH the Position Manager (unstaked NFTs) AND the Gauge (staked NFTs).

---

## Pool Parameters (from User's Transactions)

| Parameter | Value | Source |
|---|---|---|
| Pool address | `0x3FE04A59Ebd38cF06080a6F60a98D124eb59392A` | MINT tx |
| Token0 | WETH (`0x4200...0006`) | MINT tx |
| Token1 | USDC (`0x8335...2913`) | MINT tx |
| Fee tier | 50 (0.05%) | MINT tx params |
| Tick spacing | 10 (expected; query via `pool.tickSpacing()`) | Aerodrome convention |
| Current tick | -201460 | SWAP tx |
| tickLower (minted) | -201600 | MINT tx |
| tickUpper (minted) | -201250 | MINT tx |
| Range width | 350 ticks (~2% bandwidth) | Calculated |
| Token ID | 1378654 | MINT tx |
| Position Manager | `0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53` | MINT/UNMINT tx |
| Gauge | `0xA0B61fdB9f1FB9b917Fe38b49427Fd4D87472D28` | DEPOSIT/WITHDRAW tx |

The price tick range of [-201600, -201250] around tick -201460 corresponds to approximately ±1% bounds as specified.

---

## Swap Router Decision

**Recommended:** Use **Aerodrome Slipstream SwapRouter** at `0x698Cb2b6dd822994581fEa6eA4Fc755d1363A92F`.

This router supports `ISwapRouter.exactInputSingle()` — the **exact same ABI** (`SWAP_ROUTER_ABI`) the bot currently uses. No swap logic changes needed; just update the address.

**Alternative** (user's manual tx): Uniswap V4 Universal Router at `0xFdf682F51FE81Aa4898F0AE2163d8A55c127fbC7` — requires replacing the entire swap ABI with Universal Router's `execute(bytes commands, bytes[] inputs, uint256 deadline)` format. More complex, not recommended unless there's a specific liquidity reason.

---

## Gas Token Implications

- **Gas token:** ETH (18 decimals) — same decimal as HYPE, so the existing decimal math works
- **Gas reserve:** 0.0014 ETH (untouchable balance for fees) — replaces 0.02 HYPE
- **Wrapping:** ETH → WETH via `WETH.deposit()` payable with `value` field (same as wHYPE)
- **Unwrapping:** WETH → ETH via `WETH.withdraw(wad)`
- **EIP-1559:** Base uses EIP-1559; already handled by `w3.eth.gas_price` and the `gasPrice` tx param

---

## Middleware Changes

- **Remove** `ExtraDataToPOAMiddleware` — Base does not need PoA middleware
- **Rate limits:** Different RPC providers have different rate limit error codes. If using public `mainnet.base.org`, the `-32005` code may still apply

---

## Tick Spacing for Fee Tier 50

Aerodrome Slipstream fee tier 50 (0.05%) uses **tick spacing = 10**. Add to `TICK_SPACINGS`:

```python
TICK_SPACINGS = {
    50: 10,     # Aerodrome Slipstream 0.05% ← ADD
    100: 1,
    500: 10,
    3000: 60,
    10000: 200,
}
```

The `get_tick_spacing()` function already falls back to `pool.functions.tickSpacing().call()` if the dict lookup fails, so the bot will work even without the hardcoded entry.

---

## Testing Sequence (Full Loop on Base)

1. **Connect:** Web3 to `https://mainnet.base.org`, verify chain_id = 8453
2. **Read pool:** `pool.slot0()`, `pool.token0()`, `pool.token1()`, `pool.fee()` → 50
3. **Wrap:** Send 0.01 ETH → `WETH.deposit()` payable → verify WETH balance
4. **Swap:** approve WETH → SwapRouter, call `exactInputSingle(WETH→USDC)` → verify USDC balance
5. **Mint:** approve WETH+USDC → PositionManager, call `mint()` → get tokenId
6. **Stake:** call `gauge.deposit(tokenId)` → verify NFT transferred to gauge
7. **Unstake:** call `gauge.withdraw(tokenId)` → verify NFT returned, AERO received
8. **Burn:** call `position_manager.multicall([decreaseLiquidity, collect])` → verify tokens returned
9. **Full loop:** Run bot dry-run, verify all transactions are valid

---

## Key Risks & Notes

1. **Staked NFT discovery:** When the NFT is staked in the gauge, `position_manager.balanceOf(wallet)` returns 0. The bot must query the gauge contract for staked positions. Update `_auto_discover_position()` to check both the position manager AND the gauge.

2. **AERO rewards:** The `withdraw()` call on the gauge claims AERO rewards automatically. The bot may want to swap AERO → USDC/WETH periodically. This is a new optional feature.

3. **Gauge address may change:** Aerodrome deploys per-pool gauges. Verify `0xA0B61fdB9f1FB9b917Fe38b49427Fd4D87472D28` is the correct gauge for the WETH/USDC 0.05% pool. Cross-reference with `CLGaugeFactory` at `0x385293CaE378C813F16f0C1334d774AdDDf56AbB`.

4. **RPC reliability:** Public Base RPC at `mainnet.base.org` may have rate limits. Consider using Alchemy/Infura for production.

5. **Slippage on swap:** Aerodrome Slipstream pools may have different liquidity profiles than Uniswap V3. The existing 0.5% slippage tolerance should be reviewed.
