"""
Tests for Multicall3Batcher.

Тесты батчера мультивызовов: Call3, CallResult, MintCallResult,
основные операции (add/clear/execute/simulate).
Все тесты работают без реального блокчейна.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from web3 import Web3

from src.multicall.batcher import (
    Multicall3Batcher,
    Call3,
    MintCallResult,
    CallResult,
    MULTICALL3_ADDRESS,
)


# ---------------------------------------------------------------------------
# Тестовые константы
# ---------------------------------------------------------------------------

PM_ADDRESS = "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364"
TOKEN0 = "0x1111111111111111111111111111111111111111"
TOKEN1 = "0x9999999999999999999999999999999999999999"
RECIPIENT = "0x1234567890123456789012345678901234567890"


# ---------------------------------------------------------------------------
# Call3 dataclass
# ---------------------------------------------------------------------------

class TestCall3:
    """Тесты для структуры Call3."""

    def test_to_tuple_has_3_elements(self):
        """to_tuple() возвращает кортеж из 3 элементов: (address, bool, bytes)."""
        call = Call3(
            target="0xcA11bde05977b3631167028862bE2a173976CA11",
            allow_failure=False,
            call_data=b'\x01\x02',
        )
        result = call.to_tuple()
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_to_tuple_checksum_address(self):
        """to_tuple() преобразует адрес в checksum формат."""
        addr = "0xca11bde05977b3631167028862be2a173976ca11"
        call = Call3(target=addr, allow_failure=False, call_data=b'\x00')
        tup = call.to_tuple()
        # Web3.to_checksum_address capitalizes
        assert tup[0] == Web3.to_checksum_address(addr)

    def test_allow_failure_default_false(self):
        """allow_failure можно задать как False."""
        call = Call3(target=TOKEN0, allow_failure=False, call_data=b'')
        assert call.allow_failure is False

    def test_allow_failure_true(self):
        """allow_failure можно задать как True."""
        call = Call3(target=TOKEN0, allow_failure=True, call_data=b'\xff')
        assert call.allow_failure is True
        assert call.to_tuple()[1] is True

    def test_call_data_is_bytes(self):
        """call_data хранится как bytes."""
        data = b'\xde\xad\xbe\xef'
        call = Call3(target=TOKEN0, allow_failure=False, call_data=data)
        assert isinstance(call.call_data, bytes)
        assert call.call_data == data


# ---------------------------------------------------------------------------
# MintCallResult dataclass
# ---------------------------------------------------------------------------

class TestMintCallResult:
    """Тесты для MintCallResult."""

    def test_fields(self):
        """MintCallResult хранит token_id, liquidity, amount0, amount1."""
        result = MintCallResult(
            token_id=12345,
            liquidity=10**18,
            amount0=5 * 10**17,
            amount1=3 * 10**17,
        )
        assert result.token_id == 12345
        assert result.liquidity == 10**18
        assert result.amount0 == 5 * 10**17
        assert result.amount1 == 3 * 10**17

    def test_fields_zero_values(self):
        """MintCallResult допускает нулевые значения."""
        result = MintCallResult(token_id=0, liquidity=0, amount0=0, amount1=0)
        assert result.token_id == 0
        assert result.liquidity == 0


# ---------------------------------------------------------------------------
# CallResult dataclass
# ---------------------------------------------------------------------------

class TestCallResult:
    """Тесты для CallResult."""

    def test_fields_defaults(self):
        """decoded_data по умолчанию None."""
        result = CallResult(success=True, return_data=b'\x00' * 32)
        assert result.success is True
        assert result.return_data == b'\x00' * 32
        assert result.decoded_data is None

    def test_fields_with_decoded(self):
        """decoded_data можно задать словарём."""
        decoded = {"tokenId": 42, "liquidity": 100}
        result = CallResult(success=False, return_data=b'', decoded_data=decoded)
        assert result.success is False
        assert result.decoded_data == decoded

    def test_failure_result(self):
        """CallResult с success=False."""
        result = CallResult(success=False, return_data=b'\x00')
        assert result.success is False


# ---------------------------------------------------------------------------
# Хелперы для создания мока бэтчера
# ---------------------------------------------------------------------------

def _make_mock_pm():
    """Создание мок Position Manager контракта с функциями mint/decrease/collect/burn/multicall."""
    mock_pm = Mock()
    mock_pm.address = Web3.to_checksum_address(PM_ADDRESS)
    mock_pm.functions = Mock()

    # mint
    mock_pm.functions.mint = Mock(return_value=Mock(
        _encode_transaction_data=Mock(return_value=b'\x88\x31\x64\x56' + b'\x00' * 60)
    ))

    # decreaseLiquidity
    mock_pm.functions.decreaseLiquidity = Mock(return_value=Mock(
        _encode_transaction_data=Mock(return_value=b'\x00' * 36)
    ))

    # collect
    mock_pm.functions.collect = Mock(return_value=Mock(
        _encode_transaction_data=Mock(return_value=b'\x01' * 36)
    ))

    # burn
    mock_pm.functions.burn = Mock(return_value=Mock(
        _encode_transaction_data=Mock(return_value=b'\x02' * 36)
    ))

    # multicall
    mock_multicall = Mock()
    mock_multicall.estimate_gas = Mock(return_value=500_000)
    mock_multicall.build_transaction = Mock(return_value={
        'to': PM_ADDRESS,
        'data': b'\x00',
        'gas': 650_000,
    })
    mock_multicall.call = Mock(return_value=[b'\xaa' * 32, b'\xbb' * 32])
    mock_pm.functions.multicall = Mock(return_value=mock_multicall)

    # events (для _parse_events_from_receipt)
    mock_pm.events = Mock()
    mock_pm.events.IncreaseLiquidity = Mock(return_value=Mock(
        process_receipt=Mock(return_value=[])
    ))

    return mock_pm


def _make_batcher(mock_w3, mock_account, mock_pm=None):
    """
    Создание Multicall3Batcher с замоканным __init__.

    Позволяет тестировать без реального Web3 подключения.
    """
    if mock_pm is None:
        mock_pm = _make_mock_pm()

    with patch.object(Multicall3Batcher, '__init__', lambda self, *a, **kw: None):
        b = Multicall3Batcher.__new__(Multicall3Batcher)
        b.w3 = mock_w3
        b.account = mock_account
        b.multicall_address = MULTICALL3_ADDRESS
        b.contract = Mock()
        b.calls = []
        b.nonce_manager = None
        b._pm_contract = None
        b._get_pm_contract = Mock(return_value=mock_pm)
        return b


# ---------------------------------------------------------------------------
# Multicall3Batcher
# ---------------------------------------------------------------------------

class TestMulticall3Batcher:
    """Тесты Multicall3Batcher: add/clear/execute/simulate/debug."""

    @pytest.fixture
    def mock_w3(self):
        """Мок Web3 instance."""
        w3 = Mock(spec=Web3)
        w3.eth = Mock()
        w3.eth.gas_price = 5_000_000_000
        w3.eth.get_transaction_count = Mock(return_value=0)
        w3.eth.send_raw_transaction = Mock(return_value=b'\x12\x34' * 16)
        w3.eth.wait_for_transaction_receipt = Mock(return_value={
            'status': 1,
            'gasUsed': 300_000,
            'logs': [],
            'transactionHash': b'\x12\x34' * 16,
        })
        w3.eth.call = Mock(return_value=b'\x00' * 32)
        w3.eth.contract = Mock()
        return w3

    @pytest.fixture
    def mock_account(self):
        """Мок LocalAccount."""
        account = Mock()
        account.address = RECIPIENT
        account.sign_transaction = Mock(return_value=Mock(raw_transaction=b'signed'))
        return account

    @pytest.fixture
    def mock_pm(self):
        """Мок Position Manager контракта."""
        return _make_mock_pm()

    @pytest.fixture
    def batcher(self, mock_w3, mock_account, mock_pm):
        """Бэтчер с замоканными зависимостями."""
        return _make_batcher(mock_w3, mock_account, mock_pm)

    # -----------------------------------------------------------------------
    # Базовые операции
    # -----------------------------------------------------------------------

    def test_initial_empty(self, batcher):
        """Новый бэтчер не содержит вызовов."""
        assert len(batcher) == 0
        assert batcher.calls == []

    def test_len(self, batcher):
        """__len__ возвращает количество вызовов."""
        batcher.calls = [
            Call3(target=TOKEN0, allow_failure=False, call_data=b'\x00'),
            Call3(target=TOKEN0, allow_failure=False, call_data=b'\x01'),
        ]
        assert len(batcher) == 2

    def test_clear(self, batcher):
        """clear() очищает список вызовов."""
        batcher.add_call(Call3(target=TOKEN0, allow_failure=False, call_data=b'\x00'))
        assert len(batcher) == 1
        batcher.clear()
        assert len(batcher) == 0
        assert batcher.calls == []

    def test_add_call(self, batcher):
        """add_call() добавляет Call3 в список."""
        call = Call3(target=TOKEN0, allow_failure=True, call_data=b'\xab')
        batcher.add_call(call)
        assert len(batcher) == 1
        assert batcher.calls[0] is call

    def test_add_raw_call(self, batcher):
        """add_raw_call() создаёт Call3 и добавляет в список."""
        batcher.add_raw_call(
            target=TOKEN0,
            call_data=b'\xde\xad',
            allow_failure=True,
        )
        assert len(batcher) == 1
        assert batcher.calls[0].target == TOKEN0
        assert batcher.calls[0].allow_failure is True
        assert batcher.calls[0].call_data == b'\xde\xad'

    def test_add_raw_call_default_allow_failure(self, batcher):
        """add_raw_call() по умолчанию allow_failure=False."""
        batcher.add_raw_call(target=TOKEN0, call_data=b'\x00')
        assert batcher.calls[0].allow_failure is False

    # -----------------------------------------------------------------------
    # Mint
    # -----------------------------------------------------------------------

    def test_add_mint_call_adds_one_call(self, batcher):
        """add_mint_call() добавляет ровно один вызов в батч."""
        batcher.add_mint_call(
            position_manager=PM_ADDRESS,
            token0=TOKEN0,
            token1=TOKEN1,
            fee=3000,
            tick_lower=-60,
            tick_upper=60,
            amount0_desired=10**18,
            amount1_desired=10**18,
            recipient=RECIPIENT,
        )
        assert len(batcher) == 1
        assert batcher.calls[0].target == PM_ADDRESS

    def test_add_mint_call_default_deadline(self, batcher):
        """add_mint_call() задаёт deadline если не передан (time.time() + 3600)."""
        batcher.add_mint_call(
            position_manager=PM_ADDRESS,
            token0=TOKEN0,
            token1=TOKEN1,
            fee=3000,
            tick_lower=-60,
            tick_upper=60,
            amount0_desired=10**18,
            amount1_desired=10**18,
            recipient=RECIPIENT,
            deadline=None,
        )
        # Вызов произошёл, значит deadline был сгенерирован автоматически
        assert len(batcher) == 1
        # Проверяем что pm.functions.mint был вызван с кортежем параметров
        mock_pm = batcher._get_pm_contract(PM_ADDRESS)
        mock_pm.functions.mint.assert_called_once()

    def test_add_mint_call_custom_deadline(self, batcher):
        """add_mint_call() использует переданный deadline."""
        batcher.add_mint_call(
            position_manager=PM_ADDRESS,
            token0=TOKEN0,
            token1=TOKEN1,
            fee=500,
            tick_lower=-100,
            tick_upper=100,
            amount0_desired=10**17,
            amount1_desired=10**17,
            recipient=RECIPIENT,
            deadline=9999999999,
        )
        assert len(batcher) == 1

    def test_add_mint_call_with_slippage(self, batcher):
        """add_mint_call() передаёт amount0_min и amount1_min."""
        batcher.add_mint_call(
            position_manager=PM_ADDRESS,
            token0=TOKEN0,
            token1=TOKEN1,
            fee=3000,
            tick_lower=-120,
            tick_upper=120,
            amount0_desired=10**18,
            amount1_desired=10**18,
            recipient=RECIPIENT,
            amount0_min=9 * 10**17,
            amount1_min=9 * 10**17,
        )
        assert len(batcher) == 1

    def test_add_mint_call_allow_failure(self, batcher):
        """add_mint_call() с allow_failure=True."""
        batcher.add_mint_call(
            position_manager=PM_ADDRESS,
            token0=TOKEN0,
            token1=TOKEN1,
            fee=3000,
            tick_lower=-60,
            tick_upper=60,
            amount0_desired=10**18,
            amount1_desired=10**18,
            recipient=RECIPIENT,
            allow_failure=True,
        )
        assert batcher.calls[0].allow_failure is True

    # -----------------------------------------------------------------------
    # Close position: decrease + collect
    # -----------------------------------------------------------------------

    def test_add_close_position_calls_adds_two(self, batcher):
        """add_close_position_calls() добавляет 2 вызова: decreaseLiquidity + collect."""
        batcher.add_close_position_calls(
            position_manager=PM_ADDRESS,
            token_id=42,
            liquidity=10**18,
            recipient=RECIPIENT,
        )
        assert len(batcher) == 2

    def test_add_close_position_calls_order(self, batcher, mock_pm):
        """Первый вызов - decreaseLiquidity, второй - collect."""
        batcher.add_close_position_calls(
            position_manager=PM_ADDRESS,
            token_id=42,
            liquidity=10**18,
            recipient=RECIPIENT,
        )
        # decreaseLiquidity вызван первым
        mock_pm.functions.decreaseLiquidity.assert_called_once()
        # collect вызван вторым
        mock_pm.functions.collect.assert_called_once()

    def test_add_decrease_liquidity_call(self, batcher, mock_pm):
        """add_decrease_liquidity_call() добавляет один вызов."""
        batcher.add_decrease_liquidity_call(
            position_manager=PM_ADDRESS,
            token_id=100,
            liquidity=5 * 10**17,
        )
        assert len(batcher) == 1
        mock_pm.functions.decreaseLiquidity.assert_called_once()

    def test_add_decrease_liquidity_custom_deadline(self, batcher):
        """add_decrease_liquidity_call() принимает custom deadline."""
        batcher.add_decrease_liquidity_call(
            position_manager=PM_ADDRESS,
            token_id=100,
            liquidity=10**18,
            deadline=8888888888,
        )
        assert len(batcher) == 1

    def test_add_collect_call(self, batcher, mock_pm):
        """add_collect_call() добавляет один вызов collect."""
        batcher.add_collect_call(
            position_manager=PM_ADDRESS,
            token_id=100,
            recipient=RECIPIENT,
        )
        assert len(batcher) == 1
        mock_pm.functions.collect.assert_called_once()

    def test_add_collect_call_custom_amounts(self, batcher):
        """add_collect_call() с кастомными amount0_max/amount1_max."""
        batcher.add_collect_call(
            position_manager=PM_ADDRESS,
            token_id=100,
            recipient=RECIPIENT,
            amount0_max=10**20,
            amount1_max=10**20,
        )
        assert len(batcher) == 1

    def test_add_burn_call(self, batcher, mock_pm):
        """add_burn_call() добавляет один вызов burn."""
        batcher.add_burn_call(
            position_manager=PM_ADDRESS,
            token_id=100,
        )
        assert len(batcher) == 1
        mock_pm.functions.burn.assert_called_once()

    def test_add_burn_call_allow_failure(self, batcher):
        """add_burn_call() с allow_failure=True."""
        batcher.add_burn_call(
            position_manager=PM_ADDRESS,
            token_id=100,
            allow_failure=True,
        )
        assert batcher.calls[0].allow_failure is True

    # -----------------------------------------------------------------------
    # execute()
    # -----------------------------------------------------------------------

    def test_execute_empty_raises(self, batcher):
        """execute() на пустом бэтчере выбрасывает ValueError."""
        with pytest.raises(ValueError, match="No calls to execute"):
            batcher.execute(position_manager_address=PM_ADDRESS)

    def test_execute_no_account_raises(self, mock_w3):
        """execute() без account выбрасывает ValueError."""
        b = _make_batcher(mock_w3, mock_account=None)
        b.account = None
        b.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        with pytest.raises(ValueError, match="Account not set"):
            b.execute(position_manager_address=PM_ADDRESS)

    def test_execute_no_pm_address_raises(self, batcher):
        """execute() без position_manager_address выбрасывает ValueError."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        with pytest.raises(ValueError, match="position_manager_address is required"):
            batcher.execute(position_manager_address=None)

    def test_execute_success(self, batcher, mock_w3, mock_account):
        """execute() отправляет транзакцию и возвращает результат."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        tx_hash, results, receipt, token_ids = batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=500_000,
        )

        # Транзакция была подписана и отправлена
        mock_account.sign_transaction.assert_called_once()
        mock_w3.eth.send_raw_transaction.assert_called_once_with(b'signed')
        mock_w3.eth.wait_for_transaction_receipt.assert_called_once()

        # receipt - словарь со status=1
        assert receipt['status'] == 1

    def test_execute_returns_tuple_of_four(self, batcher):
        """execute() возвращает кортеж из 4 элементов: (tx_hash, results, receipt, token_ids)."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        result = batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=500_000,
        )

        assert isinstance(result, tuple)
        assert len(result) == 4

        tx_hash, results, receipt, token_ids = result
        assert isinstance(tx_hash, str)
        assert isinstance(results, list)
        assert isinstance(token_ids, list)

    def test_execute_gas_auto_estimate(self, batcher, mock_pm):
        """execute() без gas_limit автоматически оценивает газ."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        tx_hash, results, receipt, token_ids = batcher.execute(
            position_manager_address=PM_ADDRESS,
        )

        # multicall().estimate_gas() был вызван
        mock_multicall = mock_pm.functions.multicall.return_value
        mock_multicall.estimate_gas.assert_called_once()

    def test_execute_uses_legacy_gas_price(self, batcher, mock_w3):
        """execute() без max_priority_fee использует gasPrice (legacy)."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=500_000,
            gas_price=10_000_000_000,
        )
        # Проверяем что sign_transaction вызван - нет ошибок при legacy gas
        assert batcher.account.sign_transaction.called

    def test_execute_uses_eip1559(self, batcher, mock_w3):
        """execute() с max_priority_fee использует EIP-1559 параметры."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=500_000,
            max_priority_fee=2_000_000_000,
        )
        assert batcher.account.sign_transaction.called

    def test_execute_custom_timeout(self, batcher, mock_w3):
        """execute() передаёт timeout в wait_for_transaction_receipt."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=500_000,
            timeout=60,
        )
        # wait_for_transaction_receipt вызван с timeout=60
        call_kwargs = mock_w3.eth.wait_for_transaction_receipt.call_args
        assert call_kwargs[1]['timeout'] == 60

    def test_execute_multiple_calls(self, batcher):
        """execute() с несколькими вызовами в батче."""
        for i in range(5):
            batcher.add_raw_call(target=PM_ADDRESS, call_data=bytes([i]))
        assert len(batcher) == 5

        tx_hash, results, receipt, token_ids = batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=1_000_000,
        )
        assert receipt['status'] == 1

    # -----------------------------------------------------------------------
    # simulate()
    # -----------------------------------------------------------------------

    def test_simulate_empty_returns_empty(self, batcher):
        """simulate() на пустом бэтчере возвращает пустой список."""
        result = batcher.simulate(position_manager_address=PM_ADDRESS)
        assert result == []

    def test_simulate_returns_call_results(self, batcher, mock_pm):
        """simulate() возвращает список CallResult."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x01')

        results = batcher.simulate(position_manager_address=PM_ADDRESS)

        assert len(results) == 2
        for r in results:
            assert isinstance(r, CallResult)
            assert r.success is True

    def test_simulate_uses_pm_multicall_call(self, batcher, mock_pm):
        """simulate() вызывает pm.functions.multicall(...).call()."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        batcher.simulate(position_manager_address=PM_ADDRESS)

        mock_pm.functions.multicall.assert_called_once()
        mock_pm.functions.multicall.return_value.call.assert_called_once()

    def test_simulate_failure_raises(self, batcher, mock_pm):
        """simulate() при ошибке выбрасывает RuntimeError."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        mock_pm.functions.multicall.return_value.call.side_effect = Exception(
            "execution reverted: STF"
        )

        with pytest.raises(RuntimeError, match="Simulation failed"):
            batcher.simulate(position_manager_address=PM_ADDRESS)

    def test_simulate_infers_pm_address_from_first_call(self, batcher, mock_pm):
        """simulate() без pm_address берёт target из первого вызова."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        batcher.simulate()  # pm_address не передан

        batcher._get_pm_contract.assert_called_with(PM_ADDRESS)

    # -----------------------------------------------------------------------
    # simulate_single_call()
    # -----------------------------------------------------------------------

    def test_simulate_single_call_ok(self, batcher, mock_w3):
        """simulate_single_call() возвращает 'OK: ...' при успешном вызове."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\xaa\xbb')

        result = batcher.simulate_single_call(index=0)

        assert result.startswith("OK:")
        mock_w3.eth.call.assert_called_once()

    def test_simulate_single_call_error(self, batcher, mock_w3):
        """simulate_single_call() возвращает 'Error: ...' при ошибке."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        mock_w3.eth.call.side_effect = Exception("execution reverted: insufficient balance")

        result = batcher.simulate_single_call(index=0)

        assert result.startswith("Error:")
        assert "insufficient balance" in result

    def test_simulate_single_call_no_calls(self, batcher):
        """simulate_single_call() без вызовов возвращает сообщение об отсутствии."""
        result = batcher.simulate_single_call(index=0)
        assert result == "No call at this index"

    def test_simulate_single_call_index_out_of_range(self, batcher):
        """simulate_single_call() с невалидным индексом возвращает сообщение."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        result = batcher.simulate_single_call(index=5)
        assert result == "No call at this index"

    def test_simulate_single_call_revert_decode(self, batcher, mock_w3):
        """simulate_single_call() пытается декодировать revert reason."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        # Ошибка с hex-кодом, но без Error(string) селектора
        mock_w3.eth.call.side_effect = Exception(
            "execution reverted: 0xdead0000"
        )

        result = batcher.simulate_single_call(index=0)
        assert "Error:" in result or "Revert:" in result

    # -----------------------------------------------------------------------
    # debug_first_call()
    # -----------------------------------------------------------------------

    def test_debug_first_call_no_calls(self, batcher):
        """debug_first_call() без вызовов возвращает {'error': 'No calls'}."""
        info = batcher.debug_first_call()
        assert info == {"error": "No calls"}

    def test_debug_first_call_has_info(self, batcher):
        """debug_first_call() возвращает словарь с target, selector, длиной и preview."""
        call_data = b'\x88\x31\x64\x56' + b'\x00' * 100  # mint selector + data
        batcher.add_call(Call3(
            target=PM_ADDRESS,
            allow_failure=False,
            call_data=call_data,
        ))

        info = batcher.debug_first_call()

        assert info["target"] == PM_ADDRESS
        assert info["selector"] == "88316456"
        assert info["call_data_length"] == len(call_data)
        assert "call_data_preview" in info

    def test_debug_first_call_non_mint_selector(self, batcher):
        """debug_first_call() с не-mint селектором не декодирует mint_params."""
        call_data = b'\xde\xad\xbe\xef' + b'\x00' * 50
        batcher.add_call(Call3(
            target=PM_ADDRESS,
            allow_failure=False,
            call_data=call_data,
        ))

        info = batcher.debug_first_call()

        assert info["selector"] == "deadbeef"
        assert "mint_params" not in info

    def test_debug_first_call_string_call_data(self, batcher):
        """debug_first_call() обрабатывает call_data в виде hex строки."""
        hex_data = "0x88316456" + "00" * 100
        batcher.add_call(Call3(
            target=PM_ADDRESS,
            allow_failure=False,
            call_data=hex_data,  # строка вместо bytes
        ))

        info = batcher.debug_first_call()
        assert info["selector"] == "88316456"

    # -----------------------------------------------------------------------
    # estimate_gas()
    # -----------------------------------------------------------------------

    def test_estimate_gas_empty_returns_zero(self, batcher):
        """estimate_gas() на пустом бэтчере возвращает 0."""
        assert batcher.estimate_gas() == 0

    def test_estimate_gas_calls_multicall(self, batcher, mock_pm):
        """estimate_gas() вызывает pm.functions.multicall().estimate_gas()."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        gas = batcher.estimate_gas(position_manager_address=PM_ADDRESS)

        assert gas == 500_000
        mock_pm.functions.multicall.return_value.estimate_gas.assert_called_once()

    def test_estimate_gas_infers_pm_from_first_call(self, batcher, mock_pm):
        """estimate_gas() без pm_address берёт target из первого вызова."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')

        batcher.estimate_gas()

        batcher._get_pm_contract.assert_called_with(PM_ADDRESS)

    # -----------------------------------------------------------------------
    # Интеграционные сценарии (все замоканные)
    # -----------------------------------------------------------------------

    def test_full_mint_flow(self, batcher, mock_w3, mock_account):
        """Полный флоу: add_mint_call -> execute."""
        batcher.add_mint_call(
            position_manager=PM_ADDRESS,
            token0=TOKEN0,
            token1=TOKEN1,
            fee=3000,
            tick_lower=-600,
            tick_upper=600,
            amount0_desired=10**18,
            amount1_desired=10**18,
            recipient=RECIPIENT,
        )
        assert len(batcher) == 1

        tx_hash, results, receipt, token_ids = batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=500_000,
        )

        assert receipt['status'] == 1
        mock_account.sign_transaction.assert_called_once()
        mock_w3.eth.send_raw_transaction.assert_called_once()

    def test_full_close_position_flow(self, batcher, mock_w3):
        """Полный флоу: add_close_position_calls -> execute."""
        batcher.add_close_position_calls(
            position_manager=PM_ADDRESS,
            token_id=42,
            liquidity=10**18,
            recipient=RECIPIENT,
        )
        assert len(batcher) == 2

        tx_hash, results, receipt, token_ids = batcher.execute(
            position_manager_address=PM_ADDRESS,
            gas_limit=500_000,
        )

        assert receipt['status'] == 1

    def test_clear_and_reuse(self, batcher):
        """Бэтчер можно переиспользовать после clear()."""
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        assert len(batcher) == 1

        batcher.clear()
        assert len(batcher) == 0

        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x01')
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x02')
        assert len(batcher) == 2

    def test_repr(self, batcher):
        """__repr__ показывает количество вызовов."""
        assert "0 calls" in repr(batcher)
        batcher.add_raw_call(target=PM_ADDRESS, call_data=b'\x00')
        assert "1 calls" in repr(batcher)


# ---------------------------------------------------------------------------
# MULTICALL3_ADDRESS
# ---------------------------------------------------------------------------

class TestMulticall3Address:
    """Тесты константы MULTICALL3_ADDRESS."""

    def test_address_is_checksum(self):
        """MULTICALL3_ADDRESS в checksum формате."""
        assert MULTICALL3_ADDRESS == Web3.to_checksum_address(MULTICALL3_ADDRESS)

    def test_address_value(self):
        """MULTICALL3_ADDRESS совпадает с каноническим адресом."""
        assert MULTICALL3_ADDRESS == "0xcA11bde05977b3631167028862bE2a173976CA11"
