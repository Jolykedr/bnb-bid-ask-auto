"""
Tests for utility classes: NonceManager, DecimalsCache, GasEstimator, BatchRPC.
"""

import pytest
import threading
import time
from unittest.mock import Mock, MagicMock, patch, call
from dataclasses import dataclass

from src.utils import (
    NonceManager,
    DecimalsCache,
    GasEstimator,
    BatchRPC,
    BatchCall,
    BatchResult,
    get_token_info_batch,
    MULTICALL3_ADDRESS,
    eip1559_gas_fields,
    check_gas_price,
    set_gas_price_cap,
    get_gas_price_cap,
)


# ============================================================
# MockWeb3 helper
# ============================================================

class MockWeb3:
    """Mock Web3 для тестирования утилит."""

    def __init__(self, initial_nonce: int = 100):
        self._nonce = initial_nonce
        self.eth = MagicMock()
        self.eth.get_transaction_count = MagicMock(return_value=self._nonce)
        self.eth.gas_price = 5_000_000_000
        self.eth.call = MagicMock(return_value=b'\x00' * 32)
        self.eth.contract = MagicMock()

    def set_nonce(self, nonce: int):
        self._nonce = nonce
        self.eth.get_transaction_count.return_value = nonce

    @staticmethod
    def to_checksum_address(addr: str) -> str:
        return addr


# ============================================================
# NonceManager Tests
# ============================================================

class TestNonceManager:
    """Tests for NonceManager."""

    def test_initial_sync(self):
        """Первый get_next_nonce синхронизируется с блокчейном."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce = manager.get_next_nonce()

        assert nonce == 100
        assert manager.get_pending_count() == 1
        w3.eth.get_transaction_count.assert_called_once()

    def test_sequential_nonces(self):
        """Последовательные вызовы возвращают инкрементирующиеся nonce."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce1 = manager.get_next_nonce()
        nonce2 = manager.get_next_nonce()
        nonce3 = manager.get_next_nonce()

        assert nonce1 == 100
        assert nonce2 == 101
        assert nonce3 == 102
        assert manager.get_pending_count() == 3

    def test_confirm_transaction(self):
        """confirm_transaction удаляет nonce из pending."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce1 = manager.get_next_nonce()
        nonce2 = manager.get_next_nonce()

        assert manager.get_pending_count() == 2

        manager.confirm_transaction(nonce1)

        assert manager.get_pending_count() == 1
        assert nonce1 not in manager.get_pending_nonces()
        assert nonce2 in manager.get_pending_nonces()

    def test_release_nonce(self):
        """release_nonce удаляет nonce из pending."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonce = manager.get_next_nonce()
        assert manager.get_pending_count() == 1

        manager.release_nonce(nonce)

        assert manager.get_pending_count() == 0

    def test_cleanup_stale_nonces_on_sync(self):
        """Устаревшие nonce очищаются при синхронизации."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")
        manager._sync_interval = 0

        manager.get_next_nonce()  # 100
        manager.get_next_nonce()  # 101
        manager.get_next_nonce()  # 102

        assert manager.get_pending_count() == 3

        w3.set_nonce(102)

        manager.get_next_nonce(force_sync=True)

        pending = manager.get_pending_nonces()
        assert 100 not in pending
        assert 101 not in pending
        assert 102 in pending

    def test_cleanup_stale_nonces_manual(self):
        """Ручная очистка устаревших nonce."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        manager.get_next_nonce()  # 100
        manager.get_next_nonce()  # 101
        manager.get_next_nonce()  # 102

        w3.set_nonce(102)

        cleaned = manager.cleanup_stale_nonces()

        assert cleaned == 2
        assert manager.get_pending_count() == 1
        assert 102 in manager.get_pending_nonces()

    def test_reset(self):
        """reset очищает всё состояние."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        manager.get_next_nonce()
        manager.get_next_nonce()

        assert manager.get_pending_count() == 2

        manager.reset()

        assert manager.get_pending_count() == 0
        assert manager._current_nonce is None

    def test_external_transaction_handling(self):
        """Обработка внешних транзакций (MetaMask)."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")
        manager._sync_interval = 0

        nonce1 = manager.get_next_nonce()  # 100

        w3.set_nonce(105)

        nonce2 = manager.get_next_nonce(force_sync=True)

        assert nonce2 == 105
        assert 100 not in manager.get_pending_nonces()

    def test_thread_safety(self):
        """Потокобезопасность: параллельные get_next_nonce возвращают уникальные nonce."""
        w3 = MockWeb3(initial_nonce=0)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        nonces = []
        errors = []

        def get_nonce():
            try:
                n = manager.get_next_nonce()
                nonces.append(n)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_nonce) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(nonces) == 20
        assert len(set(nonces)) == 20  # Все уникальные

    def test_get_pending_nonces_returns_copy(self):
        """get_pending_nonces возвращает копию, а не ссылку."""
        w3 = MockWeb3(initial_nonce=100)
        manager = NonceManager(w3, "0x1234567890123456789012345678901234567890")

        manager.get_next_nonce()

        pending1 = manager.get_pending_nonces()
        pending1.add(999)

        assert 999 not in manager.get_pending_nonces()


# ============================================================
# DecimalsCache Tests
# ============================================================

class TestDecimalsCache:
    """Tests for DecimalsCache."""

    def test_known_decimals_from_cache(self):
        """Известные токены возвращаются из кэша без вызова блокчейна."""
        w3 = MagicMock()
        cache = DecimalsCache(w3)

        # USDT BSC (18 decimals)
        decimals = cache.get_decimals("0x55d398326f99059ff775485246999027b3197955")
        assert decimals == 18
        w3.eth.contract.assert_not_called()

    def test_usdc_base_has_6_decimals(self):
        """USDC на Base имеет 6 decimals."""
        w3 = MagicMock()
        cache = DecimalsCache(w3)

        decimals = cache.get_decimals("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913")
        assert decimals == 6

    def test_unknown_decimals_fetched_from_blockchain(self):
        """Неизвестные токены загружаются из блокчейна."""
        w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 8
        w3.eth.contract.return_value = mock_contract

        cache = DecimalsCache(w3)

        decimals = cache.get_decimals("0x0000000000000000000000000000000000000001")
        assert decimals == 8
        w3.eth.contract.assert_called_once()

    def test_cached_after_fetch(self):
        """После загрузки decimals кэшируются."""
        w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 6
        w3.eth.contract.return_value = mock_contract

        cache = DecimalsCache(w3)

        d1 = cache.get_decimals("0x0000000000000000000000000000000000000002")
        d2 = cache.get_decimals("0x0000000000000000000000000000000000000002")

        assert d1 == 6
        assert d2 == 6
        assert w3.eth.contract.call_count == 1

    def test_raises_on_rpc_error(self):
        """При ошибке RPC бросает RuntimeError (не молчит с 18)."""
        w3 = MagicMock()
        w3.eth.contract.side_effect = Exception("RPC error")

        cache = DecimalsCache(w3)
        with pytest.raises(RuntimeError, match="Cannot determine decimals"):
            cache.get_decimals("0x0000000000000000000000000000000000000003")

    def test_get_decimals_batch(self):
        """Batch получение decimals для нескольких токенов."""
        w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 12
        w3.eth.contract.return_value = mock_contract

        cache = DecimalsCache(w3)

        result = cache.get_decimals_batch([
            "0x55d398326f99059ff775485246999027b3197955",  # Известный: 18
            "0x0000000000000000000000000000000000000004",  # Неизвестный: 12
        ])

        assert result["0x55d398326f99059ff775485246999027b3197955"] == 18
        assert result["0x0000000000000000000000000000000000000004"] == 12

    def test_preload(self):
        """preload загружает decimals заранее."""
        w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 9
        w3.eth.contract.return_value = mock_contract

        cache = DecimalsCache(w3)
        cache.preload(["0x0000000000000000000000000000000000000005"])

        # Теперь должен быть в кэше
        w3.eth.contract.reset_mock()
        decimals = cache.get_decimals("0x0000000000000000000000000000000000000005")
        assert decimals == 9
        w3.eth.contract.assert_not_called()  # Из кэша

    def test_clear_keeps_known(self):
        """clear очищает кэш, но сохраняет известные токены."""
        w3 = MagicMock()
        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 8
        w3.eth.contract.return_value = mock_contract

        cache = DecimalsCache(w3)
        cache.get_decimals("0x0000000000000000000000000000000000000006")

        cache.clear()

        # Известный токен должен остаться
        assert cache.get_decimals("0x55d398326f99059ff775485246999027b3197955") == 18
        # Добавленный должен быть очищен (загрузится заново)
        w3.eth.contract.reset_mock()
        cache.get_decimals("0x0000000000000000000000000000000000000006")
        w3.eth.contract.assert_called_once()

    def test_case_insensitive_addresses(self):
        """Адреса нечувствительны к регистру."""
        w3 = MagicMock()
        cache = DecimalsCache(w3)

        d1 = cache.get_decimals("0x55d398326f99059ff775485246999027b3197955")
        d2 = cache.get_decimals("0x55D398326F99059FF775485246999027B3197955")

        assert d1 == d2 == 18


# ============================================================
# GasEstimator Tests
# ============================================================

class TestGasEstimator:
    """Tests for GasEstimator."""

    def test_estimate_with_successful_estimation(self):
        """Успешная оценка газа с буфером."""
        w3 = MagicMock()
        estimator = GasEstimator(w3, buffer_percent=20)

        mock_fn = MagicMock()
        mock_fn.estimate_gas.return_value = 100_000

        result = estimator.estimate(mock_fn, "0x4444444444444444444444444444444444444444")

        assert result == 120_000  # 100k + 20%

    def test_estimate_applies_buffer(self):
        """Буфер применяется корректно."""
        w3 = MagicMock()
        estimator = GasEstimator(w3, buffer_percent=50)

        mock_fn = MagicMock()
        mock_fn.estimate_gas.return_value = 200_000

        result = estimator.estimate(mock_fn, "0x4444444444444444444444444444444444444444")

        assert result == 300_000  # 200k + 50%

    def test_estimate_capped_at_max_gas(self):
        """Оценка не превышает max_gas."""
        w3 = MagicMock()
        estimator = GasEstimator(w3, buffer_percent=20)

        mock_fn = MagicMock()
        mock_fn.estimate_gas.return_value = 5_000_000

        result = estimator.estimate(mock_fn, "0x4444444444444444444444444444444444444444", max_gas=3_000_000)

        assert result == 3_000_000

    def test_estimate_fallback_on_error(self):
        """Fallback на дефолтное значение при ошибке."""
        w3 = MagicMock()
        estimator = GasEstimator(w3)

        mock_fn = MagicMock()
        mock_fn.estimate_gas.side_effect = Exception("estimation failed")

        result = estimator.estimate(mock_fn, "0x4444444444444444444444444444444444444444", default_type='approve')

        assert result == 60_000  # Default for 'approve'

    def test_estimate_fallback_on_contract_logic_error(self):
        """Fallback при ContractLogicError."""
        from web3.exceptions import ContractLogicError
        w3 = MagicMock()
        estimator = GasEstimator(w3)

        mock_fn = MagicMock()
        mock_fn.estimate_gas.side_effect = ContractLogicError("revert")

        result = estimator.estimate(mock_fn, "0x4444444444444444444444444444444444444444", default_type='mint_position')

        assert result == 500_000  # Default for 'mint_position'

    def test_estimate_defaults_for_all_types(self):
        """Проверка дефолтных значений для всех типов операций."""
        w3 = MagicMock()
        estimator = GasEstimator(w3)

        mock_fn = MagicMock()
        mock_fn.estimate_gas.side_effect = Exception("fail")

        assert estimator.estimate(mock_fn, "0x", default_type='approve') == 60_000
        assert estimator.estimate(mock_fn, "0x", default_type='transfer') == 65_000
        assert estimator.estimate(mock_fn, "0x", default_type='mint_position') == 500_000
        assert estimator.estimate(mock_fn, "0x", default_type='multicall') == 2_000_000
        assert estimator.estimate(mock_fn, "0x", default_type='swap') == 300_000

    def test_estimate_unknown_type_fallback(self):
        """Неизвестный тип возвращает 200_000."""
        w3 = MagicMock()
        estimator = GasEstimator(w3)

        mock_fn = MagicMock()
        mock_fn.estimate_gas.side_effect = Exception("fail")

        result = estimator.estimate(mock_fn, "0x", default_type='unknown_type')
        assert result == 200_000

    def test_estimate_batch_sums_with_overhead(self):
        """Batch оценка суммирует и добавляет 10% overhead."""
        w3 = MagicMock()
        estimator = GasEstimator(w3, buffer_percent=0)

        fn1 = MagicMock()
        fn1.estimate_gas.return_value = 100_000
        fn2 = MagicMock()
        fn2.estimate_gas.return_value = 200_000

        result = estimator.estimate_batch([(fn1, 0), (fn2, 0)], "0x4444444444444444444444444444444444444444")

        # (100k + 200k) * 1.1 = 330k
        assert result == 330_000

    def test_estimate_batch_capped_at_block_limit(self):
        """Batch не превышает лимит газа блока (8M)."""
        w3 = MagicMock()
        estimator = GasEstimator(w3, buffer_percent=0)

        fn = MagicMock()
        fn.estimate_gas.return_value = 5_000_000

        result = estimator.estimate_batch(
            [(fn, 0), (fn, 0), (fn, 0)],
            "0x4444444444444444444444444444444444444444",
            default_type='multicall'
        )

        # 3 calls * 3M (capped by max_gas default) = 9M, * 1.1 = 9.9M, capped at 8M
        assert result == 8_000_000  # Capped at block gas limit


# ============================================================
# BatchRPC Tests
# ============================================================

class TestBatchRPC:
    """Tests for BatchRPC."""

    def _make_batch(self, w3=None):
        """Создание BatchRPC с замоканным Web3."""
        if w3 is None:
            w3 = MagicMock()
        mock_multicall = MagicMock()
        w3.eth.contract.return_value = mock_multicall
        batch = BatchRPC.__new__(BatchRPC)
        batch.w3 = w3
        batch.multicall = mock_multicall
        batch._calls = []
        batch._decoders = []
        return batch

    def test_add_call(self):
        """add_call добавляет вызов в список."""
        batch = self._make_batch()

        batch.add_call("0x5555555555555555555555555555555555555555", b'\x01\x02', allow_failure=True)

        assert len(batch) == 1
        assert batch._calls[0].target == "0x5555555555555555555555555555555555555555"
        assert batch._calls[0].call_data == b'\x01\x02'

    def test_add_call_with_decoder(self):
        """add_call с декодером."""
        batch = self._make_batch()

        decoder = lambda data: int.from_bytes(data[:32], 'big')
        batch.add_call("0x5555555555555555555555555555555555555555", b'\x01', decoder=decoder)

        assert batch._decoders[0] is decoder

    def test_clear(self):
        """clear очищает все вызовы."""
        batch = self._make_batch()
        batch._calls = [Mock(), Mock()]
        batch._decoders = [None, None]

        batch.clear()

        assert len(batch) == 0
        assert len(batch._decoders) == 0

    def test_len(self):
        """__len__ возвращает количество вызовов."""
        batch = self._make_batch()
        assert len(batch) == 0

        batch._calls.append(Mock())
        assert len(batch) == 1

    def test_execute_empty_returns_empty(self):
        """execute с пустым списком возвращает []."""
        batch = self._make_batch()
        result = batch.execute()
        assert result == []

    def test_execute_decodes_results(self):
        """execute декодирует результаты через decoder."""
        batch = self._make_batch()

        # Добавляем вызов с декодером
        decoder = lambda data: 42
        batch._calls.append(BatchCall(target="0x", call_data=b'', allow_failure=True))
        batch._decoders.append(decoder)

        # Мок multicall возвращает результат
        batch.multicall.functions.aggregate3.return_value.call.return_value = [
            (True, b'\x00' * 32)
        ]

        result = batch.execute()

        assert result == [42]

    def test_execute_handles_failed_call_with_allow_failure(self):
        """Неудачный вызов с allow_failure возвращает None."""
        batch = self._make_batch()

        batch._calls.append(BatchCall(target="0x", call_data=b'', allow_failure=True))
        batch._decoders.append(lambda d: 42)

        batch.multicall.functions.aggregate3.return_value.call.return_value = [
            (False, b'')
        ]

        result = batch.execute()

        assert result == [None]

    def test_execute_raises_on_failed_required_call(self):
        """Неудачный вызов без allow_failure вызывает исключение."""
        batch = self._make_batch()

        batch._calls.append(BatchCall(target="0x", call_data=b'', allow_failure=False))
        batch._decoders.append(lambda d: 42)

        batch.multicall.functions.aggregate3.return_value.call.return_value = [
            (False, b'')
        ]

        with pytest.raises(Exception, match="Required call"):
            batch.execute()

    def test_execute_fallback_on_multicall_error(self):
        """При ошибке multicall используется fallback."""
        w3 = MagicMock()
        batch = self._make_batch(w3)

        batch._calls.append(BatchCall(target="0xAddr", call_data=b'\x01', allow_failure=True))
        decoder = lambda d: 99
        batch._decoders.append(decoder)

        # Multicall fails
        batch.multicall.functions.aggregate3.return_value.call.side_effect = Exception("multicall error")

        # Fallback individual call succeeds
        w3.eth.call.return_value = b'\x00' * 32

        result = batch.execute()

        assert result == [99]
        w3.eth.call.assert_called_once()

    def test_fallback_execute_handles_individual_failures(self):
        """Fallback обрабатывает ошибки отдельных вызовов."""
        w3 = MagicMock()
        batch = self._make_batch(w3)

        batch._calls.append(BatchCall(target="0x1", call_data=b'', allow_failure=True))
        batch._decoders.append(lambda d: 42)

        w3.eth.call.side_effect = Exception("call failed")

        result = batch._fallback_execute()

        assert result == [None]

    def test_fallback_execute_raises_on_required_failure(self):
        """Fallback вызывает исключение для required call."""
        w3 = MagicMock()
        batch = self._make_batch(w3)

        batch._calls.append(BatchCall(target="0x1", call_data=b'', allow_failure=False))
        batch._decoders.append(lambda d: 42)

        w3.eth.call.side_effect = Exception("call failed")

        with pytest.raises(Exception):
            batch._fallback_execute()


# ============================================================
# BatchCall / BatchResult Dataclass Tests
# ============================================================

class TestDataclasses:
    """Tests for dataclasses."""

    def test_batch_call_defaults(self):
        """BatchCall имеет allow_failure=True по умолчанию."""
        call = BatchCall(target="0x1", call_data=b'\x01')
        assert call.allow_failure is True

    def test_batch_result_fields(self):
        """BatchResult содержит success и return_data."""
        result = BatchResult(success=True, return_data=b'\x01')
        assert result.success is True
        assert result.return_data == b'\x01'


# ============================================================
# eip1559_gas_fields + check_gas_price
# ============================================================

class TestGasHelpers:
    """Тесты EIP-1559 хелпера и gas-price cap."""

    def setup_method(self):
        # Reset cap to disabled before each test (module-level state).
        set_gas_price_cap(0)

    def teardown_method(self):
        set_gas_price_cap(0)

    def _w3_with_eip1559(self, base_fee: int = 1_000_000_000, priority: int = 100_000_000, gas_price: int = 5_000_000_000, batched: bool = True):
        """Build a Mock w3 that supports EIP-1559.

        Args:
            batched: True → batch_requests().execute() returns the 3 values.
                     False → batch_requests raises (forces sequential fallback).
        """
        w3 = MagicMock()
        w3.eth.get_block.return_value = {"baseFeePerGas": base_fee}
        w3.eth.max_priority_fee = priority
        w3.eth.gas_price = gas_price
        if batched:
            mock_batch = MagicMock()
            mock_batch.__enter__ = Mock(return_value=mock_batch)
            mock_batch.__exit__ = Mock(return_value=False)
            mock_batch.execute = Mock(return_value=[
                gas_price,
                {"baseFeePerGas": base_fee},
                priority,
            ])
            w3.batch_requests = Mock(return_value=mock_batch)
        else:
            # Force sequential fallback by raising on batch
            w3.batch_requests = Mock(side_effect=Exception("provider does not support batching"))
        return w3

    def _w3_legacy(self, gas_price: int = 5_000_000_000, batched: bool = True):
        """Build a Mock w3 without baseFeePerGas (legacy chain)."""
        w3 = MagicMock()
        w3.eth.get_block.return_value = {}  # no baseFeePerGas
        w3.eth.gas_price = gas_price
        if batched:
            mock_batch = MagicMock()
            mock_batch.__enter__ = Mock(return_value=mock_batch)
            mock_batch.__exit__ = Mock(return_value=False)
            # Sequential mocks return only the first 3, but for legacy with no
            # base_fee + needs priority — match real behavior: gas_price + block (no baseFee) + priority.
            mock_batch.execute = Mock(return_value=[
                gas_price,
                {},
                0,  # priority — won't be used since base_fee is None
            ])
            w3.batch_requests = Mock(return_value=mock_batch)
        else:
            w3.batch_requests = Mock(side_effect=Exception("not supported"))
        return w3

    def test_eip1559_returns_type2_fields(self):
        w3 = self._w3_with_eip1559(base_fee=2_000_000_000, priority=100_000_000)
        fields = eip1559_gas_fields(w3)
        assert fields["type"] == 2
        assert fields["maxPriorityFeePerGas"] == 100_000_000
        assert fields["maxFeePerGas"] == 2_000_000_000 * 2 + 100_000_000

    def test_eip1559_priority_fee_override(self):
        w3 = self._w3_with_eip1559(base_fee=1_000_000_000, priority=999)
        # node says 999, override says 5_000_000
        fields = eip1559_gas_fields(w3, priority_fee_override=5_000_000)
        assert fields["maxPriorityFeePerGas"] == 5_000_000
        assert fields["maxFeePerGas"] == 1_000_000_000 * 2 + 5_000_000

    def test_eip1559_falls_back_to_legacy_when_no_base_fee(self):
        w3 = self._w3_legacy(gas_price=3_000_000_000)
        fields = eip1559_gas_fields(w3)
        assert fields == {"gasPrice": 3_000_000_000}

    def test_check_gas_price_under_cap_passes(self):
        w3 = MagicMock()
        w3.eth.gas_price = 4_000_000_000  # 4 gwei
        check_gas_price(w3, cap_gwei=10)  # cap=10, gas=4 → ok

    def test_check_gas_price_over_cap_raises(self):
        w3 = MagicMock()
        w3.eth.gas_price = 50_000_000_000  # 50 gwei
        with pytest.raises(RuntimeError, match="exceeds cap"):
            check_gas_price(w3, cap_gwei=10)

    def test_check_gas_price_zero_cap_disabled(self):
        w3 = MagicMock()
        w3.eth.gas_price = 1_000_000_000_000  # 1000 gwei
        check_gas_price(w3, cap_gwei=0)  # cap=0 → disabled, no raise
        check_gas_price(w3, cap_gwei=-1)  # negative → also disabled

    def test_set_gas_price_cap_global(self):
        set_gas_price_cap(15.5)
        assert get_gas_price_cap() == 15.5
        set_gas_price_cap(0)
        assert get_gas_price_cap() == 0
        set_gas_price_cap(-5)  # negative clamped to 0
        assert get_gas_price_cap() == 0

    def test_eip1559_enforces_global_cap(self):
        """eip1559_gas_fields must raise if global cap is exceeded — protects all
        TX-sending sites without per-site changes."""
        set_gas_price_cap(5)  # 5 gwei cap
        w3 = self._w3_with_eip1559(gas_price=20_000_000_000)  # 20 gwei
        with pytest.raises(RuntimeError, match="exceeds cap"):
            eip1559_gas_fields(w3)

    def test_eip1559_no_cap_does_not_raise(self):
        # cap disabled by default in setup_method
        w3 = self._w3_with_eip1559(gas_price=999_000_000_000)  # 999 gwei
        fields = eip1559_gas_fields(w3)
        assert fields["type"] == 2  # builds normally

    # ── batch_requests optimization tests ─────────────────────────────────

    def test_eip1559_uses_batch_requests_when_available(self):
        """Single batched HTTP POST instead of 3 sequential RPCs.

        Note: in real web3.py, `batch.add(w.eth.get_block(...))` defers the call —
        no actual RPC is made until batch.execute(). With Mock w3, the call is
        still recorded as a method invocation, so we can't assert "not called".
        Instead we assert that batch_requests was used AND results came from
        the batch (not from the sequential fallback's separate RPC dispatch).
        """
        w3 = self._w3_with_eip1559(base_fee=2_000_000_000, priority=100_000_000, batched=True)
        fields = eip1559_gas_fields(w3)
        # Primary evidence: batch_requests was called (one HTTP POST under the hood)
        w3.batch_requests.assert_called_once()
        # Result must be correct (verifies values came from batch.execute())
        assert fields["type"] == 2
        assert fields["maxFeePerGas"] == 2_000_000_000 * 2 + 100_000_000
        assert fields["maxPriorityFeePerGas"] == 100_000_000

    def test_eip1559_falls_back_to_sequential_when_batch_unsupported(self):
        """If batch_requests raises (old provider), helper falls back to 3 sequential calls."""
        w3 = self._w3_with_eip1559(base_fee=1_000_000_000, priority=50_000_000, batched=False)
        fields = eip1559_gas_fields(w3)
        # batch_requests was attempted
        w3.batch_requests.assert_called_once()
        # Sequential calls happened
        w3.eth.get_block.assert_called_once_with("latest")
        # Result still correct
        assert fields["type"] == 2
        assert fields["maxFeePerGas"] == 1_000_000_000 * 2 + 50_000_000

    def test_eip1559_batch_skips_priority_when_override_provided(self):
        """priority_fee_override → batch should NOT include max_priority_fee."""
        w3 = self._w3_with_eip1559(base_fee=1_000_000_000, priority=999, batched=True)
        # Override the batch.execute to return only 2 items (gas_price + block, no priority)
        gas_price = 5_000_000_000
        mock_batch = MagicMock()
        mock_batch.__enter__ = Mock(return_value=mock_batch)
        mock_batch.__exit__ = Mock(return_value=False)
        mock_batch.execute = Mock(return_value=[
            gas_price,
            {"baseFeePerGas": 1_000_000_000},
            # Note: only 2 entries — priority_fee_override means we don't queue max_priority_fee
        ])
        w3.batch_requests = Mock(return_value=mock_batch)

        fields = eip1559_gas_fields(w3, priority_fee_override=7_000_000)
        assert fields["maxPriorityFeePerGas"] == 7_000_000
        assert fields["maxFeePerGas"] == 1_000_000_000 * 2 + 7_000_000

    def test_eip1559_cap_uses_batched_gas_price_no_extra_rpc(self):
        """Cap check uses gas_price from the batch — no separate w3.eth.gas_price call."""
        set_gas_price_cap(5)  # 5 gwei
        w3 = self._w3_with_eip1559(gas_price=20_000_000_000, batched=True)  # 20 gwei
        with pytest.raises(RuntimeError, match="exceeds cap"):
            eip1559_gas_fields(w3)
        # Cap raised — but the batch was the only RPC. No extra gas_price read.
        w3.batch_requests.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
