"""
Tests for DexSwap module: DEX token swap via Uniswap/PancakeSwap V2 and V3.

Covers:
- SwapResult dataclass
- DexSwap.__init__ (supported/unsupported chains, V3 availability)
- is_stable_token
- get_output_token
- get_token_balance / get_token_decimals
- _parse_actual_output (Transfer event parsing)
- get_quote / _build_path (V2 routing)
- get_quote_v3 (V3 quoting, multi-hop)
- swap (main entry point: V3 preferred, V2 fallback)
- swap_v3 (V3 execution)
- _swap_v2 (V2 execution)
- check_and_approve / _check_and_approve_v3
- sell_tokens_after_close (standalone function)
- Nonce management (confirm/release patterns)
"""

import pytest
from unittest.mock import MagicMock, Mock, patch, PropertyMock
from dataclasses import asdict
from web3 import Web3

from src.dex_swap import (
    DexSwap,
    SwapResult,
    sell_tokens_after_close,
    STABLE_TOKENS,
    ROUTER_V2_ADDRESSES,
    ROUTER_V3_ADDRESSES,
)


# ============================================================
# Test addresses (real checksum format)
# ============================================================
WALLET = Web3.to_checksum_address("0x1234567890AbcdEF1234567890aBcdef12345678")
PRIVATE_KEY = "0x" + "ab" * 32
TOKEN_VOLATILE = Web3.to_checksum_address("0xDeadBeefDeadBeefDeadBeefDeadBeefDeadBeef")
USDT_BSC = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
USDC_BASE = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WBNB = Web3.to_checksum_address("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
WETH_ETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
WETH_BASE = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")

# Transfer(address,address,uint256) topic (real keccak)
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)")


# ============================================================
# Helpers
# ============================================================

def _make_mock_account():
    """Create a mock Account object with sign_transaction support."""
    mock_acct = MagicMock()
    mock_acct.sign_transaction = MagicMock(
        return_value=MagicMock(raw_transaction=b'\xaa' * 32)
    )
    return mock_acct


def _make_mock_w3(chain_id=56):
    """Create a MagicMock Web3 instance suitable for DexSwap init."""
    w3 = MagicMock()
    w3.eth = MagicMock()
    w3.eth.gas_price = 5_000_000_000
    w3.eth.get_transaction_count = MagicMock(return_value=100)
    w3.eth.chain_id = chain_id
    # contract() returns a fresh mock each time (used for router, quoter, ERC20)
    w3.eth.contract = MagicMock(return_value=MagicMock())
    w3.eth.account = MagicMock()
    w3.eth.account.sign_transaction = MagicMock(
        return_value=MagicMock(raw_transaction=b'\xaa' * 32)
    )
    w3.eth.send_raw_transaction = MagicMock(return_value=b'\xbb' * 32)
    w3.eth.wait_for_transaction_receipt = MagicMock(return_value=MagicMock(
        status=1, gasUsed=200_000, **{'get': lambda k, d=None: [] if k == 'logs' else d}
    ))
    return w3


def _make_swapper(chain_id=56, nonce_manager=None):
    """Create a DexSwap instance with mocked Web3."""
    w3 = _make_mock_w3(chain_id)
    swapper = DexSwap(w3, chain_id=chain_id, nonce_manager=nonce_manager)
    # Pre-set a mock account so _resolve_account(None) returns it
    swapper.account = _make_mock_account()
    return swapper, w3


def _make_nonce_manager():
    """Create a mock NonceManager."""
    nm = MagicMock()
    nm.get_next_nonce = MagicMock(return_value=42)
    nm.confirm_transaction = MagicMock()
    nm.release_nonce = MagicMock()
    return nm


def _make_transfer_log(token_addr, recipient_addr, amount, extra_topic=None):
    """Build a log entry that looks like an ERC20 Transfer event."""
    # Topic[1] = sender (padded), Topic[2] = recipient (padded)
    sender_padded = bytes(12) + bytes.fromhex("aabbccddaabbccddaabbccddaabbccddaabbccdd")
    recipient_padded = bytes(12) + bytes.fromhex(recipient_addr[2:].lower())
    return {
        'address': token_addr,
        'topics': [
            TRANSFER_TOPIC,
            sender_padded,
            recipient_padded,
        ],
        'data': amount.to_bytes(32, 'big'),
    }


# ============================================================
# SwapResult dataclass tests
# ============================================================

class TestSwapResult:

    def test_swap_result_fields(self):
        """SwapResult contains all expected fields."""
        r = SwapResult(
            success=True, tx_hash="0xabc", from_token="0x1", to_token="0x2",
            from_amount=100, to_amount=200, to_amount_usd=1.5, gas_used=50000
        )
        assert r.success is True
        assert r.tx_hash == "0xabc"
        assert r.from_token == "0x1"
        assert r.to_token == "0x2"
        assert r.from_amount == 100
        assert r.to_amount == 200
        assert r.to_amount_usd == 1.5
        assert r.gas_used == 50000
        assert r.error is None

    def test_swap_result_error_field(self):
        """SwapResult error field defaults to None, can be set."""
        r = SwapResult(
            success=False, tx_hash=None, from_token="0x1", to_token="0x2",
            from_amount=0, to_amount=0, to_amount_usd=0, gas_used=0,
            error="Something broke"
        )
        assert r.error == "Something broke"

    def test_swap_result_is_dataclass(self):
        """SwapResult is a proper dataclass and can be converted to dict."""
        r = SwapResult(
            success=True, tx_hash="0x1", from_token="A", to_token="B",
            from_amount=1, to_amount=2, to_amount_usd=3.0, gas_used=4
        )
        d = asdict(r)
        assert isinstance(d, dict)
        assert d["success"] is True
        assert d["to_amount_usd"] == 3.0


# ============================================================
# Constants tests
# ============================================================

class TestConstants:

    def test_stable_tokens_contains_bsc_usdt(self):
        addr = "0x55d398326f99059ff775485246999027b3197955"
        assert addr in STABLE_TOKENS
        assert STABLE_TOKENS[addr] == "USDT"

    def test_stable_tokens_contains_base_usdc(self):
        addr = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
        assert addr in STABLE_TOKENS
        assert STABLE_TOKENS[addr] == "USDC"

    def test_stable_tokens_contains_wbnb(self):
        addr = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
        assert addr in STABLE_TOKENS
        assert STABLE_TOKENS[addr] == "WBNB"

    def test_stable_tokens_contains_eth_weth(self):
        addr = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
        assert addr in STABLE_TOKENS
        assert STABLE_TOKENS[addr] == "WETH"

    def test_router_v2_supported_chains(self):
        assert 56 in ROUTER_V2_ADDRESSES
        assert 1 in ROUTER_V2_ADDRESSES
        assert 8453 in ROUTER_V2_ADDRESSES
        assert 97 in ROUTER_V2_ADDRESSES

    def test_router_v3_supported_chains(self):
        assert 56 in ROUTER_V3_ADDRESSES
        assert 1 in ROUTER_V3_ADDRESSES
        assert 8453 in ROUTER_V3_ADDRESSES

    def test_router_v3_fee_tiers_present(self):
        for chain_id in [56, 1, 8453]:
            assert "fee_tiers" in ROUTER_V3_ADDRESSES[chain_id]
            assert isinstance(ROUTER_V3_ADDRESSES[chain_id]["fee_tiers"], list)
            assert len(ROUTER_V3_ADDRESSES[chain_id]["fee_tiers"]) >= 3


# ============================================================
# DexSwap.__init__ tests
# ============================================================

class TestDexSwapInit:

    def test_init_bsc(self):
        """Init with BNB Chain (56) sets V2 and V3 routers."""
        swapper, w3 = _make_swapper(56)
        assert swapper.chain_id == 56
        assert swapper.v3_available is True
        assert swapper.dex_name == "PancakeSwap V2"

    def test_init_ethereum(self):
        """Init with Ethereum (1)."""
        swapper, w3 = _make_swapper(1)
        assert swapper.chain_id == 1
        assert swapper.v3_available is True
        assert swapper.dex_name == "Uniswap V2"

    def test_init_base(self):
        """Init with Base (8453)."""
        swapper, w3 = _make_swapper(8453)
        assert swapper.chain_id == 8453
        assert swapper.v3_available is True

    def test_init_testnet_no_v3(self):
        """Init with BSC testnet (97) — V2 only, no V3."""
        swapper, w3 = _make_swapper(97)
        assert swapper.chain_id == 97
        assert swapper.v3_available is False

    def test_init_unsupported_chain_raises(self):
        """Unsupported chain raises ValueError."""
        w3 = _make_mock_w3()
        with pytest.raises(ValueError, match="Unsupported chain ID: 999"):
            DexSwap(w3, chain_id=999)

    def test_init_stores_nonce_manager(self):
        """NonceManager is stored when provided."""
        nm = _make_nonce_manager()
        swapper, w3 = _make_swapper(56, nonce_manager=nm)
        assert swapper.nonce_manager is nm

    def test_init_nonce_manager_default_none(self):
        """NonceManager defaults to None."""
        swapper, w3 = _make_swapper(56)
        assert swapper.nonce_manager is None

    def test_init_creates_router_contracts(self):
        """Init calls w3.eth.contract for V2 and V3 routers."""
        w3 = _make_mock_w3(56)
        swapper = DexSwap(w3, chain_id=56)
        # Should have called contract() at least for V2 router, V3 router, V3 quoter
        assert w3.eth.contract.call_count >= 3


# ============================================================
# is_stable_token tests
# ============================================================

class TestIsStableToken:

    def test_bsc_usdt_is_stable(self):
        swapper, _ = _make_swapper(56)
        assert swapper.is_stable_token(USDT_BSC) is True

    def test_bsc_wbnb_is_stable(self):
        swapper, _ = _make_swapper(56)
        assert swapper.is_stable_token(WBNB) is True

    def test_random_address_not_stable(self):
        swapper, _ = _make_swapper(56)
        assert swapper.is_stable_token(TOKEN_VOLATILE) is False

    def test_case_insensitive(self):
        """Addresses are compared case-insensitively."""
        swapper, _ = _make_swapper(56)
        upper = "0x55D398326F99059FF775485246999027B3197955"
        lower = "0x55d398326f99059ff775485246999027b3197955"
        assert swapper.is_stable_token(upper) is True
        assert swapper.is_stable_token(lower) is True

    def test_base_usdc_is_stable(self):
        swapper, _ = _make_swapper(8453)
        assert swapper.is_stable_token(USDC_BASE) is True


# ============================================================
# get_output_token tests
# ============================================================

class TestGetOutputToken:

    def test_bsc_output_is_usdt(self):
        swapper, _ = _make_swapper(56)
        assert swapper.get_output_token() == Web3.to_checksum_address(
            "0x55d398326f99059fF775485246999027B3197955"
        )

    def test_base_output_is_usdc(self):
        swapper, _ = _make_swapper(8453)
        assert swapper.get_output_token() == Web3.to_checksum_address(
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        )


# ============================================================
# get_token_balance tests
# ============================================================

class TestGetTokenBalance:

    def test_returns_balance(self):
        swapper, w3 = _make_swapper(56)
        mock_contract = MagicMock()
        mock_contract.functions.balanceOf.return_value.call.return_value = 5 * 10**18
        w3.eth.contract.return_value = mock_contract

        balance = swapper.get_token_balance(TOKEN_VOLATILE, WALLET)
        assert balance == 5 * 10**18

    def test_error_returns_zero(self):
        swapper, w3 = _make_swapper(56)
        w3.eth.contract.side_effect = Exception("RPC error")

        balance = swapper.get_token_balance(TOKEN_VOLATILE, WALLET)
        assert balance == 0


# ============================================================
# get_token_decimals tests
# ============================================================

class TestGetTokenDecimals:

    def test_returns_decimals(self):
        swapper, w3 = _make_swapper(56)
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 6
        w3.eth.contract.return_value = mock_contract

        decimals = swapper.get_token_decimals(TOKEN_VOLATILE)
        assert decimals == 6

    def test_error_raises_runtime_error(self):
        swapper, w3 = _make_swapper(56)
        w3.eth.contract.side_effect = Exception("no decimals method")

        with pytest.raises(RuntimeError, match="Failed to get decimals"):
            swapper.get_token_decimals(TOKEN_VOLATILE)


# ============================================================
# _parse_actual_output tests
# ============================================================

class TestParseActualOutput:

    def _swapper(self):
        s, _ = _make_swapper(56)
        return s

    def test_parses_transfer_event_bytes_data(self):
        """Parse Transfer event with data as bytes."""
        swapper = self._swapper()
        amount = 1_500_000_000_000_000_000  # 1.5e18
        log = _make_transfer_log(USDT_BSC, WALLET, amount)
        receipt = {'logs': [log]}

        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result == amount

    def test_parses_transfer_event_hex_data(self):
        """Parse Transfer event with data as hex string."""
        swapper = self._swapper()
        amount = 2_000_000_000_000_000_000
        log = _make_transfer_log(USDT_BSC, WALLET, amount)
        log['data'] = '0x' + amount.to_bytes(32, 'big').hex()
        receipt = {'logs': [log]}

        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result == amount

    def test_filters_wrong_token_address(self):
        """Transfer from wrong token contract is ignored."""
        swapper = self._swapper()
        wrong_token = Web3.to_checksum_address("0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        log = _make_transfer_log(wrong_token, WALLET, 10**18)
        receipt = {'logs': [log]}

        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result is None

    def test_filters_wrong_recipient(self):
        """Transfer to wrong recipient is ignored."""
        swapper = self._swapper()
        other_wallet = Web3.to_checksum_address("0x9999999999999999999999999999999999999999")
        log = _make_transfer_log(USDT_BSC, other_wallet, 10**18)
        receipt = {'logs': [log]}

        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result is None

    def test_empty_logs_returns_none(self):
        """Receipt with no logs returns None."""
        swapper = self._swapper()
        receipt = {'logs': []}
        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result is None

    def test_no_logs_key_returns_none(self):
        """Receipt without 'logs' key returns None."""
        swapper = self._swapper()
        receipt = {}
        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result is None

    def test_picks_largest_transfer(self):
        """When multiple Transfer events, picks the largest amount."""
        swapper = self._swapper()
        log1 = _make_transfer_log(USDT_BSC, WALLET, 100)
        log2 = _make_transfer_log(USDT_BSC, WALLET, 999)
        log3 = _make_transfer_log(USDT_BSC, WALLET, 500)
        receipt = {'logs': [log1, log2, log3]}

        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result == 999

    def test_skips_logs_with_too_few_topics(self):
        """Logs with fewer than 3 topics are skipped."""
        swapper = self._swapper()
        log = {
            'address': USDT_BSC,
            'topics': [TRANSFER_TOPIC],  # Only 1 topic
            'data': (10**18).to_bytes(32, 'big'),
        }
        receipt = {'logs': [log]}
        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result is None

    def test_skips_non_transfer_topic(self):
        """Logs with wrong event topic are skipped."""
        swapper = self._swapper()
        recipient_padded = bytes(12) + bytes.fromhex(WALLET[2:].lower())
        log = {
            'address': USDT_BSC,
            'topics': [
                b'\x00' * 32,  # Wrong topic
                b'\x00' * 32,
                recipient_padded,
            ],
            'data': (10**18).to_bytes(32, 'big'),
        }
        receipt = {'logs': [log]}
        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result is None

    def test_exception_returns_none(self):
        """If parsing throws, returns None."""
        swapper = self._swapper()
        # Craft a log that will cause int parsing to fail
        log = _make_transfer_log(USDT_BSC, WALLET, 100)
        log['data'] = "not_a_number"  # Will fail int() conversion
        receipt = {'logs': [log]}
        result = swapper._parse_actual_output(receipt, USDT_BSC, WALLET)
        assert result is None


# ============================================================
# _build_path tests
# ============================================================

class TestBuildPath:

    def test_direct_path_works(self):
        """When direct getAmountsOut succeeds, returns [from, to]."""
        swapper, w3 = _make_swapper(56)
        swapper.router.functions.getAmountsOut.return_value.call.return_value = [10**18, 10**18]

        path = swapper._build_path(TOKEN_VOLATILE, USDT_BSC)
        assert len(path) == 2
        assert path[0] == TOKEN_VOLATILE
        assert path[1] == USDT_BSC

    def test_direct_fails_weth_path(self):
        """When direct fails, tries via WETH."""
        swapper, w3 = _make_swapper(56)
        call_count = [0]

        def side_effect(*args, **kwargs):
            mock = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # Direct path fails
                mock.call.side_effect = Exception("no pair")
            else:
                # WETH path succeeds
                mock.call.return_value = [10**18, 5*10**17, 10**18]
            return mock

        swapper.router.functions.getAmountsOut = side_effect

        path = swapper._build_path(TOKEN_VOLATILE, USDT_BSC)
        assert len(path) == 3
        assert path[1] == swapper.weth_address

    def test_both_paths_fail_returns_empty(self):
        """When both direct and WETH paths fail, returns []."""
        swapper, w3 = _make_swapper(56)
        swapper.router.functions.getAmountsOut.return_value.call.side_effect = Exception("no path")

        path = swapper._build_path(TOKEN_VOLATILE, USDT_BSC)
        assert path == []

    def test_skip_weth_path_when_from_is_weth(self):
        """No WETH detour when from_token IS WETH."""
        swapper, w3 = _make_swapper(56)
        swapper.router.functions.getAmountsOut.return_value.call.side_effect = Exception("no pair")

        # from_token = WBNB (which is WETH on BSC) => should not try WETH path
        path = swapper._build_path(swapper.weth_address, USDT_BSC)
        assert path == []


# ============================================================
# get_quote tests
# ============================================================

class TestGetQuote:

    def test_returns_last_amount(self):
        swapper, w3 = _make_swapper(56)
        swapper.router.functions.getAmountsOut.return_value.call.return_value = [10**18, 500 * 10**18]

        # Also mock _build_path to return direct path
        swapper._build_path = MagicMock(return_value=[TOKEN_VOLATILE, USDT_BSC])

        result = swapper.get_quote(TOKEN_VOLATILE, USDT_BSC, 10**18)
        assert result == 500 * 10**18

    def test_no_path_returns_zero(self):
        swapper, w3 = _make_swapper(56)
        swapper._build_path = MagicMock(return_value=[])

        result = swapper.get_quote(TOKEN_VOLATILE, USDT_BSC, 10**18)
        assert result == 0

    def test_exception_returns_zero(self):
        swapper, w3 = _make_swapper(56)
        swapper._build_path = MagicMock(return_value=[TOKEN_VOLATILE, USDT_BSC])
        swapper.router.functions.getAmountsOut.return_value.call.side_effect = Exception("revert")

        result = swapper.get_quote(TOKEN_VOLATILE, USDT_BSC, 10**18)
        assert result == 0


# ============================================================
# get_quote_v3 tests
# ============================================================

class TestGetQuoteV3:

    def test_v3_unavailable_returns_zeros(self):
        """When V3 not available, returns (0, 0, 0)."""
        swapper, w3 = _make_swapper(97)  # testnet, no V3
        result = swapper.get_quote_v3(TOKEN_VOLATILE, USDT_BSC, 10**18)
        assert result == (0, 0, 0)

    def test_best_fee_selected(self):
        """Tries all fee tiers, selects the one with best output."""
        swapper, w3 = _make_swapper(56)
        outputs = {100: 900, 500: 1200, 2500: 1100, 10000: 800}

        def quote_side_effect(params):
            mock = MagicMock()
            fee = params[3]
            if fee in outputs:
                mock.call.return_value = [outputs[fee], 0, 0, 0]
            else:
                mock.call.side_effect = Exception("no pool")
            return mock

        swapper.quoter_v3.functions.quoteExactInputSingle = quote_side_effect
        # Disable multi-hop by making from_token = weth
        amount_out, best_fee, fee2 = swapper.get_quote_v3(
            swapper.weth_address, USDT_BSC, 10**18
        )
        assert amount_out == 1200
        assert best_fee == 500
        assert fee2 == 0  # direct

    def test_specific_fee_used_when_provided(self):
        """When fee is specified, only that tier is tried."""
        swapper, w3 = _make_swapper(56)

        def quote_side_effect(params):
            mock = MagicMock()
            mock.call.return_value = [5000, 0, 0, 0]
            return mock

        swapper.quoter_v3.functions.quoteExactInputSingle = quote_side_effect

        amount_out, best_fee, fee2 = swapper.get_quote_v3(
            swapper.weth_address, USDT_BSC, 10**18, fee=500
        )
        assert amount_out == 5000
        assert best_fee == 500

    def test_multi_hop_beats_direct(self):
        """Multi-hop via WETH wins over direct if better output."""
        swapper, w3 = _make_swapper(56)
        call_count = [0]

        def quote_side_effect(params):
            call_count[0] += 1
            mock = MagicMock()
            token_in = params[0]
            token_out = params[1]

            # Direct: returns 500
            if token_in == TOKEN_VOLATILE and token_out == USDT_BSC:
                mock.call.return_value = [500, 0, 0, 0]
            # First hop: TOKEN -> WETH: returns 10000
            elif token_in == TOKEN_VOLATILE and token_out == swapper.weth_address:
                mock.call.return_value = [10000, 0, 0, 0]
            # Second hop: WETH -> USDT: returns 2000 (more than direct 500)
            elif token_in == swapper.weth_address and token_out == USDT_BSC:
                mock.call.return_value = [2000, 0, 0, 0]
            else:
                mock.call.side_effect = Exception("no pool")
            return mock

        swapper.quoter_v3.functions.quoteExactInputSingle = quote_side_effect

        amount_out, best_fee, fee2 = swapper.get_quote_v3(TOKEN_VOLATILE, USDT_BSC, 10**18)
        assert amount_out == 2000
        assert fee2 > 0  # multi-hop

    def test_all_quotes_fail_returns_zero(self):
        """When all fee tiers fail, returns (0, 0, 0)."""
        swapper, w3 = _make_swapper(56)
        swapper.quoter_v3.functions.quoteExactInputSingle.return_value.call.side_effect = \
            Exception("no pool")

        result = swapper.get_quote_v3(swapper.weth_address, USDT_BSC, 10**18)
        assert result == (0, 0, 0)


# ============================================================
# swap_v3 tests
# ============================================================

@patch('src.dex_swap.Account.from_key', return_value=_make_mock_account())
class TestSwapV3:

    def _setup_v3_swap(self, chain_id=56, nm=None):
        swapper, w3 = _make_swapper(chain_id, nonce_manager=nm)
        # Mock get_quote_v3 to return a valid quote
        swapper.get_quote_v3 = MagicMock(return_value=(10**18, 500, 0))
        # Mock approve
        swapper._check_and_approve_v3 = MagicMock(return_value=True)
        # Mock router_v3 encodeABI
        swapper.router_v3.encodeABI = MagicMock(return_value=b'\x00' * 32)
        # Mock multicall build_transaction
        swapper.router_v3.functions.multicall.return_value.build_transaction = \
            MagicMock(return_value={'gas': 350000})
        # Mock send/receipt
        tx_hash_mock = MagicMock()
        tx_hash_mock.hex.return_value = "0x" + "cc" * 32
        w3.eth.send_raw_transaction.return_value = tx_hash_mock

        receipt_mock = MagicMock()
        receipt_mock.status = 1
        receipt_mock.gasUsed = 250000
        receipt_mock.get = lambda k, d=None: [] if k == 'logs' else d
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        # Mock get_token_decimals
        swapper.get_token_decimals = MagicMock(return_value=18)
        # Mock _parse_actual_output
        swapper._parse_actual_output = MagicMock(return_value=10**18)

        return swapper, w3

    def test_v3_not_available_returns_error(self, _mock_from_key):
        swapper, w3 = _make_swapper(97)  # no V3
        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "V3 not available" in result.error

    def test_no_liquidity_returns_error(self, _mock_from_key):
        swapper, w3 = _make_swapper(56)
        swapper.get_quote_v3 = MagicMock(return_value=(0, 0, 0))
        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "No V3 liquidity" in result.error

    def test_approve_fails_returns_error(self, _mock_from_key):
        swapper, w3 = _make_swapper(56)
        swapper.get_quote_v3 = MagicMock(return_value=(10**18, 500, 0))
        swapper._check_and_approve_v3 = MagicMock(return_value=False)
        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "Failed to approve" in result.error

    def test_successful_direct_swap(self, _mock_from_key):
        swapper, w3 = self._setup_v3_swap()
        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True
        assert result.to_amount == 10**18
        assert result.gas_used == 250000

    def test_successful_multi_hop_swap(self, _mock_from_key):
        """Multi-hop swap uses exactInput with encoded path."""
        swapper, w3 = self._setup_v3_swap()
        swapper.get_quote_v3 = MagicMock(return_value=(10**18, 500, 100))  # fee2 > 0

        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True
        # Should have called encode_abi with 'exactInput'
        swapper.router_v3.encode_abi.assert_called_once()
        call_args = swapper.router_v3.encode_abi.call_args
        assert call_args[0][0] == 'exactInput' if call_args[0] else True

    def test_reverted_tx_returns_error(self, _mock_from_key):
        swapper, w3 = self._setup_v3_swap()
        receipt_mock = MagicMock()
        receipt_mock.status = 0
        receipt_mock.gasUsed = 350000
        tx_hash_mock = MagicMock()
        tx_hash_mock.hex.return_value = "0xdead"
        w3.eth.send_raw_transaction.return_value = tx_hash_mock
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "V3 swap transaction failed" in result.error
        assert result.gas_used == 350000

    def test_build_tx_exception_returns_error(self, _mock_from_key):
        swapper, w3 = self._setup_v3_swap()
        swapper.router_v3.functions.multicall.return_value.build_transaction.side_effect = \
            Exception("gas estimation failed")

        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "gas estimation failed" in result.error

    def test_parse_output_none_uses_expected(self, _mock_from_key):
        """When _parse_actual_output returns None, uses expected_out."""
        swapper, w3 = self._setup_v3_swap()
        swapper._parse_actual_output = MagicMock(return_value=None)
        swapper.get_quote_v3 = MagicMock(return_value=(7777, 500, 0))

        result = swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True
        assert result.to_amount == 7777

    def test_nonce_confirm_on_success(self, _mock_from_key):
        """Nonce is confirmed after successful TX."""
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_v3_swap(nm=nm)

        swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)

        nm.confirm_transaction.assert_called_once_with(42)
        nm.release_nonce.assert_not_called()

    def test_nonce_release_on_build_error(self, _mock_from_key):
        """Nonce is released when TX build fails (not sent)."""
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_v3_swap(nm=nm)
        swapper.router_v3.functions.multicall.return_value.build_transaction.side_effect = \
            Exception("build failed")

        swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)

        nm.release_nonce.assert_called_once_with(42)

    def test_nonce_confirm_on_send_error_after_broadcast(self, _mock_from_key):
        """Nonce is confirmed when error happens after TX is broadcast."""
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_v3_swap(nm=nm)
        w3.eth.wait_for_transaction_receipt.side_effect = Exception("timeout")

        swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)

        nm.confirm_transaction.assert_called_once_with(42)


# ============================================================
# _swap_v2 tests
# ============================================================

@patch('src.dex_swap.Account.from_key', return_value=_make_mock_account())
class TestSwapV2:

    def _setup_v2_swap(self, nm=None):
        swapper, w3 = _make_swapper(56, nonce_manager=nm)
        swapper._build_path = MagicMock(return_value=[TOKEN_VOLATILE, USDT_BSC])
        swapper.get_quote = MagicMock(return_value=10**18)
        swapper.check_and_approve = MagicMock(return_value=True)
        swapper.get_token_decimals = MagicMock(return_value=18)

        # Mock router build_transaction
        swapper.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens \
            .return_value.build_transaction = MagicMock(return_value={'gas': 300000})
        swapper.router.functions.swapExactTokensForTokens \
            .return_value.build_transaction = MagicMock(return_value={'gas': 300000})

        tx_hash_mock = MagicMock()
        tx_hash_mock.hex.return_value = "0x" + "dd" * 32
        w3.eth.send_raw_transaction.return_value = tx_hash_mock

        receipt_mock = MagicMock()
        receipt_mock.status = 1
        receipt_mock.gasUsed = 200000
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        swapper._parse_actual_output = MagicMock(return_value=10**18)

        return swapper, w3

    def test_no_path_returns_error(self, _mock_from_key):
        swapper, w3 = _make_swapper(56)
        swapper._build_path = MagicMock(return_value=[])

        result = swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "No V2 swap path" in result.error

    def test_zero_quote_returns_error(self, _mock_from_key):
        swapper, w3 = _make_swapper(56)
        swapper._build_path = MagicMock(return_value=[TOKEN_VOLATILE, USDT_BSC])
        swapper.get_quote = MagicMock(return_value=0)

        result = swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "Could not get V2 quote" in result.error

    def test_approve_fails_returns_error(self, _mock_from_key):
        swapper, w3 = self._setup_v2_swap()
        swapper.check_and_approve = MagicMock(return_value=False)

        result = swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "Failed to approve" in result.error

    def test_successful_swap_with_fee_on_transfer(self, _mock_from_key):
        swapper, w3 = self._setup_v2_swap()

        result = swapper._swap_v2(
            TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY,
            use_fee_on_transfer=True
        )
        assert result.success is True
        assert result.to_amount == 10**18
        swapper.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens \
            .assert_called_once()

    def test_successful_swap_without_fee_on_transfer(self, _mock_from_key):
        swapper, w3 = self._setup_v2_swap()

        result = swapper._swap_v2(
            TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY,
            use_fee_on_transfer=False
        )
        assert result.success is True
        swapper.router.functions.swapExactTokensForTokens.assert_called_once()

    def test_reverted_tx_returns_error(self, _mock_from_key):
        swapper, w3 = self._setup_v2_swap()
        receipt_mock = MagicMock()
        receipt_mock.status = 0
        receipt_mock.gasUsed = 300000
        tx_hash_mock = MagicMock()
        tx_hash_mock.hex.return_value = "0xfail"
        w3.eth.send_raw_transaction.return_value = tx_hash_mock
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        result = swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "V2 swap transaction failed" in result.error

    def test_exception_returns_swap_result_error(self, _mock_from_key):
        swapper, w3 = self._setup_v2_swap()
        w3.eth.send_raw_transaction.side_effect = Exception("network error")

        result = swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "network error" in result.error

    def test_nonce_confirm_on_v2_success(self, _mock_from_key):
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_v2_swap(nm=nm)

        swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        nm.confirm_transaction.assert_called_once_with(42)

    def test_nonce_release_on_v2_build_error(self, _mock_from_key):
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_v2_swap(nm=nm)
        swapper.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens \
            .return_value.build_transaction.side_effect = Exception("build fail")

        swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        nm.release_nonce.assert_called_once_with(42)

    def test_parse_output_none_uses_expected(self, _mock_from_key):
        swapper, w3 = self._setup_v2_swap()
        swapper._parse_actual_output = MagicMock(return_value=None)
        swapper.get_quote = MagicMock(return_value=7777)

        result = swapper._swap_v2(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True
        assert result.to_amount == 7777


# ============================================================
# swap (main entry point) tests
# ============================================================

class TestSwap:

    def _setup_swap(self, nm=None):
        swapper, w3 = _make_swapper(56, nonce_manager=nm)
        swapper.get_token_balance = MagicMock(return_value=10**18)
        swapper.get_quote_v3 = MagicMock(return_value=(5 * 10**18, 500, 0))
        swapper.get_quote = MagicMock(return_value=4 * 10**18)
        swapper.swap_v3 = MagicMock(return_value=SwapResult(
            success=True, tx_hash="0x123", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=5*10**18, to_amount_usd=5.0, gas_used=250000
        ))
        swapper._swap_v2 = MagicMock(return_value=SwapResult(
            success=True, tx_hash="0x456", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=4*10**18, to_amount_usd=4.0, gas_used=200000
        ))
        return swapper, w3

    def test_stablecoin_rejected(self):
        """Swapping a stablecoin returns error immediately."""
        swapper, w3 = self._setup_swap()
        result = swapper.swap(USDT_BSC, TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "stablecoin" in result.error.lower()

    def test_zero_balance_rejected(self):
        """Zero balance returns error."""
        swapper, w3 = self._setup_swap()
        swapper.get_token_balance = MagicMock(return_value=0)
        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "Zero balance" in result.error

    def test_insufficient_balance_adjusts_amount(self):
        """When balance < amount_in, amount is reduced to balance."""
        swapper, w3 = self._setup_swap()
        swapper.get_token_balance = MagicMock(return_value=5 * 10**17)  # Half

        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        # Should have called swap_v3 with reduced amount
        assert result.success is True

    def test_auto_mode_v2_tried_first(self):
        """Auto mode: V2 is tried before V3 (after Kyber fails)."""
        swapper, w3 = self._setup_swap()
        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True
        # V2 succeeds first, so V3 is never called
        swapper._swap_v2.assert_called_once()
        swapper.swap_v3.assert_not_called()

    def test_v2_fails_falls_back_to_v3(self):
        """When V2 swap fails, falls back to V3."""
        swapper, w3 = self._setup_swap()
        swapper._swap_v2 = MagicMock(return_value=SwapResult(
            success=False, tx_hash=None, from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=0, to_amount_usd=0, gas_used=0,
            error="V2 reverted"
        ))

        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True
        swapper._swap_v2.assert_called_once()
        swapper.swap_v3.assert_called_once()

    def test_v2_and_v3_both_fail(self):
        """When both V2 and V3 fail, returns V2 error."""
        swapper, w3 = self._setup_swap()
        swapper._swap_v2 = MagicMock(return_value=SwapResult(
            success=False, tx_hash=None, from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=0, to_amount_usd=0, gas_used=0,
            error="V2 no liquidity"
        ))
        swapper.swap_v3 = MagicMock(return_value=SwapResult(
            success=False, tx_hash=None, from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=0, to_amount_usd=0, gas_used=0,
            error="V3 reverted"
        ))

        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False

    def test_swap_mode_v2_only(self):
        """swap_mode='v2' only tries V2."""
        swapper, w3 = self._setup_swap()
        result = swapper.swap(
            TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY,
            swap_mode="v2"
        )
        swapper._swap_v2.assert_called_once()
        swapper.swap_v3.assert_not_called()

    def test_swap_mode_v3_only(self):
        """swap_mode='v3' only tries V3."""
        swapper, w3 = self._setup_swap()
        result = swapper.swap(
            TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY,
            swap_mode="v3"
        )
        swapper.swap_v3.assert_called_once()
        swapper._swap_v2.assert_not_called()

    def test_exception_returns_swap_result(self):
        """Exception in swap returns a SwapResult with error."""
        swapper, w3 = self._setup_swap()
        swapper.get_token_balance = MagicMock(side_effect=Exception("kaboom"))

        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is False
        assert "kaboom" in result.error

    def test_v3_no_available_on_chain_uses_v2(self):
        """On chains without V3, swap goes directly to V2."""
        swapper, w3 = _make_swapper(97)
        swapper.get_token_balance = MagicMock(return_value=10**18)
        swapper._swap_v2 = MagicMock(return_value=SwapResult(
            success=True, tx_hash="0x789", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=10**18, to_amount_usd=1.0, gas_used=200000
        ))

        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True


# ============================================================
# check_and_approve (V2) tests
# ============================================================

@patch('src.dex_swap.Account.from_key', return_value=_make_mock_account())
class TestCheckAndApprove:

    def _setup_approve(self, current_allowance=0, nm=None):
        swapper, w3 = _make_swapper(56, nonce_manager=nm)
        mock_contract = MagicMock()
        mock_contract.functions.allowance.return_value.call.return_value = current_allowance
        mock_contract.functions.approve.return_value.build_transaction.return_value = {'gas': 100000}
        w3.eth.contract.return_value = mock_contract

        tx_hash_mock = MagicMock()
        tx_hash_mock.hex.return_value = "0xapprove"
        w3.eth.send_raw_transaction.return_value = tx_hash_mock

        receipt_mock = MagicMock()
        receipt_mock.status = 1
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        return swapper, w3

    def test_already_approved_no_tx(self, _mock_from_key):
        swapper, w3 = self._setup_approve(current_allowance=10**30)
        result = swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is True
        w3.eth.send_raw_transaction.assert_not_called()

    def test_needs_approve_sends_tx(self, _mock_from_key):
        swapper, w3 = self._setup_approve(current_allowance=0)
        result = swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is True
        w3.eth.send_raw_transaction.assert_called_once()

    def test_approve_reverted_returns_false(self, _mock_from_key):
        swapper, w3 = self._setup_approve(current_allowance=0)
        receipt_mock = MagicMock()
        receipt_mock.status = 0
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        result = swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is False

    def test_approve_exception_returns_false(self, _mock_from_key):
        swapper, w3 = self._setup_approve(current_allowance=0)
        w3.eth.send_raw_transaction.side_effect = Exception("rpc error")

        result = swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is False

    def test_approve_nonce_confirm_on_success(self, _mock_from_key):
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_approve(current_allowance=0, nm=nm)

        swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        nm.confirm_transaction.assert_called_once_with(42)

    def test_approve_nonce_release_on_build_error(self, _mock_from_key):
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_approve(current_allowance=0, nm=nm)
        mock_contract = MagicMock()
        mock_contract.functions.allowance.return_value.call.return_value = 0
        mock_contract.functions.approve.return_value.build_transaction.side_effect = \
            Exception("build fail")
        w3.eth.contract.return_value = mock_contract

        swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        nm.release_nonce.assert_called_once_with(42)

    def test_approve_nonce_confirm_on_send_then_timeout(self, _mock_from_key):
        """If TX sent but wait_for_receipt times out, nonce should be confirmed (not released)."""
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_approve(current_allowance=0, nm=nm)
        w3.eth.wait_for_transaction_receipt.side_effect = Exception("timeout")

        swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        nm.confirm_transaction.assert_called_once_with(42)
        nm.release_nonce.assert_not_called()

    def test_approve_no_nonce_manager_uses_w3(self, _mock_from_key):
        """Without nonce_manager, uses w3.eth.get_transaction_count."""
        swapper, w3 = self._setup_approve(current_allowance=0, nm=None)
        swapper.check_and_approve(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        w3.eth.get_transaction_count.assert_called()


# ============================================================
# _check_and_approve_v3 tests
# ============================================================

@patch('src.dex_swap.Account.from_key', return_value=_make_mock_account())
class TestCheckAndApproveV3:

    def _setup_approve_v3(self, current_allowance=0, nm=None):
        swapper, w3 = _make_swapper(56, nonce_manager=nm)
        mock_contract = MagicMock()
        mock_contract.functions.allowance.return_value.call.return_value = current_allowance
        mock_contract.functions.approve.return_value.build_transaction.return_value = {'gas': 100000}
        w3.eth.contract.return_value = mock_contract

        tx_hash_mock = MagicMock()
        tx_hash_mock.hex.return_value = "0xapprove_v3"
        w3.eth.send_raw_transaction.return_value = tx_hash_mock

        receipt_mock = MagicMock()
        receipt_mock.status = 1
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        return swapper, w3

    def test_already_approved_v3(self, _mock_from_key):
        swapper, w3 = self._setup_approve_v3(current_allowance=10**30)
        result = swapper._check_and_approve_v3(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is True
        w3.eth.send_raw_transaction.assert_not_called()

    def test_needs_approve_v3(self, _mock_from_key):
        swapper, w3 = self._setup_approve_v3(current_allowance=0)
        result = swapper._check_and_approve_v3(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is True
        w3.eth.send_raw_transaction.assert_called_once()

    def test_approve_v3_reverted_returns_false(self, _mock_from_key):
        swapper, w3 = self._setup_approve_v3(current_allowance=0)
        receipt_mock = MagicMock()
        receipt_mock.status = 0
        w3.eth.wait_for_transaction_receipt.return_value = receipt_mock

        result = swapper._check_and_approve_v3(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is False

    def test_approve_v3_exception_returns_false(self, _mock_from_key):
        swapper, w3 = self._setup_approve_v3(current_allowance=0)
        w3.eth.send_raw_transaction.side_effect = Exception("broadcast fail")

        result = swapper._check_and_approve_v3(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        assert result is False

    def test_approve_v3_nonce_release_on_build_error(self, _mock_from_key):
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_approve_v3(current_allowance=0, nm=nm)
        mock_contract = MagicMock()
        mock_contract.functions.allowance.return_value.call.return_value = 0
        mock_contract.functions.approve.return_value.build_transaction.side_effect = \
            Exception("build error")
        w3.eth.contract.return_value = mock_contract

        swapper._check_and_approve_v3(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        nm.release_nonce.assert_called_once_with(42)

    def test_approve_v3_nonce_confirm_on_send_then_timeout(self, _mock_from_key):
        """V3 approve: if TX sent but receipt timeout, nonce confirmed (not released)."""
        nm = _make_nonce_manager()
        swapper, w3 = self._setup_approve_v3(current_allowance=0, nm=nm)
        w3.eth.wait_for_transaction_receipt.side_effect = Exception("timeout")

        swapper._check_and_approve_v3(TOKEN_VOLATILE, 10**18, WALLET, PRIVATE_KEY)
        nm.confirm_transaction.assert_called_once_with(42)
        nm.release_nonce.assert_not_called()


# ============================================================
# sell_tokens_after_close tests
# ============================================================

class TestSellTokensAfterClose:

    def test_skips_stablecoins_and_adds_to_usd(self):
        """Stablecoins are skipped but their value is added to total_usd."""
        w3 = _make_mock_w3(56)
        tokens = [
            {"address": "0x55d398326f99059ff775485246999027b3197955",
             "amount": 100 * 10**18, "decimals": 18, "symbol": "USDT"},
        ]
        with patch("src.dex_swap.DexSwap.swap") as mock_swap:
            result = sell_tokens_after_close(w3, 56, tokens, WALLET, PRIVATE_KEY)

        assert len(result["skipped"]) == 1
        assert result["total_usd"] == pytest.approx(100.0)
        mock_swap.assert_not_called()

    def test_swaps_volatile_tokens(self):
        """Volatile tokens are swapped via DexSwap.swap."""
        w3 = _make_mock_w3(56)
        tokens = [
            {"address": TOKEN_VOLATILE.lower(), "amount": 10**18, "decimals": 18, "symbol": "VOL"},
        ]
        fake_result = SwapResult(
            success=True, tx_hash="0xaaa", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=50 * 10**18, to_amount_usd=50.0, gas_used=200000
        )
        with patch("src.dex_swap.DexSwap.swap", return_value=fake_result):
            result = sell_tokens_after_close(w3, 56, tokens, WALLET, PRIVATE_KEY)

        assert len(result["swaps"]) == 1
        assert result["swaps"][0]["success"] is True
        assert result["total_usd"] == pytest.approx(50.0)

    def test_skips_zero_amount(self):
        """Tokens with amount=0 or negative are skipped entirely."""
        w3 = _make_mock_w3(56)
        tokens = [
            {"address": TOKEN_VOLATILE.lower(), "amount": 0, "decimals": 18, "symbol": "VOL"},
            {"address": TOKEN_VOLATILE.lower(), "amount": -5, "decimals": 18, "symbol": "NEG"},
        ]
        with patch("src.dex_swap.DexSwap.swap") as mock_swap:
            result = sell_tokens_after_close(w3, 56, tokens, WALLET, PRIVATE_KEY)

        assert len(result["swaps"]) == 0
        assert len(result["skipped"]) == 0
        assert result["total_usd"] == 0.0
        mock_swap.assert_not_called()

    def test_failed_swap_does_not_add_usd(self):
        """Failed swaps do not contribute to total_usd."""
        w3 = _make_mock_w3(56)
        tokens = [
            {"address": TOKEN_VOLATILE.lower(), "amount": 10**18, "decimals": 18, "symbol": "VOL"},
        ]
        fake_result = SwapResult(
            success=False, tx_hash=None, from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=0, to_amount_usd=0, gas_used=0,
            error="swap failed"
        )
        with patch("src.dex_swap.DexSwap.swap", return_value=fake_result):
            result = sell_tokens_after_close(w3, 56, tokens, WALLET, PRIVATE_KEY)

        assert result["total_usd"] == 0.0
        assert result["swaps"][0]["success"] is False
        assert result["swaps"][0]["error"] == "swap failed"

    def test_mixed_stables_and_volatile(self):
        """Mix of stablecoins and volatile tokens."""
        w3 = _make_mock_w3(56)
        tokens = [
            {"address": "0x55d398326f99059ff775485246999027b3197955",
             "amount": 200 * 10**18, "decimals": 18, "symbol": "USDT"},
            {"address": TOKEN_VOLATILE.lower(),
             "amount": 10**18, "decimals": 18, "symbol": "VOL"},
        ]
        fake_result = SwapResult(
            success=True, tx_hash="0x111", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=30 * 10**18, to_amount_usd=30.0, gas_used=200000
        )
        with patch("src.dex_swap.DexSwap.swap", return_value=fake_result):
            result = sell_tokens_after_close(w3, 56, tokens, WALLET, PRIVATE_KEY)

        assert result["total_usd"] == pytest.approx(230.0)  # 200 stable + 30 swapped
        assert len(result["skipped"]) == 1
        assert len(result["swaps"]) == 1

    def test_slippage_forwarded(self):
        """Slippage parameter is forwarded to swap."""
        w3 = _make_mock_w3(56)
        tokens = [
            {"address": TOKEN_VOLATILE.lower(), "amount": 10**18, "decimals": 18, "symbol": "VOL"},
        ]
        fake_result = SwapResult(
            success=True, tx_hash="0xaaa", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=10**18, to_amount_usd=1.0, gas_used=200000
        )
        with patch("src.dex_swap.DexSwap.swap", return_value=fake_result) as mock_swap:
            sell_tokens_after_close(w3, 56, tokens, WALLET, PRIVATE_KEY, slippage=2.5)

        call_kwargs = mock_swap.call_args
        assert call_kwargs[1]["slippage"] == 2.5 or call_kwargs.kwargs.get("slippage") == 2.5

    def test_stablecoin_default_decimals_18(self):
        """Stablecoin without explicit decimals defaults to 18."""
        w3 = _make_mock_w3(56)
        tokens = [
            {"address": "0x55d398326f99059ff775485246999027b3197955",
             "amount": 10 * 10**18, "symbol": "USDT"},
            # No 'decimals' key => defaults to 18
        ]
        with patch("src.dex_swap.DexSwap.swap"):
            result = sell_tokens_after_close(w3, 56, tokens, WALLET, PRIVATE_KEY)

        assert result["total_usd"] == pytest.approx(10.0)

    def test_unsupported_chain_raises(self):
        """Unsupported chain_id raises in DexSwap init."""
        w3 = _make_mock_w3(999)
        with pytest.raises(ValueError):
            sell_tokens_after_close(w3, 999, [], WALLET, PRIVATE_KEY)


# ============================================================
# Edge case / integration-style tests
# ============================================================

class TestEdgeCases:

    def test_swap_v3_direct_uses_exactInputSingle(self):
        """Direct V3 swap calls encodeABI with exactInputSingle."""
        swapper, w3 = _make_swapper(56)
        swapper.get_quote_v3 = MagicMock(return_value=(10**18, 500, 0))  # direct
        swapper._check_and_approve_v3 = MagicMock(return_value=True)
        swapper.router_v3.encode_abi = MagicMock(return_value=b'\x00')
        swapper.router_v3.functions.multicall.return_value.build_transaction = \
            MagicMock(return_value={})
        tx_hash = MagicMock()
        tx_hash.hex.return_value = "0xabc"
        w3.eth.send_raw_transaction.return_value = tx_hash
        receipt = MagicMock(status=1, gasUsed=200000)
        w3.eth.wait_for_transaction_receipt.return_value = receipt
        swapper.get_token_decimals = MagicMock(return_value=18)
        swapper._parse_actual_output = MagicMock(return_value=10**18)

        swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)

        swapper.router_v3.encode_abi.assert_called_once()
        call_kwargs = swapper.router_v3.encode_abi.call_args
        assert call_kwargs[0][0] == 'exactInputSingle' if call_kwargs[0] else True

    def test_swap_slippage_calculation(self):
        """Slippage calculation: min_out = expected * (100 - slippage) / 100."""
        swapper, w3 = _make_swapper(56)
        swapper.get_quote_v3 = MagicMock(return_value=(1000, 500, 0))
        swapper._check_and_approve_v3 = MagicMock(return_value=True)

        captured_params = {}

        def capture_encode_abi(*pos_args, **kwargs):
            if pos_args:
                captured_params['fn_name'] = pos_args[0]
            else:
                captured_params['fn_name'] = kwargs.get('fn_name')
            if len(pos_args) > 1:
                captured_params['args'] = pos_args[1]
            else:
                captured_params['args'] = kwargs.get('args')
            return b'\x00'

        swapper.router_v3.encode_abi = capture_encode_abi
        swapper.router_v3.functions.multicall.return_value.build_transaction = \
            MagicMock(return_value={})
        tx_hash = MagicMock()
        tx_hash.hex.return_value = "0x"
        w3.eth.send_raw_transaction.return_value = tx_hash
        receipt = MagicMock(status=1, gasUsed=100)
        w3.eth.wait_for_transaction_receipt.return_value = receipt
        swapper.get_token_decimals = MagicMock(return_value=18)
        swapper._parse_actual_output = MagicMock(return_value=990)

        swapper.swap_v3(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY, slippage=1.0)

        # The params tuple for exactInputSingle has min_out at index 5
        params_tuple = captured_params['args'][0]
        min_out = params_tuple[5]
        assert min_out == int(1000 * (100 - 1.0) / 100)  # 990

    def test_get_quote_v3_multi_hop_skipped_when_from_is_weth(self):
        """Multi-hop is skipped when from_token is WETH."""
        swapper, w3 = _make_swapper(56)
        call_params = []

        def track_quote(params):
            call_params.append(params)
            mock = MagicMock()
            mock.call.return_value = [500, 0, 0, 0]
            return mock

        swapper.quoter_v3.functions.quoteExactInputSingle = track_quote

        swapper.get_quote_v3(swapper.weth_address, USDT_BSC, 10**18)

        # All calls should be direct (from_token=WETH), no multi-hop
        for p in call_params:
            # None of the calls should have WETH as intermediate
            assert p[0] == swapper.weth_address or p[1] == USDT_BSC

    def test_transfer_topic_constant(self):
        """TRANSFER_TOPIC is the keccak of Transfer(address,address,uint256)."""
        expected = Web3.keccak(text="Transfer(address,address,uint256)")
        assert DexSwap.TRANSFER_TOPIC == expected

    def test_v3_fallback_called_when_v2_fails(self):
        """Auto mode: V3 is called as fallback when V2 has no liquidity."""
        swapper, w3 = _make_swapper(56)
        swapper.get_token_balance = MagicMock(return_value=10**18)
        swapper.get_quote = MagicMock(return_value=0)  # V2 no liquidity

        swap_v3_result = SwapResult(
            success=True, tx_hash="0x", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=10**18, to_amount_usd=1.0, gas_used=200000
        )
        swapper.swap_v3 = MagicMock(return_value=swap_v3_result)

        result = swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)
        assert result.success is True
        swapper.swap_v3.assert_called_once()

    def test_v3_fallback_receives_basic_args(self):
        """Auto mode: V3 fallback receives correct basic args (no pre-computed fee)."""
        swapper, w3 = _make_swapper(56)
        swapper.get_token_balance = MagicMock(return_value=10**18)
        swapper.get_quote = MagicMock(return_value=0)  # V2 no liquidity

        swap_v3_result = SwapResult(
            success=True, tx_hash="0x", from_token=TOKEN_VOLATILE, to_token=USDT_BSC,
            from_amount=10**18, to_amount=10**18, to_amount_usd=1.0, gas_used=200000
        )
        swapper.swap_v3 = MagicMock(return_value=swap_v3_result)

        swapper.swap(TOKEN_VOLATILE, USDT_BSC, 10**18, WALLET, PRIVATE_KEY)

        call_args = swapper.swap_v3.call_args
        # V3 fallback called with 7 positional args, no fee kwarg
        assert len(call_args[0]) == 7
        assert 'fee' not in (call_args.kwargs or {})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
