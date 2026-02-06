"""Check V3 pool state for debugging mint issues."""
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
print(f"Connected: {w3.is_connected()}")

# Your tokens
TOKEN0 = "0x2a846aaaf896ef393ccb76398c1d96ea97374444"
TOKEN1 = "0x55d398326f99059fF775485246999027B3197955"  # USDT

# PancakeSwap V3 Factory
PANCAKESWAP_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"

FACTORY_ABI = [
    {"inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"}, {"name": "fee", "type": "uint24"}],
     "name": "getPool", "outputs": [{"name": "pool", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "fee", "type": "uint24"}], "name": "feeAmountTickSpacing",
     "outputs": [{"name": "", "type": "int24"}], "stateMutability": "view", "type": "function"}
]

POOL_ABI = [
    {"inputs": [], "name": "slot0", "outputs": [
        {"name": "sqrtPriceX96", "type": "uint160"},
        {"name": "tick", "type": "int24"},
        {"name": "observationIndex", "type": "uint16"},
        {"name": "observationCardinality", "type": "uint16"},
        {"name": "observationCardinalityNext", "type": "uint16"},
        {"name": "feeProtocol", "type": "uint8"},
        {"name": "unlocked", "type": "bool"}
    ], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "liquidity", "outputs": [{"name": "", "type": "uint128"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "fee", "outputs": [{"name": "", "type": "uint24"}], "stateMutability": "view", "type": "function"},
]

factory = w3.eth.contract(address=Web3.to_checksum_address(PANCAKESWAP_FACTORY), abi=FACTORY_ABI)

print("\n" + "="*60)
print("CHECKING POOLS FOR TOKEN PAIR")
print("="*60)
print(f"Token0: {TOKEN0}")
print(f"Token1: {TOKEN1}")

# Check different fee tiers
FEE_TIERS = {
    100: "0.01%",
    500: "0.05%",
    2500: "0.25%",
    3000: "0.30%",
    10000: "1.00%"
}

for fee, fee_name in FEE_TIERS.items():
    print(f"\n--- Fee {fee} ({fee_name}) ---")

    try:
        # Check if this fee tier is enabled
        tick_spacing = factory.functions.feeAmountTickSpacing(fee).call()
        print(f"  Tick spacing: {tick_spacing}")

        if tick_spacing == 0:
            print(f"  ⚠️  Fee tier NOT ENABLED on this DEX")
            continue

    except Exception as e:
        print(f"  ⚠️  Fee tier check failed: {e}")
        continue

    try:
        pool_address = factory.functions.getPool(
            Web3.to_checksum_address(TOKEN0),
            Web3.to_checksum_address(TOKEN1),
            fee
        ).call()

        if pool_address == "0x0000000000000000000000000000000000000000":
            print(f"  ❌ Pool DOES NOT EXIST")
            continue

        print(f"  ✅ Pool exists: {pool_address}")

        # Get pool info
        pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ABI)

        try:
            slot0 = pool.functions.slot0().call()
            sqrt_price_x96 = slot0[0]
            current_tick = slot0[1]
            unlocked = slot0[6]

            if sqrt_price_x96 == 0:
                print(f"  ⚠️  Pool NOT INITIALIZED (sqrtPriceX96=0)")
                continue

            # Calculate price from sqrtPriceX96
            price = (sqrt_price_x96 / (2**96)) ** 2

            print(f"  sqrtPriceX96: {sqrt_price_x96}")
            print(f"  Current tick: {current_tick}")
            print(f"  Pool price (token1/token0): {price:.10f}")
            print(f"  Inverted price (token0/token1): {1/price if price > 0 else 0:.4f}")
            print(f"  Unlocked: {unlocked}")

            liquidity = pool.functions.liquidity().call()
            print(f"  Liquidity: {liquidity}")

            # Your position ticks from logs
            tick_lower = -60950
            tick_upper = -60000

            print(f"\n  Your position ticks: {tick_lower} to {tick_upper}")
            print(f"  Current tick: {current_tick}")

            if current_tick < tick_lower:
                print(f"  ℹ️  Current tick BELOW your range - position will be 100% token0")
            elif current_tick > tick_upper:
                print(f"  ℹ️  Current tick ABOVE your range - position will be 100% token1 (stablecoin)")
            else:
                print(f"  ℹ️  Current tick INSIDE your range - position will have both tokens")

            # Check tick alignment
            if tick_lower % tick_spacing != 0:
                print(f"  ❌ tick_lower NOT aligned! {tick_lower} % {tick_spacing} = {tick_lower % tick_spacing}")
            if tick_upper % tick_spacing != 0:
                print(f"  ❌ tick_upper NOT aligned! {tick_upper} % {tick_spacing} = {tick_upper % tick_spacing}")

        except Exception as e:
            print(f"  ❌ Failed to read pool state: {e}")

    except Exception as e:
        print(f"  ❌ Error: {e}")

print("\n" + "="*60)
print("SUMMARY")
print("="*60)
print("""
If pool with fee 2500 shows 'NOT INITIALIZED' or 'DOES NOT EXIST':
- You need to use a different fee tier (try 10000 which works)
- Or create and initialize the pool first

If fee tier 2500 is not enabled (tick_spacing=0):
- PancakeSwap V3 may not support this fee tier
- Use 500 (0.05%), 2500 (0.25%), or 10000 (1%)
""")
