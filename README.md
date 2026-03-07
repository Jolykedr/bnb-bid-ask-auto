# Liquidity Ladder — BNB & BASE

**Automated bid-ask liquidity ladder tool for Uniswap V3/V4 and PancakeSwap V3/V4.**

Deploy concentrated liquidity positions across custom price ranges with advanced distribution strategies. Built for professional DeFi market makers and liquidity providers on BNB Chain and BASE.

---

## Purchase & Contact

| | |
|---|---|
| **Telegram** | [@jolykedr](https://t.me/jolykedr) |
| **Updates Channel** | [t.me/liquidityladderbnbbase](https://t.me/liquidityladderbnbbase) |

> License key required. Contact via Telegram to purchase.

---

## Features

### Liquidity Management
- **Bid-Ask Ladder** — create multiple concentrated liquidity positions across a price range with one click
- **Distribution Types** — Linear, Quadratic, Exponential, Fibonacci weight distribution
- **V3 & V4 Support** — Uniswap V3/V4 and PancakeSwap V3/V4 on BNB Chain and BASE
- **Batch Operations** — mint multiple positions in a single transaction (V4)
- **Position Management** — view, close, and collect fees from all your positions
- **Fee Collection** — automatic uncollected fee harvesting on close

### Trading
- **Integrated Swap** — swap tokens directly via KyberSwap, V2, or V3 routing
- **Auto-sell on close** — optionally swap received tokens back to stablecoin when closing positions
- **Slippage control** — configurable slippage for all operations

### Calculator
- **Preview Mode** — see exact position breakdown before committing
- **Percent-based ranges** — define ranges as % from current price
- **USD denomination** — enter amounts in USD, auto-converted to token quantities

### Security
- **Encrypted wallet storage** — private keys encrypted with AES-GCM, password-protected
- **Server-validated license** — HWID-bound license key, no file sharing possible
- **Certificate pinning** — HTTPS with pinned server certificate

---

## Supported Chains & DEXes

| Chain | DEX | Versions |
|-------|-----|----------|
| BNB Chain | PancakeSwap | V3, V4 |
| BNB Chain | Uniswap | V3 |
| BASE | Uniswap | V3, V4 |
| BASE | PancakeSwap | V3 |

---

## Installation

### Requirements
- Python 3.12+
- Windows 10/11

### Setup

```bash
# Clone
git clone https://github.com/Jolykedr/bnb-bid-ask-auto.git
cd bnb-bid-ask-auto

# Install dependencies
pip install -e .

# Run
python run_ui.py
```

On first launch, you'll be prompted to enter your license key.

---

## Quick Start

### 1. Activate License
Enter the license key you received after purchase. The key is bound to your device — one key per machine.

### 2. Add Wallet
Go to **Settings** and add your wallet. Private key is encrypted locally with your password.

### 3. Select Chain & Pool
In the **Create** tab:
- Choose chain (BNB / BASE)
- Select DEX and version (PancakeSwap V3, Uniswap V4, etc.)
- Enter pool address or select token pair

### 4. Configure Ladder
- Set **price range** (% from current price or absolute values)
- Set **number of positions** (1-20)
- Set **total amount** in USD
- Choose **distribution type** (Linear recommended for beginners)

### 5. Preview & Create
Click **Preview** to see exact position breakdown. Review amounts, then click **Create** to deploy.

### 6. Manage Positions
Switch to the **Manage** tab to:
- View all your active positions
- Collect accumulated fees
- Close positions (with optional auto-swap to stablecoin)

---

## Tabs

| Tab | Description |
|-----|-------------|
| **Create** | Configure and deploy liquidity ladder |
| **Manage** | View, collect fees, close existing positions |
| **Calculator** | Preview distributions without deploying |
| **Advanced** | Pool creation, manual operations |
| **Settings** | Wallet management, RPC, chain config |

---

## Distribution Types

| Type | Pattern | Best For |
|------|---------|----------|
| **Linear** | 1, 2, 3, 4... | Even buying pressure |
| **Quadratic** | 1, 4, 9, 16... | Aggressive dip buying |
| **Exponential** | Exp growth | Maximum capital at lowest prices |
| **Fibonacci** | 1, 1, 2, 3, 5, 8... | Natural scaling |

All distributions place **more capital at lower prices** — the further price drops, the more you buy.

---

## Security & Trust

This software handles wallet private keys. Here's why you can trust it:

**Open source** — the entire codebase is public. You can read every line of code before using it. There are no hidden binaries, no obfuscated modules, no backdoors.

**Private keys never leave your machine.** They are:
- Encrypted locally with AES-256-GCM using your password
- Decrypted only in RAM when you enter your password
- Never sent to any server, API, or third party

**The license server only receives:**
- Your license key
- A hardware fingerprint (hash of your machine ID)
- That's it. No wallet data. No transaction data. No personal information.

You can verify all of this yourself — check `licensing/license_checker.py` to see exactly what is sent, and `src/crypto.py` to see how wallet encryption works.

**You can run from source** — clone the repo, read the code, run `python run_ui.py`. You don't have to trust a binary.

---

## License

This software requires a valid license key. Keys are bound to a single device (HWID).

- License is validated against a remote server on each launch
- Works offline for 24 hours after last successful validation
- If you change your PC, contact support to reset HWID binding

To purchase or renew: [@jolykedr](https://t.me/jolykedr)

---

## Updates

Follow the Telegram channel for updates, new features, and announcements:

**[t.me/liquidityladderbnbbase](https://t.me/liquidityladderbnbbase)**
