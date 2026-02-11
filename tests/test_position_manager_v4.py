"""
Tests for V4 PositionManager.

Тесты для V4PositionManager: кодирование действий, чтение позиций,
минт, закрытие, владение NFT.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from web3 import Web3

from src.contracts.v4.position_manager import V4PositionManager, V4Position, MintResult
from src.contracts.v4.pool_manager import PoolKey
from src.contracts.v4.constants import V4Protocol

try:
    from src.contracts.v4.abis import PancakeV4Actions, V4Actions
except ImportError:
    # Фоллбэк: определяем коды действий вручную
    class PancakeV4Actions:
        INCREASE_LIQUIDITY = 0x00
        DECREASE_LIQUIDITY = 0x01
        MINT_POSITION = 0x02
        BURN_POSITION = 0x03
        SETTLE = 0x0b
        SETTLE_ALL = 0x0c
        SETTLE_PAIR = 0x0d
        TAKE = 0x0e
        TAKE_ALL = 0x0f
        TAKE_PAIR = 0x11
        CLOSE_CURRENCY = 0x12
        CLEAR_OR_TAKE = 0x13
        SWEEP = 0x14

    class V4Actions:
        INCREASE_LIQUIDITY = 0x00
        DECREASE_LIQUIDITY = 0x01
        MINT_POSITION = 0x02
        BURN_POSITION = 0x03
        SETTLE = 0x0b
        SETTLE_PAIR = 0x0d
        TAKE_PAIR = 0x11


# Тестовые адреса (отсортированы: TOKEN_A < TOKEN_B по числовому значению)
TOKEN_A = "0x1111111111111111111111111111111111111111"
TOKEN_B = "0x9999999999999999999999999999999999999999"
HOOKS_ZERO = "0x0000000000000000000000000000000000000000"
WALLET_ADDR = "0x1234567890123456789012345678901234567890"


def _make_pool_key(
    currency0=TOKEN_A,
    currency1=TOKEN_B,
    fee=3000,
    tick_spacing=60,
    hooks=HOOKS_ZERO,
):
    """Хелпер: создать PoolKey с дефолтными значениями."""
    return PoolKey(
        currency0=currency0,
        currency1=currency1,
        fee=fee,
        tick_spacing=tick_spacing,
        hooks=hooks,
    )


def _make_pm(mock_w3, mock_account=None, protocol=V4Protocol.PANCAKESWAP):
    """
    Хелпер: создать V4PositionManager с обходом __init__.

    Позволяет задать мок Web3 и аккаунт без реальных контрактов.
    """
    with patch.object(V4PositionManager, '__init__', lambda self, *a, **kw: None):
        pm = V4PositionManager.__new__(V4PositionManager)
        pm.w3 = mock_w3
        pm.account = mock_account
        pm.protocol = protocol
        pm.chain_id = 56
        pm.position_manager_address = "0x55f4c8abA71A1e923edC303eb4fEfF14608cC226"

        if protocol == V4Protocol.PANCAKESWAP:
            pm.actions = PancakeV4Actions
        else:
            pm.actions = V4Actions

        mock_contract = MagicMock()
        pm.contract = mock_contract
        pm.nonce_manager = None
        return pm


# ============================================================
# V4Position dataclass
# ============================================================

class TestV4Position:
    """Тесты для датакласса V4Position."""

    def test_fields(self):
        """V4Position содержит все обязательные поля."""
        pk = _make_pool_key()
        pos = V4Position(
            token_id=42,
            pool_key=pk,
            tick_lower=-600,
            tick_upper=600,
            liquidity=10**18,
        )
        assert pos.token_id == 42
        assert pos.pool_key is pk
        assert pos.tick_lower == -600
        assert pos.tick_upper == 600
        assert pos.liquidity == 10**18

    def test_negative_ticks(self):
        """V4Position допускает отрицательные тики."""
        pos = V4Position(
            token_id=1,
            pool_key=_make_pool_key(),
            tick_lower=-887272,
            tick_upper=-100,
            liquidity=0,
        )
        assert pos.tick_lower < 0
        assert pos.tick_upper < 0

    def test_zero_liquidity(self):
        """V4Position с нулевой ликвидностью (закрытая позиция)."""
        pos = V4Position(
            token_id=99,
            pool_key=_make_pool_key(),
            tick_lower=-60,
            tick_upper=60,
            liquidity=0,
        )
        assert pos.liquidity == 0


# ============================================================
# MintResult dataclass
# ============================================================

class TestMintResult:
    """Тесты для датакласса MintResult."""

    def test_fields(self):
        """MintResult содержит все обязательные поля."""
        result = MintResult(
            token_id=100,
            liquidity=5 * 10**18,
            amount0=10**18,
            amount1=300 * 10**18,
            tx_hash="0xabcdef1234567890",
        )
        assert result.token_id == 100
        assert result.liquidity == 5 * 10**18
        assert result.amount0 == 10**18
        assert result.amount1 == 300 * 10**18
        assert result.tx_hash == "0xabcdef1234567890"

    def test_zero_amounts(self):
        """MintResult может иметь нулевые суммы (ещё не распарсены из логов)."""
        result = MintResult(
            token_id=1, liquidity=10**15, amount0=0, amount1=0, tx_hash="0x00"
        )
        assert result.amount0 == 0
        assert result.amount1 == 0


# ============================================================
# V4PositionManager
# ============================================================

class TestV4PositionManager:
    """Тесты для V4PositionManager."""

    @pytest.fixture
    def mock_w3(self):
        """Мок Web3 instance."""
        w3 = MagicMock(spec=Web3)
        w3.eth = MagicMock()
        w3.eth.gas_price = 5_000_000_000
        w3.eth.get_transaction_count = MagicMock(return_value=0)
        w3.eth.send_raw_transaction = MagicMock(return_value=b'\x12\x34' * 16)
        w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 1,
            'gasUsed': 300_000,
            'logs': [],
        })
        return w3

    @pytest.fixture
    def mock_account(self):
        """Мок аккаунта (LocalAccount)."""
        account = MagicMock()
        account.address = WALLET_ADDR
        account.sign_transaction = MagicMock(
            return_value=MagicMock(raw_transaction=b'signed_tx_data')
        )
        return account

    @pytest.fixture
    def pm(self, mock_w3, mock_account):
        """V4PositionManager с моками (PancakeSwap, BNB chain)."""
        return _make_pm(mock_w3, mock_account, V4Protocol.PANCAKESWAP)

    @pytest.fixture
    def pm_no_account(self, mock_w3):
        """V4PositionManager без аккаунта (только чтение)."""
        return _make_pm(mock_w3, mock_account=None)

    @pytest.fixture
    def pool_key(self):
        """Тестовый PoolKey."""
        return _make_pool_key()

    # ----------------------------------------------------------
    # get_position: tuple info
    # ----------------------------------------------------------

    def test_get_position_tuple_info(self, pm):
        """getPoolAndPositionInfo возвращает распакованный tuple с тиками."""
        pool_key_tuple = (
            Web3.to_checksum_address(TOKEN_A),
            Web3.to_checksum_address(TOKEN_B),
            3000,
            60,
            Web3.to_checksum_address(HOOKS_ZERO),
        )
        # info tuple: (hasSubscriber=False, tickLower=-600, tickUpper=600)
        info_tuple = (False, -600, 600)
        pm.contract.functions.getPoolAndPositionInfo.return_value = MagicMock(
            call=MagicMock(return_value=(pool_key_tuple, info_tuple))
        )
        pm.contract.functions.getPositionLiquidity.return_value = MagicMock(
            call=MagicMock(return_value=10**18)
        )

        pos = pm.get_position(42)

        assert isinstance(pos, V4Position)
        assert pos.token_id == 42
        assert pos.tick_lower == -600
        assert pos.tick_upper == 600
        assert pos.liquidity == 10**18
        assert pos.pool_key.currency0 == Web3.to_checksum_address(TOKEN_A)
        assert pos.pool_key.currency1 == Web3.to_checksum_address(TOKEN_B)
        assert pos.pool_key.fee == 3000
        assert pos.pool_key.tick_spacing == 60

    def test_get_position_tuple_info_two_elements(self, pm):
        """getPoolAndPositionInfo с info из 2 элементов (tickLower, tickUpper)."""
        pool_key_tuple = (
            Web3.to_checksum_address(TOKEN_A),
            Web3.to_checksum_address(TOKEN_B),
            3000,
            60,
            Web3.to_checksum_address(HOOKS_ZERO),
        )
        info_tuple = (-1200, 1200)
        pm.contract.functions.getPoolAndPositionInfo.return_value = MagicMock(
            call=MagicMock(return_value=(pool_key_tuple, info_tuple))
        )
        pm.contract.functions.getPositionLiquidity.return_value = MagicMock(
            call=MagicMock(return_value=5 * 10**17)
        )

        pos = pm.get_position(7)
        assert pos.tick_lower == -1200
        assert pos.tick_upper == 1200

    # ----------------------------------------------------------
    # get_position: packed int info
    # ----------------------------------------------------------

    def test_get_position_packed_info(self, pm):
        """getPoolAndPositionInfo возвращает упакованный int с тиками (Layout 1)."""
        pool_key_tuple = (
            Web3.to_checksum_address(TOKEN_A),
            Web3.to_checksum_address(TOKEN_B),
            3000,
            60,
            Web3.to_checksum_address(HOOKS_ZERO),
        )
        # Упакованные тики в Layout 1: tickLower в битах 232-255, tickUpper в 208-231
        tick_lower = -600
        tick_upper = 600
        # Кодируем: tickLower как signed 24-bit в позиции 232
        tl_unsigned = tick_lower & 0xFFFFFF  # дополнение до 2 для отрицательных
        tu_unsigned = tick_upper & 0xFFFFFF
        packed = (tl_unsigned << 232) | (tu_unsigned << 208)

        pm.contract.functions.getPoolAndPositionInfo.return_value = MagicMock(
            call=MagicMock(return_value=(pool_key_tuple, packed))
        )
        pm.contract.functions.getPositionLiquidity.return_value = MagicMock(
            call=MagicMock(return_value=10**18)
        )

        pos = pm.get_position(10)
        assert pos.tick_lower == tick_lower
        assert pos.tick_upper == tick_upper
        assert pos.liquidity == 10**18

    # ----------------------------------------------------------
    # get_position: fallback to legacy getPositionInfo
    # ----------------------------------------------------------

    def test_get_position_fallback_legacy(self, pm):
        """Если getPoolAndPositionInfo падает, фоллбэк на getPositionInfo."""
        pm.contract.functions.getPoolAndPositionInfo.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("not supported"))
        )
        # positions() (PancakeSwap path) also fails
        pm.contract.functions.positions.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("not supported"))
        )

        pool_key_tuple = (
            Web3.to_checksum_address(TOKEN_A),
            Web3.to_checksum_address(TOKEN_B),
            10000,
            200,
            Web3.to_checksum_address(HOOKS_ZERO),
        )
        pm.contract.functions.getPositionInfo.return_value = MagicMock(
            call=MagicMock(return_value=(pool_key_tuple, -2000, 2000, 10**17))
        )

        pos = pm.get_position(55)
        assert pos.token_id == 55
        assert pos.tick_lower == -2000
        assert pos.tick_upper == 2000
        assert pos.liquidity == 10**17
        assert pos.pool_key.fee == 10000

    # ----------------------------------------------------------
    # get_position: getPositionLiquidity fallback
    # ----------------------------------------------------------

    def test_get_position_liquidity_fallback(self, pm):
        """Если getPositionLiquidity падает, используется _get_position_liquidity."""
        pool_key_tuple = (
            Web3.to_checksum_address(TOKEN_A),
            Web3.to_checksum_address(TOKEN_B),
            3000,
            60,
            Web3.to_checksum_address(HOOKS_ZERO),
        )
        info_tuple = (False, -300, 300)
        pm.contract.functions.getPoolAndPositionInfo.return_value = MagicMock(
            call=MagicMock(return_value=(pool_key_tuple, info_tuple))
        )
        # getPositionLiquidity бросает исключение
        pm.contract.functions.getPositionLiquidity.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("revert"))
        )

        # _get_position_liquidity тоже вызывает contract.functions.getPositionLiquidity,
        # так что мокаем w3.eth.call для raw fallback
        liq_raw = (10**18).to_bytes(32, 'big')
        pm.w3.eth.call = MagicMock(return_value=liq_raw)
        pm.w3.keccak = MagicMock(return_value=b'\x00' * 32)

        pos = pm.get_position(77)
        assert pos.tick_lower == -300
        assert pos.tick_upper == 300
        # Ликвидность из raw call
        assert pos.liquidity == 10**18

    # ----------------------------------------------------------
    # _get_position_liquidity
    # ----------------------------------------------------------

    def test_get_position_liquidity_contract_method(self, pm):
        """_get_position_liquidity через contract.functions."""
        pm.contract.functions.getPositionLiquidity.return_value = MagicMock(
            call=MagicMock(return_value=12345)
        )
        assert pm._get_position_liquidity(1) == 12345

    def test_get_position_liquidity_raw_fallback(self, pm):
        """_get_position_liquidity через raw call при ошибке contract метода."""
        pm.contract.functions.getPositionLiquidity.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("revert"))
        )
        liq = 99999
        pm.w3.eth.call = MagicMock(return_value=liq.to_bytes(32, 'big'))
        pm.w3.keccak = MagicMock(return_value=b'\xab\xcd' * 16)

        result = pm._get_position_liquidity(5)
        assert result == liq

    def test_get_position_liquidity_both_fail_returns_zero(self, pm):
        """_get_position_liquidity возвращает 0 если оба метода падают."""
        pm.contract.functions.getPositionLiquidity.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("fail1"))
        )
        pm.w3.eth.call = MagicMock(side_effect=Exception("fail2"))
        pm.w3.keccak = MagicMock(return_value=b'\x00' * 32)

        result = pm._get_position_liquidity(999)
        assert result == 0

    # ----------------------------------------------------------
    # encode_mint_position
    # ----------------------------------------------------------

    def test_encode_mint_position_returns_bytes(self, pm, pool_key):
        """encode_mint_position возвращает bytes, начинающиеся с MINT_POSITION."""
        result = pm.encode_mint_position(
            pool_key=pool_key,
            tick_lower=-600,
            tick_upper=600,
            liquidity=10**18,
            amount0_max=10**18,
            amount1_max=300 * 10**18,
            recipient=WALLET_ADDR,
        )
        assert isinstance(result, bytes)
        assert len(result) > 1
        assert result[0] == PancakeV4Actions.MINT_POSITION

    def test_encode_mint_position_with_hook_data(self, pm, pool_key):
        """encode_mint_position с непустыми hookData."""
        hook_data = b'\x01\x02\x03\x04'
        result = pm.encode_mint_position(
            pool_key=pool_key,
            tick_lower=-120,
            tick_upper=120,
            liquidity=10**16,
            amount0_max=10**16,
            amount1_max=10**16,
            recipient=WALLET_ADDR,
            hook_data=hook_data,
        )
        assert isinstance(result, bytes)
        assert result[0] == PancakeV4Actions.MINT_POSITION

    # ----------------------------------------------------------
    # encode_settle_pair
    # ----------------------------------------------------------

    def test_encode_settle_pair_returns_bytes(self, pm):
        """encode_settle_pair возвращает bytes с кодом SETTLE_PAIR."""
        result = pm.encode_settle_pair(TOKEN_A, TOKEN_B)
        assert isinstance(result, bytes)
        assert len(result) > 1
        assert result[0] == PancakeV4Actions.SETTLE_PAIR

    def test_encode_settle_pair_different_tokens(self, pm):
        """encode_settle_pair для разных пар токенов дает разный payload."""
        r1 = pm.encode_settle_pair(TOKEN_A, TOKEN_B)
        r2 = pm.encode_settle_pair(TOKEN_B, TOKEN_A)
        # Первый байт одинаковый (оба SETTLE_PAIR), но параметры отличаются
        assert r1[0] == r2[0]
        assert r1[1:] != r2[1:]

    # ----------------------------------------------------------
    # encode_take_pair
    # ----------------------------------------------------------

    def test_encode_take_pair_returns_bytes(self, pm):
        """encode_take_pair возвращает bytes с кодом TAKE_PAIR."""
        result = pm.encode_take_pair(TOKEN_A, TOKEN_B, WALLET_ADDR)
        assert isinstance(result, bytes)
        assert len(result) > 1
        assert result[0] == PancakeV4Actions.TAKE_PAIR

    # ----------------------------------------------------------
    # encode_decrease_liquidity
    # ----------------------------------------------------------

    def test_encode_decrease_liquidity_returns_bytes(self, pm):
        """encode_decrease_liquidity возвращает bytes с кодом DECREASE_LIQUIDITY."""
        result = pm.encode_decrease_liquidity(
            token_id=42,
            liquidity=10**18,
            amount0_min=0,
            amount1_min=0,
        )
        assert isinstance(result, bytes)
        assert len(result) > 1
        assert result[0] == PancakeV4Actions.DECREASE_LIQUIDITY

    def test_encode_decrease_liquidity_with_minimums(self, pm):
        """encode_decrease_liquidity с ненулевыми минимальными суммами."""
        result = pm.encode_decrease_liquidity(
            token_id=10,
            liquidity=10**17,
            amount0_min=10**15,
            amount1_min=10**15,
            hook_data=b'\xaa',
        )
        assert isinstance(result, bytes)
        assert result[0] == PancakeV4Actions.DECREASE_LIQUIDITY

    # ----------------------------------------------------------
    # encode_burn_position
    # ----------------------------------------------------------

    def test_encode_burn_position_returns_bytes(self, pm):
        """encode_burn_position возвращает bytes с кодом BURN_POSITION."""
        result = pm.encode_burn_position(
            token_id=42,
            amount0_min=0,
            amount1_min=0,
        )
        assert isinstance(result, bytes)
        assert len(result) > 1
        assert result[0] == PancakeV4Actions.BURN_POSITION

    # ----------------------------------------------------------
    # build_mint_action
    # ----------------------------------------------------------

    def test_build_mint_action_same_as_encode(self, pm, pool_key):
        """build_mint_action возвращает то же, что и encode_mint_position."""
        params = dict(
            pool_key=pool_key,
            tick_lower=-600,
            tick_upper=600,
            liquidity=10**18,
            amount0_max=10**18,
            amount1_max=300 * 10**18,
            recipient=WALLET_ADDR,
        )
        a = pm.build_mint_action(**params)
        b = pm.encode_mint_position(**params)
        assert a == b

    # ----------------------------------------------------------
    # build_mint_payload
    # ----------------------------------------------------------

    def test_build_mint_payload_has_two_actions(self, pm, pool_key):
        """build_mint_payload возвращает список из 2 действий (MINT + SETTLE_PAIR)."""
        actions = pm.build_mint_payload(
            pool_key=pool_key,
            tick_lower=-600,
            tick_upper=600,
            liquidity=10**18,
            amount0_max=10**18,
            amount1_max=300 * 10**18,
            recipient=WALLET_ADDR,
        )
        assert isinstance(actions, list)
        assert len(actions) == 2
        # Первый - MINT_POSITION
        assert actions[0][0] == PancakeV4Actions.MINT_POSITION
        # Второй - SETTLE_PAIR
        assert actions[1][0] == PancakeV4Actions.SETTLE_PAIR

    # ----------------------------------------------------------
    # build_close_action
    # ----------------------------------------------------------

    def test_build_close_action_without_burn(self, pm):
        """build_close_action без burn возвращает [DECREASE_LIQUIDITY]."""
        actions = pm.build_close_action(token_id=42, liquidity=10**18, burn=False)
        assert isinstance(actions, list)
        assert len(actions) == 1
        assert actions[0][0] == PancakeV4Actions.DECREASE_LIQUIDITY

    def test_build_close_action_with_burn(self, pm):
        """build_close_action с burn=True возвращает [DECREASE, BURN]."""
        actions = pm.build_close_action(token_id=42, liquidity=10**18, burn=True)
        assert isinstance(actions, list)
        assert len(actions) == 2
        assert actions[0][0] == PancakeV4Actions.DECREASE_LIQUIDITY
        assert actions[1][0] == PancakeV4Actions.BURN_POSITION

    # ----------------------------------------------------------
    # build_close_position_payload
    # ----------------------------------------------------------

    def test_build_close_position_payload_returns_bytes(self, pm):
        """build_close_position_payload возвращает закодированный payload."""
        payload = pm.build_close_position_payload(
            token_id=42,
            liquidity=10**18,
            recipient=WALLET_ADDR,
            currency0=TOKEN_A,
            currency1=TOKEN_B,
            burn=False,
        )
        assert isinstance(payload, bytes)
        assert len(payload) > 0

    def test_build_close_position_payload_with_burn(self, pm):
        """build_close_position_payload с burn=True содержит больше данных."""
        payload_no_burn = pm.build_close_position_payload(
            token_id=42, liquidity=10**18, recipient=WALLET_ADDR,
            currency0=TOKEN_A, currency1=TOKEN_B, burn=False,
        )
        payload_burn = pm.build_close_position_payload(
            token_id=42, liquidity=10**18, recipient=WALLET_ADDR,
            currency0=TOKEN_A, currency1=TOKEN_B, burn=True,
        )
        # С burn payload длиннее (дополнительное действие BURN_POSITION)
        assert len(payload_burn) > len(payload_no_burn)

    # ----------------------------------------------------------
    # _encode_actions
    # ----------------------------------------------------------

    def test_encode_actions_format(self, pm):
        """_encode_actions корректно разделяет action_ids и params."""
        # Создаём фейковые действия: action_id (1 байт) + params
        action1 = bytes([0x02]) + b'\x00' * 32  # MINT_POSITION + params
        action2 = bytes([0x0d]) + b'\x00' * 64  # SETTLE_PAIR + params
        actions = [action1, action2]

        encoded = pm._encode_actions(actions)
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

    def test_encode_actions_single_action(self, pm):
        """_encode_actions для одного действия."""
        action = bytes([0x01]) + b'\xaa' * 20
        encoded = pm._encode_actions([action])
        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

    def test_encode_actions_preserves_action_ids(self, pm):
        """_encode_actions сохраняет порядок action IDs."""
        from eth_abi import decode as abi_decode

        a1 = bytes([0x02]) + b'\x11' * 10
        a2 = bytes([0x0d]) + b'\x22' * 10
        a3 = bytes([0x11]) + b'\x33' * 10

        encoded = pm._encode_actions([a1, a2, a3])
        # Декодируем обратно
        action_ids_bytes, params_list = abi_decode(['bytes', 'bytes[]'], encoded)
        assert action_ids_bytes == bytes([0x02, 0x0d, 0x11])
        assert len(params_list) == 3
        assert params_list[0] == b'\x11' * 10
        assert params_list[1] == b'\x22' * 10
        assert params_list[2] == b'\x33' * 10

    # ----------------------------------------------------------
    # Ownership methods
    # ----------------------------------------------------------

    def test_get_owner_of_exists(self, pm):
        """get_owner_of возвращает адрес владельца."""
        pm.contract.functions.ownerOf.return_value = MagicMock(
            call=MagicMock(return_value=WALLET_ADDR)
        )
        owner = pm.get_owner_of(42)
        assert owner == WALLET_ADDR

    def test_get_owner_of_burned_returns_none(self, pm):
        """get_owner_of возвращает None для сожжённой позиции."""
        pm.contract.functions.ownerOf.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("ERC721: invalid token ID"))
        )
        owner = pm.get_owner_of(999)
        assert owner is None

    def test_is_position_owned_by_true(self, pm):
        """is_position_owned_by возвращает True для владельца."""
        pm.contract.functions.ownerOf.return_value = MagicMock(
            call=MagicMock(return_value=WALLET_ADDR)
        )
        assert pm.is_position_owned_by(42, WALLET_ADDR) is True

    def test_is_position_owned_by_true_case_insensitive(self, pm):
        """is_position_owned_by нечувствителен к регистру адреса."""
        pm.contract.functions.ownerOf.return_value = MagicMock(
            call=MagicMock(return_value=WALLET_ADDR.lower())
        )
        assert pm.is_position_owned_by(42, WALLET_ADDR.upper()) is True

    def test_is_position_owned_by_false(self, pm):
        """is_position_owned_by возвращает False для чужой позиции."""
        other_addr = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        pm.contract.functions.ownerOf.return_value = MagicMock(
            call=MagicMock(return_value=other_addr)
        )
        assert pm.is_position_owned_by(42, WALLET_ADDR) is False

    def test_is_position_owned_by_burned_returns_false(self, pm):
        """is_position_owned_by возвращает False для несуществующей позиции."""
        pm.contract.functions.ownerOf.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("invalid token"))
        )
        assert pm.is_position_owned_by(999, WALLET_ADDR) is False

    def test_get_positions_count(self, pm):
        """get_positions_count возвращает количество NFT позиций."""
        pm.contract.functions.balanceOf.return_value = MagicMock(
            call=MagicMock(return_value=5)
        )
        count = pm.get_positions_count(WALLET_ADDR)
        assert count == 5

    def test_get_positions_count_error_returns_zero(self, pm):
        """get_positions_count возвращает 0 при ошибке."""
        pm.contract.functions.balanceOf.return_value = MagicMock(
            call=MagicMock(side_effect=Exception("call failed"))
        )
        count = pm.get_positions_count(WALLET_ADDR)
        assert count == 0

    def test_get_positions_count_empty_wallet(self, pm):
        """get_positions_count для пустого кошелька."""
        pm.contract.functions.balanceOf.return_value = MagicMock(
            call=MagicMock(return_value=0)
        )
        count = pm.get_positions_count(WALLET_ADDR)
        assert count == 0

    # ----------------------------------------------------------
    # get_position_token_ids
    # ----------------------------------------------------------

    def test_get_position_token_ids_via_enumerable(self, pm):
        """get_position_token_ids через tokenOfOwnerByIndex."""
        pm.contract.functions.balanceOf.return_value = MagicMock(
            call=MagicMock(return_value=3)
        )
        # tokenOfOwnerByIndex возвращает ID для каждого индекса
        pm.contract.functions.tokenOfOwnerByIndex = MagicMock(
            side_effect=lambda addr, idx: MagicMock(
                call=MagicMock(return_value=100 + idx)
            )
        )
        ids = pm.get_position_token_ids(WALLET_ADDR)
        assert ids == [100, 101, 102]

    def test_get_position_token_ids_empty_wallet(self, pm):
        """get_position_token_ids для кошелька без позиций."""
        pm.contract.functions.balanceOf.return_value = MagicMock(
            call=MagicMock(return_value=0)
        )
        ids = pm.get_position_token_ids(WALLET_ADDR)
        assert ids == []

    # ----------------------------------------------------------
    # scan_wallet_positions
    # ----------------------------------------------------------

    def test_scan_wallet_positions_returns_dicts(self, pm):
        """scan_wallet_positions возвращает список словарей."""
        # Мокаем get_position_token_ids
        pm.get_position_token_ids = MagicMock(return_value=[10, 20])

        pk = _make_pool_key()
        pos1 = V4Position(token_id=10, pool_key=pk, tick_lower=-600, tick_upper=600, liquidity=10**18)
        pos2 = V4Position(token_id=20, pool_key=pk, tick_lower=-1200, tick_upper=1200, liquidity=5 * 10**17)

        pm.get_position = MagicMock(side_effect=[pos1, pos2])

        results = pm.scan_wallet_positions(WALLET_ADDR)
        assert len(results) == 2
        assert results[0]['token_id'] == 10
        assert results[0]['tick_lower'] == -600
        assert results[0]['tick_upper'] == 600
        assert results[0]['liquidity'] == 10**18
        assert results[0]['token0'] == TOKEN_A
        assert results[0]['token1'] == TOKEN_B
        assert results[0]['fee'] == 3000
        assert results[1]['token_id'] == 20

    def test_scan_wallet_positions_skips_errors(self, pm):
        """scan_wallet_positions пропускает позиции с ошибками."""
        pm.get_position_token_ids = MagicMock(return_value=[10, 20, 30])

        pk = _make_pool_key()
        pos_ok = V4Position(token_id=10, pool_key=pk, tick_lower=-60, tick_upper=60, liquidity=100)

        def side_effect_pos(tid):
            if tid == 20:
                raise ValueError("position not found")
            return pos_ok

        pm.get_position = MagicMock(side_effect=side_effect_pos)

        results = pm.scan_wallet_positions(WALLET_ADDR)
        # Только позиции 10 и 30 (без 20)
        assert len(results) == 2

    def test_scan_wallet_positions_empty(self, pm):
        """scan_wallet_positions возвращает пустой список для пустого кошелька."""
        pm.get_position_token_ids = MagicMock(return_value=[])
        results = pm.scan_wallet_positions(WALLET_ADDR)
        assert results == []

    # ----------------------------------------------------------
    # mint_position
    # ----------------------------------------------------------

    def test_mint_position_no_account_raises(self, pm_no_account, pool_key):
        """mint_position без аккаунта бросает ValueError."""
        with pytest.raises(ValueError, match="Account not set"):
            pm_no_account.mint_position(
                pool_key=pool_key,
                tick_lower=-600,
                tick_upper=600,
                liquidity=10**18,
                amount0_max=10**18,
                amount1_max=300 * 10**18,
            )

    def test_mint_position_sends_transaction(self, pm, pool_key):
        """mint_position строит и отправляет транзакцию."""
        # Мок modifyLiquidities
        build_tx_mock = MagicMock(return_value={
            'from': WALLET_ADDR,
            'nonce': 0,
            'gas': 500_000,
            'gasPrice': 5_000_000_000,
            'value': 0,
        })
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(build_transaction=build_tx_mock)
        )

        # Мок Transfer event для получения token_id
        transfer_event = MagicMock()
        transfer_event.__getitem__ = lambda self, key: {
            'args': {'from': '0x0000000000000000000000000000000000000000', 'tokenId': 777}
        }[key] if key == 'args' else None
        pm.contract.events = MagicMock()
        pm.contract.events.Transfer = MagicMock(
            return_value=MagicMock(
                process_receipt=MagicMock(return_value=[
                    {'args': {'from': '0x0000000000000000000000000000000000000000', 'tokenId': 777}}
                ])
            )
        )

        result = pm.mint_position(
            pool_key=pool_key,
            tick_lower=-600,
            tick_upper=600,
            liquidity=10**18,
            amount0_max=10**18,
            amount1_max=300 * 10**18,
            gas_limit=500_000,
        )

        assert isinstance(result, MintResult)
        assert result.token_id == 777
        assert result.liquidity == 10**18
        pm.w3.eth.send_raw_transaction.assert_called_once()
        pm.w3.eth.wait_for_transaction_receipt.assert_called_once()

    def test_mint_position_default_deadline(self, pm, pool_key):
        """mint_position при deadline=None генерирует deadline +1 час."""
        build_tx_mock = MagicMock(return_value={})
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(build_transaction=build_tx_mock)
        )
        pm.contract.events = MagicMock()
        pm.contract.events.Transfer = MagicMock(
            return_value=MagicMock(
                process_receipt=MagicMock(return_value=[])
            )
        )

        pm.mint_position(
            pool_key=pool_key,
            tick_lower=-60,
            tick_upper=60,
            liquidity=10**16,
            amount0_max=10**16,
            amount1_max=10**16,
            deadline=None,
        )

        # Проверяем, что modifyLiquidities был вызван с deadline > текущего времени
        call_args = pm.contract.functions.modifyLiquidities.call_args
        deadline_arg = call_args[0][1]
        import time
        assert deadline_arg > time.time()

    # ----------------------------------------------------------
    # close_position
    # ----------------------------------------------------------

    def test_close_position_no_account_raises(self, pm_no_account):
        """close_position без аккаунта бросает ValueError."""
        with pytest.raises(ValueError, match="Account not set"):
            pm_no_account.close_position(token_id=42)

    def test_close_position_null_tokens_raises(self, pm):
        """close_position с нулевыми адресами токенов бросает ValueError."""
        null_key = PoolKey(
            currency0="0x0000000000000000000000000000000000000000",
            currency1="0x0000000000000000000000000000000000000000",
            fee=0, tick_spacing=0,
        )
        null_pos = V4Position(
            token_id=42, pool_key=null_key,
            tick_lower=-60, tick_upper=60, liquidity=100,
        )
        pm.get_position = MagicMock(return_value=null_pos)

        with pytest.raises(ValueError, match="token addresses unavailable"):
            pm.close_position(token_id=42)

    def test_close_position_sends_transaction(self, pm):
        """close_position отправляет транзакцию закрытия позиции."""
        pk = _make_pool_key()
        pos = V4Position(token_id=42, pool_key=pk, tick_lower=-600, tick_upper=600, liquidity=10**18)
        pm.get_position = MagicMock(return_value=pos)

        build_tx_mock = MagicMock(return_value={
            'from': WALLET_ADDR, 'nonce': 0,
            'gas': 500_000, 'gasPrice': 5_000_000_000,
        })
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(build_transaction=build_tx_mock)
        )

        tx_hash, amount0, amount1 = pm.close_position(token_id=42)
        assert isinstance(tx_hash, str)
        pm.w3.eth.send_raw_transaction.assert_called_once()

    def test_close_position_default_recipient(self, pm):
        """close_position использует адрес аккаунта как получателя по умолчанию."""
        pk = _make_pool_key()
        pos = V4Position(token_id=42, pool_key=pk, tick_lower=-60, tick_upper=60, liquidity=100)
        pm.get_position = MagicMock(return_value=pos)

        build_tx_mock = MagicMock(return_value={})
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(build_transaction=build_tx_mock)
        )

        pm.close_position(token_id=42, recipient=None)
        # Метод не бросает исключение, значит recipient = account.address

    # ----------------------------------------------------------
    # close_position_with_tokens
    # ----------------------------------------------------------

    def test_close_position_with_tokens_no_account_raises(self, pm_no_account):
        """close_position_with_tokens без аккаунта бросает ValueError."""
        with pytest.raises(ValueError, match="Account not set"):
            pm_no_account.close_position_with_tokens(
                token_id=42, currency0=TOKEN_A, currency1=TOKEN_B, liquidity=10**18,
            )

    def test_close_position_with_tokens_sends_tx(self, pm):
        """close_position_with_tokens отправляет транзакцию с явными адресами."""
        build_tx_mock = MagicMock(return_value={})
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(build_transaction=build_tx_mock)
        )
        tx_hash, a0, a1 = pm.close_position_with_tokens(
            token_id=42, currency0=TOKEN_A, currency1=TOKEN_B, liquidity=10**18,
        )
        assert isinstance(tx_hash, str)
        pm.w3.eth.send_raw_transaction.assert_called_once()

    # ----------------------------------------------------------
    # close_positions_batch
    # ----------------------------------------------------------

    def test_close_positions_batch_no_account_raises(self, pm_no_account):
        """close_positions_batch без аккаунта бросает ValueError."""
        with pytest.raises(ValueError, match="Account not set"):
            pm_no_account.close_positions_batch(positions=[{'token_id': 1, 'liquidity': 100, 'currency0': TOKEN_A, 'currency1': TOKEN_B}])

    def test_close_positions_batch_empty_raises(self, pm):
        """close_positions_batch с пустым списком бросает ValueError."""
        with pytest.raises(ValueError, match="No positions to close"):
            pm.close_positions_batch(positions=[])

    def test_close_positions_batch_null_currency_raises(self, pm):
        """close_positions_batch с нулевым адресом токена бросает ValueError."""
        with pytest.raises(ValueError, match="missing currency0"):
            pm.close_positions_batch(positions=[{
                'token_id': 1, 'liquidity': 100,
                'currency0': "0x0000000000000000000000000000000000000000",
                'currency1': TOKEN_B,
            }])

    def test_close_positions_batch_sends_tx(self, pm):
        """close_positions_batch отправляет одну транзакцию для нескольких позиций."""
        build_tx_mock = MagicMock(return_value={})
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(build_transaction=build_tx_mock)
        )
        positions = [
            {'token_id': 1, 'liquidity': 100, 'currency0': TOKEN_A, 'currency1': TOKEN_B},
            {'token_id': 2, 'liquidity': 200, 'currency0': TOKEN_A, 'currency1': TOKEN_B},
        ]
        tx_hash, success, gas_used = pm.close_positions_batch(positions=positions)
        assert isinstance(tx_hash, str)
        assert success is True
        pm.w3.eth.send_raw_transaction.assert_called_once()

    def test_close_positions_batch_normalizes_keys(self, pm):
        """close_positions_batch обрабатывает альтернативные ключи (token0/token1)."""
        build_tx_mock = MagicMock(return_value={})
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(build_transaction=build_tx_mock)
        )
        positions = [
            {'token_id': 5, 'liquidity': 100, 'token0': TOKEN_A, 'token1': TOKEN_B},
        ]
        tx_hash, success, gas_used = pm.close_positions_batch(positions=positions)
        assert success is True

    # ----------------------------------------------------------
    # multicall
    # ----------------------------------------------------------

    def test_multicall_no_account_raises(self, pm_no_account):
        """multicall без аккаунта бросает ValueError."""
        with pytest.raises(ValueError, match="Account not set"):
            pm_no_account.multicall(payloads=[[b'\x00']])

    def test_multicall_sends_combined_actions(self, pm, pool_key):
        """multicall объединяет все действия в одну транзакцию."""
        # Мок estimate_gas
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(return_value=400_000),
                build_transaction=MagicMock(return_value={}),
            )
        )

        # Создаём два payload (каждый = [mint_action, settle_pair])
        action1 = [bytes([0x02]) + b'\x00' * 32, bytes([0x0d]) + b'\x00' * 32]
        action2 = [bytes([0x02]) + b'\x00' * 32, bytes([0x0d]) + b'\x00' * 32]

        tx_hash, results = pm.multicall(payloads=[action1, action2])
        assert isinstance(tx_hash, str)
        pm.w3.eth.send_raw_transaction.assert_called_once()

    def test_multicall_reverted_raises(self, pm):
        """multicall при revert транзакции бросает Exception."""
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(return_value=300_000),
                build_transaction=MagicMock(return_value={}),
            )
        )
        pm.w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 0, 'gasUsed': 300_000, 'logs': [],
        })

        with pytest.raises(Exception, match="Transaction reverted"):
            pm.multicall(payloads=[[bytes([0x02]) + b'\x00' * 32]])

    # ----------------------------------------------------------
    # execute_modify_liquidities
    # ----------------------------------------------------------

    def test_execute_modify_liquidities_no_account_raises(self, pm_no_account):
        """execute_modify_liquidities без аккаунта бросает ValueError."""
        with pytest.raises(ValueError, match="Account not set"):
            pm_no_account.execute_modify_liquidities(unlock_data=b'\x00' * 32)

    def test_execute_modify_liquidities_gas_estimation_fail_raises(self, pm):
        """execute_modify_liquidities при ошибке gas estimation бросает Exception."""
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(side_effect=Exception("execution reverted")),
            )
        )
        with pytest.raises(Exception, match="Gas estimation failed"):
            pm.execute_modify_liquidities(unlock_data=b'\x00' * 32)

    def test_execute_modify_liquidities_success(self, pm):
        """execute_modify_liquidities успешно отправляет транзакцию."""
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(return_value=350_000),
                build_transaction=MagicMock(return_value={}),
            )
        )
        tx_hash, results = pm.execute_modify_liquidities(unlock_data=b'\xaa' * 64)
        assert isinstance(tx_hash, str)
        pm.w3.eth.send_raw_transaction.assert_called_once()

    def test_execute_modify_liquidities_reverted_raises(self, pm):
        """execute_modify_liquidities при revert бросает Exception."""
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(return_value=300_000),
                build_transaction=MagicMock(return_value={}),
            )
        )
        pm.w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 0, 'gasUsed': 250_000,
        })
        with pytest.raises(Exception, match="Transaction reverted"):
            pm.execute_modify_liquidities(unlock_data=b'\x00' * 32)

    def test_execute_modify_liquidities_explicit_gas_limit(self, pm):
        """execute_modify_liquidities с явным gas_limit не вызывает estimate_gas."""
        mock_modify = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(return_value=350_000),
                build_transaction=MagicMock(return_value={}),
            )
        )
        pm.contract.functions.modifyLiquidities = mock_modify

        pm.execute_modify_liquidities(unlock_data=b'\xbb' * 32, gas_limit=600_000)

        # estimate_gas НЕ должен вызываться при явном gas_limit
        mock_modify.return_value.estimate_gas.assert_not_called()

    # ----------------------------------------------------------
    # _parse_mint_event
    # ----------------------------------------------------------

    def test_parse_mint_event_found(self, pm):
        """_parse_mint_event извлекает tokenId из Transfer event (from=zero)."""
        receipt = {'logs': []}
        pm.contract.events.Transfer = MagicMock(
            return_value=MagicMock(
                process_receipt=MagicMock(return_value=[
                    {'args': {'from': '0x0000000000000000000000000000000000000000', 'tokenId': 555}},
                ])
            )
        )
        token_id = pm._parse_mint_event(receipt)
        assert token_id == 555

    def test_parse_mint_event_no_mint_returns_zero(self, pm):
        """_parse_mint_event возвращает 0 если нет Transfer от нулевого адреса."""
        receipt = {'logs': []}
        pm.contract.events.Transfer = MagicMock(
            return_value=MagicMock(
                process_receipt=MagicMock(return_value=[
                    {'args': {'from': WALLET_ADDR, 'tokenId': 100}},
                ])
            )
        )
        token_id = pm._parse_mint_event(receipt)
        assert token_id == 0

    def test_parse_mint_event_exception_returns_zero(self, pm):
        """_parse_mint_event возвращает 0 при ошибке парсинга."""
        receipt = {'logs': []}
        pm.contract.events.Transfer = MagicMock(
            return_value=MagicMock(
                process_receipt=MagicMock(side_effect=Exception("decode error"))
            )
        )
        token_id = pm._parse_mint_event(receipt)
        assert token_id == 0

    # ----------------------------------------------------------
    # Protocol variants
    # ----------------------------------------------------------

    def test_uniswap_protocol_uses_v4actions(self, mock_w3, mock_account):
        """Для UNISWAP протокола используются V4Actions."""
        pm = _make_pm(mock_w3, mock_account, V4Protocol.UNISWAP)
        assert pm.actions is V4Actions
        assert pm.protocol == V4Protocol.UNISWAP

    def test_pancakeswap_protocol_uses_pancake_actions(self, pm):
        """Для PANCAKESWAP протокола используются PancakeV4Actions."""
        assert pm.actions is PancakeV4Actions
        assert pm.protocol == V4Protocol.PANCAKESWAP

    # ----------------------------------------------------------
    # Integration: encode -> _encode_actions roundtrip
    # ----------------------------------------------------------

    def test_mint_settle_roundtrip(self, pm, pool_key):
        """Полный цикл: encode_mint + encode_settle -> _encode_actions -> декодирование."""
        from eth_abi import decode as abi_decode

        mint_action = pm.encode_mint_position(
            pool_key=pool_key,
            tick_lower=-600,
            tick_upper=600,
            liquidity=10**18,
            amount0_max=10**18,
            amount1_max=300 * 10**18,
            recipient=WALLET_ADDR,
        )
        settle_action = pm.encode_settle_pair(TOKEN_A, TOKEN_B)

        payload = pm._encode_actions([mint_action, settle_action])

        action_ids, params_list = abi_decode(['bytes', 'bytes[]'], payload)
        assert len(action_ids) == 2
        assert action_ids[0] == PancakeV4Actions.MINT_POSITION
        assert action_ids[1] == PancakeV4Actions.SETTLE_PAIR
        assert len(params_list) == 2
        # params = всё после первого байта
        assert params_list[0] == mint_action[1:]
        assert params_list[1] == settle_action[1:]

    def test_close_roundtrip(self, pm):
        """Полный цикл: build_close_action + take_pair -> _encode_actions."""
        from eth_abi import decode as abi_decode

        close_actions = pm.build_close_action(token_id=42, liquidity=10**18, burn=True)
        take_action = pm.encode_take_pair(TOKEN_A, TOKEN_B, WALLET_ADDR)
        all_actions = close_actions + [take_action]

        payload = pm._encode_actions(all_actions)
        action_ids, params_list = abi_decode(['bytes', 'bytes[]'], payload)

        assert len(action_ids) == 3  # DECREASE + BURN + TAKE_PAIR
        assert action_ids[0] == PancakeV4Actions.DECREASE_LIQUIDITY
        assert action_ids[1] == PancakeV4Actions.BURN_POSITION
        assert action_ids[2] == PancakeV4Actions.TAKE_PAIR
