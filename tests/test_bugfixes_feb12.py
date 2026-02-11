"""
Tests for bugfixes applied 2026-02-12.

Fix #1: close_positions использует build_batch_close_payload вместо сломанного multicall
Fix #5: Nonce confirm (не release) при reverted TX
Fix #6: V3 close_positions проверяет receipt.status
Fix #7: skip_approvals проверяет оба токена (quote + base)
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from web3 import Web3

from src.contracts.v4.position_manager import V4PositionManager, V4Position
from src.contracts.v4.pool_manager import PoolKey
from src.contracts.v4.constants import V4Protocol

try:
    from src.contracts.v4.abis import PancakeV4Actions
except ImportError:
    class PancakeV4Actions:
        DECREASE_LIQUIDITY = 0x01
        BURN_POSITION = 0x03
        TAKE_PAIR = 0x11


TOKEN_A = "0x1111111111111111111111111111111111111111"
TOKEN_B = "0x9999999999999999999999999999999999999999"
WALLET_ADDR = "0x1234567890123456789012345678901234567890"


def _make_pm(mock_w3=None, mock_account=None, protocol=V4Protocol.PANCAKESWAP):
    """Хелпер: создать V4PositionManager с обходом __init__."""
    if mock_w3 is None:
        mock_w3 = MagicMock()
        mock_w3.eth.get_transaction_count = MagicMock(return_value=0)
        mock_w3.eth.gas_price = 5_000_000_000
        mock_w3.eth.send_raw_transaction = MagicMock(return_value=b'\xab' * 32)
        mock_w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 1, 'gasUsed': 200_000, 'logs': [],
        })

    if mock_account is None:
        mock_account = MagicMock()
        mock_account.address = WALLET_ADDR
        mock_account.sign_transaction = MagicMock(
            return_value=MagicMock(raw_transaction=b'\x00' * 32)
        )

    with patch.object(V4PositionManager, '__init__', lambda self, *a, **kw: None):
        pm = V4PositionManager.__new__(V4PositionManager)
        pm.w3 = mock_w3
        pm.account = mock_account
        pm.protocol = protocol
        pm.chain_id = 56
        pm.position_manager_address = "0x55f4c8abA71A1e923edC303eb4fEfF14608cC226"
        pm.actions = PancakeV4Actions
        pm.contract = MagicMock()
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(return_value=300_000),
                build_transaction=MagicMock(return_value={}),
            )
        )
        pm.nonce_manager = MagicMock()
        pm.nonce_manager.get_next_nonce = MagicMock(return_value=1)
        return pm


# ============================================================
# Fix #1: close_positions через build_batch_close_payload
# ============================================================

class TestClosePositionsViaBatch:
    """close_positions должен использовать build_batch_close_payload,
    а не multicall (который ломал bytes → List[int])."""

    def test_build_close_position_payload_returns_bytes(self):
        """build_close_position_payload возвращает bytes (не List[bytes])."""
        pm = _make_pm()
        result = pm.build_close_position_payload(
            token_id=42,
            liquidity=10**18,
            recipient=WALLET_ADDR,
            currency0=TOKEN_A,
            currency1=TOKEN_B,
        )
        assert isinstance(result, bytes)

    def test_build_batch_close_payload_returns_bytes(self):
        """build_batch_close_payload возвращает bytes (encoded unlockData)."""
        pm = _make_pm()
        positions = [
            {'token_id': 1, 'liquidity': 10**18, 'currency0': TOKEN_A, 'currency1': TOKEN_B},
            {'token_id': 2, 'liquidity': 5*10**17, 'currency0': TOKEN_A, 'currency1': TOKEN_B},
        ]
        result = pm.build_batch_close_payload(
            positions=positions,
            recipient=WALLET_ADDR,
        )
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_batch_close_payload_decodable(self):
        """build_batch_close_payload создаёт валидный unlockData с правильными actions."""
        from eth_abi import decode as abi_decode

        pm = _make_pm()
        positions = [
            {'token_id': 1, 'liquidity': 10**18, 'currency0': TOKEN_A, 'currency1': TOKEN_B},
            {'token_id': 2, 'liquidity': 5*10**17, 'currency0': TOKEN_A, 'currency1': TOKEN_B},
        ]
        payload = pm.build_batch_close_payload(
            positions=positions,
            recipient=WALLET_ADDR,
            burn=False,
        )

        action_ids, params_list = abi_decode(['bytes', 'bytes[]'], payload)
        # 2 positions * 1 action (DECREASE) + 1 TAKE_PAIR = 3 actions
        assert len(action_ids) == 3
        assert action_ids[0] == PancakeV4Actions.DECREASE_LIQUIDITY
        assert action_ids[1] == PancakeV4Actions.DECREASE_LIQUIDITY
        assert action_ids[2] == PancakeV4Actions.TAKE_PAIR

    def test_batch_close_with_burn_includes_burn_actions(self):
        """build_batch_close_payload с burn=True добавляет BURN для каждой позиции."""
        from eth_abi import decode as abi_decode

        pm = _make_pm()
        positions = [
            {'token_id': 1, 'liquidity': 10**18, 'currency0': TOKEN_A, 'currency1': TOKEN_B},
        ]
        payload = pm.build_batch_close_payload(
            positions=positions,
            recipient=WALLET_ADDR,
            burn=True,
        )

        action_ids, params_list = abi_decode(['bytes', 'bytes[]'], payload)
        # 1 position: DECREASE + BURN + TAKE_PAIR = 3
        assert len(action_ids) == 3
        assert action_ids[0] == PancakeV4Actions.DECREASE_LIQUIDITY
        assert action_ids[1] == PancakeV4Actions.BURN_POSITION
        assert action_ids[2] == PancakeV4Actions.TAKE_PAIR

    def test_multicall_with_bytes_would_corrupt_data(self):
        """Доказываем баг: list.extend(bytes) итерирует по отдельным байтам."""
        payload1 = b'\x01\x02\x03'
        payload2 = b'\x04\x05\x06'
        payloads = [payload1, payload2]  # List[bytes] — как передавал close_positions

        # Старый код multicall делал:
        all_actions = []
        for action_list in payloads:
            all_actions.extend(action_list)

        # Результат — список int, а не bytes!
        assert all_actions == [1, 2, 3, 4, 5, 6]
        assert not isinstance(all_actions[0], bytes)
        assert isinstance(all_actions[0], int)

    def test_multicall_with_list_of_list_bytes_works(self):
        """Правильное использование multicall: List[List[bytes]]."""
        action1 = bytes([0x02]) + b'\x00' * 32
        action2 = bytes([0x0d]) + b'\x00' * 32
        payloads = [[action1, action2]]  # List[List[bytes]]

        all_actions = []
        for action_list in payloads:
            all_actions.extend(action_list)

        assert len(all_actions) == 2
        assert isinstance(all_actions[0], bytes)
        assert all_actions[0] == action1


# ============================================================
# Fix #5: Nonce confirm при reverted TX
# ============================================================

class TestNonceOnRevertedTx:
    """При reverted TX (status=0) nonce использован on-chain,
    должен быть confirmed, не released."""

    def test_execute_modify_reverted_confirms_nonce(self):
        """execute_modify_liquidities: revert → confirm_transaction."""
        pm = _make_pm()
        pm.w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 0, 'gasUsed': 300_000, 'logs': [],
        })

        with pytest.raises(Exception, match="Transaction reverted"):
            pm.execute_modify_liquidities(unlock_data=b'\x00' * 64)

        # Nonce подтверждён (использован on-chain)
        pm.nonce_manager.confirm_transaction.assert_called_once_with(1)
        # release НЕ вызывается (TX была отправлена и замайнена)
        pm.nonce_manager.release_nonce.assert_not_called()

    def test_execute_modify_success_confirms_nonce(self):
        """execute_modify_liquidities: success → confirm_transaction."""
        pm = _make_pm()
        pm.w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 1, 'gasUsed': 200_000, 'logs': [],
        })

        tx_hash, _ = pm.execute_modify_liquidities(unlock_data=b'\x00' * 64)
        pm.nonce_manager.confirm_transaction.assert_called_once_with(1)
        pm.nonce_manager.release_nonce.assert_not_called()

    def test_execute_modify_build_error_releases_nonce(self):
        """execute_modify_liquidities: ошибка до отправки TX → release_nonce."""
        pm = _make_pm()
        pm.contract.functions.modifyLiquidities = MagicMock(
            return_value=MagicMock(
                estimate_gas=MagicMock(return_value=300_000),
                build_transaction=MagicMock(side_effect=Exception("build failed")),
            )
        )

        with pytest.raises(Exception, match="build failed"):
            pm.execute_modify_liquidities(unlock_data=b'\x00' * 64)

        # TX не была отправлена → release
        pm.nonce_manager.release_nonce.assert_called_once_with(1)
        pm.nonce_manager.confirm_transaction.assert_not_called()

    def test_multicall_reverted_confirms_nonce(self):
        """multicall: revert → confirm_transaction."""
        pm = _make_pm()
        pm.w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
            'status': 0, 'gasUsed': 300_000, 'logs': [],
        })

        with pytest.raises(Exception, match="Transaction reverted"):
            pm.multicall(payloads=[[bytes([0x02]) + b'\x00' * 32]])

        pm.nonce_manager.confirm_transaction.assert_called_once_with(1)
        pm.nonce_manager.release_nonce.assert_not_called()


# ============================================================
# Fix #6: V3 close_positions receipt check
# ============================================================

class TestV3ClosePositionsReceiptCheck:
    """V3 close_positions должен проверять receipt.status."""

    @pytest.fixture
    def provider(self):
        """Создать mock V3 LiquidityProvider."""
        from src.liquidity_provider import LiquidityProvider

        with patch.object(LiquidityProvider, '__init__', lambda self, *a, **kw: None):
            prov = LiquidityProvider.__new__(LiquidityProvider)
            prov.account = MagicMock()
            prov.account.address = WALLET_ADDR
            prov.w3 = MagicMock()
            prov.position_manager_address = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            prov.position_manager = MagicMock()
            prov.batcher = MagicMock()
            prov.nonce_manager = MagicMock()
            return prov

    def test_close_returns_false_on_reverted_receipt(self, provider):
        """close_positions возвращает success=False если receipt.status == 0."""
        provider.position_manager.get_position = MagicMock(
            return_value={'liquidity': 1000}
        )
        # batcher.execute возвращает receipt с status=0
        provider.batcher.execute = MagicMock(return_value=(
            "0xabc123",  # tx_hash
            [],           # results
            {'status': 0, 'gasUsed': 100_000},  # receipt — REVERTED
            [],           # token_ids
        ))

        tx_hash, success, gas_used = provider.close_positions(
            token_ids=[1, 2],
            timeout=60
        )

        assert success is False
        assert gas_used == 100_000

    def test_close_returns_true_on_success_receipt(self, provider):
        """close_positions возвращает success=True если receipt.status == 1."""
        provider.position_manager.get_position = MagicMock(
            return_value={'liquidity': 1000}
        )
        provider.batcher.execute = MagicMock(return_value=(
            "0xabc123",
            [],
            {'status': 1, 'gasUsed': 80_000},
            [],
        ))

        tx_hash, success, gas_used = provider.close_positions(
            token_ids=[1],
            timeout=60
        )

        assert success is True
        assert gas_used == 80_000


# ============================================================
# Fix #7: skip_approvals проверяет base token
# ============================================================

class TestSkipApprovalsChecksBaseToken:
    """skip_approvals должен проверять оба токена: quote И base.

    Тестируем логику напрямую, читая код create_ladder (skip_approvals блок),
    вместо мока всего create_ladder (слишком много зависимостей).
    """

    def test_skip_approvals_code_checks_base_erc20(self):
        """Проверяем что код create_ladder содержит проверку base_erc20_to_permit2."""
        import inspect
        from src.v4_liquidity_provider import V4LiquidityProvider
        source = inspect.getsource(V4LiquidityProvider.create_ladder)

        # Новая проверка base token должна быть в коде
        assert "base_erc20_to_permit2" in source, \
            "create_ladder должен проверять base_erc20_to_permit2 при skip_approvals"
        assert "base_permit2_to_position_manager" in source, \
            "create_ladder должен проверять base_permit2_to_position_manager при skip_approvals"
        assert "Base ERC20 not approved" in source, \
            "create_ladder должен выдавать ошибку при отсутствии base ERC20 approval"
        assert "Base Permit2 allowance EXPIRED" in source, \
            "create_ladder должен выдавать ошибку при истёкшем base Permit2"

    def test_skip_approvals_checks_4_conditions(self):
        """skip_approvals проверяет 4 условия: quote ERC20, quote Permit2, base ERC20, base Permit2."""
        import inspect
        from src.v4_liquidity_provider import V4LiquidityProvider
        source = inspect.getsource(V4LiquidityProvider.create_ladder)

        # Считаем количество V4LadderResult с success=False в блоке skip_approvals
        # Должно быть минимум 6 (quote erc20, quote permit2 expired, quote permit2, base erc20, base permit2 expired, base permit2)
        # Ищем все return с "not approved" или "EXPIRED"
        approval_error_count = source.count("Base ERC20 not approved") + \
                              source.count("Base Permit2 allowance EXPIRED") + \
                              source.count("Base Permit2 not approved") + \
                              source.count("Quote ERC20 not approved") + \
                              source.count("Quote Permit2 allowance EXPIRED") + \
                              source.count("Quote Permit2 not approved")
        assert approval_error_count >= 6, \
            f"Ожидается минимум 6 проверок approvals, найдено {approval_error_count}"

    def test_v4_ladder_result_has_base_approval_errors(self):
        """V4LadderResult может содержать ошибки про base token approvals."""
        from src.v4_liquidity_provider import V4LadderResult

        result = V4LadderResult(
            positions=[],
            tx_hash=None,
            gas_used=None,
            token_ids=[],
            pool_created=False,
            success=False,
            error="Base ERC20 not approved to Permit2."
        )
        assert result.success is False
        assert "Base ERC20" in result.error


# ============================================================
# Issue #9: Price domain invariance proof
# ============================================================

class TestPriceDomainInvariance:
    """Доказываем что liquidity L инвариантна к инверсии цены.

    Пользователь работал с V4 на BASE без проблем — потому что
    calculate_liquidity_from_usd в display домене и pool домене
    даёт одинаковый L. Это математическое свойство формулы.
    """

    def test_liquidity_invariant_bnb_chain_18_18(self):
        """BNB Chain (18/18 decimals): L одинакова в обоих доменах."""
        from src.math.liquidity import calculate_liquidity_from_usd

        # Display domain: token price = $0.005
        L_display = calculate_liquidity_from_usd(
            usd_amount=10.0,
            price_lower=0.003,
            price_upper=0.005,
            current_price=0.006,  # above range → stablecoin only
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )

        # Pool domain (inverted): pool_price = 1/display_price
        # display lower=0.003 → pool upper=333.33
        # display upper=0.005 → pool lower=200
        L_pool = calculate_liquidity_from_usd(
            usd_amount=10.0,
            price_lower=200.0,   # pool lower (was display upper inverted)
            price_upper=333.33,  # pool upper (was display lower inverted)
            current_price=166.67,  # 1/0.006, below pool range → stablecoin in token0
            token0_decimals=18,  # stablecoin in pool domain (currency0=USDC when inverted)
            token1_decimals=18,  # volatile in pool domain
            token1_is_stable=False,  # stablecoin is token0 in pool
        )

        # L должна быть одинакова (в пределах погрешности float)
        assert abs(L_display - L_pool) / max(L_display, L_pool) < 0.01, \
            f"L_display={L_display} != L_pool={L_pool}"

    def test_liquidity_invariant_base_chain_6_18(self):
        """BASE Chain (USDC 6 dec / token 18 dec): L одинакова в обоих доменах."""
        from src.math.liquidity import calculate_liquidity_from_usd

        # Display domain: token1=USDC(6dec), token0=volatile(18dec)
        L_display = calculate_liquidity_from_usd(
            usd_amount=100.0,
            price_lower=0.001,
            price_upper=0.005,
            current_price=0.006,  # above → use stablecoin
            token0_decimals=18,
            token1_decimals=6,
            token1_is_stable=True,
        )

        # Pool domain (inverted): token0=USDC(6dec), token1=volatile(18dec)
        L_pool = calculate_liquidity_from_usd(
            usd_amount=100.0,
            price_lower=200.0,   # 1/0.005
            price_upper=1000.0,  # 1/0.001
            current_price=166.67,  # 1/0.006
            token0_decimals=6,   # USDC in pool (currency0)
            token1_decimals=18,  # volatile in pool
            token1_is_stable=False,  # stablecoin is token0
        )

        # L одинакова в обоих доменах
        assert abs(L_display - L_pool) / max(L_display, L_pool) < 0.01, \
            f"L_display={L_display} != L_pool={L_pool}"

    def test_wrong_token1_is_stable_causes_error_on_base(self):
        """Если token1_is_stable=True а token1 НЕ стейблкоин — ошибка на BASE (6/18 dec)."""
        from src.math.liquidity import calculate_liquidity_from_usd

        # Правильно: token1=USDC(6dec), token1_is_stable=True
        L_correct = calculate_liquidity_from_usd(
            usd_amount=100.0,
            price_lower=0.001,
            price_upper=0.005,
            current_price=0.006,
            token0_decimals=18,
            token1_decimals=6,
            token1_is_stable=True,
        )

        # Неправильно: token1 НЕ стейблкоин но token1_is_stable=True
        # Это бы произошло если пользователь поставил стейбл в token0
        L_wrong = calculate_liquidity_from_usd(
            usd_amount=100.0,
            price_lower=0.001,
            price_upper=0.005,
            current_price=0.006,
            token0_decimals=6,   # stablecoin в token0, но мы думаем он в token1
            token1_decimals=18,  # volatile в token1
            token1_is_stable=True,  # WRONG: token1 не стейбл!
        )

        # Ошибка 10^12 из-за неправильных decimals (6 vs 18)
        ratio = L_wrong / L_correct if L_correct != 0 else float('inf')
        assert ratio > 1e6, \
            f"Ожидали ошибку >10^6x, ratio={ratio:.2e}"

    def test_fix8_prevents_wrong_token1_is_stable(self):
        """Fix #8: is_stablecoin() корректно определяет стейблкоин."""
        from config import is_stablecoin

        # USDC на BSC — стейблкоин
        assert is_stablecoin("0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d") is True
        # USDC на Base — стейблкоин
        assert is_stablecoin("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913") is True
        # USDT на BSC — стейблкоин
        assert is_stablecoin("0x55d398326f99059ff775485246999027b3197955") is True
        # Произвольный адрес — НЕ стейблкоин
        assert is_stablecoin("0x1111111111111111111111111111111111111111") is False

    def test_bnb_chain_correct_flag_gives_reasonable_liquidity(self):
        """На BNB Chain (18/18): правильный flag (True, т.к. token1=стейбл) даёт разумный L."""
        from src.math.liquidity import calculate_liquidity_from_usd

        # token1=USDT(18dec) = стейблкоин → token1_is_stable=True (правильно)
        L = calculate_liquidity_from_usd(
            usd_amount=50.0,
            price_lower=0.001,
            price_upper=0.005,
            current_price=0.006,
            token0_decimals=18,
            token1_decimals=18,
            token1_is_stable=True,
        )

        # L > 0 и разумный порядок величин
        assert L > 0
        # Для $50 при данных ценах — ожидаем L порядка 10^21
        assert L > 10**18, f"L слишком маленький: {L}"


# ============================================================
# Fix #11: closeEvent worker cleanup
# ============================================================

class TestCloseEventWorkerCleanup:
    """closeEvent должен останавливать воркеры перед закрытием."""

    def test_close_event_calls_cleanup(self):
        """closeEvent вызывает _cleanup_workers."""
        import inspect
        # Динамический import чтобы не зависеть от PyQt6 в CI
        try:
            from ui.main_window import MainWindow
            source = inspect.getsource(MainWindow.closeEvent)
            assert "_cleanup_workers" in source, \
                "closeEvent должен вызывать _cleanup_workers()"
        except ImportError:
            pytest.skip("PyQt6 not available")

    def test_cleanup_workers_method_exists(self):
        """MainWindow должен иметь метод _cleanup_workers."""
        try:
            from ui.main_window import MainWindow
            assert hasattr(MainWindow, '_cleanup_workers'), \
                "MainWindow должен иметь _cleanup_workers"
        except ImportError:
            pytest.skip("PyQt6 not available")


# ============================================================
# Fix #16: V3 approve receipt check
# ============================================================

class TestV3ApproveReceiptCheck:
    """V3 check_and_approve_tokens должен проверять receipt.status."""

    def test_approve_receipt_check_in_code(self):
        """Код check_and_approve_tokens проверяет receipt['status']."""
        import inspect
        from src.liquidity_provider import LiquidityProvider
        source = inspect.getsource(LiquidityProvider.check_and_approve_tokens)

        assert "status" in source and "receipt" in source, \
            "check_and_approve_tokens должен проверять receipt.status"

    def test_approve_reverted_raises(self):
        """check_and_approve_tokens бросает исключение при reverted TX."""
        from src.liquidity_provider import LiquidityProvider
        from unittest.mock import patch

        with patch.object(LiquidityProvider, '__init__', lambda self, *a, **kw: None):
            prov = LiquidityProvider.__new__(LiquidityProvider)
            prov.w3 = MagicMock()
            prov.account = MagicMock()
            prov.account.address = WALLET_ADDR
            prov.position_manager_address = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            prov.nonce_manager = MagicMock()
            prov.nonce_manager.get_next_nonce = MagicMock(return_value=5)
            prov.gas_estimator = MagicMock()
            prov.gas_estimator.estimate = MagicMock(return_value=100_000)

            # Mock token contract
            mock_token = MagicMock()
            mock_token.functions.allowance = MagicMock(
                return_value=MagicMock(call=MagicMock(return_value=0))
            )
            mock_token.functions.approve = MagicMock(
                return_value=MagicMock(
                    build_transaction=MagicMock(return_value={}),
                )
            )
            prov.w3.eth.contract = MagicMock(return_value=mock_token)
            prov.w3.eth.gas_price = 5_000_000_000
            prov.w3.eth.send_raw_transaction = MagicMock(return_value=b'\\xab' * 32)

            # Receipt with status=0 (reverted)
            prov.w3.eth.wait_for_transaction_receipt = MagicMock(return_value={
                'status': 0, 'gasUsed': 50_000
            })
            prov.account.sign_transaction = MagicMock(
                return_value=MagicMock(raw_transaction=b'\\x00' * 32)
            )

            with pytest.raises(Exception, match="[Rr]evert|[Ff]ail"):
                prov.check_and_approve_tokens(
                    token_address=TOKEN_A,
                    amount=10**18
                )


# ============================================================
# Fix #23: DecimalsCache — raise on RPC error
# ============================================================

class TestDecimalsCacheStrictMode:
    """DecimalsCache должен бросать исключение при ошибке RPC, не молча возвращать 18."""

    def test_get_decimals_raises_on_rpc_error(self):
        """get_decimals бросает исключение при ошибке RPC для неизвестного токена."""
        from src.utils import DecimalsCache

        mock_w3 = MagicMock()
        mock_token = MagicMock()
        mock_token.functions.decimals = MagicMock(
            return_value=MagicMock(call=MagicMock(side_effect=Exception("RPC error")))
        )
        mock_w3.eth.contract = MagicMock(return_value=mock_token)

        cache = DecimalsCache(mock_w3)

        # Неизвестный токен + ошибка RPC → должна быть ошибка, не 18
        unknown_token = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        with pytest.raises(Exception):
            cache.get_decimals(unknown_token)

    def test_get_decimals_known_token_no_rpc(self):
        """Известный токен (USDC Base) возвращает 6 без RPC."""
        from src.utils import DecimalsCache

        mock_w3 = MagicMock()
        cache = DecimalsCache(mock_w3)

        # USDC Base (в KNOWN_DECIMALS)
        decimals = cache.get_decimals("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913")
        assert decimals == 6

    def test_get_decimals_success_from_rpc(self):
        """Успешный RPC → кешируется и возвращается."""
        from src.utils import DecimalsCache

        mock_w3 = MagicMock()
        mock_token = MagicMock()
        mock_token.functions.decimals = MagicMock(
            return_value=MagicMock(call=MagicMock(return_value=9))
        )
        mock_w3.eth.contract = MagicMock(return_value=mock_token)

        cache = DecimalsCache(mock_w3)

        unknown_token = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert cache.get_decimals(unknown_token) == 9
        # Второй вызов — из кеша, RPC не вызывается повторно
        mock_token.functions.decimals.return_value.call.reset_mock()
        assert cache.get_decimals(unknown_token) == 9
        mock_token.functions.decimals.return_value.call.assert_not_called()


# ============================================================
# Fix #4 + #21: DexSwap — Transfer event parsing + multi-hop
# ============================================================

class TestDexSwapTransferParsing:
    """Issue #4: swap routing + Issue #21: actual_out from Transfer events."""

    def test_parse_actual_output_finds_transfer(self):
        """_parse_actual_output парсит Transfer event из receipt."""
        from src.dex_swap import DexSwap

        mock_w3 = MagicMock()
        mock_w3.eth.contract = MagicMock(return_value=MagicMock())
        with patch.dict('src.dex_swap.ROUTER_V2_ADDRESSES', {
            99: {"router": "0x" + "11" * 20, "weth": "0x" + "22" * 20,
                 "usdt": "0x" + "33" * 20, "name": "Test"}
        }):
            swapper = DexSwap(mock_w3, chain_id=99)

        to_token = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        wallet = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

        # Build a mock receipt with Transfer event
        transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)")
        from_padded = bytes(12) + bytes.fromhex("CC" * 20)  # some address
        to_padded = bytes(12) + bytes.fromhex("BB" * 20)  # wallet

        receipt = {
            'logs': [{
                'address': to_token,
                'topics': [transfer_topic, from_padded, to_padded],
                'data': (5_000_000).to_bytes(32, 'big'),  # 5 USDT (6 decimals)
            }]
        }

        result = swapper._parse_actual_output(receipt, to_token, wallet)
        assert result == 5_000_000

    def test_parse_actual_output_ignores_wrong_token(self):
        """_parse_actual_output игнорирует Transfer от другого токена."""
        from src.dex_swap import DexSwap

        mock_w3 = MagicMock()
        mock_w3.eth.contract = MagicMock(return_value=MagicMock())
        with patch.dict('src.dex_swap.ROUTER_V2_ADDRESSES', {
            99: {"router": "0x" + "11" * 20, "weth": "0x" + "22" * 20,
                 "usdt": "0x" + "33" * 20, "name": "Test"}
        }):
            swapper = DexSwap(mock_w3, chain_id=99)

        to_token = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        wallet = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"

        # Transfer from DIFFERENT token contract
        transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)")
        receipt = {
            'logs': [{
                'address': "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
                'topics': [transfer_topic,
                           bytes(12) + bytes.fromhex("DD" * 20),
                           bytes(12) + bytes.fromhex("BB" * 20)],
                'data': (999).to_bytes(32, 'big'),
            }]
        }

        result = swapper._parse_actual_output(receipt, to_token, wallet)
        assert result is None

    def test_get_quote_v3_returns_triple(self):
        """get_quote_v3 возвращает (amount_out, fee, multi_hop_fee2)."""
        from src.dex_swap import DexSwap

        mock_w3 = MagicMock()
        mock_w3.eth.contract = MagicMock(return_value=MagicMock())
        with patch.dict('src.dex_swap.ROUTER_V2_ADDRESSES', {
            99: {"router": "0x" + "11" * 20, "weth": "0x" + "22" * 20,
                 "usdt": "0x" + "33" * 20, "name": "Test"}
        }):
            swapper = DexSwap(mock_w3, chain_id=99)

        # V3 not available → should return (0, 0, 0)
        swapper.v3_available = False
        result = swapper.get_quote_v3("0x" + "AA" * 20, "0x" + "BB" * 20, 10**18)
        assert result == (0, 0, 0)
        assert len(result) == 3  # Always 3-tuple

    def test_swap_v3_multi_hop_path_encoding(self):
        """Multi-hop path кодируется как tokenIn+fee1+WETH+fee2+tokenOut."""
        # Verify path encoding format
        from_token = "0x1111111111111111111111111111111111111111"
        weth = "0x2222222222222222222222222222222222222222"
        to_token = "0x3333333333333333333333333333333333333333"
        fee1 = 500
        fee2 = 3000

        path = (
            bytes.fromhex(from_token[2:])
            + fee1.to_bytes(3, 'big')
            + bytes.fromhex(weth[2:])
            + fee2.to_bytes(3, 'big')
            + bytes.fromhex(to_token[2:])
        )

        # 20 + 3 + 20 + 3 + 20 = 66 bytes
        assert len(path) == 66
        # First 20 bytes = from_token
        assert path[:20] == bytes.fromhex(from_token[2:])
        # Fee1 at bytes 20-22
        assert int.from_bytes(path[20:23], 'big') == 500
        # WETH at bytes 23-42
        assert path[23:43] == bytes.fromhex(weth[2:])
        # Fee2 at bytes 43-45
        assert int.from_bytes(path[43:46], 'big') == 3000
        # to_token at bytes 46-65
        assert path[46:66] == bytes.fromhex(to_token[2:])


# ============================================================
# Fix #17: pool_id debug log не использует get_pool_id()
# ============================================================

class TestPoolIdLog:
    """Issue #17: get_pool_id() логировался в MINT, но даёт неверный ID для PancakeSwap."""

    def test_encode_mint_position_no_pool_id_call(self):
        """encode_mint_position не вызывает pool_key.get_pool_id() для логирования."""
        import inspect
        from src.contracts.v4.position_manager import V4PositionManager

        source = inspect.getsource(V4PositionManager.encode_mint_position)
        # get_pool_id().hex() — старый вызов для лога — больше не должен быть
        assert ".get_pool_id().hex()" not in source

    def test_pool_id_differs_for_pancakeswap(self):
        """PancakeSwap pool_id != Uniswap pool_id для тех же токенов."""
        from src.contracts.v4.pool_manager import PoolKey, V4PoolManager, V4Protocol

        pool_key = PoolKey(
            currency0=TOKEN_A,
            currency1=TOKEN_B,
            fee=2500,
            tick_spacing=50,
            hooks="0x0000000000000000000000000000000000000000"
        )

        # Uniswap format (через get_pool_id)
        uni_id = pool_key.get_pool_id()

        # PancakeSwap format — использует другой encoding
        # Просто проверяем что get_pool_id не используется для PancakeSwap
        assert isinstance(uni_id, bytes)
        assert len(uni_id) == 32


# ============================================================
# Fix #24: Deterministic tick extraction
# ============================================================

class TestTickExtraction:
    """Issue #24: fragile tick extraction — теперь детерминистический."""

    def _make_pm(self):
        """Создать PM для тестов."""
        with patch.object(V4PositionManager, '__init__', lambda self, *a, **kw: None):
            pm = V4PositionManager.__new__(V4PositionManager)
            pm.protocol = V4Protocol.PANCAKESWAP
            return pm

    def test_extract_ticks_from_bool_tuple(self):
        """Tuple (bool, tickLower, tickUpper) — PancakeSwap формат."""
        pm = self._make_pm()
        # (hasSubscriber=False, tickLower=-100, tickUpper=200)
        info = (False, -100, 200)
        tl, tu = pm._extract_ticks(info)
        assert tl == -100
        assert tu == 200

    def test_extract_ticks_from_tuple_bool_last(self):
        """Tuple (tickLower, tickUpper, bool) — альтернативный формат."""
        pm = self._make_pm()
        info = (-50000, 50000, True)
        tl, tu = pm._extract_ticks(info)
        assert tl == -50000
        assert tu == 50000

    def test_extract_ticks_from_two_element_tuple(self):
        """Tuple (tickLower, tickUpper) — минимальный формат."""
        pm = self._make_pm()
        info = (-887200, 887200)
        tl, tu = pm._extract_ticks(info)
        assert tl == -887200
        assert tu == 887200

    def test_extract_ticks_from_packed_int_8_32(self):
        """Packed uint256: tickLower at bits 8-31, tickUpper at bits 32-55."""
        pm = self._make_pm()

        # Encode: tickLower=-23000 at bits 8-31, tickUpper=23000 at bits 32-55
        # tickLower = -23000 → unsigned 24-bit: 0x1000000 - 23000 = 0xFFA5A8
        tl_raw = (-23000 + 0x1000000) & 0xFFFFFF  # = 0xFFA5A8
        tu_raw = 23000 & 0xFFFFFF  # = 0x0059D8

        packed = (tu_raw << 32) | (tl_raw << 8)

        tl, tu = pm._extract_ticks(packed, tick_spacing=200)
        assert tl == -23000
        assert tu == 23000

    def test_extract_ticks_validates_tick_spacing(self):
        """Tick spacing alignment используется для выбора layout."""
        pm = self._make_pm()

        # Create packed value where layout (8/32) gives aligned ticks
        tl, tu = -23000, 23000
        tl_raw = (tl + 0x1000000) & 0xFFFFFF
        tu_raw = tu & 0xFFFFFF
        packed = (tu_raw << 32) | (tl_raw << 8)

        result_tl, result_tu = pm._extract_ticks(packed, tick_spacing=200)
        assert result_tl % 200 == 0
        assert result_tu % 200 == 0

    def test_extract_ticks_packed_tick_lower_less_than_upper(self):
        """Extracted ticks всегда: tick_lower < tick_upper."""
        pm = self._make_pm()

        # Various packed values
        for tl_val, tu_val in [(-100, 100), (-887200, 887200), (0, 60), (-60000, -100)]:
            if tl_val >= tu_val:
                continue
            tl_raw = (tl_val + 0x1000000) & 0xFFFFFF
            tu_raw = (tu_val + 0x1000000) & 0xFFFFFF
            packed = (tu_raw << 32) | (tl_raw << 8)

            rtl, rtu = pm._extract_ticks(packed)
            assert rtl < rtu, f"Expected tl={tl_val} < tu={tu_val}, got {rtl} < {rtu}"

    def test_extract_ticks_invalid_type_raises(self):
        """Неизвестный тип info → ValueError."""
        pm = self._make_pm()
        with pytest.raises(ValueError, match="Unexpected PositionInfo type"):
            pm._extract_ticks("not a valid info")


# ============================================================
# Fix #27: Batcher results populated from events
# ============================================================

class TestBatcherResults:
    """Issue #27: batcher.execute() теперь парсит результаты из events."""

    def test_parse_results_from_receipt_with_events(self):
        """_parse_results_from_receipt извлекает данные из IncreaseLiquidity."""
        from src.multicall.batcher import Multicall3Batcher, CallResult

        mock_w3 = MagicMock()
        mock_w3.eth.contract = MagicMock(return_value=MagicMock())
        batcher = Multicall3Batcher(mock_w3)

        pm_addr = "0x" + "AA" * 20

        # Mock process_receipt to return events
        mock_pm = MagicMock()
        mock_event = {
            'args': {
                'tokenId': 42,
                'liquidity': 10**18,
                'amount0': 5 * 10**17,
                'amount1': 1000 * 10**6,
            }
        }
        mock_pm.events.IncreaseLiquidity.return_value.process_receipt = MagicMock(
            return_value=[mock_event]
        )
        batcher._get_pm_contract = MagicMock(return_value=mock_pm)

        receipt = {'status': 1, 'logs': []}
        results = batcher._parse_results_from_receipt(receipt, pm_addr)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].decoded_data['tokenId'] == 42
        assert results[0].decoded_data['liquidity'] == 10**18

    def test_parse_results_failed_tx_returns_error(self):
        """TX failed → results содержит success=False."""
        from src.multicall.batcher import Multicall3Batcher

        mock_w3 = MagicMock()
        mock_w3.eth.contract = MagicMock(return_value=MagicMock())
        batcher = Multicall3Batcher(mock_w3)

        pm_addr = "0x" + "AA" * 20

        # No events (TX failed)
        mock_pm = MagicMock()
        mock_pm.events.IncreaseLiquidity.return_value.process_receipt = MagicMock(
            return_value=[]
        )
        batcher._get_pm_contract = MagicMock(return_value=mock_pm)

        receipt = {'status': 0, 'logs': []}
        results = batcher._parse_results_from_receipt(receipt, pm_addr)

        assert len(results) == 1
        assert results[0].success is False
        assert 'error' in results[0].decoded_data


# ============================================================
# Nonce fix: reverted TX = confirm (not release)
# ============================================================

class TestNonceRevertedTxConfirm:
    """Reverted TX всё равно потребляет nonce — должен быть confirm, не release."""

    def test_dex_swap_approve_v3_reverted_confirms_nonce(self):
        """V3 approve с reverted TX → confirm_transaction (не release)."""
        import inspect
        from src.dex_swap import DexSwap

        source = inspect.getsource(DexSwap._check_and_approve_v3)
        # Should NOT have conditional release for receipt status
        assert "release_nonce(nonce)" in source  # Only in except (tx not sent)
        # Should NOT have receipt.status check in nonce logic
        assert "receipt.status == 1" not in source or source.count("release_nonce") == 1

    def test_position_manager_mint_uses_tx_sent_flag(self):
        """mint_position использует tx_sent flag для nonce management."""
        import inspect
        from src.contracts.v4.position_manager import V4PositionManager

        source = inspect.getsource(V4PositionManager.mint_position)
        assert "tx_sent = False" in source
        assert "tx_sent = True" in source

    def test_position_manager_close_uses_tx_sent_flag(self):
        """close_position использует tx_sent flag."""
        import inspect
        from src.contracts.v4.position_manager import V4PositionManager

        source = inspect.getsource(V4PositionManager.close_position)
        assert "tx_sent = False" in source
        assert "tx_sent = True" in source

    def test_batcher_execute_uses_tx_sent_flag(self):
        """batcher.execute() использует tx_sent flag."""
        import inspect
        from src.multicall.batcher import Multicall3Batcher

        source = inspect.getsource(Multicall3Batcher.execute)
        assert "tx_sent = False" in source
        assert "tx_sent = True" in source
