"""Check token balances and approvals for V4"""
from web3 import Web3

# Your wallet address (replace with yours!)
WALLET = input("Enter your wallet address: ").strip()

# Tokens
YOUR_TOKEN = "0x22ca9beffdc68c20ab5989cddaf4a4d9ad374444"
USDT = "0x55d398326f99059fF775485246999027B3197955"

# Uniswap V4 contracts on BSC
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
POSITION_MANAGER = "0x7a4a5c919ae2541aed11041a1aeee68f1287f95b"

w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
print(f"Connected: {w3.is_connected()}")

# ERC20 ABI (minimal)
ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
]

# Permit2 ABI (minimal)
PERMIT2_ABI = [
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "token", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "amount", "type": "uint160"}, {"name": "expiration", "type": "uint48"}, {"name": "nonce", "type": "uint48"}],
     "stateMutability": "view", "type": "function"}
]

def check_token(token_address, name):
    print(f"\n{'='*50}")
    print(f"Token: {name}")
    print(f"Address: {token_address}")
    print(f"{'='*50}")

    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    permit2 = w3.eth.contract(address=Web3.to_checksum_address(PERMIT2), abi=PERMIT2_ABI)

    try:
        symbol = token.functions.symbol().call()
        decimals = token.functions.decimals().call()
        balance = token.functions.balanceOf(Web3.to_checksum_address(WALLET)).call()
        balance_human = balance / (10 ** decimals)

        print(f"Symbol: {symbol}")
        print(f"Decimals: {decimals}")
        print(f"Balance: {balance_human:,.6f} {symbol}")

        # Check ERC20 allowance to Permit2
        erc20_allowance = token.functions.allowance(
            Web3.to_checksum_address(WALLET),
            Web3.to_checksum_address(PERMIT2)
        ).call()
        erc20_allowance_human = erc20_allowance / (10 ** decimals)
        print(f"\n[ERC20 → Permit2] Allowance: {erc20_allowance_human:,.6f} {symbol}")
        if erc20_allowance == 0:
            print("   ❌ NOT APPROVED to Permit2!")
        elif erc20_allowance >= balance:
            print("   ✅ Approved (unlimited or sufficient)")
        else:
            print(f"   ⚠️ Approved but limited to {erc20_allowance_human:,.6f}")

        # Check Permit2 allowance to PositionManager
        try:
            permit2_allowance = permit2.functions.allowance(
                Web3.to_checksum_address(WALLET),
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(POSITION_MANAGER)
            ).call()
            amount, expiration, nonce = permit2_allowance
            amount_human = amount / (10 ** decimals)

            import time
            current_time = int(time.time())
            is_expired = expiration <= current_time if expiration > 0 else True

            print(f"\n[Permit2 → PositionManager] Allowance: {amount_human:,.6f} {symbol}")
            print(f"   Expiration: {expiration} ({'EXPIRED!' if is_expired else 'Valid'})")
            print(f"   Nonce: {nonce}")

            if amount == 0:
                print("   ❌ NOT APPROVED on Permit2!")
            elif is_expired:
                print("   ❌ EXPIRED! Need to re-approve on Permit2")
            elif amount >= balance:
                print("   ✅ Approved and valid")
            else:
                print(f"   ⚠️ Approved but limited to {amount_human:,.6f}")

        except Exception as e:
            print(f"\n[Permit2 → PositionManager] Error: {e}")

    except Exception as e:
        print(f"Error: {e}")

# Check both tokens
check_token(YOUR_TOKEN, "YOUR TOKEN")
check_token(USDT, "USDT")

print(f"\n{'='*50}")
print("SUMMARY")
print(f"{'='*50}")
print(f"For V4 to work, BOTH tokens need:")
print("1. ERC20 approve to Permit2: ✅")
print("2. Permit2 approve to PositionManager: ✅ and not expired")
print("\nIf any ❌, run approve_tokens_for_ladder() or approve manually.")
