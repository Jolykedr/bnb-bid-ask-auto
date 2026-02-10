"""Compare our encoding with successful Uniswap transaction"""
from web3 import Web3
from eth_abi import decode, encode

# Successful transaction input (after selector)
RAW_TX = """dd46508f00000000000000000000000000000000000000000000000000000000000000400000000000000000000000000000000000000000000000000000000069822d0a0000000000000000000000000000000000000000000000000000000000000300000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000000800000000000000000000000000000000000000000000000000000000000000002020d00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000040000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000001a000000000000000000000000022ca9beffdc68c20ab5989cddaf4a4d9ad37444400000000000000000000000055d398326f99059ff775485246999027b3197955000000000000000000000000000000000000000000000000000000000000a02800000000000000000000000000000000000000000000000000000000000003340000000000000000000000000000000000000000000000000000000000000000fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe6934fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffee620000000000000000000000000000000000000000000000002818ed7fafc3172af00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000de0b6b3a76400000000000000000000000000005b4ed753f09a0fe1c37cf2127a3c42bff23fc15c00000000000000000000000000000000000000000000000000000000000001800000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000004000000000000000000000000022ca9beffdc68c20ab5989cddaf4a4d9ad37444400000000000000000000000055d398326f99059ff775485246999027b3197955"""

# Remove any whitespace
raw = RAW_TX.replace('\n', '').replace(' ', '')

# Skip selector (8 chars)
params_hex = raw[8:]

print("=" * 70)
print("DECODING SUCCESSFUL UNISWAP V4 TRANSACTION")
print("=" * 70)

# Decode top-level: (bytes unlockData, uint256 deadline)
offset_to_bytes = int(params_hex[:64], 16)
deadline = int(params_hex[64:128], 16)

print(f"Offset to unlockData: {offset_to_bytes}")
print(f"Deadline: {deadline}")

# Get unlockData
unlock_data_len_pos = offset_to_bytes * 2  # Convert to hex position
unlock_data_len = int(params_hex[unlock_data_len_pos:unlock_data_len_pos+64], 16)
print(f"UnlockData length: {unlock_data_len} bytes")

unlock_data_hex = params_hex[unlock_data_len_pos+64:unlock_data_len_pos+64+unlock_data_len*2]
print(f"UnlockData hex length: {len(unlock_data_hex)} chars ({len(unlock_data_hex)//2} bytes)")

# Parse unlockData: abi.encode(bytes actions, bytes[] params)
# Offset to actions bytes
offset_actions = int(unlock_data_hex[:64], 16)
# Offset to params[]
offset_params = int(unlock_data_hex[64:128], 16)

print(f"\nOffset to actions: {offset_actions}")
print(f"Offset to params[]: {offset_params}")

# Get actions bytes
actions_len = int(unlock_data_hex[offset_actions*2:offset_actions*2+64], 16)
actions_data = unlock_data_hex[offset_actions*2+64:offset_actions*2+64+64]  # Padded to 32 bytes
print(f"\nActions length: {actions_len}")
print(f"Actions (raw padded): {actions_data}")
print(f"Actions decoded: {[f'0x{actions_data[i*2:i*2+2]}' for i in range(actions_len)]}")

# Decode action names
ACTIONS = {
    0x02: "MINT_POSITION",
    0x0d: "SETTLE_PAIR",
}
for i in range(actions_len):
    action_byte = int(actions_data[i*2:i*2+2], 16)
    action_name = ACTIONS.get(action_byte, "UNKNOWN")
    print(f"  Action {i}: 0x{action_byte:02x} = {action_name}")

# Get params array
params_offset_in_data = offset_params * 2
params_array_len = int(unlock_data_hex[params_offset_in_data:params_offset_in_data+64], 16)
print(f"\nParams array length: {params_array_len}")

# Parse params array offsets
param_offsets = []
for i in range(params_array_len):
    offset_pos = params_offset_in_data + 64 + i * 64
    param_offset = int(unlock_data_hex[offset_pos:offset_pos+64], 16)
    param_offsets.append(param_offset)
    print(f"  Param {i} offset: {param_offset}")

# Decode first param (MINT_POSITION)
print("\n" + "=" * 70)
print("MINT_POSITION PARAMS")
print("=" * 70)

# The param offset is relative to the start of the params array data
# After the offsets array, actual data starts
params_data_start = params_offset_in_data + 64 + params_array_len * 64

# Param 0 (MINT_POSITION)
param0_start = params_data_start + param_offsets[0] * 2
# Get length
param0_len = int(unlock_data_hex[param0_start:param0_start+64], 16)
param0_data = unlock_data_hex[param0_start+64:param0_start+64+param0_len*2]
print(f"MINT_POSITION param length: {param0_len} bytes")
print(f"MINT_POSITION raw hex ({len(param0_data)} chars):")
# Print in 64-char chunks for readability
for i in range(0, len(param0_data), 64):
    chunk = param0_data[i:i+64]
    val = int(chunk, 16) if chunk else 0
    # Try to interpret
    if i == 0:
        # First 32 bytes could be offset to PositionConfig
        print(f"  [{i//2:3d}] {chunk} = offset/data: {val}")
    else:
        print(f"  [{i//2:3d}] {chunk} = {val}")

# Let me manually extract the key values
print("\n" + "=" * 70)
print("EXTRACTED VALUES FROM MINT_POSITION")
print("=" * 70)

# The structure seems to be:
# abi.encode(PositionConfig, uint256 liquidity, uint128 amount0Max, uint128 amount1Max, address owner, bytes hookData)
# Where PositionConfig = (PoolKey poolKey, int24 tickLower, int24 tickUpper)
# And PoolKey = (address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)

# Looking at the data, extract known values:
# currency0 should be 0x22ca9beffdc68c20ab5989cddaf4a4d9ad374444
# currency1 should be 0x55d398326f99059ff775485246999027b3197955
# fee should be 41000 (0xa028)
# tickSpacing should be 820 (0x334)

# Find these in the hex
search_currency0 = "22ca9beffdc68c20ab5989cddaf4a4d9ad374444"
search_currency1 = "55d398326f99059ff775485246999027b3197955"

if search_currency0 in unlock_data_hex.lower():
    pos = unlock_data_hex.lower().find(search_currency0)
    print(f"Found currency0 at position {pos//2} bytes")
if search_currency1 in unlock_data_hex.lower():
    pos = unlock_data_hex.lower().find(search_currency1)
    print(f"Found currency1 at position {pos//2} bytes")

# Find 0xa028 (41000)
if "0000a028" in unlock_data_hex:
    pos = unlock_data_hex.find("0000a028")
    print(f"Found fee (0xa028) at position {pos//2} bytes")

# Find 0x334 (820)
if "00000334" in unlock_data_hex:
    pos = unlock_data_hex.find("00000334")
    print(f"Found tickSpacing (0x334) at position {pos//2} bytes")

# Find tick values (negative, starts with ffff)
print("\nLooking for tick values:")
idx = 0
while True:
    pos = unlock_data_hex.find("fffe", idx)
    if pos == -1:
        break
    val_hex = unlock_data_hex[pos:pos+64]
    val = int(val_hex, 16)
    if val > 2**255:  # Negative
        val = val - 2**256
    print(f"  Found at {pos//2}: {val_hex[:16]}... = {val}")
    idx = pos + 4

# Find amounts
print("\nLooking for amounts:")
# amount0Max = 0
# amount1Max = 1 USDT = 0xde0b6b3a7640000
if "de0b6b3a7640000" in unlock_data_hex:
    pos = unlock_data_hex.find("de0b6b3a7640000")
    print(f"Found 1e18 (1 USDT) at position {pos//2} bytes")

# Liquidity
print("\nLooking for liquidity (should be around 2818ed7fafc3172af):")
if "2818ed7fafc3172af" in unlock_data_hex:
    pos = unlock_data_hex.find("2818ed7fafc3172af")
    print(f"Found liquidity at position {pos//2} bytes")
    liq = int("2818ed7fafc3172af", 16)
    print(f"  Liquidity value: {liq}")

print("\n" + "=" * 70)
print("SETTLE_PAIR PARAMS")
print("=" * 70)

# Param 1 (SETTLE_PAIR) - should just be (address, address)
if params_array_len > 1:
    param1_start = params_data_start + param_offsets[1] * 2
    param1_len = int(unlock_data_hex[param1_start:param1_start+64], 16)
    param1_data = unlock_data_hex[param1_start+64:param1_start+64+param1_len*2]
    print(f"SETTLE_PAIR param length: {param1_len} bytes")
    print(f"SETTLE_PAIR raw hex: {param1_data}")

    # Should be abi.encode(address, address)
    # = 32 bytes for currency0 + 32 bytes for currency1
    if len(param1_data) >= 128:
        settle_currency0 = "0x" + param1_data[24:64]  # Skip padding
        settle_currency1 = "0x" + param1_data[88:128]
        print(f"  currency0: {settle_currency0}")
        print(f"  currency1: {settle_currency1}")

print("\n" + "=" * 70)
print("DONE")
print("=" * 70)
