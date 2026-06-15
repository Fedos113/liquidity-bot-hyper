
# Liquidity Bot for Hyperliquid (HYPE/USDC)

An automated concentrated liquidity provision bot for the Hyperliquid ecosystem, designed to manage HYPE/USDC liquidity positions. It automatically adjusts position bounds, compounds fees, and manages liquidity based on predefined parameters.

## ⚠️ Disclaimer
This bot interacts with real funds on the blockchain. **Use at your own risk.** Always test with `DRY_RUN=true` before deploying real capital. Never share your `.env` file or private keys.

---

## 🚀 Quick Local Setup

### 1. Clone the Repository
```bash
git clone https://github.com/Fedos113/liquidity-bot-hyper.git
cd liquidity-bot-hyper
```

### 2. Create and Activate a Virtual Environment
```bash
# Create virtual environment
python3 -m venv venv

# Activate on macOS/Linux
source venv/bin/activate

# Activate on Windows
venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the example environment file to create your local configuration:
```bash
cp .env.example .env
```

Open `.env` in a text editor and configure the required variables:

#### Required Variables
```env
RPC_URL=https://your-hyperliquid-rpc-url.com
PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
WALLET_ADDRESS=0xYOUR_WALLET_ADDRESS_HERE
POOL_ADDRESS=0x6c9A33E3b592C0d65B3Ba59355d5Be0d38259285
POSITION_MANAGER_ADDRESS=0xeaD19AE861c29bBb2101E834922B2FEee69B9091
HYPE_ADDRESS=0x5555555555555555555555555555555555555555
USDC_ADDRESS=0xb88339cb7199b77e23db6e890353e22632ba630f
```

#### Bot Parameters & Configuration
```env
# Set to 0 to auto-create a position on the first run
TOKEN_ID=0

# Set to false ONLY when you are ready to execute real transactions
DRY_RUN=true

# Logging level: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=INFO

# Position bounds relative to current price
LOWER_BOUND_PCT=0.96    # -4% from current price
UPPER_BOUND_PCT=1.06    # +6% from current price

# Bot execution interval (in seconds)
SLEEP_INTERVAL=3600     # 1 hour

# Trading parameters
SLIPPAGE_TOLERANCE=0.005        # 0.5%
FEE_TIER=3000                   # 0.3% fee tier
FEE_COMPOUND_THRESHOLD_USD=5.0  # Min fee value (in USD) to trigger compounding

# Token decimals (defaults are usually correct)
HYPE_DECIMALS=18
USDC_DECIMALS=6
```

---

## ⚙️ Usage

### Running the Bot
Ensure your virtual environment is activated, then start the bot:
```bash
python main.py
```

### Command Line Overrides
You can override `.env` settings directly from the command line without editing the file:

```bash
# Force dry run mode (no real transactions will be sent)
python main.py --dry-run

# Change log level to DEBUG for detailed troubleshooting
python main.py --log-level DEBUG

# Combine arguments
python main.py --dry-run --log-level DEBUG
```

---

## 📝 Logging
The bot logs all activity to both the console and a file named `liqbot.log` in the root directory. 

To monitor the bot's activity in real-time:
```bash
tail -f liqbot.log
```

---

## 🔒 Security Best Practices
- **Never commit your `.env` file.** It is already excluded via `.gitignore`.
- Use a dedicated wallet for the bot with only the funds you intend to provide as liquidity.
- Keep your `PRIVATE_KEY` secure and never share it.
- Regularly check `liqbot.log` to ensure the bot is behaving as expected.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).