"""
TX receipt parser for PnL calculation.
Parses ERC20 Transfer events from close/swap transaction receipts.
"""

import logging
from web3 import Web3

logger = logging.getLogger(__name__)

# ERC20 Transfer(address indexed from, address indexed to, uint256 value)
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)")


def parse_close_receipt(
    receipt: dict,
    wallet_address: str,
    token0: str,
    token1: str,
) -> dict[str, int]:
    """Parse ERC20 Transfer events TO wallet from close TX receipt.

    Returns dict: {token0_lower: amount_wei, token1_lower: amount_wei}
    Amounts include both liquidity withdrawal and collected fees.
    """
    wallet_lower = wallet_address.lower()
    t0_lower = token0.lower()
    t1_lower = token1.lower()
    received = {t0_lower: 0, t1_lower: 0}

    logs = receipt.get("logs", [])
    for log in logs:
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue

        topic0 = topics[0]
        if isinstance(topic0, str):
            topic0 = bytes.fromhex(topic0.replace("0x", ""))
        if topic0 != TRANSFER_TOPIC:
            continue

        # Extract 'to' address from topics[2]
        raw_to = topics[2]
        if isinstance(raw_to, bytes):
            to_addr = "0x" + raw_to.hex()[-40:]
        else:
            to_addr = "0x" + str(raw_to).replace("0x", "")[-40:]

        if to_addr.lower() != wallet_lower:
            continue

        # Extract token address from log
        token_addr = log.get("address", "").lower()
        if token_addr not in received:
            continue

        # Extract amount from data
        data = log.get("data", "0x")
        if isinstance(data, bytes):
            amount = int.from_bytes(data, "big")
        elif data and data != "0x":
            amount = int(data, 16)
        else:
            amount = 0

        received[token_addr] += amount

    logger.info(
        f"parse_close_receipt: wallet={wallet_lower[:10]}... "
        f"token0={t0_lower[:10]}...={received[t0_lower]} "
        f"token1={t1_lower[:10]}...={received[t1_lower]}"
    )
    return received


def parse_swap_receipt(
    receipt: dict,
    wallet_address: str,
    stablecoin_address: str,
) -> int:
    """Parse swap TX receipt for stablecoin amount received.

    Returns the total stablecoin amount (raw wei) transferred TO wallet.
    """
    wallet_lower = wallet_address.lower()
    stable_lower = stablecoin_address.lower()
    total = 0

    logs = receipt.get("logs", [])
    for log in logs:
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue

        topic0 = topics[0]
        if isinstance(topic0, str):
            topic0 = bytes.fromhex(topic0.replace("0x", ""))
        if topic0 != TRANSFER_TOPIC:
            continue

        token_addr = log.get("address", "").lower()
        if token_addr != stable_lower:
            continue

        raw_to = topics[2]
        if isinstance(raw_to, bytes):
            to_addr = "0x" + raw_to.hex()[-40:]
        else:
            to_addr = "0x" + str(raw_to).replace("0x", "")[-40:]

        if to_addr.lower() != wallet_lower:
            continue

        data = log.get("data", "0x")
        if isinstance(data, bytes):
            amount = int.from_bytes(data, "big")
        elif data and data != "0x":
            amount = int(data, 16)
        else:
            amount = 0

        total += amount

    logger.info(
        f"parse_swap_receipt: wallet={wallet_lower[:10]}... "
        f"stablecoin={stable_lower[:10]}... total={total}"
    )
    return total


def parse_swap_volatile_sent(
    receipt: dict,
    wallet_address: str,
    volatile_token_address: str,
) -> int:
    """Parse swap TX receipt for volatile token amount SENT FROM wallet.

    Returns the total volatile token amount (raw wei) transferred FROM wallet.
    """
    wallet_lower = wallet_address.lower()
    volatile_lower = volatile_token_address.lower()
    total = 0

    logs = receipt.get("logs", [])
    for log in logs:
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue

        topic0 = topics[0]
        if isinstance(topic0, str):
            topic0 = bytes.fromhex(topic0.replace("0x", ""))
        if topic0 != TRANSFER_TOPIC:
            continue

        token_addr = log.get("address", "").lower()
        if token_addr != volatile_lower:
            continue

        # Check 'from' is wallet (topics[1])
        raw_from = topics[1]
        if isinstance(raw_from, bytes):
            from_addr = "0x" + raw_from.hex()[-40:]
        else:
            from_addr = "0x" + str(raw_from).replace("0x", "")[-40:]

        if from_addr.lower() != wallet_lower:
            continue

        data = log.get("data", "0x")
        if isinstance(data, bytes):
            amount = int.from_bytes(data, "big")
        elif data and data != "0x":
            amount = int(data, 16)
        else:
            amount = 0

        total += amount

    logger.info(
        f"parse_swap_volatile_sent: wallet={wallet_lower[:10]}... "
        f"volatile={volatile_lower[:10]}... total_sent={total}"
    )
    return total


def calculate_usd_value(
    received: dict[str, int],
    token0: str,
    token1: str,
    close_price: float,
    token0_decimals: int,
    token1_decimals: int,
    stablecoin_address: str,
) -> float:
    """Convert received token amounts to USD value.

    stablecoin_address: which of token0/token1 is the stablecoin (1:1 with USD).
    close_price: price of volatile token in USD.
    """
    t0_lower = token0.lower()
    t1_lower = token1.lower()
    stable_lower = stablecoin_address.lower()

    t0_amount = received.get(t0_lower, 0) / (10 ** token0_decimals)
    t1_amount = received.get(t1_lower, 0) / (10 ** token1_decimals)

    if stable_lower == t0_lower:
        # token0 is stablecoin, token1 is volatile
        usd = t0_amount + t1_amount * close_price
    else:
        # token1 is stablecoin, token0 is volatile
        usd = t0_amount * close_price + t1_amount

    return round(usd, 2)
