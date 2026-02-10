"""
Tests for config.py module.

Covers all dataclasses, data constants, and helper functions:
- ChainConfig, TokenConfig, V3DexConfig dataclasses
- Chain configurations (BNB_CHAIN, BNB_TESTNET, ETHEREUM, BASE)
- Token dictionaries (TOKENS_BNB, TOKENS_BASE)
- FEE_TIERS, TICK_SPACING, V3_DEXES
- get_chain_config(), get_token(), get_v3_dex_config(), detect_v3_dex_by_pool()
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock

from config import (
    # Dataclasses
    ChainConfig,
    TokenConfig,
    V3DexConfig,
    # Chain configs
    BNB_CHAIN,
    BNB_TESTNET,
    ETHEREUM,
    BASE,
    # Token dicts
    TOKENS_BNB,
    TOKENS_BASE,
    # Fee / tick data
    FEE_TIERS,
    TICK_SPACING,
    # V3 DEX data
    V3_DEXES,
    UNISWAP_V3_BSC,
    PANCAKESWAP_V3_BSC,
    # Default settings
    DEFAULT_SLIPPAGE,
    DEFAULT_DEADLINE_MINUTES,
    DEFAULT_GAS_LIMIT_MINT,
    DEFAULT_GAS_LIMIT_MULTICALL,
    # Helper functions
    get_chain_config,
    get_token,
    get_v3_dex_config,
    detect_v3_dex_by_pool,
)


# ============================================================
# Dataclass tests
# ============================================================

class TestChainConfig:
    """Tests for ChainConfig dataclass."""

    def test_create_chain_config(self):
        cfg = ChainConfig(
            chain_id=999,
            rpc_url="https://example.com",
            explorer_url="https://explorer.example.com",
            native_token="TEST",
            position_manager="0xABC",
            multicall3="0xDEF",
            pool_factory="0x123",
        )
        assert cfg.chain_id == 999
        assert cfg.rpc_url == "https://example.com"
        assert cfg.explorer_url == "https://explorer.example.com"
        assert cfg.native_token == "TEST"
        assert cfg.position_manager == "0xABC"
        assert cfg.multicall3 == "0xDEF"
        assert cfg.pool_factory == "0x123"

    def test_pool_factory_defaults_to_empty_string(self):
        cfg = ChainConfig(
            chain_id=1,
            rpc_url="",
            explorer_url="",
            native_token="ETH",
            position_manager="0x0",
            multicall3="0x0",
        )
        assert cfg.pool_factory == ""


class TestTokenConfig:
    """Tests for TokenConfig dataclass."""

    def test_create_token_config(self):
        token = TokenConfig(address="0xABC", symbol="TKN", decimals=18)
        assert token.address == "0xABC"
        assert token.symbol == "TKN"
        assert token.decimals == 18


class TestV3DexConfig:
    """Tests for V3DexConfig dataclass."""

    def test_create_v3_dex_config(self):
        dex = V3DexConfig(
            name="TestDEX",
            position_manager="0xPM",
            pool_factory="0xPF",
            fee_tiers=[100, 500],
        )
        assert dex.name == "TestDEX"
        assert dex.position_manager == "0xPM"
        assert dex.pool_factory == "0xPF"
        assert dex.fee_tiers == [100, 500]


# ============================================================
# Chain configuration data validation
# ============================================================

class TestChainConfigData:
    """Validate the predefined chain configurations."""

    def test_bnb_chain_id(self):
        assert BNB_CHAIN.chain_id == 56

    def test_bnb_chain_has_position_manager(self):
        assert BNB_CHAIN.position_manager
        assert BNB_CHAIN.position_manager.startswith("0x")

    def test_bnb_chain_has_multicall3(self):
        assert BNB_CHAIN.multicall3
        assert BNB_CHAIN.multicall3.startswith("0x")

    def test_bnb_chain_has_pool_factory(self):
        assert BNB_CHAIN.pool_factory
        assert BNB_CHAIN.pool_factory.startswith("0x")

    def test_bnb_chain_native_token(self):
        assert BNB_CHAIN.native_token == "BNB"

    def test_bnb_testnet_chain_id(self):
        assert BNB_TESTNET.chain_id == 97

    def test_bnb_testnet_has_pool_factory(self):
        assert BNB_TESTNET.pool_factory
        assert BNB_TESTNET.pool_factory.startswith("0x")

    def test_ethereum_chain_id(self):
        assert ETHEREUM.chain_id == 1

    def test_ethereum_native_token(self):
        assert ETHEREUM.native_token == "ETH"

    def test_ethereum_has_position_manager(self):
        assert ETHEREUM.position_manager
        assert ETHEREUM.position_manager.startswith("0x")

    def test_base_chain_id(self):
        assert BASE.chain_id == 8453

    def test_base_native_token(self):
        assert BASE.native_token == "ETH"

    def test_base_has_position_manager(self):
        assert BASE.position_manager
        assert BASE.position_manager.startswith("0x")

    def test_base_has_pool_factory(self):
        assert BASE.pool_factory
        assert BASE.pool_factory.startswith("0x")

    def test_all_chains_have_rpc_url(self):
        for chain in [BNB_CHAIN, BNB_TESTNET, ETHEREUM, BASE]:
            assert chain.rpc_url, f"Chain {chain.chain_id} missing rpc_url"
            assert chain.rpc_url.startswith("https://"), (
                f"Chain {chain.chain_id} rpc_url should start with https://"
            )

    def test_all_chains_have_explorer_url(self):
        for chain in [BNB_CHAIN, BNB_TESTNET, ETHEREUM, BASE]:
            assert chain.explorer_url, f"Chain {chain.chain_id} missing explorer_url"
            assert chain.explorer_url.startswith("https://"), (
                f"Chain {chain.chain_id} explorer_url should start with https://"
            )


# ============================================================
# Token data validation
# ============================================================

class TestTokensBNB:
    """Validate TOKENS_BNB dictionary."""

    EXPECTED_SYMBOLS = ["WBNB", "USDT", "USDC", "BUSD", "CAKE", "ETH", "BTCB"]

    def test_all_expected_tokens_present(self):
        for symbol in self.EXPECTED_SYMBOLS:
            assert symbol in TOKENS_BNB, f"Missing token: {symbol}"

    def test_no_unexpected_tokens(self):
        for key in TOKENS_BNB:
            assert key in self.EXPECTED_SYMBOLS, f"Unexpected token: {key}"

    def test_all_addresses_start_with_0x(self):
        for symbol, token in TOKENS_BNB.items():
            assert token.address.startswith("0x"), (
                f"{symbol} address does not start with 0x"
            )

    def test_symbol_matches_key(self):
        for key, token in TOKENS_BNB.items():
            assert token.symbol == key, (
                f"Key '{key}' does not match symbol '{token.symbol}'"
            )

    def test_all_decimals_positive(self):
        for symbol, token in TOKENS_BNB.items():
            assert token.decimals > 0, f"{symbol} has non-positive decimals"

    def test_all_bnb_tokens_have_18_decimals(self):
        """All BSC tokens in this config use 18 decimals."""
        for symbol, token in TOKENS_BNB.items():
            assert token.decimals == 18, (
                f"{symbol} expected 18 decimals, got {token.decimals}"
            )

    def test_addresses_are_unique(self):
        addresses = [t.address.lower() for t in TOKENS_BNB.values()]
        assert len(addresses) == len(set(addresses)), "Duplicate token addresses in TOKENS_BNB"


class TestTokensBASE:
    """Validate TOKENS_BASE dictionary."""

    EXPECTED_SYMBOLS = ["WETH", "USDC", "USDbC", "DAI", "cbETH"]

    def test_all_expected_tokens_present(self):
        for symbol in self.EXPECTED_SYMBOLS:
            assert symbol in TOKENS_BASE, f"Missing token: {symbol}"

    def test_no_unexpected_tokens(self):
        for key in TOKENS_BASE:
            assert key in self.EXPECTED_SYMBOLS, f"Unexpected token: {key}"

    def test_all_addresses_start_with_0x(self):
        for symbol, token in TOKENS_BASE.items():
            assert token.address.startswith("0x"), (
                f"{symbol} address does not start with 0x"
            )

    def test_symbol_matches_key(self):
        for key, token in TOKENS_BASE.items():
            assert token.symbol == key, (
                f"Key '{key}' does not match symbol '{token.symbol}'"
            )

    def test_all_decimals_positive(self):
        for symbol, token in TOKENS_BASE.items():
            assert token.decimals > 0, f"{symbol} has non-positive decimals"

    def test_usdc_has_6_decimals(self):
        assert TOKENS_BASE["USDC"].decimals == 6

    def test_usdbc_has_6_decimals(self):
        assert TOKENS_BASE["USDbC"].decimals == 6

    def test_weth_has_18_decimals(self):
        assert TOKENS_BASE["WETH"].decimals == 18

    def test_dai_has_18_decimals(self):
        assert TOKENS_BASE["DAI"].decimals == 18

    def test_cbeth_has_18_decimals(self):
        assert TOKENS_BASE["cbETH"].decimals == 18

    def test_addresses_are_unique(self):
        addresses = [t.address.lower() for t in TOKENS_BASE.values()]
        assert len(addresses) == len(set(addresses)), "Duplicate token addresses in TOKENS_BASE"


# ============================================================
# FEE_TIERS and TICK_SPACING data validation
# ============================================================

class TestFeeTiers:
    """Validate FEE_TIERS dictionary."""

    def test_has_lowest(self):
        assert "LOWEST" in FEE_TIERS
        assert FEE_TIERS["LOWEST"] == 100

    def test_has_low(self):
        assert "LOW" in FEE_TIERS
        assert FEE_TIERS["LOW"] == 500

    def test_has_medium(self):
        assert "MEDIUM" in FEE_TIERS
        assert FEE_TIERS["MEDIUM"] == 2500

    def test_has_high(self):
        assert "HIGH" in FEE_TIERS
        assert FEE_TIERS["HIGH"] == 10000

    def test_exactly_four_tiers(self):
        assert len(FEE_TIERS) == 4

    def test_all_values_positive_ints(self):
        for name, fee in FEE_TIERS.items():
            assert isinstance(fee, int), f"{name} fee is not int"
            assert fee > 0, f"{name} fee is not positive"


class TestTickSpacing:
    """Validate TICK_SPACING dictionary."""

    def test_tick_spacing_100(self):
        assert TICK_SPACING[100] == 1

    def test_tick_spacing_500(self):
        assert TICK_SPACING[500] == 10

    def test_tick_spacing_2500(self):
        assert TICK_SPACING[2500] == 50

    def test_tick_spacing_3000(self):
        assert TICK_SPACING[3000] == 60

    def test_tick_spacing_10000(self):
        assert TICK_SPACING[10000] == 200

    def test_all_fee_tiers_have_tick_spacing(self):
        """Every fee value in FEE_TIERS should map to a tick spacing."""
        for name, fee in FEE_TIERS.items():
            assert fee in TICK_SPACING, (
                f"FEE_TIERS['{name}'] = {fee} has no TICK_SPACING entry"
            )

    def test_matches_fee_to_tick_spacing_in_ticks_module(self):
        """TICK_SPACING in config must match FEE_TO_TICK_SPACING in ticks.py."""
        from src.math.ticks import FEE_TO_TICK_SPACING
        for fee, spacing in TICK_SPACING.items():
            assert fee in FEE_TO_TICK_SPACING, (
                f"Fee {fee} from config.TICK_SPACING not in ticks.FEE_TO_TICK_SPACING"
            )
            assert FEE_TO_TICK_SPACING[fee] == spacing, (
                f"Mismatch for fee {fee}: config={spacing}, "
                f"ticks={FEE_TO_TICK_SPACING[fee]}"
            )

    def test_all_spacings_positive(self):
        for fee, spacing in TICK_SPACING.items():
            assert spacing > 0, f"Non-positive spacing for fee {fee}"


# ============================================================
# V3_DEXES data validation
# ============================================================

class TestV3Dexes:
    """Validate the V3_DEXES mapping."""

    def test_bsc_has_entries(self):
        assert 56 in V3_DEXES
        assert "uniswap" in V3_DEXES[56]
        assert "pancakeswap" in V3_DEXES[56]

    def test_ethereum_has_uniswap(self):
        assert 1 in V3_DEXES
        assert "uniswap" in V3_DEXES[1]

    def test_base_has_uniswap(self):
        assert 8453 in V3_DEXES
        assert "uniswap" in V3_DEXES[8453]

    def test_uniswap_v3_bsc_is_correct_object(self):
        assert V3_DEXES[56]["uniswap"] is UNISWAP_V3_BSC

    def test_pancakeswap_v3_bsc_is_correct_object(self):
        assert V3_DEXES[56]["pancakeswap"] is PANCAKESWAP_V3_BSC

    def test_all_entries_are_v3dex_config(self):
        for chain_id, dexes in V3_DEXES.items():
            for name, dex in dexes.items():
                assert isinstance(dex, V3DexConfig), (
                    f"V3_DEXES[{chain_id}]['{name}'] is not V3DexConfig"
                )

    def test_all_dexes_have_nonempty_fields(self):
        for chain_id, dexes in V3_DEXES.items():
            for name, dex in dexes.items():
                assert dex.name, f"V3_DEXES[{chain_id}]['{name}'] missing name"
                assert dex.position_manager.startswith("0x"), (
                    f"V3_DEXES[{chain_id}]['{name}'] bad position_manager"
                )
                assert dex.pool_factory.startswith("0x"), (
                    f"V3_DEXES[{chain_id}]['{name}'] bad pool_factory"
                )
                assert len(dex.fee_tiers) > 0, (
                    f"V3_DEXES[{chain_id}]['{name}'] has no fee tiers"
                )

    def test_uniswap_bsc_fee_tiers(self):
        assert UNISWAP_V3_BSC.fee_tiers == [100, 500, 3000, 10000]

    def test_pancakeswap_bsc_fee_tiers(self):
        assert PANCAKESWAP_V3_BSC.fee_tiers == [100, 500, 2500, 10000]

    def test_bsc_uniswap_and_pancakeswap_have_different_factories(self):
        assert UNISWAP_V3_BSC.pool_factory != PANCAKESWAP_V3_BSC.pool_factory

    def test_bsc_uniswap_and_pancakeswap_have_different_position_managers(self):
        assert UNISWAP_V3_BSC.position_manager != PANCAKESWAP_V3_BSC.position_manager


# ============================================================
# Default settings validation
# ============================================================

class TestDefaultSettings:
    """Validate default configuration values."""

    def test_default_slippage(self):
        assert DEFAULT_SLIPPAGE == 0.5

    def test_default_deadline_minutes(self):
        assert DEFAULT_DEADLINE_MINUTES == 60

    def test_default_gas_limit_mint(self):
        assert DEFAULT_GAS_LIMIT_MINT == 500000

    def test_default_gas_limit_multicall(self):
        assert DEFAULT_GAS_LIMIT_MULTICALL == 2000000


# ============================================================
# get_chain_config() tests
# ============================================================

class TestGetChainConfig:
    """Tests for get_chain_config() function."""

    def test_returns_bnb_chain_for_56(self):
        result = get_chain_config(56)
        assert result is BNB_CHAIN

    def test_returns_bnb_testnet_for_97(self):
        result = get_chain_config(97)
        assert result is BNB_TESTNET

    def test_returns_ethereum_for_1(self):
        result = get_chain_config(1)
        assert result is ETHEREUM

    def test_returns_base_for_8453(self):
        result = get_chain_config(8453)
        assert result is BASE

    def test_unknown_chain_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown chain_id"):
            get_chain_config(999)

    def test_unknown_chain_zero_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown chain_id"):
            get_chain_config(0)

    def test_unknown_chain_negative_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown chain_id"):
            get_chain_config(-1)

    def test_return_type_is_chain_config(self):
        result = get_chain_config(56)
        assert isinstance(result, ChainConfig)


# ============================================================
# get_token() tests
# ============================================================

class TestGetToken:
    """Tests for get_token() function."""

    # --- BSC tokens (default chain_id=56) ---

    def test_get_wbnb(self):
        token = get_token("WBNB")
        assert token.symbol == "WBNB"
        assert token.decimals == 18

    def test_get_usdt(self):
        token = get_token("USDT")
        assert token.symbol == "USDT"

    def test_get_usdc_bsc(self):
        token = get_token("USDC")
        assert token.symbol == "USDC"
        assert token.decimals == 18

    def test_get_busd(self):
        token = get_token("BUSD")
        assert token.symbol == "BUSD"

    def test_get_cake(self):
        token = get_token("CAKE")
        assert token.symbol == "CAKE"

    def test_get_eth_bsc(self):
        token = get_token("ETH")
        assert token.symbol == "ETH"

    def test_get_btcb(self):
        token = get_token("BTCB")
        assert token.symbol == "BTCB"

    def test_bsc_explicit_chain_id(self):
        token = get_token("WBNB", chain_id=56)
        assert token.symbol == "WBNB"

    def test_bsc_returns_same_object(self):
        """get_token returns the same object from TOKENS_BNB."""
        token = get_token("WBNB", chain_id=56)
        assert token is TOKENS_BNB["WBNB"]

    # --- BASE tokens (chain_id=8453) ---

    def test_get_weth_base(self):
        token = get_token("WETH", chain_id=8453)
        assert token.symbol == "WETH"
        assert token.decimals == 18

    def test_get_usdc_base(self):
        token = get_token("USDC", chain_id=8453)
        assert token.symbol == "USDC"
        assert token.decimals == 6

    def test_get_usdbc_base(self):
        token = get_token("USDbC", chain_id=8453)
        assert token.symbol == "USDbC"
        assert token.decimals == 6

    def test_get_dai_base(self):
        token = get_token("DAI", chain_id=8453)
        assert token.symbol == "DAI"
        assert token.decimals == 18

    def test_get_cbeth_base(self):
        token = get_token("cbETH", chain_id=8453)
        assert token.symbol == "cbETH"
        assert token.decimals == 18

    def test_base_returns_same_object(self):
        """get_token returns the same object from TOKENS_BASE."""
        token = get_token("USDC", chain_id=8453)
        assert token is TOKENS_BASE["USDC"]

    # --- Error cases ---

    def test_unknown_symbol_on_bsc_raises(self):
        with pytest.raises(ValueError, match="Unknown token"):
            get_token("NONEXISTENT")

    def test_unknown_symbol_on_base_raises(self):
        with pytest.raises(ValueError, match="Unknown token"):
            get_token("NONEXISTENT", chain_id=8453)

    def test_unknown_chain_raises(self):
        with pytest.raises(ValueError, match="Tokens not configured for chain_id"):
            get_token("WBNB", chain_id=999)

    def test_bsc_token_not_found_on_base(self):
        """WBNB exists on BSC but not on BASE."""
        with pytest.raises(ValueError, match="Unknown token"):
            get_token("WBNB", chain_id=8453)

    def test_base_token_not_found_on_bsc(self):
        """WETH exists on BASE but not on BSC (BSC has 'ETH' instead)."""
        with pytest.raises(ValueError, match="Unknown token"):
            get_token("WETH", chain_id=56)

    def test_return_type_is_token_config(self):
        token = get_token("WBNB")
        assert isinstance(token, TokenConfig)

    def test_chain_id_1_raises(self):
        """Ethereum chain_id=1 has no token dict configured."""
        with pytest.raises(ValueError, match="Tokens not configured for chain_id"):
            get_token("ETH", chain_id=1)


# ============================================================
# get_v3_dex_config() tests
# ============================================================

class TestGetV3DexConfig:
    """Tests for get_v3_dex_config() function."""

    # --- Exact key lookups ---

    def test_uniswap_bsc(self):
        result = get_v3_dex_config("uniswap", chain_id=56)
        assert result is UNISWAP_V3_BSC

    def test_pancakeswap_bsc(self):
        result = get_v3_dex_config("pancakeswap", chain_id=56)
        assert result is PANCAKESWAP_V3_BSC

    # --- Case-insensitive / substring matching ---

    def test_uniswap_case_insensitive(self):
        result = get_v3_dex_config("Uniswap", chain_id=56)
        assert result is UNISWAP_V3_BSC

    def test_uniswap_v3_full_name(self):
        result = get_v3_dex_config("Uniswap V3", chain_id=56)
        assert result is UNISWAP_V3_BSC

    def test_pancakeswap_full_name(self):
        result = get_v3_dex_config("PancakeSwap", chain_id=56)
        assert result is PANCAKESWAP_V3_BSC

    def test_pancakeswap_v3_full_name(self):
        result = get_v3_dex_config("PancakeSwap V3", chain_id=56)
        assert result is PANCAKESWAP_V3_BSC

    def test_pancake_lowercase(self):
        result = get_v3_dex_config("pancake", chain_id=56)
        assert result is PANCAKESWAP_V3_BSC

    def test_uniswap_uppercase(self):
        result = get_v3_dex_config("UNISWAP", chain_id=56)
        assert result is UNISWAP_V3_BSC

    # --- Other chains ---

    def test_uniswap_on_ethereum(self):
        result = get_v3_dex_config("uniswap", chain_id=1)
        assert isinstance(result, V3DexConfig)
        assert result.name == "Uniswap V3"

    def test_uniswap_on_base(self):
        result = get_v3_dex_config("uniswap", chain_id=8453)
        assert isinstance(result, V3DexConfig)
        assert result.name == "Uniswap V3"

    # --- Error cases ---

    def test_unknown_dex_raises(self):
        with pytest.raises(ValueError, match="Unknown DEX"):
            get_v3_dex_config("sushiswap", chain_id=56)

    def test_chain_without_v3_dexes_raises(self):
        with pytest.raises(ValueError, match="No V3 DEXes configured for chain_id"):
            get_v3_dex_config("uniswap", chain_id=97)

    def test_unknown_chain_raises(self):
        with pytest.raises(ValueError, match="No V3 DEXes configured for chain_id"):
            get_v3_dex_config("uniswap", chain_id=999)

    def test_pancakeswap_not_on_ethereum_returns_none(self):
        """Ethereum only has uniswap; pancakeswap key does not exist so .get() returns None."""
        result = get_v3_dex_config("pancakeswap", chain_id=1)
        assert result is None

    def test_return_type(self):
        result = get_v3_dex_config("uniswap", chain_id=56)
        assert isinstance(result, V3DexConfig)

    def test_default_chain_is_56(self):
        """Default chain_id parameter is 56 (BSC)."""
        result = get_v3_dex_config("uniswap")
        assert result is UNISWAP_V3_BSC


# ============================================================
# detect_v3_dex_by_pool() tests (mocked Web3)
# ============================================================

class TestDetectV3DexByPool:
    """Tests for detect_v3_dex_by_pool() function using mocked Web3."""

    def _make_mock_w3(self, factory_return_value: str):
        """Helper: build a Mock w3 where pool.functions.factory().call() returns
        the given factory address."""
        w3 = MagicMock()
        # Web3.to_checksum_address -- just pass through
        w3.to_checksum_address = lambda addr: addr

        # Build nested mock:  w3.eth.contract(address=..., abi=...).functions.factory().call()
        mock_factory_fn = MagicMock()
        mock_factory_fn.call.return_value = factory_return_value

        mock_functions = MagicMock()
        mock_functions.factory.return_value = mock_factory_fn

        mock_contract = MagicMock()
        mock_contract.functions = mock_functions

        w3.eth.contract.return_value = mock_contract
        return w3

    @patch("web3.Web3")
    def test_detects_pancakeswap_by_factory(self, MockWeb3Class):
        """If pool's factory matches PancakeSwap, return PANCAKESWAP_V3_BSC."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        factory_addr = PANCAKESWAP_V3_BSC.pool_factory
        w3 = self._make_mock_w3(factory_addr)
        pool_address = "0x0000000000000000000000000000000000001234"

        result = detect_v3_dex_by_pool(w3, pool_address, chain_id=56)
        assert result is PANCAKESWAP_V3_BSC

    @patch("web3.Web3")
    def test_detects_uniswap_by_factory(self, MockWeb3Class):
        """If pool's factory matches Uniswap, return UNISWAP_V3_BSC."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        factory_addr = UNISWAP_V3_BSC.pool_factory
        w3 = self._make_mock_w3(factory_addr)
        pool_address = "0x0000000000000000000000000000000000005678"

        result = detect_v3_dex_by_pool(w3, pool_address, chain_id=56)
        assert result is UNISWAP_V3_BSC

    @patch("web3.Web3")
    def test_unknown_factory_raises(self, MockWeb3Class):
        """If factory address doesn't match any known DEX, raise ValueError."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        unknown_factory = "0x0000000000000000000000000000000000000000"
        w3 = self._make_mock_w3(unknown_factory)
        pool_address = "0x0000000000000000000000000000000000009999"

        with pytest.raises(ValueError, match="Failed to detect V3 DEX"):
            detect_v3_dex_by_pool(w3, pool_address, chain_id=56)

    @patch("web3.Web3")
    def test_unsupported_chain_raises(self, MockWeb3Class):
        """Chain ID without V3 DEXes should raise ValueError."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        w3 = self._make_mock_w3("0x0")
        with pytest.raises(ValueError, match="No V3 DEXes configured for chain_id"):
            detect_v3_dex_by_pool(w3, "0xABC", chain_id=999)

    @patch("web3.Web3")
    def test_rpc_error_raises_value_error(self, MockWeb3Class):
        """If the RPC call fails, should raise ValueError wrapping the original error."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        w3 = MagicMock()

        mock_contract = MagicMock()
        mock_contract.functions.factory.return_value.call.side_effect = Exception(
            "connection timeout"
        )
        w3.eth.contract.return_value = mock_contract

        with pytest.raises(ValueError, match="Failed to detect V3 DEX"):
            detect_v3_dex_by_pool(w3, "0xABC", chain_id=56)

    @patch("web3.Web3")
    def test_default_chain_id_is_56(self, MockWeb3Class):
        """Default chain_id parameter is 56."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        factory_addr = PANCAKESWAP_V3_BSC.pool_factory
        w3 = self._make_mock_w3(factory_addr)

        # Call without explicit chain_id
        result = detect_v3_dex_by_pool(w3, "0x1234")
        assert result is PANCAKESWAP_V3_BSC

    @patch("web3.Web3")
    def test_calls_contract_with_pool_address(self, MockWeb3Class):
        """Ensure the function creates a contract with the pool address."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        factory_addr = UNISWAP_V3_BSC.pool_factory
        w3 = self._make_mock_w3(factory_addr)
        pool_address = "0xABCDEF1234567890ABCDEF1234567890ABCDEF12"

        detect_v3_dex_by_pool(w3, pool_address, chain_id=56)

        # Verify w3.eth.contract was called with the pool address
        w3.eth.contract.assert_called_once()
        call_kwargs = w3.eth.contract.call_args
        assert call_kwargs[1]["address"] == pool_address or call_kwargs[0][0] == pool_address

    @patch("web3.Web3")
    def test_detect_on_base_chain(self, MockWeb3Class):
        """Detection should work on BASE (chain_id=8453)."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        base_uniswap_factory = V3_DEXES[8453]["uniswap"].pool_factory
        w3 = self._make_mock_w3(base_uniswap_factory)

        result = detect_v3_dex_by_pool(w3, "0x1234", chain_id=8453)
        assert result is V3_DEXES[8453]["uniswap"]

    @patch("web3.Web3")
    def test_detect_on_ethereum(self, MockWeb3Class):
        """Detection should work on Ethereum (chain_id=1)."""
        MockWeb3Class.to_checksum_address = lambda addr: addr

        eth_uniswap_factory = V3_DEXES[1]["uniswap"].pool_factory
        w3 = self._make_mock_w3(eth_uniswap_factory)

        result = detect_v3_dex_by_pool(w3, "0x1234", chain_id=1)
        assert result is V3_DEXES[1]["uniswap"]


# ============================================================
# Cross-consistency checks
# ============================================================

class TestCrossConsistency:
    """Cross-checks between different config data structures."""

    def test_bnb_chain_pool_factory_matches_pancakeswap(self):
        """BNB_CHAIN.pool_factory should equal PANCAKESWAP_V3_BSC.pool_factory."""
        assert BNB_CHAIN.pool_factory == PANCAKESWAP_V3_BSC.pool_factory

    def test_bnb_chain_position_manager_matches_pancakeswap(self):
        """BNB_CHAIN.position_manager should equal PANCAKESWAP_V3_BSC.position_manager."""
        assert BNB_CHAIN.position_manager == PANCAKESWAP_V3_BSC.position_manager

    def test_ethereum_pool_factory_matches_uniswap_v3(self):
        """ETHEREUM.pool_factory should equal Uniswap V3 on Ethereum."""
        assert ETHEREUM.pool_factory == V3_DEXES[1]["uniswap"].pool_factory

    def test_ethereum_position_manager_matches_uniswap_v3(self):
        """ETHEREUM.position_manager should equal Uniswap V3 on Ethereum."""
        assert ETHEREUM.position_manager == V3_DEXES[1]["uniswap"].position_manager

    def test_base_pool_factory_matches_uniswap_v3(self):
        """BASE.pool_factory should equal Uniswap V3 on Base."""
        assert BASE.pool_factory == V3_DEXES[8453]["uniswap"].pool_factory

    def test_base_position_manager_matches_uniswap_v3(self):
        """BASE.position_manager should equal Uniswap V3 on Base."""
        assert BASE.position_manager == V3_DEXES[8453]["uniswap"].position_manager

    def test_v3_dexes_chain_ids_have_chain_configs(self):
        """Every chain in V3_DEXES should have a valid chain config."""
        for chain_id in V3_DEXES:
            config = get_chain_config(chain_id)
            assert config.chain_id == chain_id
