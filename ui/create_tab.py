"""
Create Tab

Tab for creating real liquidity positions on the blockchain.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QComboBox, QPushButton,
    QTextEdit, QProgressBar, QMessageBox, QDoubleSpinBox,
    QSpinBox, QCheckBox, QSplitter, QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt6.QtGui import QFont

import logging

from .widgets.position_table import PositionTableWidget
from .password_dialog import ask_master_password, create_master_password

logger = logging.getLogger(__name__)

# Note: run_ui.py adds bnb/ to sys.path, so these imports work
from src.liquidity_provider import LiquidityProvider, LiquidityLadderConfig
from src.crypto import (
    encrypt_key, decrypt_key, is_encrypted_format,
    is_crypto_available, DecryptionError, CryptoNotAvailable,
    migrate_from_base64
)
from src.math.distribution import calculate_bid_ask_from_percent
from src.v4_liquidity_provider import V4LiquidityProvider, V4LadderConfig
from src.contracts.v4.constants import V4Protocol
from src.contracts.v4.subgraph import try_all_sources_with_web3 as query_v4_subgraph
from config import BNB_CHAIN, ETHEREUM, BASE, TOKENS_BNB, TOKENS_BASE, STABLECOINS, is_stablecoin


def _format_price(price: float) -> str:
    """Format price without scientific notation."""
    if price == 0:
        return "0"
    abs_price = abs(price)
    if abs_price >= 1:
        return f"{price:.6f}".rstrip('0').rstrip('.')
    elif abs_price >= 0.0001:
        return f"{price:.8f}".rstrip('0').rstrip('.')
    else:
        return f"{price:.12f}".rstrip('0').rstrip('.')


# Subscript digits used by DexScreener/DEXTools: ₀₁₂₃₄₅₆₇₈₉
_SUBSCRIPT_MAP = {chr(0x2080 + i): str(i) for i in range(10)}

def _parse_price_text(text: str) -> float:
    """Parse price text, handling DexScreener subscript notation.

    Examples:
        '0.0₄5962' → 0.00005962  (₄ = 4 zeros after decimal)
        '0.0₅1234' → 0.000001234
        '$0.0₄5962' → 0.00005962
        '0.00125' → 0.00125 (normal)
    """
    text = text.strip().lstrip('$').replace(',', '').replace(' ', '')
    # Check for subscript digits
    for sub_char, digit in _SUBSCRIPT_MAP.items():
        if sub_char in text:
            # Pattern: 0.0₄5962 → zeros_count=4, significant=5962
            idx = text.index(sub_char)
            before = text[:idx]  # e.g. "0.0"
            zeros_count = int(digit)
            after = text[idx + 1:]  # e.g. "5962"
            # Build normal decimal: "0." + "0" * zeros_count + significant
            return float("0." + "0" * zeros_count + after)
    return float(text)


class LoadPoolWorker(QThread):
    """Worker thread for loading pool info without blocking UI."""

    pool_loaded = pyqtSignal(dict)    # result data dict
    progress = pyqtSignal(str)        # log messages
    error = pyqtSignal(str)           # error message

    def __init__(self, rpc_url, pool_input, chain_id, proxy=None,
                 existing_token0=None, existing_token1=None):
        super().__init__()
        self.rpc_url = rpc_url
        self.pool_input = pool_input
        self.chain_id = chain_id
        self.proxy = proxy
        self.existing_token0 = existing_token0  # For V4 fallback when no subgraph
        self.existing_token1 = existing_token1

    def run(self):
        try:
            from web3 import Web3
            import math

            if self.proxy:
                w3 = Web3(Web3.HTTPProvider(endpoint_uri=self.rpc_url, request_kwargs={"proxies": self.proxy}))
            else:
                w3 = Web3(Web3.HTTPProvider(self.rpc_url))

            result = {'success': True}
            is_v4 = len(self.pool_input) == 66
            result['is_v4'] = is_v4

            if is_v4:
                self._load_v4(w3, result)
            else:
                self._load_v3(w3, result)

            self.pool_loaded.emit(result)
        except Exception as e:
            import traceback
            self.progress.emit(f"Failed to load pool: {e}")
            self.error.emit(str(e))

    def _load_v3(self, w3, result):
        """Load V3 pool data (runs in worker thread)."""
        from web3 import Web3

        pool_address = Web3.to_checksum_address(self.pool_input)
        result['pool_address'] = pool_address

        # Detect DEX
        detected_dex = None
        try:
            from config import detect_v3_dex_by_pool
            detected_dex = detect_v3_dex_by_pool(w3, pool_address, self.chain_id)
            result['detected_dex'] = detected_dex
            self.progress.emit(f"Detected V3 DEX: {detected_dex.name}")
        except Exception as e:
            self.progress.emit(f"Could not detect V3 DEX: {e}")
            result['detected_dex'] = None

        # Use BatchRPC for all pool data in one multicall
        from src.utils import BatchRPC

        pool_abi = [
            {"inputs": [], "name": "token0", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
            {"inputs": [], "name": "token1", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
            {"inputs": [], "name": "fee", "outputs": [{"type": "uint24"}], "stateMutability": "view", "type": "function"},
        ]
        pool = w3.eth.contract(address=pool_address, abi=pool_abi)

        # Phase 1: pool token0/1/fee + slot0 in one multicall
        batch = BatchRPC(w3)
        # Encode token0(), token1(), fee() calls manually
        batch.add_call(pool_address, bytes.fromhex('0dfe1681'),  # token0()
                       lambda d: Web3.to_checksum_address('0x' + d[12:32].hex()) if len(d) >= 32 else None)
        batch.add_call(pool_address, bytes.fromhex('d21220a7'),  # token1()
                       lambda d: Web3.to_checksum_address('0x' + d[12:32].hex()) if len(d) >= 32 else None)
        batch.add_call(pool_address, bytes.fromhex('ddca3f43'),  # fee()
                       lambda d: int.from_bytes(d[:32], 'big') if len(d) >= 32 else None)
        batch.add_pool_slot0(pool_address)
        results1 = batch.execute()

        token0_addr = results1[0]
        token1_addr = results1[1]
        fee = results1[2]
        slot0 = results1[3]

        if not token0_addr or not token1_addr:
            raise Exception("Failed to read pool token addresses")

        # Phase 2: token info in one multicall
        batch2 = BatchRPC(w3)
        batch2.add_erc20_symbol(token0_addr)
        batch2.add_erc20_symbol(token1_addr)
        batch2.add_decimals(token0_addr)
        batch2.add_decimals(token1_addr)
        results2 = batch2.execute()

        token0_symbol = results2[0] or '???'
        token1_symbol = results2[1] or '???'
        decimals0 = results2[2] if results2[2] is not None else 18
        decimals1 = results2[3] if results2[3] is not None else 18

        # Calculate pool price
        pool_price = None
        if slot0 and slot0['sqrtPriceX96'] > 0:
            sqrt_price_x96 = slot0['sqrtPriceX96']
            pool_price = (sqrt_price_x96 / (2 ** 96)) ** 2
            pool_price = pool_price * (10 ** (decimals0 - decimals1))

        # Determine stablecoin ordering
        token0_is_stable = is_stablecoin(token0_addr)
        token1_is_stable = is_stablecoin(token1_addr)

        if token0_is_stable and not token1_is_stable:
            # Swap: volatile → token0 field, stablecoin → token1 field
            result['ui_token0'] = token1_addr
            result['ui_token1'] = token0_addr
            result['ui_dec0'] = decimals1
            result['ui_dec1'] = decimals0
            display_price = (1 / pool_price) if pool_price and pool_price > 0 else None
            result['display_pair'] = f"{token1_symbol}/{token0_symbol}"
            self.progress.emit(f"Swapped order: {token0_symbol} is stablecoin")
        else:
            result['ui_token0'] = token0_addr
            result['ui_token1'] = token1_addr
            result['ui_dec0'] = decimals0
            result['ui_dec1'] = decimals1
            display_price = pool_price
            result['display_pair'] = f"{token0_symbol}/{token1_symbol}"

        result['display_price'] = display_price
        result['fee'] = fee
        result['token0_addr'] = token0_addr
        result['token1_addr'] = token1_addr
        result['token0_symbol'] = token0_symbol
        result['token1_symbol'] = token1_symbol
        result['decimals0'] = decimals0
        result['decimals1'] = decimals1

        self.progress.emit(f"Loaded pool: {result['display_pair']}, fee={fee/10000}%")

    def _load_v4(self, w3, result):
        """Load V4 pool data (runs in worker thread)."""
        from src.contracts.v4.pool_manager import V4PoolManager
        from src.contracts.v4.constants import V4Protocol

        pool_id_bytes = bytes.fromhex(self.pool_input[2:])
        result['pool_id_bytes'] = pool_id_bytes
        self.progress.emit(f"Looking for V4 pool ID: {self.pool_input}")

        pool_found = False
        errors = []
        for protocol, protocol_name in [(V4Protocol.UNISWAP, "Uniswap V4"), (V4Protocol.PANCAKESWAP, "PancakeSwap V4")]:
            try:
                pool_manager = V4PoolManager(w3, protocol=protocol, chain_id=self.chain_id)
                self.progress.emit(f"Checking {protocol_name}...")
                state = pool_manager.get_pool_state_by_id(pool_id_bytes)

                if state.initialized:
                    pool_found = True
                    result['v4_protocol'] = protocol
                    result['v4_protocol_name'] = protocol_name
                    result['v4_fee'] = state.lp_fee
                    result['v4_tick'] = state.tick
                    result['v4_sqrt_price_x96'] = state.sqrt_price_x96

                    # Try subgraph
                    subgraph_info = None
                    try:
                        self.progress.emit("Querying subgraph for token addresses...")
                        subgraph_info = query_v4_subgraph(self.pool_input, w3=w3, chain_id=self.chain_id, proxy=self.proxy)
                    except Exception as e:
                        self.progress.emit(f"Subgraph error: {e}")

                    if subgraph_info:
                        result['subgraph'] = subgraph_info
                        result['ui_token0'] = subgraph_info.token0_address
                        result['ui_token1'] = subgraph_info.token1_address
                        result['ui_dec0'] = subgraph_info.token0_decimals
                        result['ui_dec1'] = subgraph_info.token1_decimals
                        result['tick_spacing'] = subgraph_info.tick_spacing

                        # Calculate price from tick
                        import math
                        price_from_tick = 1.0001 ** state.tick
                        price_adjusted = price_from_tick * (10 ** (subgraph_info.token0_decimals - subgraph_info.token1_decimals))

                        t0_is_stable = is_stablecoin(subgraph_info.token0_address)
                        t1_is_stable = is_stablecoin(subgraph_info.token1_address)

                        if t0_is_stable and not t1_is_stable:
                            display_price = (1 / price_adjusted) if price_adjusted > 0 else price_adjusted
                        else:
                            display_price = price_adjusted

                        result['display_price'] = display_price
                    else:
                        # No subgraph — try user-entered token addresses
                        result['subgraph'] = None
                        self._load_v4_token_info_fallback(w3, result, state)

                    self.progress.emit(f"V4 pool found on {protocol_name}")
                    break
            except Exception as e:
                errors.append(f"{protocol_name}: {str(e)[:100]}")
                self.progress.emit(f"Error checking {protocol_name}: {e}")
                continue

        if not pool_found:
            # Fallback: try subgraph only
            self.progress.emit("On-chain query failed, trying subgraph...")
            try:
                subgraph_info = query_v4_subgraph(self.pool_input, w3=w3, chain_id=self.chain_id, proxy=self.proxy)
                if subgraph_info:
                    result['v4_protocol'] = V4Protocol.UNISWAP
                    result['v4_protocol_name'] = 'Subgraph'
                    result['subgraph'] = subgraph_info
                    result['ui_token0'] = subgraph_info.token0_address
                    result['ui_token1'] = subgraph_info.token1_address
                    result['ui_dec0'] = subgraph_info.token0_decimals
                    result['ui_dec1'] = subgraph_info.token1_decimals
                    result['tick_spacing'] = subgraph_info.tick_spacing
                    result['v4_fee'] = subgraph_info.fee_tier
                    result['pool_id_bytes'] = pool_id_bytes
                    pool_found = True
                    self.progress.emit(f"Found via subgraph: {subgraph_info.token0_symbol}/{subgraph_info.token1_symbol}")
            except Exception as e:
                self.progress.emit(f"Subgraph fallback error: {e}")

        result['pool_found'] = pool_found
        if not pool_found:
            result['errors'] = errors

    def _load_v4_token_info_fallback(self, w3, result, state):
        """V4 fallback: get token info from user-entered addresses."""
        from src.utils import BatchRPC

        token0_addr = self.existing_token0
        token1_addr = self.existing_token1

        if token0_addr and token0_addr.startswith("0x") and len(token0_addr) == 42 and \
           token1_addr and token1_addr.startswith("0x") and len(token1_addr) == 42:
            batch = BatchRPC(w3)
            batch.add_erc20_symbol(token0_addr)
            batch.add_erc20_symbol(token1_addr)
            batch.add_decimals(token0_addr)
            batch.add_decimals(token1_addr)
            results = batch.execute()

            sym0 = results[0] or '???'
            sym1 = results[1] or '???'
            dec0 = results[2] if results[2] is not None else 18
            dec1 = results[3] if results[3] is not None else 18

            result['ui_dec0'] = dec0
            result['ui_dec1'] = dec1
            result['token0_symbol'] = sym0
            result['token1_symbol'] = sym1

            # Calculate price
            sqrt_price = state.sqrt_price_x96 / (2 ** 96)
            raw_price = sqrt_price ** 2
            price_adjusted = raw_price * (10 ** (dec0 - dec1))

            t0_is_stable = is_stablecoin(token0_addr)
            if t0_is_stable:
                result['display_price'] = (1 / price_adjusted) if price_adjusted > 0 else price_adjusted
            else:
                result['display_price'] = price_adjusted

            self.progress.emit(f"Token info from addresses: {sym0}/{sym1}")


class BalanceWorker(QThread):
    """Worker thread for fetching token balances without blocking UI."""

    result = pyqtSignal(str)   # formatted balance string
    error = pyqtSignal(str)

    def __init__(self, rpc_url, wallet_address, tokens_dict, proxy=None):
        super().__init__()
        self.rpc_url = rpc_url
        self.wallet_address = wallet_address
        self.tokens_dict = tokens_dict  # {symbol: TokenConfig}
        self.proxy = proxy

    def run(self):
        try:
            from web3 import Web3
            from src.contracts.abis import ERC20_ABI

            if self.proxy:
                w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"proxies": self.proxy}))
            else:
                w3 = Web3(Web3.HTTPProvider(self.rpc_url))

            address = Web3.to_checksum_address(self.wallet_address)
            balances = []
            for symbol, token in self.tokens_dict.items():
                try:
                    contract = w3.eth.contract(
                        address=Web3.to_checksum_address(token.address),
                        abi=ERC20_ABI
                    )
                    balance = contract.functions.balanceOf(address).call()
                    formatted = f"{balance / (10 ** token.decimals):.4f}"
                    balances.append(f"{symbol}: {formatted}")
                except Exception:
                    balances.append(f"{symbol}: ?")

            self.result.emit(" | ".join(balances))
        except Exception as e:
            self.error.emit(str(e))


class CreatePoolOnlyWorker(QThread):
    """Worker thread for V4 pool creation without blocking UI."""

    progress = pyqtSignal(str)
    result = pyqtSignal(object, object, bool)  # tx_hash, pool_id, success
    error = pyqtSignal(str)

    def __init__(self, rpc_url, private_key, protocol, chain_id,
                 token0, token1, fee_percent, initial_price,
                 tick_spacing, token0_decimals, token1_decimals,
                 invert_price, proxy=None):
        super().__init__()
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.protocol = protocol
        self.chain_id = chain_id
        self.token0 = token0
        self.token1 = token1
        self.fee_percent = fee_percent
        self.initial_price = initial_price
        self.tick_spacing = tick_spacing
        self.token0_decimals = token0_decimals
        self.token1_decimals = token1_decimals
        self.invert_price = invert_price
        self.proxy = proxy

    def run(self):
        try:
            self.progress.emit("Creating V4 provider...")
            provider = V4LiquidityProvider(
                rpc_url=self.rpc_url,
                private_key=self.private_key,
                protocol=self.protocol,
                chain_id=self.chain_id
            )

            self.progress.emit("Sending pool creation TX...")
            tx_hash, pool_id, success = provider.create_pool_only(
                token0=self.token0,
                token1=self.token1,
                fee_percent=self.fee_percent,
                initial_price=self.initial_price,
                tick_spacing=self.tick_spacing,
                token0_decimals=self.token0_decimals,
                token1_decimals=self.token1_decimals,
                invert_price=self.invert_price
            )

            self.result.emit(tx_hash, pool_id, success)
        except BaseException as e:
            self.error.emit(str(e))
        finally:
            self.private_key = None


class CreateLadderWorkerV4(QThread):
    """Worker thread for V4 blockchain operations."""

    progress = pyqtSignal(str)
    create_result = pyqtSignal(bool, str, dict)

    def __init__(self, rpc_url: str, private_key: str, config: V4LadderConfig, chain_id: int = 56, auto_create_pool: bool = False, proxy: dict = None, gas_limit: int = 0):
        super().__init__()
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.config = config
        self.chain_id = chain_id
        self.auto_create_pool = auto_create_pool
        self.proxy = proxy
        self.gas_limit = gas_limit  # 0 = auto

    def run(self):
        provider = None
        try:
            self.progress.emit(f"Connecting to {self.config.protocol.value}...")

            # Create V4 provider with proxy support
            provider = V4LiquidityProvider(
                rpc_url=self.rpc_url,
                private_key=self.private_key,
                protocol=self.config.protocol,
                chain_id=self.chain_id,
                proxy=self.proxy
            )

            # Auto-detect decimals if still default (18) - moved from UI thread
            if self.config.token0_decimals == 18 or self.config.token1_decimals == 18:
                self.progress.emit("Auto-detecting token decimals...")
                erc20_abi = [{"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"}]
                w3 = provider.w3
                if self.config.token0_decimals == 18:
                    try:
                        t0c = w3.eth.contract(address=w3.to_checksum_address(self.config.token0), abi=erc20_abi)
                        self.config.token0_decimals = t0c.functions.decimals().call()
                        self.progress.emit(f"Token0 decimals: {self.config.token0_decimals}")
                    except Exception as e:
                        self.progress.emit(f"Could not detect token0 decimals: {e}")
                if self.config.token1_decimals == 18:
                    try:
                        t1c = w3.eth.contract(address=w3.to_checksum_address(self.config.token1), abi=erc20_abi)
                        self.config.token1_decimals = t1c.functions.decimals().call()
                        self.progress.emit(f"Token1 decimals: {self.config.token1_decimals}")
                    except Exception as e:
                        self.progress.emit(f"Could not detect token1 decimals: {e}")

            self.progress.emit(f"Using custom fee: {self.config.fee_percent}%")

            # Validate balances
            self.progress.emit("Validating balances...")
            is_valid, error = provider.validate_balances(self.config)

            if not is_valid:
                self.create_result.emit(False, error, {})
                return

            # Create ladder (auto-creates pool only if checkbox is checked)
            self.progress.emit("Creating V4 ladder positions...")
            result = provider.create_ladder(
                self.config,
                auto_create_pool=self.auto_create_pool,
                simulate_first=True,
                timeout=300,
                gas_limit=self.gas_limit if self.gas_limit > 0 else None
            )

            if result.success:
                self.create_result.emit(True, "Success!", {
                    'tx_hash': result.tx_hash,
                    'gas_used': result.gas_used,
                    'token_ids': result.token_ids,
                    'pool_created': result.pool_created
                })
            else:
                self.create_result.emit(False, result.error or "Unknown error", {})

        except Exception as e:
            self.create_result.emit(False, str(e), {})
        finally:
            # Zero private key from memory
            self.private_key = None
            # Cleanup provider resources (Web3 connections)
            if provider is not None:
                try:
                    if hasattr(provider, 'w3') and hasattr(provider.w3, 'provider'):
                        if hasattr(provider.w3.provider, 'disconnect'):
                            provider.w3.provider.disconnect()
                except Exception:
                    pass


class CreateLadderWorker(QThread):
    """Worker thread for blockchain operations."""

    progress = pyqtSignal(str)
    create_result = pyqtSignal(bool, str, dict)

    def __init__(self, provider, config, auto_create_pool=False, factory_address=None, loaded_pool_address=None):
        super().__init__()
        self.provider = provider
        self.config = config
        self.auto_create_pool = auto_create_pool
        self.factory_address = factory_address  # V3 DEX factory address
        self.loaded_pool_address = loaded_pool_address  # Pool address user loaded directly

    def run(self):
        try:
            # Auto-detect decimals if still default (18) - moved from UI thread
            if self.config.token0_decimals == 18 or self.config.token1_decimals == 18:
                self.progress.emit("Auto-detecting token decimals...")
                erc20_abi = [{"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"}]
                w3 = self.provider.w3
                if self.config.token0_decimals == 18:
                    try:
                        t0c = w3.eth.contract(address=w3.to_checksum_address(self.config.token0), abi=erc20_abi)
                        self.config.token0_decimals = t0c.functions.decimals().call()
                        self.progress.emit(f"Token0 decimals: {self.config.token0_decimals}")
                    except Exception as e:
                        self.progress.emit(f"Could not detect token0 decimals: {e}")
                if self.config.token1_decimals == 18:
                    try:
                        t1c = w3.eth.contract(address=w3.to_checksum_address(self.config.token1), abi=erc20_abi)
                        self.config.token1_decimals = t1c.functions.decimals().call()
                        self.progress.emit(f"Token1 decimals: {self.config.token1_decimals}")
                    except Exception as e:
                        self.progress.emit(f"Could not detect token1 decimals: {e}")

            # Check if pool exists
            self.progress.emit("Checking if pool exists...")

            from src.contracts.pool_factory import PoolFactory
            from src.liquidity_provider import POSITION_MANAGER_TO_FACTORY

            # Determine factory address:
            # 1. Use provided factory_address if available (from detected DEX)
            # 2. Otherwise, look up factory by Position Manager
            # 3. If still None, PoolFactory will use its default
            effective_factory_address = self.factory_address
            if not effective_factory_address:
                pm_lower = self.provider.position_manager_address.lower()
                effective_factory_address = POSITION_MANAGER_TO_FACTORY.get(pm_lower)
                if effective_factory_address:
                    self.progress.emit(f"Factory derived from Position Manager: {effective_factory_address[:20]}...")
                else:
                    self.progress.emit("Warning: Could not determine factory from Position Manager")

            pool_factory = PoolFactory(
                self.provider.w3,
                self.provider.account,
                factory_address=effective_factory_address,
                chain_id=self.provider.chain_id
            )

            self.progress.emit(f"Using factory: {pool_factory.factory_address[:20]}...")
            self.progress.emit(f"Position manager: {self.provider.position_manager_address[:20]}...")
            self.progress.emit(f"Fee tier from config: {self.config.fee_tier} ({self.config.fee_tier/10000}%)")

            # If user loaded a pool directly by address, use that pool
            pool_address = None
            if self.loaded_pool_address:
                self.progress.emit(f"Using loaded pool address: {self.loaded_pool_address[:20]}...")
                pool_address = self.loaded_pool_address

                # Read pool's actual parameters and update config
                try:
                    pool_abi = [
                        {"inputs": [], "name": "fee", "outputs": [{"type": "uint24"}], "stateMutability": "view", "type": "function"},
                        {"inputs": [], "name": "tickSpacing", "outputs": [{"type": "int24"}], "stateMutability": "view", "type": "function"},
                        {"inputs": [], "name": "token0", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
                        {"inputs": [], "name": "token1", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
                    ]
                    pool_contract = self.provider.w3.eth.contract(
                        address=self.provider.w3.to_checksum_address(pool_address),
                        abi=pool_abi
                    )

                    pool_fee = pool_contract.functions.fee().call()
                    pool_tick_spacing = pool_contract.functions.tickSpacing().call()
                    pool_token0 = pool_contract.functions.token0().call()
                    pool_token1 = pool_contract.functions.token1().call()

                    self.progress.emit(f"Pool fee: {pool_fee} ({pool_fee/10000}%)")
                    self.progress.emit(f"Pool tick_spacing: {pool_tick_spacing}")
                    self.progress.emit(f"Pool tokens: {pool_token0[:15]}... / {pool_token1[:15]}...")

                    # Update config with pool's actual fee if different
                    if pool_fee != self.config.fee_tier:
                        self.progress.emit(f"⚠️ Updating config fee from {self.config.fee_tier} to {pool_fee}")
                        self.config.fee_tier = pool_fee

                except Exception as e:
                    self.progress.emit(f"Warning: Could not read pool params: {e}")
            else:
                # Query factory for pool
                pool_address = pool_factory.get_pool_address(
                    self.config.token0,
                    self.config.token1,
                    self.config.fee_tier
                )

                # If not found in primary factory, search in other known factories
                if not pool_address:
                    from config import V3_DEXES
                    self.progress.emit(f"Pool not found in {pool_factory.factory_address[:15]}..., searching other DEXes...")

                    chain_dexes = V3_DEXES.get(self.provider.chain_id, {})
                    for dex_name, dex_config in chain_dexes.items():
                        # Skip if same factory
                        if dex_config.pool_factory.lower() == pool_factory.factory_address.lower():
                            continue

                        self.progress.emit(f"Searching in {dex_config.name}...")
                        try:
                            other_factory = PoolFactory(
                                self.provider.w3,
                                self.provider.account,
                                factory_address=dex_config.pool_factory,
                                chain_id=self.provider.chain_id
                            )
                            pool_address = other_factory.get_pool_address(
                                self.config.token0,
                                self.config.token1,
                                self.config.fee_tier
                            )
                            if pool_address:
                                self.progress.emit(f"✓ Pool found in {dex_config.name}!")
                                # Update Position Manager to match the found pool's DEX
                                self.provider.position_manager_address = dex_config.position_manager
                                from src.contracts.position_manager import UniswapV3PositionManager
                                self.provider.position_manager = UniswapV3PositionManager(
                                    self.provider.w3,
                                    dex_config.position_manager,
                                    self.provider.account
                                )
                                self.progress.emit(f"✓ Position Manager updated to {dex_config.name}")
                                break
                        except Exception as e:
                            self.progress.emit(f"Warning: Failed to search in {dex_config.name}: {e}")

            if not pool_address:
                if not self.auto_create_pool:
                    self.create_result.emit(
                        False,
                        f"Pool does not exist for this token pair and fee tier ({self.config.fee_tier/10000}%). "
                        "Enable 'Auto-create pool' option to create it automatically.",
                        {}
                    )
                    return

                # Create the pool
                self.progress.emit("Pool not found. Creating new pool...")

                try:
                    create_tx, pool_address = pool_factory.create_pool(
                        self.config.token0,
                        self.config.token1,
                        self.config.fee_tier,
                        timeout=300
                    )
                    self.progress.emit(f"Pool created: {pool_address[:20]}...")

                    # Initialize pool with current price
                    self.progress.emit("Initializing pool with price...")
                    init_tx = pool_factory.initialize_pool(
                        pool_address,
                        self.config.current_price,
                        self.config.token0_decimals,
                        self.config.token1_decimals,
                        timeout=300
                    )
                    self.progress.emit("Pool initialized successfully!")

                except Exception as e:
                    self.create_result.emit(False, f"Failed to create pool: {e}", {})
                    return
            else:
                self.progress.emit(f"Pool found: {pool_address[:20]}...")

                # Detect which V3 DEX this pool belongs to and update Position Manager
                try:
                    from config import detect_v3_dex_by_pool
                    detected_dex = detect_v3_dex_by_pool(self.provider.w3, pool_address, self.provider.chain_id)
                    if detected_dex:
                        self.progress.emit(f"Pool belongs to: {detected_dex.name}")
                        if self.provider.position_manager_address.lower() != detected_dex.position_manager.lower():
                            self.progress.emit(f"⚠️ Updating Position Manager from {self.provider.position_manager_address[:15]}...")
                            self.progress.emit(f"   to {detected_dex.position_manager[:15]}... ({detected_dex.name})")
                            self.provider.position_manager_address = detected_dex.position_manager
                            # Also update the position_manager object
                            from src.contracts.position_manager import UniswapV3PositionManager
                            self.provider.position_manager = UniswapV3PositionManager(
                                self.provider.w3,
                                detected_dex.position_manager,
                                self.provider.account
                            )
                except Exception as dex_err:
                    self.progress.emit(f"Warning: Could not detect pool DEX: {dex_err}")

                # Verify pool's fee and tick spacing match our config
                try:
                    pool_abi = [
                        {"inputs": [], "name": "fee", "outputs": [{"type": "uint24"}], "stateMutability": "view", "type": "function"},
                        {"inputs": [], "name": "tickSpacing", "outputs": [{"type": "int24"}], "stateMutability": "view", "type": "function"},
                    ]
                    pool_contract = self.provider.w3.eth.contract(
                        address=self.provider.w3.to_checksum_address(pool_address),
                        abi=pool_abi
                    )

                    # Get pool fee
                    actual_fee = pool_contract.functions.fee().call()
                    self.progress.emit(f"Pool actual fee: {actual_fee} ({actual_fee/10000}%)")
                    if actual_fee != self.config.fee_tier:
                        self.progress.emit(f"⚠️ FEE MISMATCH! Config fee={self.config.fee_tier}, pool fee={actual_fee}")
                        # Use pool's actual fee
                        self.progress.emit(f"Updating config to use pool's fee: {actual_fee}")
                        self.config.fee_tier = actual_fee

                    # Get pool tick spacing
                    actual_tick_spacing = pool_contract.functions.tickSpacing().call()
                    self.progress.emit(f"Pool tick_spacing: {actual_tick_spacing}")

                    # Calculate expected tick spacing from fee
                    from src.math.ticks import get_tick_spacing
                    expected_tick_spacing = get_tick_spacing(actual_fee, allow_custom=False)
                    self.progress.emit(f"Expected tick_spacing for fee {actual_fee}: {expected_tick_spacing}")

                    if actual_tick_spacing != expected_tick_spacing:
                        self.progress.emit(f"⚠️ TICK SPACING MISMATCH! Pool has {actual_tick_spacing}, expected {expected_tick_spacing}")

                    # Get current pool tick via raw eth_call
                    # (works for both Uniswap V3 and PancakeSwap V3 slot0 formats)
                    pool_addr_cs = self.provider.w3.to_checksum_address(pool_address)
                    raw_slot0 = self.provider.w3.eth.call({
                        'to': pool_addr_cs,
                        'data': bytes.fromhex('3850c7bd')
                    })
                    if len(raw_slot0) >= 64:
                        tick_raw = int.from_bytes(raw_slot0[32:64], 'big')
                        current_tick = tick_raw - 2**256 if tick_raw >= 2**255 else tick_raw
                        self.progress.emit(f"Pool current tick: {current_tick}")
                    else:
                        self.progress.emit(f"⚠️ slot0 returned {len(raw_slot0)} bytes, cannot read tick")

                except Exception as fee_err:
                    self.progress.emit(f"Could not verify pool info: {fee_err}")

            # Validate balances
            self.progress.emit("Validating balances...")
            is_valid, error = self.provider.validate_balances_for_ladder(self.config)

            if not is_valid:
                self.create_result.emit(False, error, {})
                return

            self.progress.emit("Creating ladder positions...")
            self.progress.emit(f"Config: token0={self.config.token0[:15]}... token1={self.config.token1[:15]}...")
            self.progress.emit(f"Config: fee={self.config.fee_tier}, price={_format_price(self.config.current_price)}")
            self.progress.emit(f"Config: lower_price={_format_price(self.config.lower_price)}, n_pos={self.config.n_positions}")

            # Pre-validate: calculate positions and check tick alignment
            from src.math.ticks import get_tick_spacing, compute_decimal_tick_offset
            from src.math.distribution import calculate_bid_ask_distribution
            from web3 import Web3

            tick_spacing = get_tick_spacing(self.config.fee_tier, allow_custom=False)
            self.progress.emit(f"Tick spacing for fee {self.config.fee_tier}: {tick_spacing}")

            # Determine invert_price based on token order (same logic as in LiquidityProvider)
            t0 = Web3.to_checksum_address(self.config.token0).lower()
            t1 = Web3.to_checksum_address(self.config.token1).lower()
            stablecoin_is_token1_in_pool = t1 > t0
            invert_price = not stablecoin_is_token1_in_pool
            self.progress.emit(f"Stablecoin is pool's token1: {stablecoin_is_token1_in_pool}, invert_price: {invert_price}")

            # Compute decimal tick offset for mixed-decimal pairs
            dec_offset = compute_decimal_tick_offset(
                token0_address=self.config.token0,
                token0_decimals=self.config.token0_decimals,
                token1_address=self.config.token1,
                token1_decimals=self.config.token1_decimals,
            )
            self.progress.emit(f"Decimal tick offset: {dec_offset}")

            # Calculate positions to check ticks
            test_positions = calculate_bid_ask_distribution(
                current_price=self.config.current_price,
                lower_price=self.config.lower_price,
                total_usd=self.config.total_usd,
                n_positions=self.config.n_positions,
                fee_tier=self.config.fee_tier,
                distribution_type=self.config.distribution_type,
                token0_decimals=self.config.token0_decimals,
                token1_decimals=self.config.token1_decimals,
                token1_is_stable=is_stablecoin(self.config.token1),
                invert_price=invert_price,
                decimal_tick_offset=dec_offset
            )

            if test_positions:
                pos = test_positions[0]
                self.progress.emit(f"First position: tick_lower={pos.tick_lower}, tick_upper={pos.tick_upper}")
                self.progress.emit(f"Price range: ${_format_price(pos.price_lower)} - ${_format_price(pos.price_upper)}")

                # Check token order
                # NOTE: ticks from preview_ladder are already calculated with correct invert_price
                # so we do NOT need to negate them here
                swapped = t0 > t1
                self.progress.emit(f"Token order swapped: {swapped}")

                # Ticks are already correct from preview_ladder (accounts for invert_price)
                actual_tick_lower = pos.tick_lower
                actual_tick_upper = pos.tick_upper

                self.progress.emit(f"Ticks for mint: lower={actual_tick_lower}, upper={actual_tick_upper}")

                # Validate alignment
                lower_rem = actual_tick_lower % tick_spacing
                upper_rem = actual_tick_upper % tick_spacing
                self.progress.emit(f"Tick alignment: lower%{tick_spacing}={lower_rem}, upper%{tick_spacing}={upper_rem}")

                if lower_rem != 0 or upper_rem != 0:
                    self.progress.emit(f"⚠️ TICKS NOT ALIGNED! This will cause revert!")
                    self.create_result.emit(False, f"Ticks not aligned to {tick_spacing}: lower%{tick_spacing}={lower_rem}, upper%{tick_spacing}={upper_rem}", {})
                    return
                else:
                    self.progress.emit(f"✓ Ticks properly aligned to {tick_spacing}")

                # Verify Position Manager is valid contract
                pm_addr = self.provider.position_manager_address
                self.progress.emit(f"Position Manager: {pm_addr}")
                pm_code = self.provider.w3.eth.get_code(pm_addr)
                if len(pm_code) <= 2:
                    self.progress.emit(f"⚠️ Position Manager is NOT a contract!")
                    self.create_result.emit(False, f"Position Manager {pm_addr} is not deployed", {})
                    return
                self.progress.emit(f"✓ Position Manager verified ({len(pm_code)} bytes)")

                # Calculate stablecoin amount for first position
                # Detect stablecoin decimals (using centralized registry from config)
                from config import STABLECOINS as _STABLES
                _t0l = self.config.token0.lower()
                _t1l = self.config.token1.lower()
                if _t0l in _STABLES:
                    _stable_dec = _STABLES[_t0l]
                elif _t1l in _STABLES:
                    _stable_dec = _STABLES[_t1l]
                else:
                    _stable_dec = self.config.token1_decimals

                first_usd = pos.usd_amount
                stablecoin_amount = int(first_usd * (10 ** _stable_dec))
                self.progress.emit(f"First pos USD: ${first_usd:.4f}, stablecoin wei: {stablecoin_amount}")

                if stablecoin_amount == 0:
                    self.create_result.emit(False, "Stablecoin amount is 0 - position too small!", {})
                    return

            result = self.provider.create_ladder(
                self.config,
                simulate_first=True,
                timeout=300,
                validated_pool_address=pool_address  # Skip validation if already found
            )

            if result.success:
                self.create_result.emit(True, "Success!", {
                    'tx_hash': result.tx_hash,
                    'gas_used': result.gas_used,
                    'token_ids': result.token_ids
                })
            else:
                self.create_result.emit(False, result.error or "Unknown error", {})

        except Exception as e:
            self.create_result.emit(False, str(e), {})
        finally:
            # Cleanup temporary resources (PoolFactory etc.)
            pool_factory = None


class CreateTab(QWidget):
    """
    Tab for creating real liquidity positions.

    Features:
    - Wallet connection
    - Token selection
    - Position preview
    - Transaction execution
    """

    # Signal emitted when new positions are created
    positions_created = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.provider = None
        self.positions = []
        self.worker = None
        self._balance_worker = None
        self._pool_create_worker = None
        self.custom_tokens = {}  # symbol -> address mapping
        self.loaded_v4_pool_id = None  # Store loaded V4 pool ID for verification
        self._detected_v3_dex = None  # Store detected V3 DEX config (Uniswap/PancakeSwap)
        self._loaded_v3_pool_address = None  # Store loaded V3 pool address
        self.settings = QSettings("BNBLiquidityLadder", "Wallet")
        # Store token decimals (default 18, updated when pool is loaded)
        self._token0_decimals = 18
        self._token1_decimals = 18
        self._loaded_pool_fee = None
        self._loaded_pool_id_bytes = None
        self.setup_ui()
        self._load_saved_wallet()

    def _reset_pool_state(self):
        """Reset all pool-related state variables. Call when switching tokens/protocols."""
        self.loaded_v4_pool_id = None
        self._detected_v3_dex = None
        self._loaded_v3_pool_address = None
        self._loaded_pool_fee = None
        self._loaded_pool_id_bytes = None
        self._token0_decimals = 18
        self._token1_decimals = 18
        self.positions = []
        # Clear pool info display
        if hasattr(self, 'pool_info_label'):
            self.pool_info_label.setText("")
            self.pool_info_label.setStyleSheet("")
        self._log("Pool state reset")

    def _on_reset_pool(self):
        """Reset button handler — clear loaded pool, token inputs, price, and position table."""
        self._reset_pool_state()
        # Clear pool address input
        if hasattr(self, 'pool_input'):
            self.pool_input.clear()
        # Clear token address inputs
        if hasattr(self, 'token0_input'):
            self.token0_input.clear()
        if hasattr(self, 'token1_input'):
            self.token1_input.clear()
        # Reset price
        if hasattr(self, 'price_input'):
            self.price_input.setText("1.0")
        # Clear position table
        if hasattr(self, 'position_table'):
            self.position_table.set_positions([], 0)
        self._log("Pool reset — все поля очищены")

    def setup_ui(self):
        main_layout = QHBoxLayout(self)

        # Left side - Settings with scroll
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setMinimumWidth(480)
        scroll_area.setMaximumWidth(600)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(15)
        left_layout.setContentsMargins(10, 10, 10, 10)

        # Wallet Group
        wallet_group = QGroupBox("Wallet Connection")
        wallet_layout = QVBoxLayout(wallet_group)

        # Network selection
        network_row = QHBoxLayout()
        network_row.addWidget(QLabel("Network:"))
        self.network_combo = QComboBox()
        self.network_combo.addItems(["BNB Mainnet", "Ethereum Mainnet", "Base Mainnet"])
        network_row.addWidget(self.network_combo)
        wallet_layout.addLayout(network_row)

        # RPC URL
        rpc_row = QHBoxLayout()
        rpc_row.addWidget(QLabel("RPC URL:"))
        self.rpc_input = QLineEdit()
        self.rpc_input.setText(BNB_CHAIN.rpc_url)
        self.rpc_input.setPlaceholderText("https://...")
        rpc_row.addWidget(self.rpc_input)
        wallet_layout.addLayout(rpc_row)

        # Private key
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Private Key:"))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("0x...")
        key_row.addWidget(self.key_input)

        self.show_key_btn = QPushButton("Show")
        self.show_key_btn.setMaximumWidth(60)
        self.show_key_btn.clicked.connect(self._toggle_key_visibility)
        key_row.addWidget(self.show_key_btn)
        wallet_layout.addLayout(key_row)

        # Save wallet checkbox
        save_row = QHBoxLayout()
        self.save_wallet_cb = QCheckBox("Encrypt and save wallet")
        self.save_wallet_cb.setToolTip(
            "Encrypt private key with AES-256 and save locally.\n"
            "You will need to enter master password on each launch."
        )
        save_row.addWidget(self.save_wallet_cb)
        save_row.addStretch()
        wallet_layout.addLayout(save_row)

        # Connect button
        self.connect_btn = QPushButton("Connect Wallet")
        self.connect_btn.clicked.connect(self._connect_wallet)
        wallet_layout.addWidget(self.connect_btn)

        # Wallet status
        self.wallet_status = QLabel("Not connected")
        self.wallet_status.setObjectName("subtitleLabel")
        wallet_layout.addWidget(self.wallet_status)

        # Balance display
        self.balance_label = QLabel("")
        self.balance_label.setWordWrap(True)
        wallet_layout.addWidget(self.balance_label)

        # --- Proxy Settings ---
        proxy_label = QLabel("Proxy (optional):")
        wallet_layout.addWidget(proxy_label)

        proxy_row = QHBoxLayout()
        self.proxy_type_combo = QComboBox()
        self.proxy_type_combo.addItems(["None", "SOCKS5", "HTTP"])
        self.proxy_type_combo.setMaximumWidth(80)
        self.proxy_type_combo.currentIndexChanged.connect(self._on_proxy_type_changed)
        proxy_row.addWidget(self.proxy_type_combo)

        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("host:port (e.g. 127.0.0.1:1080)")
        self.proxy_input.setEnabled(False)
        proxy_row.addWidget(self.proxy_input)
        wallet_layout.addLayout(proxy_row)

        # Proxy auth (optional)
        proxy_auth_row = QHBoxLayout()
        proxy_auth_row.addWidget(QLabel("Auth:"))
        self.proxy_user_input = QLineEdit()
        self.proxy_user_input.setPlaceholderText("username")
        self.proxy_user_input.setEnabled(False)
        self.proxy_user_input.setMaximumWidth(100)
        proxy_auth_row.addWidget(self.proxy_user_input)

        self.proxy_pass_input = QLineEdit()
        self.proxy_pass_input.setPlaceholderText("password")
        self.proxy_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_pass_input.setEnabled(False)
        self.proxy_pass_input.setMaximumWidth(100)
        proxy_auth_row.addWidget(self.proxy_pass_input)
        proxy_auth_row.addStretch()
        wallet_layout.addLayout(proxy_auth_row)

        left_layout.addWidget(wallet_group)

        # Pool / Token Selection Group
        token_group = QGroupBox("Pool / Token Pair")
        token_layout = QVBoxLayout(token_group)

        # Pool address (optional - auto-detects tokens)
        pool_label = QLabel("Pool Address (optional):")
        token_layout.addWidget(pool_label)

        pool_row = QHBoxLayout()
        self.pool_input = QLineEdit()
        self.pool_input.setPlaceholderText("Paste pool contract (0x...) - auto-detects tokens")
        pool_row.addWidget(self.pool_input, 1)

        self.load_pool_btn = QPushButton("Load")
        self.load_pool_btn.setMaximumWidth(60)
        self.load_pool_btn.clicked.connect(self._load_pool_info)
        pool_row.addWidget(self.load_pool_btn)

        self.reset_pool_btn = QPushButton("Reset")
        self.reset_pool_btn.setMaximumWidth(60)
        self.reset_pool_btn.setToolTip("Сбросить загруженный пул и очистить все поля")
        self.reset_pool_btn.clicked.connect(self._on_reset_pool)
        pool_row.addWidget(self.reset_pool_btn)
        token_layout.addLayout(pool_row)

        # Pool info label
        self.pool_info_label = QLabel("")
        self.pool_info_label.setObjectName("subtitleLabel")
        self.pool_info_label.setWordWrap(True)
        token_layout.addWidget(self.pool_info_label)

        # Separator
        separator = QLabel("— OR enter tokens manually —")
        separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        separator.setObjectName("subtitleLabel")
        token_layout.addWidget(separator)

        # Token0
        token0_row = QHBoxLayout()
        token0_row.addWidget(QLabel("Token0:"))
        self.token0_combo = QComboBox()
        self.token0_combo.addItems(["WBNB", "USDT", "USDC", "CAKE", "ETH", "BTCB"])
        self.token0_combo.setMinimumWidth(80)
        self.token0_combo.currentIndexChanged.connect(self._on_token0_combo_changed)
        token0_row.addWidget(self.token0_combo)

        self.token0_input = QLineEdit()
        self.token0_input.setPlaceholderText("Contract (0x...)")
        token0_row.addWidget(self.token0_input, 1)
        token_layout.addLayout(token0_row)

        # Swap button between tokens
        swap_row = QHBoxLayout()
        swap_row.addStretch()
        self.swap_tokens_btn = QPushButton("⇅ Swap")
        self.swap_tokens_btn.setToolTip("Swap Token0 and Token1")
        self.swap_tokens_btn.setFixedWidth(80)
        self.swap_tokens_btn.clicked.connect(self._swap_tokens)
        swap_row.addWidget(self.swap_tokens_btn)
        swap_row.addStretch()
        token_layout.addLayout(swap_row)

        # Token1
        token1_row = QHBoxLayout()
        token1_row.addWidget(QLabel("Token1:"))
        self.token1_combo = QComboBox()
        self.token1_combo.addItems(["USDT", "USDC", "BUSD", "WBNB"])
        self.token1_combo.setMinimumWidth(80)
        self.token1_combo.currentIndexChanged.connect(self._on_token1_combo_changed)
        token1_row.addWidget(self.token1_combo)

        self.token1_input = QLineEdit()
        self.token1_input.setPlaceholderText("Contract (0x...)")
        token1_row.addWidget(self.token1_input, 1)
        token_layout.addLayout(token1_row)

        left_layout.addWidget(token_group)

        # Position Settings Group
        settings_group = QGroupBox("Position Settings")
        settings_layout = QVBoxLayout(settings_group)

        # Protocol Selection (V3/V4)
        protocol_row = QHBoxLayout()
        protocol_row.addWidget(QLabel("Protocol:"))
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems([
            "PancakeSwap V3",
            "PancakeSwap V4",
            "Uniswap V3",
            "Uniswap V4"
        ])
        self.protocol_combo.currentIndexChanged.connect(self._on_protocol_changed)
        protocol_row.addWidget(self.protocol_combo)
        protocol_row.addStretch()
        settings_layout.addLayout(protocol_row)

        # Current price - use QLineEdit for easy paste and many decimals
        price_row = QHBoxLayout()
        price_row.addWidget(QLabel("Current Price:"))
        self.price_input = QLineEdit()
        self.price_input.setPlaceholderText("e.g. 0.00125 or 600.50")
        self.price_input.setText("1.0")
        self.price_input.textChanged.connect(self._normalize_price_input)
        price_row.addWidget(self.price_input)
        settings_layout.addLayout(price_row)

        # Range - supports both positive and negative percentages
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Range From:"))
        self.range_from_spin = QDoubleSpinBox()
        self.range_from_spin.setRange(-99, 500)  # Support +500% to -99%
        self.range_from_spin.setValue(-5)
        self.range_from_spin.setSuffix(" %")
        self.range_from_spin.setToolTip("Positive = above current price, Negative = below")
        range_row.addWidget(self.range_from_spin)

        range_row.addWidget(QLabel("To:"))
        self.range_to_spin = QDoubleSpinBox()
        self.range_to_spin.setRange(-99, 500)  # Support +500% to -99%
        self.range_to_spin.setValue(-50)
        self.range_to_spin.setSuffix(" %")
        self.range_to_spin.setToolTip("Positive = above current price, Negative = below")
        range_row.addWidget(self.range_to_spin)
        settings_layout.addLayout(range_row)

        # Positions and USD
        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("Positions:"))
        self.positions_spin = QSpinBox()
        self.positions_spin.setRange(1, 20)
        self.positions_spin.setValue(7)
        params_row.addWidget(self.positions_spin)

        params_row.addWidget(QLabel("Total USD:"))
        self.total_usd_spin = QDoubleSpinBox()
        self.total_usd_spin.setRange(0.01, 10000000)
        self.total_usd_spin.setValue(1000)
        self.total_usd_spin.setDecimals(2)
        self.total_usd_spin.setPrefix("$ ")
        params_row.addWidget(self.total_usd_spin)
        settings_layout.addLayout(params_row)

        # Distribution and Fee
        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel("Distribution:"))
        self.dist_combo = QComboBox()
        self.dist_combo.addItems(["linear", "quadratic", "exponential", "fibonacci"])
        dist_row.addWidget(self.dist_combo)

        # Fee label
        self.fee_label = QLabel("Fee:")
        dist_row.addWidget(self.fee_label)

        # V3 Fee combo (preset fees)
        self.fee_combo = QComboBox()
        self.fee_combo.addItems(["0.05%", "0.25%", "0.30%", "1.00%"])
        self.fee_combo.setCurrentIndex(1)
        dist_row.addWidget(self.fee_combo)

        # V4 Custom Fee input (hidden by default)
        self.custom_fee_spin = QDoubleSpinBox()
        self.custom_fee_spin.setRange(0.001, 100.0)
        self.custom_fee_spin.setValue(0.30)
        self.custom_fee_spin.setDecimals(4)  # 4 decimals to support fees like 3.8998%
        self.custom_fee_spin.setSuffix(" %")
        self.custom_fee_spin.setToolTip("V4 allows any fee from 0.001% to 100%")
        self.custom_fee_spin.hide()  # Hidden by default (V3 mode)
        self.custom_fee_spin.valueChanged.connect(self._on_fee_changed)  # Auto-update tick spacing
        dist_row.addWidget(self.custom_fee_spin)

        settings_layout.addLayout(dist_row)

        # Tick Spacing (V4 only, hidden by default)
        self.tick_spacing_row = QHBoxLayout()
        self.tick_spacing_label = QLabel("Tick Spacing:")
        self.tick_spacing_row.addWidget(self.tick_spacing_label)
        self.tick_spacing_spin = QSpinBox()
        self.tick_spacing_spin.setRange(1, 8388607)  # int24 max value
        self.tick_spacing_spin.setValue(60)
        self.tick_spacing_spin.setToolTip("V4 tick spacing (int24). Lower = more precision, higher gas.")
        self.tick_spacing_row.addWidget(self.tick_spacing_spin)
        self.tick_spacing_auto_cb = QCheckBox("Auto")
        self.tick_spacing_auto_cb.setChecked(True)
        self.tick_spacing_auto_cb.toggled.connect(self._on_tick_spacing_auto_changed)
        self.tick_spacing_row.addWidget(self.tick_spacing_auto_cb)

        self.tick_spacing_row.addStretch()
        settings_layout.addLayout(self.tick_spacing_row)

        # Hide tick spacing row by default (V3)
        self.tick_spacing_label.hide()
        self.tick_spacing_spin.hide()
        self.tick_spacing_auto_cb.hide()

        # Slippage and Gas Limit
        slip_row = QHBoxLayout()
        slip_row.addWidget(QLabel("Slippage:"))
        self.slippage_spin = QDoubleSpinBox()
        self.slippage_spin.setRange(0.1, 100.0)  # Allow up to 100% slippage
        self.slippage_spin.setValue(0.5)
        self.slippage_spin.setSuffix(" %")
        self.slippage_spin.setToolTip("Max slippage tolerance (0.1% - 100%)")
        slip_row.addWidget(self.slippage_spin)

        slip_row.addWidget(QLabel("Gas Limit:"))
        self.gas_limit_spin = QSpinBox()
        self.gas_limit_spin.setRange(0, 50000000)  # 0 = auto
        self.gas_limit_spin.setValue(0)
        self.gas_limit_spin.setSpecialValueText("Auto")
        self.gas_limit_spin.setSingleStep(100000)
        self.gas_limit_spin.setToolTip("0 = Auto estimate, or set manual gas limit")
        slip_row.addWidget(self.gas_limit_spin)

        slip_row.addStretch()
        settings_layout.addLayout(slip_row)

        # Auto-create pool option
        pool_row = QHBoxLayout()
        self.auto_create_pool_cb = QCheckBox("Auto-create pool if not exists")
        self.auto_create_pool_cb.setToolTip(
            "If the pool doesn't exist for the selected token pair and fee tier,\n"
            "it will be created and initialized automatically.\n"
            "V3: Only standard fee tiers (0.05%, 0.25%, 0.30%, 1.00%)\n"
            "V4: Any custom fee from 0.001% to 100%"
        )
        pool_row.addWidget(self.auto_create_pool_cb)
        pool_row.addStretch()
        settings_layout.addLayout(pool_row)

        left_layout.addWidget(settings_group)

        # Action buttons
        action_layout = QHBoxLayout()

        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self._preview_positions)
        action_layout.addWidget(self.preview_btn)

        self.create_pool_only_btn = QPushButton("Create Pool Only")
        self.create_pool_only_btn.setToolTip(
            "Create/initialize pool without adding liquidity (V4 only)"
        )
        self.create_pool_only_btn.clicked.connect(self._create_pool_only)
        self.create_pool_only_btn.hide()  # Hidden by default (V3)
        action_layout.addWidget(self.create_pool_only_btn)

        self.create_btn = QPushButton("Create Position")
        self.create_btn.setObjectName("primaryButton")
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._create_ladder)
        action_layout.addWidget(self.create_btn)

        left_layout.addLayout(action_layout)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.hide()
        left_layout.addWidget(self.progress_bar)

        # Set scroll area widget
        scroll_area.setWidget(left_widget)

        # Right side - Results
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Position table
        self.position_table = PositionTableWidget()
        right_layout.addWidget(self.position_table)

        # Transaction log
        log_group = QGroupBox("Transaction Log")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self.log_text)

        right_layout.addWidget(log_group)

        # Add to main layout
        main_layout.addWidget(scroll_area)
        main_layout.addWidget(right_widget, 1)

        # Connect network change
        self.network_combo.currentIndexChanged.connect(self._on_network_changed)

        # Initialize token inputs with default addresses
        self._on_token0_combo_changed(0)
        self._on_token1_combo_changed(0)

    def _toggle_key_visibility(self):
        """Toggle private key visibility."""
        if self.key_input.echoMode() == QLineEdit.EchoMode.Password:
            self.key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_key_btn.setText("Hide")
        else:
            self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_key_btn.setText("Show")

    def _get_current_tokens(self) -> dict:
        """Get token dict for the currently selected network."""
        index = self.network_combo.currentIndex()
        if index == 2:  # Base Mainnet
            return TOKENS_BASE
        # BNB Mainnet, Ethereum — all use BNB tokens
        return TOKENS_BNB

    def _rebuild_token_combos(self):
        """Rebuild token combo boxes for the current network."""
        tokens = self._get_current_tokens()
        symbols = list(tokens.keys())

        # Determine default selections and token1 list per network
        index = self.network_combo.currentIndex()
        if index == 2:  # Base
            default_token0 = "WETH"
            default_token1 = "USDC"
            token1_symbols = [s for s in symbols if s in ("USDC", "USDbC", "DAI", "WETH")]
        else:  # BNB / ETH
            default_token0 = "WBNB"
            default_token1 = "USDT"
            token1_symbols = [s for s in symbols if s in ("USDT", "USDC", "BUSD", "WBNB")]

        self.token0_combo.blockSignals(True)
        self.token1_combo.blockSignals(True)

        self.token0_combo.clear()
        self.token0_combo.addItems(symbols)

        self.token1_combo.clear()
        self.token1_combo.addItems(token1_symbols)

        # Add custom tokens
        for symbol in self.custom_tokens:
            custom_label = f"[Custom] {symbol}"
            self.token0_combo.addItem(custom_label)
            self.token1_combo.addItem(custom_label)

        # Set defaults
        idx0 = self.token0_combo.findText(default_token0)
        if idx0 >= 0:
            self.token0_combo.setCurrentIndex(idx0)
        idx1 = self.token1_combo.findText(default_token1)
        if idx1 >= 0:
            self.token1_combo.setCurrentIndex(idx1)

        self.token0_combo.blockSignals(False)
        self.token1_combo.blockSignals(False)

        # Update address inputs
        self._on_token0_combo_changed(self.token0_combo.currentIndex())
        self._on_token1_combo_changed(self.token1_combo.currentIndex())

    def _normalize_price_input(self, text: str):
        """Auto-convert DexScreener subscript notation on paste."""
        for sub_char in _SUBSCRIPT_MAP:
            if sub_char in text:
                try:
                    normalized = _format_price(_parse_price_text(text))
                    self.price_input.blockSignals(True)
                    self.price_input.setText(normalized)
                    self.price_input.blockSignals(False)
                except (ValueError, OverflowError):
                    pass
                return

    def _on_network_changed(self, index):
        """Update RPC URL and token combos when network changes."""
        if index == 0:
            self.rpc_input.setText(BNB_CHAIN.rpc_url)
        elif index == 1:
            self.rpc_input.setText(ETHEREUM.rpc_url)
        elif index == 2:
            self.rpc_input.setText(BASE.rpc_url)

        # Rebuild token combos for the new network
        self._rebuild_token_combos()

    def _get_current_network(self):
        """Get current network config based on dropdown selection."""
        index = self.network_combo.currentIndex()
        if index == 0:
            return BNB_CHAIN
        elif index == 1:
            return ETHEREUM
        elif index == 2:
            return BASE
        return BNB_CHAIN  # Default

    def _on_proxy_type_changed(self, index):
        """Enable/disable proxy inputs based on type."""
        is_proxy_enabled = index > 0  # Not "None"
        self.proxy_input.setEnabled(is_proxy_enabled)
        self.proxy_user_input.setEnabled(is_proxy_enabled)
        self.proxy_pass_input.setEnabled(is_proxy_enabled)

    def _get_proxy_config(self) -> dict:
        """Get proxy configuration dict for requests/web3."""
        proxy_type = self.proxy_type_combo.currentText()
        if proxy_type == "None":
            return {}

        proxy_addr = self.proxy_input.text().strip()
        if not proxy_addr:
            return {}

        proxy_user = self.proxy_user_input.text().strip()
        proxy_pass = self.proxy_pass_input.text().strip()

        # Build proxy URL (URL-encode credentials — спецсимволы @:/ ломают urlparse)
        from urllib.parse import quote
        if proxy_user and proxy_pass:
            auth = f"{quote(proxy_user, safe='')}:{quote(proxy_pass, safe='')}@"
        else:
            auth = ""

        if proxy_type == "SOCKS5":
            proxy_url = f"socks5://{auth}{proxy_addr}"
        else:  # HTTP
            proxy_url = f"http://{auth}{proxy_addr}"

        return {
            "http": proxy_url,
            "https": proxy_url,
        }

    def _on_token0_combo_changed(self, index):
        """Update token0 input when combo changes."""
        symbol = self.token0_combo.currentText()
        tokens = self._get_current_tokens()
        if symbol.startswith("[Custom] "):
            actual_symbol = symbol.replace("[Custom] ", "")
            if actual_symbol in self.custom_tokens:
                self.token0_input.setText(self.custom_tokens[actual_symbol])
                if hasattr(self, 'custom_token_decimals') and actual_symbol in self.custom_token_decimals:
                    self._token0_decimals = self.custom_token_decimals[actual_symbol]
        elif symbol in tokens:
            self.token0_input.setText(tokens[symbol].address)
            self._token0_decimals = tokens[symbol].decimals
        else:
            self.token0_input.clear()

    def _on_token1_combo_changed(self, index):
        """Update token1 input when combo changes."""
        symbol = self.token1_combo.currentText()
        tokens = self._get_current_tokens()
        if symbol.startswith("[Custom] "):
            actual_symbol = symbol.replace("[Custom] ", "")
            if actual_symbol in self.custom_tokens:
                self.token1_input.setText(self.custom_tokens[actual_symbol])
                if hasattr(self, 'custom_token_decimals') and actual_symbol in self.custom_token_decimals:
                    self._token1_decimals = self.custom_token_decimals[actual_symbol]
        elif symbol in tokens:
            self.token1_input.setText(tokens[symbol].address)
            self._token1_decimals = tokens[symbol].decimals
        else:
            self.token1_input.clear()

    def _swap_tokens(self):
        """Swap Token0 and Token1 addresses and combo selections."""
        # Swap input text
        token0_addr = self.token0_input.text()
        token1_addr = self.token1_input.text()
        self.token0_input.setText(token1_addr)
        self.token1_input.setText(token0_addr)

        # Swap combo selections
        token0_idx = self.token0_combo.currentIndex()
        token0_text = self.token0_combo.currentText()
        token1_idx = self.token1_combo.currentIndex()
        token1_text = self.token1_combo.currentText()

        # Block signals to avoid triggering combo change handlers
        self.token0_combo.blockSignals(True)
        self.token1_combo.blockSignals(True)

        # Try to find the text in the other combo, if not found keep as-is
        idx0_in_combo1 = self.token1_combo.findText(token0_text)
        idx1_in_combo0 = self.token0_combo.findText(token1_text)

        if idx1_in_combo0 >= 0:
            self.token0_combo.setCurrentIndex(idx1_in_combo0)
        if idx0_in_combo1 >= 0:
            self.token1_combo.setCurrentIndex(idx0_in_combo1)

        self.token0_combo.blockSignals(False)
        self.token1_combo.blockSignals(False)

        # Swap decimals
        old_dec0 = self._token0_decimals
        old_dec1 = self._token1_decimals
        self._token0_decimals = old_dec1
        self._token1_decimals = old_dec0

        # NOTE: Price is NOT inverted on swap!
        # The price field shows "USD price of volatile token" which doesn't change
        # when we swap the order of tokens in the UI.
        # If price was wrong initially, user should reload the pool or enter manually.

        self._log(f"Swapped tokens: Token0={token1_addr[:15] if token1_addr else 'empty'}..., Token1={token0_addr[:15] if token0_addr else 'empty'}...")
        self._log(f"Price unchanged - enter correct price manually if needed")

    def _on_protocol_changed(self, index):
        """Handle protocol selection change."""
        is_v4 = self._is_v4_mode()

        if is_v4:
            # Show custom fee input, hide combo
            self.fee_combo.hide()
            self.custom_fee_spin.show()
            # Show tick spacing controls
            self.tick_spacing_label.show()
            self.tick_spacing_spin.show()
            self.tick_spacing_auto_cb.show()
            # Show "Create Pool Only" button for V4
            self.create_pool_only_btn.show()
            protocol_name = self.protocol_combo.currentText()
            self._log(f"Switched to V4 mode ({protocol_name}) - custom fees enabled")
        else:
            # Show fee combo, hide custom input
            self.fee_combo.show()
            self.custom_fee_spin.hide()
            # Hide tick spacing controls
            self.tick_spacing_label.hide()
            self.tick_spacing_spin.hide()
            self.tick_spacing_auto_cb.hide()
            # Hide "Create Pool Only" button for V3
            self.create_pool_only_btn.hide()
            # Note which V3 will be used based on network
            network = self._get_current_network()
            v3_name = "PancakeSwap V3" if network.chain_id in [56, 97] else "Uniswap V3"
            self._log(f"Switched to V3 mode ({v3_name} on chain {network.chain_id})")

    def _on_fee_changed(self, fee_value: float):
        """Handle fee value change - auto-update tick spacing if auto is checked."""
        if self.tick_spacing_auto_cb.isChecked():
            suggested = self._suggest_tick_spacing(fee_value)
            self.tick_spacing_spin.setValue(suggested)

    def _on_tick_spacing_auto_changed(self, checked):
        """Handle tick spacing auto checkbox change."""
        self.tick_spacing_spin.setEnabled(not checked)
        if checked:
            # Auto-suggest tick spacing based on fee
            fee = self.custom_fee_spin.value()
            suggested = self._suggest_tick_spacing(fee)
            self.tick_spacing_spin.setValue(suggested)

    def _suggest_tick_spacing(self, fee_percent: float) -> int:
        """
        Calculate tick spacing based on fee using Uniswap V4 formula.

        Formula: tick_spacing = fee_percent × 200
        """
        tick_spacing = round(fee_percent * 200)
        return max(1, tick_spacing)

    @staticmethod
    def _format_price(price: float) -> str:
        """Format price without scientific notation."""
        return _format_price(price)

    def _should_invert_price(self, token0: str, token1: str) -> bool:
        """
        Determine if price inversion is needed based on stablecoin position IN THE POOL.

        IMPORTANT: In Uniswap V3/V4, pool tokens are sorted by address (lower = currency0).
        The config's token0/token1 order may NOT match the pool's currency0/currency1 order.
        We must check the stablecoin's position AFTER address sorting.

        Pool price = currency1/currency0 (sorted by address, NOT by config order).

        If stablecoin is pool's currency1 (higher address):
            - Pool price = stablecoin/token = "price of token in USD"
            - User enters price in USD → matches pool format
            - invert_price = False

        If stablecoin is pool's currency0 (lower address):
            - Pool price = token/stablecoin = "how many tokens per USD"
            - User enters price in USD → inverse of pool format
            - invert_price = True

        Returns:
            True if inversion is needed, False otherwise
        """
        token0_lower = token0.lower()
        token1_lower = token1.lower()

        # Find which token is the stablecoin (using centralized registry from config)
        token0_is_stable = is_stablecoin(token0)
        token1_is_stable = is_stablecoin(token1)

        if not token0_is_stable and not token1_is_stable:
            # Neither is a known stablecoin - default to True (old behavior)
            return True

        # Determine which token is the stablecoin
        if token0_is_stable and not token1_is_stable:
            stablecoin_addr = token0_lower
        elif token1_is_stable and not token0_is_stable:
            stablecoin_addr = token1_lower
        else:
            # Both are stablecoins - default to True
            return True

        # Sort by address to find pool ordering (like PoolKey.from_tokens does)
        # Lower address = pool's currency0, higher address = pool's currency1
        addr0_int = int(token0_lower, 16)
        addr1_int = int(token1_lower, 16)

        if addr0_int < addr1_int:
            pool_currency0 = token0_lower
        else:
            pool_currency0 = token1_lower

        # If stablecoin is pool's currency0 (lower address) → need inversion
        # If stablecoin is pool's currency1 (higher address) → no inversion
        stablecoin_is_pool_currency0 = (stablecoin_addr == pool_currency0)
        return stablecoin_is_pool_currency0

    def _is_v4_mode(self) -> bool:
        """Check if V4 protocol is selected."""
        current_text = self.protocol_combo.currentText().lower()
        return "v4" in current_text

    def _get_v4_protocol(self):
        """Get V4 protocol enum based on selected protocol text."""
        from src.contracts.v4.constants import V4Protocol
        current_text = self.protocol_combo.currentText().lower()
        if "pancakeswap" in current_text and "v4" in current_text:
            return V4Protocol.PANCAKESWAP
        elif "uniswap" in current_text and "v4" in current_text:
            return V4Protocol.UNISWAP
        return None

    def _log(self, message: str):
        """Add message to log."""
        self.log_text.append(message)

    def _connect_wallet(self):
        """Connect to wallet."""
        private_key = self.key_input.text().strip()
        rpc_url = self.rpc_input.text().strip()

        if not private_key:
            QMessageBox.warning(self, "Error", "Please enter your private key.")
            return

        if not rpc_url:
            QMessageBox.warning(self, "Error", "Please enter RPC URL.")
            return

        try:
            network = self._get_current_network()

            # Get proxy configuration
            proxy = self._get_proxy_config()

            self.provider = LiquidityProvider(
                rpc_url=rpc_url,
                private_key=private_key,
                position_manager_address=network.position_manager,
                chain_id=network.chain_id,
                proxy=proxy if proxy else None
            )

            address = self.provider.account.address
            self.wallet_status.setText(f"Connected: {address[:8]}...{address[-6:]}")
            self.wallet_status.setStyleSheet("color: #00b894;")

            # Save wallet if checkbox is checked
            self._save_wallet()

            # Get balances
            self._update_balances()

            self._log(f"Connected to wallet: {address}")
            self.create_btn.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Connection Error", str(e))
            self._log(f"Connection failed: {e}")

    def _update_balances(self):
        """Update token balances display (async — runs in worker thread)."""
        if not self.provider:
            return

        rpc_url = self.rpc_input.text().strip()
        wallet_address = self.provider.account.address
        tokens = self._get_current_tokens()
        proxy = self._get_proxy_config()

        self.balance_label.setText("Loading balances...")

        # Clean up previous balance worker
        if hasattr(self, '_balance_worker') and self._balance_worker is not None:
            if self._balance_worker.isRunning():
                self._balance_worker.quit()
                self._balance_worker.wait(2000)
            self._balance_worker.deleteLater()
            self._balance_worker = None

        self._balance_worker = BalanceWorker(rpc_url, wallet_address, tokens, proxy)
        self._balance_worker.result.connect(self._on_balances_loaded)
        self._balance_worker.error.connect(lambda e: self.balance_label.setText(f"Error: {e}"))
        self._balance_worker.finished.connect(lambda: self._cleanup_balance_worker())
        self._balance_worker.start()

    def _on_balances_loaded(self, text: str):
        """Handle balance worker result."""
        self.balance_label.setText(text)

    def _cleanup_balance_worker(self):
        """Clean up balance worker after completion."""
        if hasattr(self, '_balance_worker') and self._balance_worker is not None:
            self._balance_worker.deleteLater()
            self._balance_worker = None

    def _get_fee_tier(self) -> int:
        """Get fee tier from combo."""
        tiers = [500, 2500, 3000, 10000]
        return tiers[self.fee_combo.currentIndex()]

    def _preview_positions(self):
        """Preview positions without creating."""
        try:
            current_price = _parse_price_text(self.price_input.text())
            percent_from = self.range_from_spin.value()
            percent_to = self.range_to_spin.value()

            if percent_from >= percent_to:
                percent_from, percent_to = percent_to, percent_from

            # Get fee tier - V4 uses custom fee percent converted to V4 format
            if self._is_v4_mode():
                # V4: convert percent to hundredths of bip (0.3% -> 3000)
                # Use round() to avoid float precision issues (3.8998 * 10000 = 38997.999...)
                fee_tier = round(self.custom_fee_spin.value() * 10000)
                # Get tick spacing for V4 - use custom value if not auto
                if self.tick_spacing_auto_cb.isChecked():
                    tick_spacing = None  # Auto-calculate from fee
                else:
                    tick_spacing = self.tick_spacing_spin.value()
            else:
                # V3: use preset fee tiers
                fee_tier = self._get_fee_tier()
                tick_spacing = None  # V3 uses standard spacing

            # Compute additional parameters for accurate preview
            extra_kwargs = {}
            token0 = self.token0_input.text().strip()
            token1 = self.token1_input.text().strip()
            if token0 and token1:
                invert = self._should_invert_price(token0, token1)
                extra_kwargs['invert_price'] = invert
                extra_kwargs['token0_decimals'] = self._token0_decimals
                extra_kwargs['token1_decimals'] = self._token1_decimals
                # Compute decimal tick offset for mixed-decimal pairs (e.g. USDC 6 / token 18)
                from src.math.ticks import compute_decimal_tick_offset
                dec_offset = compute_decimal_tick_offset(
                    token0_address=token0,
                    token0_decimals=self._token0_decimals,
                    token1_address=token1,
                    token1_decimals=self._token1_decimals,
                )
                if dec_offset != 0:
                    extra_kwargs['decimal_tick_offset'] = dec_offset
                    self._log(f"Decimal tick offset: {dec_offset}")

            self.positions = calculate_bid_ask_from_percent(
                current_price=current_price,
                percent_from=percent_from,
                percent_to=percent_to,
                total_usd=self.total_usd_spin.value(),
                n_positions=self.positions_spin.value(),
                fee_tier=fee_tier,
                distribution_type=self.dist_combo.currentText(),
                allow_custom_fee=self._is_v4_mode(),  # V4 supports custom fees
                tick_spacing=tick_spacing,  # Use custom tick spacing for V4
                **extra_kwargs
            )

            self.position_table.set_positions(self.positions, current_price)
            self._log(f"Preview: {len(self.positions)} positions calculated")

            if self.provider or self._is_v4_mode():
                self.create_btn.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self._log(f"Preview failed: {e}")

    def _create_ladder(self):
        """Create liquidity ladder."""
        # V4 mode doesn't require V3 provider - it creates its own
        if self._is_v4_mode():
            # Check that we have RPC and private key for V4
            if not self.rpc_input.text().strip():
                QMessageBox.warning(self, "Error", "Please enter RPC URL.")
                return
            if not self.key_input.text().strip():
                QMessageBox.warning(self, "Error", "Please enter private key.")
                return
        else:
            # V3 mode requires provider
            if not self.provider:
                QMessageBox.warning(self, "Error", "Please connect wallet first.")
                return

        if not self.positions:
            QMessageBox.warning(self, "Error", "Please preview positions first.")
            return

        # Confirm
        reply = QMessageBox.question(
            self, "Confirm",
            f"Create {len(self.positions)} positions with ${self.total_usd_spin.value():,.2f} total?\n\n"
            "This will submit a blockchain transaction.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Get token addresses - prefer direct input, fallback to combo
        token0 = self.token0_input.text().strip()
        token1 = self.token1_input.text().strip()

        if not token0:
            try:
                token0 = self._get_token_address(self.token0_combo.currentText())
            except ValueError as e:
                QMessageBox.critical(self, "Error", f"Token0: {e}")
                return

        if not token1:
            try:
                token1 = self._get_token_address(self.token1_combo.currentText())
            except ValueError as e:
                QMessageBox.critical(self, "Error", f"Token1: {e}")
                return

        # Validate addresses
        if not token0.startswith("0x") or len(token0) != 42:
            QMessageBox.critical(self, "Error", "Invalid Token0 address")
            return
        if not token1.startswith("0x") or len(token1) != 42:
            QMessageBox.critical(self, "Error", "Invalid Token1 address")
            return

        # Calculate price range
        try:
            current_price = _parse_price_text(self.price_input.text())
        except (ValueError, AttributeError):
            QMessageBox.critical(self, "Error", "Invalid price value")
            return
        percent_from = self.range_from_spin.value()
        percent_to = self.range_to_spin.value()

        if percent_from >= percent_to:
            percent_from, percent_to = percent_to, percent_from

        upper_price = current_price * (1 + max(percent_from, percent_to) / 100)
        lower_price = current_price * (1 + min(percent_from, percent_to) / 100)

        # Start worker
        self.progress_bar.show()
        self.create_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)

        # Check if V4 mode
        if self._is_v4_mode():
            # V4 mode - use V4LiquidityProvider
            protocol = self._get_v4_protocol()
            fee_percent = self.custom_fee_spin.value()

            # Get tick spacing
            if self.tick_spacing_auto_cb.isChecked():
                tick_spacing = None  # Auto
            else:
                tick_spacing = self.tick_spacing_spin.value()

            # Auto-detect invert_price based on stablecoin position
            # Pool price in Uniswap = token1/token0
            # If stablecoin is token1: pool price = stablecoin/token = price in USD → NO inversion
            # If stablecoin is token0: pool price = token/stablecoin = inverse → NEED inversion
            invert_price = self._should_invert_price(token0, token1)
            self._log(f"Auto-detected invert_price: {invert_price}")

            v4_config = V4LadderConfig(
                current_price=upper_price,
                lower_price=lower_price,
                total_usd=self.total_usd_spin.value(),
                n_positions=self.positions_spin.value(),
                token0=token0,
                token1=token1,
                token0_decimals=self._token0_decimals,  # Use loaded/detected decimals
                token1_decimals=self._token1_decimals,  # Use loaded/detected decimals
                fee_percent=fee_percent,
                tick_spacing=tick_spacing,
                distribution_type=self.dist_combo.currentText(),
                slippage_percent=self.slippage_spin.value(),
                protocol=protocol,
                pool_id=self.loaded_v4_pool_id,  # Use pre-loaded pool ID if available
                invert_price=invert_price,
                actual_current_price=current_price  # The REAL current price user entered
            )

            network = self._get_current_network()

            self._log(f"Using V4 protocol: {protocol.value}")
            self._log(f"Custom fee: {fee_percent}%")

            # Get proxy and gas limit settings
            proxy = self._get_proxy_config()
            gas_limit = self.gas_limit_spin.value()

            # Create V4 provider and store it so manage_tab can use it later
            try:
                v4_provider = V4LiquidityProvider(
                    rpc_url=self.rpc_input.text().strip(),
                    private_key=self.key_input.text().strip(),
                    protocol=protocol,
                    chain_id=network.chain_id,
                    proxy=proxy if proxy else None
                )
                self.provider = v4_provider  # Store for manage_tab
                # Update wallet status for V4
                address = v4_provider.account.address
                self.wallet_status.setText(f"V4 {protocol.value}: {address[:8]}...{address[-6:]}")
                self.wallet_status.setStyleSheet("color: #00b894;")
                self._log(f"V4 Provider created for {protocol.value}")
                if proxy:
                    self._log(f"Using proxy: {self.proxy_type_combo.currentText()}")
            except Exception as e:
                self._log(f"Failed to create V4 provider: {e}")
                self.progress_bar.hide()
                self.create_btn.setEnabled(True)
                self.preview_btn.setEnabled(True)
                QMessageBox.critical(self, "Error", f"Failed to create V4 provider: {e}")
                return

            self.worker = CreateLadderWorkerV4(
                rpc_url=self.rpc_input.text().strip(),
                private_key=self.key_input.text().strip(),
                config=v4_config,
                chain_id=network.chain_id,
                auto_create_pool=self.auto_create_pool_cb.isChecked(),
                proxy=proxy if proxy else None,
                gas_limit=gas_limit
            )
        else:
            # V3 mode - use original LiquidityProvider
            network = self._get_current_network()

            # Determine correct Position Manager based on detected DEX
            detected_dex = getattr(self, '_detected_v3_dex', None)
            if detected_dex:
                position_manager_addr = detected_dex.position_manager
                v3_name = detected_dex.name
                self._log(f"Detected DEX: {v3_name}")
            else:
                position_manager_addr = network.position_manager
                v3_name = "PancakeSwap V3" if network.chain_id in [56, 97] else "Uniswap V3"
                self._log(f"No DEX detected, using default: {v3_name}")

            self._log(f"Required Position Manager: {position_manager_addr}")

            # Check if we need to recreate provider:
            # - Provider is None
            # - Provider is V4 type
            # - Provider has WRONG Position Manager address
            current_pm = getattr(self.provider, 'position_manager_address', None) if self.provider else None
            need_recreate = (
                self.provider is None or
                isinstance(self.provider, V4LiquidityProvider) or
                current_pm != position_manager_addr
            )

            if current_pm and current_pm != position_manager_addr:
                self._log(f"⚠️ Provider has wrong PM: {current_pm[:20]}...")
                self._log(f"   Need PM: {position_manager_addr[:20]}...")

            if need_recreate:
                self._log("Creating V3 LiquidityProvider with correct Position Manager...")
                rpc_url = self.rpc_input.text().strip()
                private_key = self.key_input.text().strip()

                if not rpc_url:
                    QMessageBox.warning(self, "Error", "Please enter RPC URL.")
                    self.progress_bar.hide()
                    self.create_btn.setEnabled(True)
                    self.preview_btn.setEnabled(True)
                    return
                if not private_key:
                    QMessageBox.warning(self, "Error", "Please enter private key.")
                    self.progress_bar.hide()
                    self.create_btn.setEnabled(True)
                    self.preview_btn.setEnabled(True)
                    return

                try:
                    proxy = self._get_proxy_config()

                    self.provider = LiquidityProvider(
                        rpc_url=rpc_url,
                        private_key=private_key,
                        position_manager_address=position_manager_addr,
                        chain_id=network.chain_id,
                        proxy=proxy if proxy else None
                    )
                    address = self.provider.account.address
                    self.wallet_status.setText(f"{v3_name}: {address[:8]}...{address[-6:]}")
                    self.wallet_status.setStyleSheet("color: #00b894;")
                    self._log(f"V3 Provider created for {v3_name}")
                except Exception as e:
                    self._log(f"Failed to create V3 provider: {e}")
                    self.progress_bar.hide()
                    self.create_btn.setEnabled(True)
                    self.preview_btn.setEnabled(True)
                    QMessageBox.critical(self, "Error", f"Failed to create V3 provider: {e}")
                    return

            config = LiquidityLadderConfig(
                current_price=upper_price,
                lower_price=lower_price,
                total_usd=self.total_usd_spin.value(),
                n_positions=self.positions_spin.value(),
                token0=token0,
                token1=token1,
                fee_tier=self._get_fee_tier(),
                distribution_type=self.dist_combo.currentText(),
                slippage_percent=self.slippage_spin.value(),
                token0_decimals=self._token0_decimals,
                token1_decimals=self._token1_decimals
            )

            # Get factory address from detected DEX
            detected_dex = getattr(self, '_detected_v3_dex', None)
            factory_address = detected_dex.pool_factory if detected_dex else None

            # Only use loaded pool address if pool_input is not empty
            # This ensures we don't use stale data if user cleared the field
            loaded_pool = None
            if self.pool_input.text().strip():
                loaded_pool = self._loaded_v3_pool_address

            self.worker = CreateLadderWorker(
                self.provider,
                config,
                auto_create_pool=self.auto_create_pool_cb.isChecked(),
                factory_address=factory_address,
                loaded_pool_address=loaded_pool
            )

        self.worker.progress.connect(self._on_progress)
        self.worker.create_result.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, message: str):
        """Handle progress updates."""
        self._log(message)

    def _on_finished(self, success: bool, message: str, data: dict):
        """Handle worker completion."""
        self.progress_bar.hide()
        self.create_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)

        # Cleanup worker thread to prevent memory leak
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

        if success:
            self._log(f"SUCCESS: {message}")
            self._log(f"TX Hash: {data.get('tx_hash', 'N/A')}")
            self._log(f"Gas Used: {data.get('gas_used', 'N/A')}")
            self._log(f"Token IDs: {data.get('token_ids', [])}")

            if data.get('pool_created'):
                self._log("New pool was created!")

            pool_info = ""
            if data.get('pool_created'):
                pool_info = "\n(New pool was created)"

            QMessageBox.information(
                self, "Success",
                f"Ladder created successfully!{pool_info}\n\n"
                f"TX: {data.get('tx_hash', 'N/A')}\n"
                f"Token IDs: {data.get('token_ids', [])}"
            )

            # Emit signal with new token IDs for manage tab
            token_ids = data.get('token_ids', [])
            if token_ids:
                self.positions_created.emit(token_ids)

            # Refresh balances
            self._update_balances()
        else:
            self._log(f"FAILED: {message}")
            QMessageBox.critical(self, "Error", f"Failed to create ladder:\n{message}")

    def _create_pool_only(self):
        """Create V4 pool without adding liquidity."""
        if not self._is_v4_mode():
            QMessageBox.warning(self, "Error", "Pool creation only is available in V4 mode.")
            return

        # Validate inputs
        rpc_url = self.rpc_input.text().strip()
        private_key = self.key_input.text().strip()

        if not rpc_url:
            QMessageBox.warning(self, "Error", "Please enter RPC URL.")
            return

        if not private_key:
            QMessageBox.warning(self, "Error", "Please enter private key.")
            return

        token0 = self.token0_input.text().strip()
        token1 = self.token1_input.text().strip()

        if not token0 or not token0.startswith("0x") or len(token0) != 42:
            QMessageBox.warning(self, "Error", "Please enter valid Token0 address.")
            return

        if not token1 or not token1.startswith("0x") or len(token1) != 42:
            QMessageBox.warning(self, "Error", "Please enter valid Token1 address.")
            return

        try:
            initial_price = _parse_price_text(self.price_input.text())
            if initial_price <= 0:
                raise ValueError("Price must be positive")
        except ValueError:
            QMessageBox.warning(self, "Error", "Please enter a valid initial price.")
            return

        fee_percent = self.custom_fee_spin.value()

        # Get tick spacing
        if self.tick_spacing_auto_cb.isChecked():
            tick_spacing = None  # Auto
        else:
            tick_spacing = self.tick_spacing_spin.value()

        protocol = self._get_v4_protocol()

        # Confirm
        msg = (
            f"Create V4 Pool (without liquidity):\n\n"
            f"Protocol: {protocol.value}\n"
            f"Token0: {token0[:20]}...\n"
            f"Token1: {token1[:20]}...\n"
            f"Fee: {fee_percent}%\n"
            f"Tick Spacing: {'Auto' if tick_spacing is None else tick_spacing}\n"
            f"Initial Price: {initial_price}\n\n"
            f"This will create and initialize the pool."
        )

        reply = QMessageBox.question(
            self, "Confirm Pool Creation", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Start creation
        self.progress_bar.show()
        self.create_pool_only_btn.setEnabled(False)
        self.create_btn.setEnabled(False)

        self._log(f"Creating V4 pool ({protocol.value})...")
        self._log(f"Token0: {token0}")
        self._log(f"Token1: {token1}")
        self._log(f"Fee: {fee_percent}%")
        self._log(f"Initial Price: {initial_price}")

        network = self._get_current_network()
        invert_price = self._should_invert_price(token0, token1)
        self._log(f"Auto-detected invert_price: {invert_price}")
        proxy = self._get_proxy_config()

        # Clean up previous pool creation worker
        if hasattr(self, '_pool_create_worker') and self._pool_create_worker is not None:
            if self._pool_create_worker.isRunning():
                self._pool_create_worker.quit()
                self._pool_create_worker.wait(2000)
            self._pool_create_worker.deleteLater()

        self._pool_create_worker = CreatePoolOnlyWorker(
            rpc_url=rpc_url,
            private_key=private_key,
            protocol=protocol,
            chain_id=network.chain_id,
            token0=token0,
            token1=token1,
            fee_percent=fee_percent,
            initial_price=initial_price,
            tick_spacing=tick_spacing,
            token0_decimals=self._token0_decimals,
            token1_decimals=self._token1_decimals,
            invert_price=invert_price,
            proxy=proxy if proxy else None
        )
        self._pool_create_worker.progress.connect(self._log)
        self._pool_create_worker.result.connect(self._on_pool_created)
        self._pool_create_worker.error.connect(self._on_pool_create_error)
        self._pool_create_worker.finished.connect(lambda: self._cleanup_pool_create_worker())
        self._pool_create_worker.start()

    def _on_pool_created(self, tx_hash, pool_id, success):
        """Handle pool creation result from worker."""
        self.progress_bar.hide()
        self.create_pool_only_btn.setEnabled(True)
        self.create_btn.setEnabled(True)

        if success:
            pool_id_hex = f"0x{pool_id.hex()}" if pool_id else "N/A"
            self._log(f"SUCCESS! Pool created.")
            self._log(f"Pool ID: {pool_id_hex}")
            self._log(f"TX: {tx_hash}")

            self.loaded_v4_pool_id = pool_id
            self._loaded_pool_id_bytes = pool_id

            QMessageBox.information(
                self, "Success",
                f"V4 Pool created successfully!\n\n"
                f"Pool ID: {pool_id_hex}\n"
                f"TX: {tx_hash}\n\n"
                f"You can now create positions in this pool."
            )
        else:
            self._log(f"FAILED: Pool creation failed")
            self._log(f"TX: {tx_hash}")
            QMessageBox.critical(
                self, "Error",
                f"Failed to create pool.\n\nTX: {tx_hash}"
            )

    def _on_pool_create_error(self, error_msg):
        """Handle pool creation error from worker."""
        self.progress_bar.hide()
        self.create_pool_only_btn.setEnabled(True)
        self.create_btn.setEnabled(True)
        self._log(f"ERROR: {error_msg}")
        QMessageBox.critical(self, "Error", f"Pool creation failed:\n{error_msg}")

    def _cleanup_pool_create_worker(self):
        """Clean up pool creation worker after completion."""
        if hasattr(self, '_pool_create_worker') and self._pool_create_worker is not None:
            self._pool_create_worker.deleteLater()
            self._pool_create_worker = None

    def update_custom_tokens(self, tokens: list):
        """Update combo boxes with custom tokens from Advanced tab."""
        # Save current selections
        current_token0 = self.token0_combo.currentText()
        current_token1 = self.token1_combo.currentText()

        # Clear custom tokens and add new ones (store decimals too)
        self.custom_tokens.clear()
        if not hasattr(self, 'custom_token_decimals'):
            self.custom_token_decimals = {}
        self.custom_token_decimals.clear()
        for token in tokens:
            symbol = token.get('symbol', '')
            address = token.get('address', '')
            decimals = token.get('decimals', 18)
            if symbol and address:
                self.custom_tokens[symbol] = address
                self.custom_token_decimals[symbol] = decimals

        # Rebuild combos using current network's tokens + custom tokens
        self._rebuild_token_combos()

        # Try to restore previous selections
        idx0 = self.token0_combo.findText(current_token0)
        if idx0 >= 0:
            self.token0_combo.setCurrentIndex(idx0)
        idx1 = self.token1_combo.findText(current_token1)
        if idx1 >= 0:
            self.token1_combo.setCurrentIndex(idx1)

        self._log(f"Updated with {len(tokens)} custom tokens")

    def _get_token_address(self, symbol: str) -> str:
        """Get token address by symbol, checking custom tokens first."""
        # Check if it's a custom token
        if symbol.startswith("[Custom] "):
            actual_symbol = symbol.replace("[Custom] ", "")
            if actual_symbol in self.custom_tokens:
                return self.custom_tokens[actual_symbol]

        # Check tokens for current network
        tokens = self._get_current_tokens()
        if symbol in tokens:
            return tokens[symbol].address

        raise ValueError(f"Unknown token: {symbol}")

    def reload_settings(self):
        """Reload settings from QSettings (called when settings dialog closes)."""
        tx_settings = QSettings("BNBLiquidityLadder", "Settings")
        gas_override = tx_settings.value("tx/gas_limit_override", 0, type=int)
        if gas_override > 0:
            self.gas_limit_spin.setValue(gas_override)
        slippage = tx_settings.value("tx/slippage", 0.5, type=float)
        self.slippage_spin.setValue(slippage)

    def _load_saved_wallet(self):
        """Load saved wallet from settings (with master password decryption)."""
        try:
            saved_key = self.settings.value("private_key", "")
            saved_rpc = self.settings.value("rpc_url", "")
            saved_network = self.settings.value("network_index", 0)
            remember = self.settings.value("remember", False)

            if remember and saved_key:
                # Check if key is encrypted (new format) or base64 (old format)
                if is_crypto_available() and is_encrypted_format(saved_key):
                    # New encrypted format - ask for master password
                    password = ask_master_password(self, "Разблокировка кошелька")
                    if password:
                        try:
                            decrypted_key = decrypt_key(saved_key, password)
                            self.key_input.setText(decrypted_key)
                            self.save_wallet_cb.setChecked(True)
                        except DecryptionError as e:
                            QMessageBox.warning(
                                self, "Ошибка",
                                f"Неверный пароль или повреждённые данные:\n{e}"
                            )
                    # If cancelled or wrong password, don't load anything
                else:
                    # Old base64 format - offer migration
                    try:
                        import base64
                        decoded_key = base64.b64decode(saved_key.encode()).decode()

                        if is_crypto_available():
                            # Offer to migrate to encrypted format
                            reply = QMessageBox.question(
                                self, "Обновление безопасности",
                                "Обнаружен незашифрованный ключ.\n"
                                "Хотите защитить его мастер-паролем?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                            )
                            if reply == QMessageBox.StandardButton.Yes:
                                password = create_master_password(self)
                                if password:
                                    encrypted = encrypt_key(decoded_key, password)
                                    self.settings.setValue("private_key", encrypted)
                                    QMessageBox.information(
                                        self, "Готово",
                                        "Ключ зашифрован! При следующем запуске\n"
                                        "потребуется ввести мастер-пароль."
                                    )

                        self.key_input.setText(decoded_key)
                        self.save_wallet_cb.setChecked(True)
                    except Exception:
                        pass

            # Set network FIRST with signals blocked to avoid _on_network_changed
            # overwriting the saved RPC with hardcoded defaults
            if saved_network:
                net_idx = int(saved_network)
                # Migration: old indices had BNB Testnet at 1
                # Old: BNB(0), Testnet(1), Ethereum(2), Base(3)
                # New: BNB(0), Ethereum(1), Base(2)
                if net_idx == 1:
                    net_idx = 0  # Testnet removed → fallback to BNB
                elif net_idx == 2:
                    net_idx = 1  # Ethereum shifted
                elif net_idx == 3:
                    net_idx = 2  # Base shifted
                self.network_combo.blockSignals(True)
                self.network_combo.setCurrentIndex(net_idx)
                self.network_combo.blockSignals(False)
                # Manually rebuild token combos (normally done by _on_network_changed)
                self._rebuild_token_combos()

            # Set saved RPC AFTER network change so it's not overwritten
            if saved_rpc:
                self.rpc_input.setText(saved_rpc)

        except Exception as e:
            logger.warning(f"Error loading saved wallet: {e}")

    def _save_wallet(self):
        """Save wallet to settings with AES-256 encryption."""
        if self.save_wallet_cb.isChecked():
            private_key = self.key_input.text().strip()
            if not private_key:
                return

            # Skip re-encryption if already saved (don't ask master password every connect)
            if self.settings.value("remember", False, type=bool) and self.settings.value("private_key"):
                # Just update RPC and network in case they changed
                self.settings.setValue("rpc_url", self.rpc_input.text().strip())
                self.settings.setValue("network_index", self.network_combo.currentIndex())
                return

            if is_crypto_available():
                # Ask for master password to encrypt (only on first save)
                password = create_master_password(self)
                if password:
                    try:
                        encrypted_key = encrypt_key(private_key, password)
                        self.settings.setValue("private_key", encrypted_key)
                        self.settings.setValue("rpc_url", self.rpc_input.text().strip())
                        self.settings.setValue("network_index", self.network_combo.currentIndex())
                        self.settings.setValue("remember", True)
                        QMessageBox.information(
                            self, "Сохранено",
                            "Ключ зашифрован и сохранён.\n"
                            "При следующем запуске потребуется мастер-пароль."
                        )
                    except CryptoNotAvailable as e:
                        QMessageBox.warning(self, "Ошибка", str(e))
                else:
                    # User cancelled - uncheck the checkbox
                    self.save_wallet_cb.setChecked(False)
            else:
                # Crypto not available - warn user
                reply = QMessageBox.warning(
                    self, "Предупреждение",
                    "Библиотека шифрования не установлена.\n"
                    "Ключ будет сохранён без защиты!\n\n"
                    "Установите: pip install cryptography\n\n"
                    "Продолжить без шифрования?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    import base64
                    encoded_key = base64.b64encode(private_key.encode()).decode()
                    self.settings.setValue("private_key", encoded_key)
                    self.settings.setValue("rpc_url", self.rpc_input.text().strip())
                    self.settings.setValue("network_index", self.network_combo.currentIndex())
                    self.settings.setValue("remember", True)
                else:
                    self.save_wallet_cb.setChecked(False)
        else:
            # Clear saved wallet
            self.settings.remove("private_key")
            self.settings.setValue("remember", False)

    def _clear_saved_wallet(self):
        """Clear saved wallet data."""
        self.settings.remove("private_key")
        self.settings.remove("rpc_url")
        self.settings.remove("network_index")
        self.settings.setValue("remember", False)
        self.save_wallet_cb.setChecked(False)

    def _load_pool_info(self):
        """Load pool info via worker thread (non-blocking)."""
        self._reset_pool_state()

        pool_input = self.pool_input.text().strip()
        network = self._get_current_network()
        self._log(f"Load button clicked. Input: '{pool_input}' Network: {network.chain_id}")

        if not pool_input:
            QMessageBox.warning(self, "Error", "Please enter a pool address or ID.")
            return
        if not pool_input.startswith("0x"):
            QMessageBox.warning(self, "Error", f"Invalid format. Must start with 0x")
            return

        is_v4 = len(pool_input) == 66
        is_v3 = len(pool_input) == 42
        if not is_v3 and not is_v4:
            QMessageBox.warning(self, "Error",
                f"Invalid format (length={len(pool_input)}).\n\n"
                "V3 Pool: 42 characters (0x + 40 hex)\n"
                "V4 Pool ID: 66 characters (0x + 64 hex)")
            return

        # Disable button while loading
        self.load_pool_btn.setEnabled(False)
        self.pool_info_label.setText("Loading pool...")
        self.pool_info_label.setStyleSheet("color: #fdcb6e;")

        rpc_url = self.rpc_input.text().strip() or BNB_CHAIN.rpc_url
        proxy = self._get_proxy_config()

        # Existing token inputs for V4 fallback
        existing_t0 = self.token0_input.text().strip()
        existing_t1 = self.token1_input.text().strip()

        self._load_pool_worker = LoadPoolWorker(
            rpc_url, pool_input, network.chain_id, proxy,
            existing_token0=existing_t0, existing_token1=existing_t1
        )
        self._load_pool_worker.pool_loaded.connect(self._on_pool_data_loaded)
        self._load_pool_worker.progress.connect(self._log)
        self._load_pool_worker.error.connect(self._on_pool_load_error)
        self._load_pool_worker.finished.connect(lambda: self.load_pool_btn.setEnabled(True))
        self._load_pool_worker.start()

    def _on_pool_load_error(self, error_msg):
        """Handle pool loading error."""
        self.pool_info_label.setText(f"Error: {error_msg[:100]}")
        self.pool_info_label.setStyleSheet("color: #e94560;")

    def _on_pool_data_loaded(self, data: dict):
        """Apply loaded pool data to UI (runs on main thread)."""
        from src.contracts.v4.constants import V4Protocol

        is_v4 = data.get('is_v4', False)
        network = self._get_current_network()

        if is_v4:
            # ── V4 pool results ──
            pool_id_bytes = data.get('pool_id_bytes')
            pool_found = data.get('pool_found', False)

            if pool_found:
                v4_fee = data.get('v4_fee', 0)
                fee_percent = v4_fee / 10000
                protocol_name = data.get('v4_protocol_name', 'V4')
                v4_protocol = data.get('v4_protocol')

                self._loaded_pool_fee = v4_fee
                self._loaded_pool_id_bytes = pool_id_bytes
                self.loaded_v4_pool_id = pool_id_bytes

                subgraph = data.get('subgraph')
                if subgraph:
                    self.token0_input.setText(subgraph.token0_address)
                    self.token1_input.setText(subgraph.token1_address)
                    self.tick_spacing_spin.setValue(subgraph.tick_spacing)
                    self.tick_spacing_auto_cb.setChecked(False)
                    self.tick_spacing_spin.setEnabled(True)
                    self._token0_decimals = subgraph.token0_decimals
                    self._token1_decimals = subgraph.token1_decimals
                elif 'ui_dec0' in data:
                    self._token0_decimals = data['ui_dec0']
                    self._token1_decimals = data['ui_dec1']

                # Set price
                display_price = data.get('display_price')
                if display_price:
                    self.price_input.setText(_format_price(display_price))

                # Set fee
                self.custom_fee_spin.setValue(fee_percent)

                # Set protocol combo
                if v4_protocol == V4Protocol.UNISWAP:
                    self.protocol_combo.setCurrentIndex(3)
                else:
                    self.protocol_combo.setCurrentIndex(1)

                # Build info label
                if subgraph:
                    price_str = self.price_input.text() or "N/A"
                    tick = data.get('v4_tick', '?')
                    ts = data.get('tick_spacing', subgraph.tick_spacing if subgraph else '?')
                    self.pool_info_label.setText(
                        f"✅ V4 Pool found on {protocol_name}!\n"
                        f"Tokens: {subgraph.token0_symbol}/{subgraph.token1_symbol}\n"
                        f"Fee: {fee_percent}% | TickSpacing: {ts} | Tick: {tick}\n"
                        f"Price: {price_str} | Token addresses auto-filled!"
                    )
                else:
                    self.pool_info_label.setText(f"✅ V4 Pool found on {protocol_name}!\nFee: {fee_percent}%")
                self.pool_info_label.setStyleSheet("color: #00b894;")
            else:
                # Not found
                self.pool_info_label.setText("V4 pool ID not found on-chain or in subgraph.\nEnter token addresses manually.")
                self.pool_info_label.setStyleSheet("color: #fdcb6e;")
                self.loaded_v4_pool_id = None

                # Switch to V4 mode
                idx = self.protocol_combo.currentIndex()
                if idx == 0:
                    self.protocol_combo.setCurrentIndex(1)
                elif idx == 2:
                    self.protocol_combo.setCurrentIndex(3)

                for err in data.get('errors', []):
                    self._log(f"  {err}")
        else:
            # ── V3 pool results ──
            pool_address = data.get('pool_address')
            self._loaded_v3_pool_address = pool_address
            self._detected_v3_dex = data.get('detected_dex')

            # Set token inputs
            self.token0_input.setText(data.get('ui_token0', ''))
            self.token1_input.setText(data.get('ui_token1', ''))
            self._token0_decimals = data.get('ui_dec0', 18)
            self._token1_decimals = data.get('ui_dec1', 18)

            fee = data.get('fee', 0)
            display_price = data.get('display_price')
            display_pair = data.get('display_pair', '???/???')

            # Set fee combo
            fee_map = {500: 0, 2500: 1, 3000: 2, 10000: 3}
            detected = self._detected_v3_dex
            if detected and hasattr(detected, 'name'):
                v3_idx = 2 if "uniswap" in detected.name.lower() else 0
            else:
                v3_idx = 0 if network.chain_id in [56, 97] else 2

            if fee in fee_map:
                self.protocol_combo.setCurrentIndex(v3_idx)
                self.fee_combo.setCurrentIndex(fee_map[fee])
            else:
                self.protocol_combo.setCurrentIndex(1)
                self.custom_fee_spin.setValue(fee / 10000)

            # Set price
            if display_price:
                self.price_input.setText(_format_price(display_price))

            # Info label
            dex_name = detected.name if detected and hasattr(detected, 'name') else "V3"
            info_text = f"✅ {dex_name}: {display_pair} | Fee: {fee/10000}%"
            if display_price:
                info_text += f" | Price: {_format_price(display_price)}"
            self.pool_info_label.setText(info_text)
            self.pool_info_label.setStyleSheet("color: #00b894;")

            self._log(f"Loaded pool: {display_pair}, fee={fee/10000}%")
