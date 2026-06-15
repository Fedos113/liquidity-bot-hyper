# HYPE/USDC Local Liquidity Bot Specification

## 1. Project Overview
Build a local Python/TypeScript bot to manage concentrated liquidity positions for the HYPE/USDC pool on HyperEVM (via prjx.com). The bot operates on a long-term uptrend assumption, utilizing a tight, asymmetric range (-4% to +6%) to maximize capital efficiency while checking and updating status every 1 hour.

## 2. Environment & Setup
- **Execution:** Local machine (no cloud deployment).
- **Language:** Python (using `web3.py`) or TypeScript (using `viem` or `ethers.js`).
- **Dependencies:** Web3 library, `python-dotenv` / `dotenv`, `schedule` or `asyncio.sleep` (Python) / `node-cron` (TS), logging library.
- **Configuration:** Store `PRIVATE_KEY`, `RPC_URL` (HyperEVM), `WALLET_ADDRESS`, and `POOL_ADDRESS` in a local `.env` file.

## 3. Core Configuration & Math
- **Pool:** HYPE/USDC on HyperEVM (prjx.com).
- **Base Range:** -4% (Lower Bound) to +6% (Upper Bound) from the entry/current price.
- **Price to Tick Conversion:** 
  - `Lower_Price = Current_Price * 0.96`
  - `Upper_Price = Current_Price * 1.06`
  - Convert these prices to the nearest valid pool `tickLower` and `tickUpper` using the pool's `tickSpacing`.
- **Token Ratio:** Calculate the exact HYPE/USDC ratio required for the specific tick range at the current price to ensure zero leftover dust.

## 4. Bot Execution Loop (1-Hour Cycle)
The bot must run an infinite loop with a strict 3600-second (1 hour) sleep interval between cycles.

### Step 4.1: Status Check & Logging
- Fetch current HYPE/USDC price from the pool or a reliable oracle.
- Fetch current active position details (liquidity, token amounts, tick bounds).
- Calculate and log: Current Price, Position Lower/Upper Bounds, Distance to Bounds (%), Unclaimed Fees, and Total Position Value.

### Step 4.2: Boundary Evaluation
- Check if `Current_Price < Lower_Bound` OR `Current_Price > Upper_Bound`.
- If within bounds: Log "Position active and in range." Proceed to Step 4.4.
- If out of bounds: Proceed to Step 4.3.

### Step 4.3: Rebalancing (Out of Bounds)
- **Harvest:** Collect all accumulated fees from the current position.
- **Close:** Remove 100% of liquidity from the current position.
- **Recalculate:** 
  - Set new `Lower_Price = Current_Price * 0.96`.
  - Set new `Upper_Price = Current_Price * 1.06`.
  - Convert to new `tickLower` and `tickUpper`.
- **Reopen:** Mint a new concentrated liquidity position with the new ticks using 100% of the reclaimed HYPE and USDC.
- Log the rebalance event, old bounds, new bounds, and gas costs.

### Step 4.4: Fee Compounding (Optional but recommended)
- If unclaimed fees exceed a predefined gas-cost threshold, collect and automatically add them back to the current position (or just collect and hold, depending on gas efficiency).

### Step 4.5: Sleep
- `sleep(3600)` (Wait exactly 1 hour before restarting the loop).

## 5. Smart Contract Interactions (prjx / HyperEVM)
The AI must implement wrappers for the following NonfungiblePositionManager (or prjx equivalent) functions:
1. `mint(params)`: To open a new position. Requires `token0`, `token1`, `fee`, `tickLower`, `tickUpper`, `amount0Desired`, `amount1Desired`, `amount0Min`, `amount1Min`, `recipient`, `deadline`.
2. `decreaseLiquidity(params)`: To remove liquidity. Requires `tokenId`, `liquidity`, `amount0Min`, `amount1Min`, `deadline`.
3. `collect(params)`: To collect fees. Requires `tokenId`, `recipient`, `amount0Max`, `amount1Max`.
4. `positions(tokenId)`: To read current position state.

## 6. Error Handling & Safety
- **RPC Failures:** Implement exponential backoff for RPC rate limits or node downtime.
- **Transaction Failures:** Catch reverted transactions. If a rebalance fails due to slippage, log the error, do not crash, and retry on the next 1-hour cycle.
- **Slippage Protection:** Set `amount0Min` and `amount1Min` to 99.5% of the expected amounts (0.5% slippage tolerance) during minting/removing.
- **Graceful Shutdown:** Catch `KeyboardInterrupt` (Ctrl+C) to safely log the final state and exit without corrupting local state files.

## 7. Step-by-Step Implementation Tasks for AI
1. Initialize project, install dependencies, and set up `.env` loading.
2. Write Web3 provider setup and contract ABI loaders for the prjx Position Manager and ERC20 tokens (HYPE, USDC).
3. Implement helper functions: `get_current_price()`, `price_to_tick()`, `get_position_details()`.
4. Implement core actions: `collect_fees()`, `remove_liquidity()`, `add_liquidity()`.
5. Build the main `while True` loop with the 1-hour sleep and boundary logic.
6. Add comprehensive `logging` (info for status, warning for out-of-bounds, error for tx failures).
7. Write a dry-run/test mode toggle that calculates bounds and logs actions without sending actual blockchain transactions.