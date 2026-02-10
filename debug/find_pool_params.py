"""Find exact pool parameters by brute-forcing fee and tick_spacing"""
from web3 import Web3
from eth_abi import encode
import time

w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
print(f"Connected: {w3.is_connected()}")

# Target pool ID
TARGET_POOL_ID = bytes.fromhex("4220a4bd26d2d2d24efbf1ab9a1e8dca8f37371bdd86a140ad4d95fc3208f17e")

# Tokens
FISH = "0x22ca9beffdc68c20ab5989cddaf4a4d9ad374444"
USDT = "0x55d398326f99059fF775485246999027B3197955"

# Uniswap V4 StateView for BSC
STATE_VIEW = "0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4"

STATE_VIEW_ABI = [
    {"inputs": [{"name": "poolId", "type": "bytes32"}], "name": "getSlot0",
     "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
                 {"name": "protocolFee", "type": "uint24"}, {"name": "lpFee", "type": "uint24"}],
     "stateMutability": "view", "type": "function"}
]

def compute_pool_id(token0, token1, fee, tick_spacing, hooks="0x0000000000000000000000000000000000000000"):
    addr0 = Web3.to_checksum_address(token0)
    addr1 = Web3.to_checksum_address(token1)
    # Sort by address
    if int(addr0, 16) > int(addr1, 16):
        addr0, addr1 = addr1, addr0
    encoded = encode(
        ['address', 'address', 'uint24', 'int24', 'address'],
        [addr0, addr1, fee, tick_spacing, Web3.to_checksum_address(hooks)]
    )
    return Web3.keccak(encoded)

print("=" * 60)
print(f"Target Pool ID: 0x{TARGET_POOL_ID.hex()}")
print("=" * 60)

# First, query the pool to get the actual fee
print("\n[1] Querying pool state...")
try:
    contract = w3.eth.contract(address=Web3.to_checksum_address(STATE_VIEW), abi=STATE_VIEW_ABI)
    slot0 = contract.functions.getSlot0(TARGET_POOL_ID).call()
    sqrt_price = slot0[0]
    tick = slot0[1]
    protocol_fee = slot0[2]
    lp_fee = slot0[3]

    print(f"✅ Pool found!")
    print(f"   sqrtPriceX96: {sqrt_price}")
    print(f"   tick: {tick}")
    print(f"   protocolFee: {protocol_fee}")
    print(f"   lpFee: {lp_fee} ({lp_fee/10000:.4f}%)")

    ACTUAL_FEE = lp_fee
except Exception as e:
    print(f"❌ Pool not found or error: {e}")
    ACTUAL_FEE = None

if ACTUAL_FEE:
    print(f"\n[2] Brute-forcing tick_spacing with fee={ACTUAL_FEE}...")

    # Try many tick_spacing values
    found = False
    for ts in range(1, 10001):
        pool_id = compute_pool_id(FISH, USDT, ACTUAL_FEE, ts)
        if pool_id == TARGET_POOL_ID:
            print(f"\n🎯 FOUND EXACT MATCH!")
            print(f"   Token0: {FISH}")
            print(f"   Token1: {USDT}")
            print(f"   Fee: {ACTUAL_FEE} ({ACTUAL_FEE/10000:.4f}%)")
            print(f"   TickSpacing: {ts}")
            print(f"   Hooks: 0x0000000000000000000000000000000000000000")
            print(f"\n   Pool ID: 0x{pool_id.hex()}")
            found = True
            break

        if ts % 1000 == 0:
            print(f"   Checked tick_spacing 1-{ts}...")

    if not found:
        print("❌ No match found with standard tokens and zero hooks")
        print("\n[3] Trying with swapped token order...")

        for ts in range(1, 10001):
            pool_id = compute_pool_id(USDT, FISH, ACTUAL_FEE, ts)
            if pool_id == TARGET_POOL_ID:
                print(f"\n🎯 FOUND with swapped tokens!")
                print(f"   Token0: {USDT}")
                print(f"   Token1: {FISH}")
                print(f"   Fee: {ACTUAL_FEE} ({ACTUAL_FEE/10000:.4f}%)")
                print(f"   TickSpacing: {ts}")
                found = True
                break

        if not found:
            print("❌ Still no match. Pool might use non-zero hooks address.")
else:
    print("\n[2] Pool not queryable. Trying full brute force...")

    # Common fees in V4 format
    fees_to_try = [
        100, 500, 1000, 2500, 3000, 5000, 10000,
        # More exotic fees
        33321, 38998, 40000, 50000,
        # Low fees
        50, 200, 300, 400
    ]

    for fee in fees_to_try:
        print(f"\n   Trying fee={fee} ({fee/10000:.4f}%)...")
        for ts in range(1, 2001):
            pool_id = compute_pool_id(FISH, USDT, fee, ts)
            if pool_id == TARGET_POOL_ID:
                print(f"\n🎯 FOUND!")
                print(f"   Fee: {fee} ({fee/10000:.4f}%)")
                print(f"   TickSpacing: {ts}")
                break
        else:
            continue
        break

print("\n" + "=" * 60)
print("Use these parameters in your UI:")
print("- Load pool ID: 0x4220a4bd26d2d2d24efbf1ab9a1e8dca8f37371bdd86a140ad4d95fc3208f17e")
print("- After loading, set Fee and TickSpacing to the values found above")
print("- Uncheck 'Auto' for tick spacing")
print("=" * 60)
