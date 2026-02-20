"""
Multicall3 Batcher

Батчинг нескольких вызовов в одну транзакцию через Multicall3.
Позволяет открыть/закрыть 7 позиций одной транзакцией.

Адрес Multicall3 (одинаковый на всех EVM сетях):
0xcA11bde05977b3631167028862bE2a173976CA11
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from web3 import Web3
from web3.contract import Contract
from web3.types import TxReceipt
from eth_account.signers.local import LocalAccount
from eth_abi import decode
import time

from ..contracts.abis import MULTICALL3_ABI, POSITION_MANAGER_ABI
from ..utils import NonceManager

# Настройка логгера
logger = logging.getLogger(__name__)


# Multicall3 deployed at same address on all chains
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"


@dataclass
class Call3:
    """Структура вызова для Multicall3."""
    target: str          # Адрес контракта
    allow_failure: bool  # Разрешить ли провал этого вызова
    call_data: bytes     # Закодированные данные вызова

    def to_tuple(self) -> tuple:
        return (
            Web3.to_checksum_address(self.target),
            self.allow_failure,
            self.call_data
        )


@dataclass
class MintCallResult:
    """Результат mint вызова."""
    token_id: int
    liquidity: int
    amount0: int
    amount1: int


@dataclass
class CallResult:
    """Результат одного вызова."""
    success: bool
    return_data: bytes
    decoded_data: Optional[Dict[str, Any]] = None


class Multicall3Batcher:
    """
    Батчер для объединения нескольких вызовов в одну транзакцию.

    Использование:
    ```python
    batcher = Multicall3Batcher(w3, account)

    # Добавляем вызовы mint
    for params in mint_params_list:
        batcher.add_mint_call(position_manager_address, params)

    # Выполняем всё одной транзакцией
    results = await batcher.execute()
    ```
    """

    def __init__(
        self,
        w3: Web3,
        account: LocalAccount = None,
        nonce_manager: 'NonceManager' = None
    ):
        self.w3 = w3
        self.account = account
        self.nonce_manager = nonce_manager
        self.calls: List[Call3] = []

        # Position Manager контракт для кодирования
        self._pm_contract = None

    def _get_pm_contract(self, address: str) -> Contract:
        """Получение контракта PositionManager."""
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=POSITION_MANAGER_ABI
        )

    def clear(self):
        """Очистка списка вызовов."""
        self.calls = []

    def add_call(self, call: Call3):
        """Добавление вызова в батч."""
        self.calls.append(call)

    def add_raw_call(
        self,
        target: str,
        call_data: bytes,
        allow_failure: bool = False
    ):
        """Добавление сырого вызова."""
        self.calls.append(Call3(
            target=target,
            allow_failure=allow_failure,
            call_data=call_data
        ))

    def add_mint_call(
        self,
        position_manager: str,
        token0: str,
        token1: str,
        fee: int,
        tick_lower: int,
        tick_upper: int,
        amount0_desired: int,
        amount1_desired: int,
        recipient: str,
        deadline: int = None,
        amount0_min: int = 0,
        amount1_min: int = 0,
        allow_failure: bool = False
    ):
        """
        Добавление mint вызова в батч.

        Args:
            position_manager: Адрес NonfungiblePositionManager
            token0, token1: Адреса токенов
            fee: Fee tier
            tick_lower, tick_upper: Диапазон тиков
            amount0_desired, amount1_desired: Желаемые количества
            recipient: Получатель NFT
            deadline: Deadline транзакции
            amount0_min, amount1_min: Минимальные количества (slippage protection)
            allow_failure: Разрешить ли провал
        """
        if deadline is None:
            deadline = int(time.time()) + 3600

        pm = self._get_pm_contract(position_manager)

        params = (
            Web3.to_checksum_address(token0),
            Web3.to_checksum_address(token1),
            fee,
            tick_lower,
            tick_upper,
            amount0_desired,
            amount1_desired,
            amount0_min,
            amount1_min,
            Web3.to_checksum_address(recipient),
            deadline
        )

        # web3.py v6+: use functions.method()._encode_transaction_data()
        call_data = pm.functions.mint(params)._encode_transaction_data()

        self.add_call(Call3(
            target=position_manager,
            allow_failure=allow_failure,
            call_data=call_data
        ))

    def add_decrease_liquidity_call(
        self,
        position_manager: str,
        token_id: int,
        liquidity: int,
        amount0_min: int = 0,
        amount1_min: int = 0,
        deadline: int = None,
        allow_failure: bool = False
    ):
        """Добавление decreaseLiquidity вызова."""
        if deadline is None:
            deadline = int(time.time()) + 3600

        pm = self._get_pm_contract(position_manager)
        params = (token_id, liquidity, amount0_min, amount1_min, deadline)
        call_data = pm.functions.decreaseLiquidity(params)._encode_transaction_data()

        self.add_call(Call3(
            target=position_manager,
            allow_failure=allow_failure,
            call_data=call_data
        ))

    def add_collect_call(
        self,
        position_manager: str,
        token_id: int,
        recipient: str,
        amount0_max: int = 2**128 - 1,
        amount1_max: int = 2**128 - 1,
        allow_failure: bool = False
    ):
        """Добавление collect вызова."""
        pm = self._get_pm_contract(position_manager)
        params = (token_id, Web3.to_checksum_address(recipient), amount0_max, amount1_max)
        call_data = pm.functions.collect(params)._encode_transaction_data()

        self.add_call(Call3(
            target=position_manager,
            allow_failure=allow_failure,
            call_data=call_data
        ))

    def add_burn_call(
        self,
        position_manager: str,
        token_id: int,
        allow_failure: bool = False
    ):
        """Добавление burn вызова."""
        pm = self._get_pm_contract(position_manager)
        call_data = pm.functions.burn(token_id)._encode_transaction_data()

        self.add_call(Call3(
            target=position_manager,
            allow_failure=allow_failure,
            call_data=call_data
        ))

    def add_close_position_calls(
        self,
        position_manager: str,
        token_id: int,
        liquidity: int,
        recipient: str,
        deadline: int = None
    ):
        """
        Добавление вызовов для полного закрытия позиции.

        Последовательность:
        1. decreaseLiquidity (вывод всей ликвидности)
        2. collect (сбор токенов и fees)

        NFT не сжигается - позиция остаётся с нулевой ликвидностью.
        """
        # 1. Decrease liquidity to 0
        self.add_decrease_liquidity_call(
            position_manager=position_manager,
            token_id=token_id,
            liquidity=liquidity,
            deadline=deadline
        )

        # 2. Collect all tokens
        self.add_collect_call(
            position_manager=position_manager,
            token_id=token_id,
            recipient=recipient
        )

    def estimate_gas(self, position_manager_address: str = None) -> int:
        """Оценка газа для батча через PositionManager.multicall."""
        if not self.calls:
            return 0

        if not position_manager_address:
            position_manager_address = self.calls[0].target

        call_data_list = [c.call_data for c in self.calls]
        pm_contract = self._get_pm_contract(position_manager_address)

        return pm_contract.functions.multicall(call_data_list).estimate_gas({
            'from': self.account.address if self.account else None
        })

    def _decode_mint_result(self, return_data: bytes) -> MintCallResult:
        """Декодирование результата mint вызова."""
        # mint возвращает (uint256 tokenId, uint128 liquidity, uint256 amount0, uint256 amount1)
        decoded = decode(['uint256', 'uint128', 'uint256', 'uint256'], return_data)
        return MintCallResult(
            token_id=decoded[0],
            liquidity=decoded[1],
            amount0=decoded[2],
            amount1=decoded[3]
        )

    def _parse_events_from_receipt(
        self,
        receipt: TxReceipt,
        position_manager_address: str
    ) -> List[int]:
        """
        Парсинг событий IncreaseLiquidity из receipt для получения token_ids.

        Args:
            receipt: Transaction receipt
            position_manager_address: Адрес Position Manager

        Returns:
            Список token_ids созданных позиций
        """
        token_ids = []
        pm_contract = self._get_pm_contract(position_manager_address)

        # Ищем события IncreaseLiquidity
        try:
            events = pm_contract.events.IncreaseLiquidity().process_receipt(receipt)
            for event in events:
                token_ids.append(event['args']['tokenId'])
        except Exception:
            # Если не удалось через process_receipt, парсим вручную
            # IncreaseLiquidity event signature
            increase_liquidity_topic = Web3.keccak(
                text="IncreaseLiquidity(uint256,uint128,uint256,uint256)"
            )

            for log in receipt.get('logs', []):
                if log['address'].lower() == position_manager_address.lower():
                    if log['topics'] and log['topics'][0] == increase_liquidity_topic:
                        # tokenId is indexed, so it's in topics[1]
                        token_id = int(log['topics'][1].hex(), 16)
                        token_ids.append(token_id)

        return token_ids

    def _parse_results_from_receipt(
        self,
        receipt: TxReceipt,
        position_manager_address: str
    ) -> List[CallResult]:
        """
        Парсинг результатов вызовов из событий receipt.

        Multicall on-chain не возвращает return data в receipt,
        но мы можем извлечь информацию из IncreaseLiquidity events.
        Каждое событие содержит: tokenId, liquidity, amount0, amount1.
        """
        results = []
        pm_contract = self._get_pm_contract(position_manager_address)

        # Парсим IncreaseLiquidity events для получения деталей
        try:
            events = pm_contract.events.IncreaseLiquidity().process_receipt(receipt)
            for event in events:
                args = event.get('args', {})
                result = CallResult(
                    success=True,
                    return_data=b'',
                    decoded_data={
                        'event': 'IncreaseLiquidity',
                        'tokenId': args.get('tokenId', 0),
                        'liquidity': args.get('liquidity', 0),
                        'amount0': args.get('amount0', 0),
                        'amount1': args.get('amount1', 0),
                    }
                )
                results.append(result)
                logger.info(
                    f"  Mint result: tokenId={args.get('tokenId')}, "
                    f"liq={args.get('liquidity')}, "
                    f"amount0={args.get('amount0')}, amount1={args.get('amount1')}"
                )
        except Exception as e:
            logger.debug(f"Could not parse IncreaseLiquidity events via ABI: {e}")

            # Fallback: парсим вручную из raw logs
            increase_liq_topic = Web3.keccak(
                text="IncreaseLiquidity(uint256,uint128,uint256,uint256)"
            )
            for log in receipt.get('logs', []):
                if log['address'].lower() != position_manager_address.lower():
                    continue
                topics = log.get('topics', [])
                if not topics or topics[0] != increase_liq_topic:
                    continue
                try:
                    token_id = int(topics[1].hex(), 16) if len(topics) > 1 else 0
                    data = log.get('data', b'')
                    if isinstance(data, (bytes, bytearray)) and len(data) >= 96:
                        liquidity = int.from_bytes(data[0:32], 'big')
                        amount0 = int.from_bytes(data[32:64], 'big')
                        amount1 = int.from_bytes(data[64:96], 'big')
                    else:
                        liquidity = amount0 = amount1 = 0

                    results.append(CallResult(
                        success=True,
                        return_data=b'',
                        decoded_data={
                            'event': 'IncreaseLiquidity',
                            'tokenId': token_id,
                            'liquidity': liquidity,
                            'amount0': amount0,
                            'amount1': amount1,
                        }
                    ))
                    logger.info(
                        f"  Mint result (raw): tokenId={token_id}, "
                        f"liq={liquidity}, amount0={amount0}, amount1={amount1}"
                    )
                except Exception as parse_err:
                    logger.warning(f"Failed to parse IncreaseLiquidity log: {parse_err}")

        # Если TX failed — один результат с success=False
        if receipt.get('status', 0) != 1 and not results:
            results.append(CallResult(
                success=False,
                return_data=b'',
                decoded_data={'error': 'Transaction reverted'}
            ))

        return results

    def execute(
        self,
        gas_limit: int = None,
        gas_price: int = None,
        max_priority_fee: int = None,
        timeout: int = 600,
        position_manager_address: str = None
    ) -> Tuple[str, List[CallResult], TxReceipt, List[int]]:
        """
        Выполнение батча через PositionManager.multicall (НЕ Multicall3!).

        ВАЖНО: Используем multicall самого Position Manager, чтобы msg.sender
        оставался нашим кошельком, а не контрактом Multicall3.

        Args:
            gas_limit: Лимит газа (если None - автоопределение)
            gas_price: Цена газа (для legacy txs)
            max_priority_fee: Priority fee (для EIP-1559)
            timeout: Таймаут ожидания подтверждения в секундах (default 300)
            position_manager_address: Адрес Position Manager (ОБЯЗАТЕЛЬНО!)

        Returns:
            (tx_hash, list of CallResult, receipt, token_ids)
        """
        if not self.calls:
            raise ValueError("No calls to execute")

        if not self.account:
            raise ValueError("Account not set")

        if not position_manager_address:
            raise ValueError("position_manager_address is required for execute()")

        # Собираем только call_data для каждого вызова (без target и allowFailure)
        call_data_list = [c.call_data for c in self.calls]

        # Получаем контракт Position Manager
        pm_contract = self._get_pm_contract(position_manager_address)

        # Оценка газа
        if gas_limit is None:
            try:
                estimated = pm_contract.functions.multicall(call_data_list).estimate_gas({
                    'from': self.account.address
                })
                gas_limit = int(estimated * 1.3)  # +30% буфер
            except Exception as e:
                logger.warning(f"Gas estimation failed: {e}")
                gas_limit = 3000000  # fallback

        # Построение транзакции
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx_params = {
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
            }

            # EIP-1559 или legacy
            if max_priority_fee:
                tx_params['maxPriorityFeePerGas'] = max_priority_fee
                tx_params['maxFeePerGas'] = self.w3.eth.gas_price * 2
            else:
                tx_params['gasPrice'] = gas_price or self.w3.eth.gas_price

            # Используем multicall Position Manager'а
            tx = pm_contract.functions.multicall(call_data_list).build_transaction(tx_params)

            # Подпись и отправка
            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            logger.info(f"Transaction sent: {tx_hash.hex()}")

            # Ожидание подтверждения с таймаутом
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # TX mined — nonce consumed (even if reverted)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            # Парсим события для получения token_ids и результатов
            token_ids = self._parse_events_from_receipt(receipt, position_manager_address)
            results = self._parse_results_from_receipt(receipt, position_manager_address)

            gas_used = receipt.get('gasUsed', 0)
            status = receipt.get('status', 0)
            logger.info(
                f"Batch TX {'SUCCESS' if status == 1 else 'FAILED'}: "
                f"{len(token_ids)} positions created, gas={gas_used}, "
                f"{len(results)} call results parsed"
            )

            return tx_hash.hex(), results, receipt, token_ids

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def simulate(self, position_manager_address: str = None) -> List[CallResult]:
        """
        Симуляция батча без отправки транзакции.

        Использует PositionManager.multicall через eth_call.
        """
        if not self.calls:
            return []

        if not position_manager_address:
            # Берём target из первого вызова (предполагаем что все вызовы к одному PM)
            position_manager_address = self.calls[0].target

        # Собираем только call_data
        call_data_list = [c.call_data for c in self.calls]

        pm_contract = self._get_pm_contract(position_manager_address)

        try:
            result = pm_contract.functions.multicall(call_data_list).call({
                'from': self.account.address if self.account else None
            })

            # multicall возвращает bytes[] - список результатов
            return [
                CallResult(success=True, return_data=r)
                for r in result
            ]
        except Exception as e:
            raise RuntimeError(f"Simulation failed: {e}")

    def simulate_single_call(self, index: int = 0) -> str:
        """
        Симулировать один вызов напрямую (без Multicall) для получения детальной ошибки.

        Args:
            index: Индекс вызова в списке

        Returns:
            Сообщение об ошибке или "OK"
        """
        if not self.calls or index >= len(self.calls):
            return "No call at this index"

        call = self.calls[index]

        try:
            # Делаем прямой eth_call к target контракту
            result = self.w3.eth.call({
                'to': Web3.to_checksum_address(call.target),
                'data': call.call_data,
                'from': self.account.address if self.account else None
            })
            # Result might be bytes or hex string
            if isinstance(result, bytes):
                result_hex = result.hex()
            else:
                result_hex = str(result)
            return f"OK: {result_hex[:100]}..."
        except Exception as e:
            error_str = str(e)
            # Пытаемся извлечь человекочитаемую ошибку
            if "execution reverted" in error_str.lower():
                # Пробуем декодировать hex строку ошибки
                import re
                hex_match = re.search(r'0x[0-9a-fA-F]+', error_str)
                if hex_match:
                    try:
                        hex_data = hex_match.group()
                        if len(hex_data) > 10:
                            # Error(string) selector = 0x08c379a0
                            if hex_data.startswith('0x08c379a0'):
                                # Decode the string
                                error_bytes = bytes.fromhex(hex_data[10:])
                                # String starts at offset 32, length at offset 64
                                if len(error_bytes) >= 64:
                                    str_len = int.from_bytes(error_bytes[32:64], 'big')
                                    error_msg = error_bytes[64:64+str_len].decode('utf-8', errors='ignore')
                                    return f"Revert: {error_msg}"
                    except Exception:
                        pass
            return f"Error: {error_str}"

    def debug_first_call(self) -> dict:
        """Вывод отладочной информации о первом вызове."""
        if not self.calls:
            return {"error": "No calls"}

        call = self.calls[0]
        call_data = call.call_data

        # Ensure call_data is bytes
        if isinstance(call_data, str):
            if call_data.startswith('0x'):
                call_data = bytes.fromhex(call_data[2:])
            else:
                call_data = bytes.fromhex(call_data)

        # Decode mint params from call_data
        # mint selector = first 4 bytes
        selector = call_data[:4].hex()

        # For mint, params start at byte 4
        # MintParams is a tuple, ABI-encoded
        info = {
            "target": call.target,
            "selector": selector,
            "call_data_length": len(call_data),
            "call_data_preview": call_data[:100].hex() + "..."
        }

        # Try to decode mint params
        if selector == "88316456":  # mint selector
            try:
                # Decode the tuple
                from eth_abi import decode
                params_data = call_data[4:]
                # MintParams: (address, address, uint24, int24, int24, uint256, uint256, uint256, uint256, address, uint256)
                decoded = decode(
                    ['address', 'address', 'uint24', 'int24', 'int24', 'uint256', 'uint256', 'uint256', 'uint256', 'address', 'uint256'],
                    params_data
                )
                info["mint_params"] = {
                    "token0": decoded[0],
                    "token1": decoded[1],
                    "fee": decoded[2],
                    "tickLower": decoded[3],
                    "tickUpper": decoded[4],
                    "amount0Desired": decoded[5],
                    "amount1Desired": decoded[6],
                    "amount0Min": decoded[7],
                    "amount1Min": decoded[8],
                    "recipient": decoded[9],
                    "deadline": decoded[10]
                }
            except Exception as e:
                info["decode_error"] = str(e)

        return info

    def __len__(self) -> int:
        return len(self.calls)

    def __repr__(self) -> str:
        return f"Multicall3Batcher({len(self.calls)} calls)"
