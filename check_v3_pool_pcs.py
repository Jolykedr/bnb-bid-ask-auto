"""Check PancakeSwap V3 pool state with correct ABI."""
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
print(f"Connected: {w3.is_connected()}")

# Your pool
POOL_ADDRESS = "0x873E9C3993CC43FEF030B984C82b8d1708C8d131"

# PancakeSwap V3 Pool ABI (extended slot0)
PANCAKESWAP_V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"name": "sqrtPriceX96", "type": "uint160"},
            {"name": "tick", "type": "int24"},
            {"name": "observationIndex", "type": "uint16"},
            {"name": "observationCardinality", "type": "uint16"},
            {"name": "observationCardinalityNext", "type": "uint16"},
            {"name": "feeProtocol", "type": "uint32"},  # PancakeSwap uses uint32!
            {"name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {"inputs": [], "name": "liquidity", "outputs": [{"name": "", "type": "uint128"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "fee", "outputs": [{"name": "", "type": "uint24"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "tickSpacing", "outputs": [{"name": "", "type": "int24"}], "stateMutability": "view", "type": "function"},
]

pool = w3.eth.contract(address=Web3.to_checksum_address(POOL_ADDRESS), abi=PANCAKESWAP_V3_POOL_ABI)

print(f"\n{'='*60}")
print(f"POOL: {POOL_ADDRESS}")
print(f"{'='*60}")

# Get token addresses
token0 = pool.functions.token0().call()
token1 = pool.functions.token1().call()
fee = pool.functions.fee().call()
tick_spacing = pool.functions.tickSpacing().call()

print(f"Token0: {token0}")
print(f"Token1: {token1}")
print(f"Fee: {fee} ({fee/10000:.2f}%)")
print(f"Tick Spacing: {tick_spacing}")

# Try raw call to slot0 and decode manually
print(f"\n--- Raw slot0 call ---")
try:
    # Get raw data
    result = w3.eth.call({
        'to': Web3.to_checksum_address(POOL_ADDRESS),
        'data': '0x3850c7bd'  # slot0() selector
    })
    print(f"Raw result length: {len(result)} bytes")
    print(f"Raw result: {result.hex()}")

    # Parse manually
    # sqrtPriceX96 is first 32 bytes (uint160 padded to 32)
    sqrt_price_x96 = int.from_bytes(result[0:32], 'big')
    print(f"\nsqrtPriceX96: {sqrt_price_x96}")

    # tick is next 32 bytes (int24 sign-extended to 32)
    tick_bytes = result[32:64]
    tick = int.from_bytes(tick_bytes, 'big', signed=False)
    # Convert to signed int24
    if tick > 2**23 - 1:
        tick = tick - 2**256
    # Actually for int24 in uint256 slot, if high bit of int24 is set, it's padded with 0xff
    tick_raw = int.from_bytes(result[32:64], 'big')
    if tick_raw > 2**255:  # negative in two's complement
        tick = tick_raw - 2**256
    else:
        tick = tick_raw
    # Get last 3 bytes for int24
    tick_24 = int.from_bytes(result[61:64], 'big', signed=False)
    if result[61] & 0x80:  # negative
        tick_24 = tick_24 - 2**24
    print(f"Current tick: {tick_24}")

    # Calculate price
    if sqrt_price_x96 > 0:
        price = (sqrt_price_x96 / (2**96)) ** 2
        print(f"\nPool price (token1/token0): {price:.18f}")
        if price > 0:
            print(f"Inverted (token0/token1): {1/price:.6f}")

        # Your position
        tick_lower = -60950
        tick_upper = -60000

        print(f"\n--- Your position ---")
        print(f"Tick lower: {tick_lower}")
        print(f"Tick upper: {tick_upper}")
        print(f"Current tick: {tick_24}")

        # Tick alignment check
        print(f"\n--- Tick alignment ---")
        if tick_lower % tick_spacing != 0:
            print(f"❌ tick_lower {tick_lower} NOT aligned to {tick_spacing}! Remainder: {tick_lower % tick_spacing}")
            aligned_lower = (tick_lower // tick_spacing) * tick_spacing
            print(f"   Should be: {aligned_lower}")
        else:
            print(f"✅ tick_lower aligned")

        if tick_upper % tick_spacing != 0:
            print(f"❌ tick_upper {tick_upper} NOT aligned to {tick_spacing}! Remainder: {tick_upper % tick_spacing}")
            aligned_upper = ((tick_upper // tick_spacing) + 1) * tick_spacing
            print(f"   Should be: {aligned_upper}")
        else:
            print(f"✅ tick_upper aligned")

        # Position analysis
        print(f"\n--- Position Analysis ---")
        if tick_24 < tick_lower:
            print(f"Current tick ({tick_24}) is BELOW range [{tick_lower}, {tick_upper}]")
            print(f"Position needs: 100% token0 (volatile token)")
            print(f"Your amounts show: amount0=0 - THIS IS THE PROBLEM!")
        elif tick_24 > tick_upper:
            print(f"Current tick ({tick_24}) is ABOVE range [{tick_lower}, {tick_upper}]")
            print(f"Position needs: 100% token1 (stablecoin)")
        else:
            print(f"Current tick ({tick_24}) is INSIDE range")
            print(f"Position needs: both tokens")

    # Check liquidity
    liquidity = pool.functions.liquidity().call()
    print(f"\nPool liquidity: {liquidity}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print(f"\n{'='*60}")
print("DIAGNOSIS")
print(f"{'='*60}")
print("""
The error 'execution reverted: 0x' without a message usually means:

1. TICK ALIGNMENT ISSUE - ticks must be divisible by tick_spacing
2. POSITION BELOW CURRENT PRICE - if current_tick > tick_upper, you need
   100% stablecoin (token1), but if amount1=0, it will fail
3. POSITION ABOVE CURRENT PRICE - if current_tick < tick_lower, you need
   100% volatile token (token0), but if amount0=0, it will fail

From your logs:
- amount0Desired: 0
- amount1Desired: 35714285714285712 (stablecoin)

This means you're trying to create a position 100% in stablecoin.
This ONLY works if current_tick > tick_upper (price is ABOVE your range).

If current_tick < tick_lower, you need amount0 > 0 (volatile token)!
""")
