"""Quick pool check"""
from web3 import Web3
from eth_abi import encode

w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
print(f"Connected: {w3.is_connected()}")

# Your data
target_pool_id = bytes.fromhex("4220a4bd26d2d2d24efbf1ab9a1e8dca8f37371bdd86a140ad4d95fc3208f17e")
your_token = "0x22ca9beffdc68c20ab5989cddaf4a4d9ad374444"
usdt = "0x55d398326f99059fF775485246999027B3197955"
wbnb = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

# Query Uniswap V4 StateView
STATE_VIEW = "0xd13dd3d6e93f276fafc9db9e6bb47c1180aee0c4"
ABI = [{"inputs": [{"name": "poolId", "type": "bytes32"}], "name": "getSlot0",
        "outputs": [{"name": "sqrtPriceX96", "type": "uint160"}, {"name": "tick", "type": "int24"},
                    {"name": "protocolFee", "type": "uint24"}, {"name": "lpFee", "type": "uint24"}],
        "stateMutability": "view", "type": "function"}]

print(f"\nTarget Pool ID: 0x{target_pool_id.hex()}")

try:
    contract = w3.eth.contract(address=Web3.to_checksum_address(STATE_VIEW), abi=ABI)
    slot0 = contract.functions.getSlot0(target_pool_id).call()
    print(f"\n✅ Pool found on Uniswap V4!")
    print(f"   sqrtPriceX96: {slot0[0]}")
    print(f"   tick: {slot0[1]}")
    print(f"   lpFee: {slot0[3]} ({slot0[3]/10000:.4f}%)")
    actual_fee = slot0[3]
except Exception as e:
    print(f"Uniswap V4: Not found - {e}")
    actual_fee = None

# Try PancakeSwap V4
PCSV4 = "0xa0FfB9c1CE1Fe56963B0321B32E7A0302114058b"
try:
    contract = w3.eth.contract(address=Web3.to_checksum_address(PCSV4), abi=ABI)
    slot0 = contract.functions.getSlot0(target_pool_id).call()
    print(f"\n✅ Pool found on PancakeSwap V4!")
    print(f"   sqrtPriceX96: {slot0[0]}")
    print(f"   tick: {slot0[1]}")
    print(f"   lpFee: {slot0[3]} ({slot0[3]/10000:.4f}%)")
    actual_fee = slot0[3]
except Exception as e:
    print(f"PancakeSwap V4: Not found - {e}")

def compute_pool_id(token0, token1, fee, tick_spacing, hooks="0x0000000000000000000000000000000000000000"):
    addr0 = Web3.to_checksum_address(token0)
    addr1 = Web3.to_checksum_address(token1)
    if int(addr0, 16) > int(addr1, 16):
        addr0, addr1 = addr1, addr0
    encoded = encode(['address', 'address', 'uint24', 'int24', 'address'],
                     [addr0, addr1, fee, tick_spacing, Web3.to_checksum_address(hooks)])
    return Web3.keccak(encoded)

# Brute force tick_spacing
if actual_fee:
    print(f"\n🔍 Searching for tick_spacing with fee={actual_fee}...")

    # Try common values first
    for ts in [1, 10, 50, 60, 100, 200, 500, 780, 800, 820, 1000]:
        for pair in [(your_token, usdt), (your_token, wbnb)]:
            pid = compute_pool_id(pair[0], pair[1], actual_fee, ts)
            if pid == target_pool_id:
                print(f"\n✅ FOUND!")
                print(f"   Token0: {pair[0]}")
                print(f"   Token1: {pair[1]}")
                print(f"   Fee: {actual_fee} ({actual_fee/10000:.4f}%)")
                print(f"   tick_spacing: {ts}")
                exit()

    # Extended search
    print("   Not in common values, trying extended range...")
    for ts in range(1, 10001):
        for pair in [(your_token, usdt), (your_token, wbnb)]:
            pid = compute_pool_id(pair[0], pair[1], actual_fee, ts)
            if pid == target_pool_id:
                print(f"\n✅ FOUND!")
                print(f"   Token0: {pair[0]}")
                print(f"   Token1: {pair[1]}")
                print(f"   Fee: {actual_fee} ({actual_fee/10000:.4f}%)")
                print(f"   tick_spacing: {ts}")
                exit()

    print("   Not found in range 1-10000")
else:
    # Try without knowing fee - brute force both
    print("\n🔍 Brute forcing fee and tick_spacing...")
    common_fees = [100, 500, 1000, 2500, 3000, 5000, 10000, 38998, 40000]
    for fee in common_fees:
        for ts in [1, 10, 50, 60, 100, 200, 500, 780, 800, 820]:
            for pair in [(your_token, usdt), (your_token, wbnb)]:
                pid = compute_pool_id(pair[0], pair[1], fee, ts)
                if pid == target_pool_id:
                    print(f"\n✅ FOUND!")
                    print(f"   Token0: {pair[0]}")
                    print(f"   Token1: {pair[1]}")
                    print(f"   Fee: {fee} ({fee/10000:.4f}%)")
                    print(f"   tick_spacing: {ts}")
                    exit()

print("\n❌ Could not find matching parameters")
print("The pool might use different tokens or a non-zero hooks address")
