"""Decode successful Uniswap V4 transaction to see what it does"""
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))

# Your successful transaction
TX_HASH = "0x3107edab2cee04052b2c72e05ec86731d332879a8ad1a47024fbf88d91599c3f"

print("=" * 70)
print(f"Decoding transaction: {TX_HASH}")
print("=" * 70)

tx = w3.eth.get_transaction(TX_HASH)

print(f"\nFrom: {tx['from']}")
print(f"To: {tx['to']}")
print(f"Value: {tx['value']}")
print(f"Gas: {tx['gas']}")

input_data = tx['input'].hex() if isinstance(tx['input'], bytes) else tx['input']
print(f"\nInput data length: {len(input_data)} chars")
print(f"Input data (first 200 chars): {input_data[:200]}...")

# Function selector (first 4 bytes / 8 hex chars after 0x)
selector = input_data[2:10] if input_data.startswith('0x') else input_data[:8]
print(f"\nFunction selector: 0x{selector}")

# Known selectors
KNOWN_SELECTORS = {
    "ac9650d8": "multicall(bytes[])",
    "0c49ccbe": "modifyLiquidities(bytes,uint256)",
    "f3995c67": "modifyLiquiditiesWithoutUnlock(bytes,uint256)",
    "09b81346": "mintPosition(PoolKey,int24,int24,uint256,uint128,uint128,address,bytes)",
}

if selector in KNOWN_SELECTORS:
    print(f"Function: {KNOWN_SELECTORS[selector]}")
else:
    print(f"Unknown function selector")

# If it's modifyLiquidities, decode the unlockData
if selector == "0c49ccbe":
    print("\n" + "=" * 70)
    print("Decoding modifyLiquidities parameters...")
    print("=" * 70)

    # Skip selector (10 chars with 0x)
    params_hex = input_data[10:]

    # First 64 chars = offset to bytes array
    # Next 64 chars = deadline
    offset = int(params_hex[:64], 16)
    deadline = int(params_hex[64:128], 16)

    print(f"Deadline: {deadline}")

    # Bytes data starts at offset
    bytes_offset = offset * 2  # Convert to hex position
    bytes_len_hex = params_hex[bytes_offset:bytes_offset+64]
    bytes_len = int(bytes_len_hex, 16)

    print(f"UnlockData length: {bytes_len} bytes")

    # Extract unlockData
    unlock_data_hex = params_hex[bytes_offset+64:bytes_offset+64+bytes_len*2]
    print(f"\nUnlockData (hex): {unlock_data_hex[:200]}...")

    # First byte is number of actions
    if len(unlock_data_hex) >= 2:
        num_actions_or_first_byte = int(unlock_data_hex[:2], 16)
        print(f"\nFirst byte: 0x{unlock_data_hex[:2]} = {num_actions_or_first_byte}")

    # V4 Actions
    ACTIONS = {
        0x00: "INCREASE_LIQUIDITY",
        0x01: "DECREASE_LIQUIDITY",
        0x02: "MINT_POSITION",
        0x03: "BURN_POSITION",
        0x04: "INCREASE_LIQUIDITY_FROM_DELTAS",
        0x05: "MINT_POSITION_FROM_DELTAS",
        0x0b: "SETTLE",
        0x0c: "SETTLE_ALL",
        0x0d: "SETTLE_PAIR",
        0x0e: "TAKE",
        0x0f: "TAKE_ALL",
        0x10: "TAKE_PORTION",
        0x11: "TAKE_PAIR",
        0x12: "CLOSE_CURRENCY",
        0x13: "CLEAR_OR_TAKE",
        0x14: "SWEEP",
    }

    # Try to find action bytes in the data
    print("\n" + "=" * 70)
    print("Looking for action codes...")
    print("=" * 70)

    for action_code, action_name in ACTIONS.items():
        action_hex = f"{action_code:02x}"
        if action_hex in unlock_data_hex.lower():
            positions = []
            idx = 0
            while True:
                pos = unlock_data_hex.lower().find(action_hex, idx)
                if pos == -1:
                    break
                positions.append(pos // 2)  # Convert to byte position
                idx = pos + 2
            if len(positions) <= 10:  # Don't spam if too many matches
                print(f"  0x{action_hex} ({action_name}): found at byte positions {positions}")

# Get transaction receipt for logs
print("\n" + "=" * 70)
print("Transaction Receipt & Logs")
print("=" * 70)

receipt = w3.eth.get_transaction_receipt(TX_HASH)
print(f"Status: {'SUCCESS' if receipt['status'] == 1 else 'FAILED'}")
print(f"Gas used: {receipt['gasUsed']}")
print(f"Logs count: {len(receipt['logs'])}")

# Look for Transfer events (token transfers)
for i, log in enumerate(receipt['logs']):
    if len(log['topics']) > 0:
        topic0 = log['topics'][0].hex()
        # Transfer event signature
        if topic0 == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
            token = log['address']
            if len(log['topics']) >= 3:
                from_addr = "0x" + log['topics'][1].hex()[-40:]
                to_addr = "0x" + log['topics'][2].hex()[-40:]
                amount = int(log['data'].hex(), 16) if log['data'] else 0
                print(f"\nTransfer #{i}:")
                print(f"  Token: {token}")
                print(f"  From: {from_addr}")
                print(f"  To: {to_addr}")
                print(f"  Amount: {amount} ({amount/10**18:.6f} if 18 decimals)")

print("\n" + "=" * 70)
print("RAW INPUT DATA (for manual analysis)")
print("=" * 70)
print(input_data)
