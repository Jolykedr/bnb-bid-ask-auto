"""
Uniswap V3 Position Manager Integration

Работа с NonfungiblePositionManager для создания и управления позициями.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional
from web3 import Web3
from web3.contract import Contract
from eth_account.signers.local import LocalAccount
import time

from .abis import POSITION_MANAGER_ABI, ERC20_ABI, ERC721_ENUMERABLE_ABI
from ..math.distribution import BidAskPosition
from ..utils import NonceManager

logger = logging.getLogger(__name__)


@dataclass
class MintParams:
    """Параметры для создания позиции."""
    token0: str
    token1: str
    fee: int
    tick_lower: int
    tick_upper: int
    amount0_desired: int
    amount1_desired: int
    amount0_min: int = 0
    amount1_min: int = 0
    recipient: str = None
    deadline: int = None

    def to_tuple(self, recipient: str, deadline: int = None) -> tuple:
        """Конвертация в tuple для контракта."""
        if deadline is None:
            deadline = int(time.time()) + 3600  # +1 час

        return (
            Web3.to_checksum_address(self.token0),
            Web3.to_checksum_address(self.token1),
            self.fee,
            self.tick_lower,
            self.tick_upper,
            self.amount0_desired,
            self.amount1_desired,
            self.amount0_min,
            self.amount1_min,
            Web3.to_checksum_address(recipient),
            deadline
        )


@dataclass
class MintResult:
    """Результат создания позиции."""
    token_id: int
    liquidity: int
    amount0: int
    amount1: int
    tx_hash: str


class UniswapV3PositionManager:
    """
    Класс для работы с Uniswap V3 NonfungiblePositionManager.

    Поддерживает:
    - Создание позиций (mint)
    - Добавление ликвидности (increaseLiquidity)
    - Удаление ликвидности (decreaseLiquidity)
    - Сбор fees (collect)
    - Закрытие позиций (burn)
    """

    def __init__(
        self,
        w3: Web3,
        position_manager_address: str,
        account: LocalAccount = None,
        nonce_manager: 'NonceManager' = None
    ):
        self.w3 = w3
        self.account = account
        self.nonce_manager = nonce_manager
        self.position_manager_address = Web3.to_checksum_address(position_manager_address)
        self.contract: Contract = w3.eth.contract(
            address=self.position_manager_address,
            abi=POSITION_MANAGER_ABI
        )

    def _get_token_contract(self, token_address: str) -> Contract:
        """Получение контракта ERC20."""
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_ABI
        )

    def check_and_approve(
        self,
        token_address: str,
        amount: int,
        spender: str = None,
        timeout: int = 120
    ) -> Optional[str]:
        """
        Проверка и approve токенов если нужно.

        Args:
            token_address: Адрес токена
            amount: Требуемая сумма
            spender: Адрес spender
            timeout: Таймаут ожидания в секундах

        Returns:
            tx_hash если был approve, None если уже достаточно allowance
        """
        if spender is None:
            spender = self.position_manager_address

        token = self._get_token_contract(token_address)
        current_allowance = token.functions.allowance(
            self.account.address,
            spender
        ).call()

        if current_allowance >= amount:
            return None

        # Делаем approve на максимум
        max_uint256 = 2**256 - 1
        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx = token.functions.approve(spender, max_uint256).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': 100000,
                'gasPrice': self.w3.eth.gas_price
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            # Ждём подтверждения с таймаутом
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # TX mined — nonce consumed (even if reverted)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            if receipt['status'] != 1:
                raise Exception(f"Approve transaction reverted! TX: {tx_hash.hex()}")

            return tx_hash.hex()

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def encode_mint(self, params: MintParams, recipient: str, deadline: int = None) -> bytes:
        """
        Кодирование вызова mint для использования в multicall.

        Args:
            params: Параметры позиции
            recipient: Адрес получателя NFT
            deadline: Deadline транзакции

        Returns:
            Закодированные данные вызова
        """
        return self.contract.encode_abi(
            fn_name='mint',
            args=[params.to_tuple(recipient, deadline)]
        )

    def encode_decrease_liquidity(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int = 0,
        amount1_min: int = 0,
        deadline: int = None
    ) -> bytes:
        """Кодирование decreaseLiquidity."""
        if deadline is None:
            deadline = int(time.time()) + 3600

        params = (token_id, liquidity, amount0_min, amount1_min, deadline)
        return self.contract.functions.decreaseLiquidity(params)._encode_transaction_data()

    def encode_collect(
        self,
        token_id: int,
        recipient: str,
        amount0_max: int = 2**128 - 1,
        amount1_max: int = 2**128 - 1
    ) -> bytes:
        """Кодирование collect."""
        params = (token_id, Web3.to_checksum_address(recipient), amount0_max, amount1_max)
        return self.contract.functions.collect(params)._encode_transaction_data()

    def encode_burn(self, token_id: int) -> bytes:
        """Кодирование burn."""
        return self.contract.functions.burn(token_id)._encode_transaction_data()

    def build_mint_params_from_distribution(
        self,
        positions: List[BidAskPosition],
        token0: str,
        token1: str,
        fee: int,
        token0_decimals: int = 18,
        token1_decimals: int = 18,
        stablecoin_is_token0: bool = False
    ) -> List[MintParams]:
        """
        Создание MintParams из распределения BidAsk.

        Args:
            positions: Список позиций из calculate_bid_ask_distribution
            token0: Адрес token0
            token1: Адрес token1
            fee: Fee tier (500, 3000, 10000)
            token0_decimals: Decimals token0
            token1_decimals: Decimals token1
            stablecoin_is_token0: True если стейблкоин = token0 (по адресу пула)

        Returns:
            Список MintParams готовых для mint
        """
        mint_params_list = []

        for pos in positions:
            # Для bid-ask стратегии ниже текущей цены:
            # Вносим только стейблкоин, другой токен = 0
            if stablecoin_is_token0:
                stable_decimals = token0_decimals
                amount = int(pos.usd_amount * (10 ** stable_decimals))
                amount0 = amount
                amount1 = 0
            else:
                stable_decimals = token1_decimals
                amount = int(pos.usd_amount * (10 ** stable_decimals))
                amount0 = 0
                amount1 = amount

            params = MintParams(
                token0=token0,
                token1=token1,
                fee=fee,
                tick_lower=pos.tick_lower,
                tick_upper=pos.tick_upper,
                amount0_desired=amount0,
                amount1_desired=amount1,
                amount0_min=0,
                amount1_min=0
            )
            mint_params_list.append(params)

        return mint_params_list

    def _parse_mint_events(self, receipt) -> Optional[dict]:
        """
        Парсинг событий IncreaseLiquidity из receipt.

        Returns:
            dict с token_id, liquidity, amount0, amount1 или None
        """
        try:
            events = self.contract.events.IncreaseLiquidity().process_receipt(receipt)
            if events:
                event = events[0]
                return {
                    'token_id': event['args']['tokenId'],
                    'liquidity': event['args']['liquidity'],
                    'amount0': event['args']['amount0'],
                    'amount1': event['args']['amount1']
                }
        except Exception:
            pass

        # Fallback: парсим Transfer event для получения tokenId
        try:
            events = self.contract.events.Transfer().process_receipt(receipt)
            for event in events:
                # Transfer при mint идёт от address(0) к recipient
                if event['args']['from'] == '0x0000000000000000000000000000000000000000':
                    return {
                        'token_id': event['args']['tokenId'],
                        'liquidity': 0,
                        'amount0': 0,
                        'amount1': 0
                    }
        except Exception:
            pass

        return None

    def mint_single(
        self,
        params: MintParams,
        gas_limit: int = 500000,
        timeout: int = 300
    ) -> MintResult:
        """
        Создание одной позиции.

        Args:
            params: Параметры позиции
            gas_limit: Лимит газа
            timeout: Таймаут ожидания подтверждения в секундах

        Returns:
            MintResult с информацией о созданной позиции
        """
        deadline = int(time.time()) + 3600

        nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                self.w3.eth.get_transaction_count(self.account.address, 'pending')

        tx_sent = False
        try:
            tx = self.contract.functions.mint(
                params.to_tuple(self.account.address, deadline)
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': gas_limit,
                'gasPrice': self.w3.eth.gas_price
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_sent = True

            # Ждём подтверждения с таймаутом
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

            # TX mined — nonce consumed (even if reverted)
            if self.nonce_manager:
                self.nonce_manager.confirm_transaction(nonce)

            # Парсим события для получения результатов
            event_data = self._parse_mint_events(receipt)

            if event_data:
                return MintResult(
                    token_id=event_data['token_id'],
                    liquidity=event_data['liquidity'],
                    amount0=event_data['amount0'],
                    amount1=event_data['amount1'],
                    tx_hash=tx_hash.hex()
                )

            # Fallback если не удалось распарсить события
            return MintResult(
                token_id=0,
                liquidity=0,
                amount0=0,
                amount1=0,
                tx_hash=tx_hash.hex()
            )

        except Exception as e:
            if self.nonce_manager:
                if tx_sent:
                    self.nonce_manager.confirm_transaction(nonce)
                else:
                    self.nonce_manager.release_nonce(nonce)
            raise

    def get_position(self, token_id: int) -> dict:
        """Получение информации о позиции."""
        result = self.contract.functions.positions(token_id).call()

        return {
            'nonce': result[0],
            'operator': result[1],
            'token0': result[2],
            'token1': result[3],
            'fee': result[4],
            'tick_lower': result[5],
            'tick_upper': result[6],
            'liquidity': result[7],
            'fee_growth_inside0_last_x128': result[8],
            'fee_growth_inside1_last_x128': result[9],
            'tokens_owed0': result[10],
            'tokens_owed1': result[11]
        }

    def get_owner_of(self, token_id: int) -> Optional[str]:
        """
        Получение владельца NFT позиции.

        Args:
            token_id: ID позиции

        Returns:
            Адрес владельца или None если позиция не существует (сожжена)
        """
        try:
            # Создаём контракт с ERC721 ABI
            erc721_contract = self.w3.eth.contract(
                address=self.position_manager_address,
                abi=ERC721_ENUMERABLE_ABI
            )
            owner = erc721_contract.functions.ownerOf(token_id).call()
            return owner
        except Exception:
            # ownerOf выбрасывает исключение если токен не существует (сожжён)
            return None

    def is_position_owned_by(self, token_id: int, address: str) -> bool:
        """
        Проверка, принадлежит ли позиция указанному адресу.

        Args:
            token_id: ID позиции
            address: Адрес для проверки

        Returns:
            True если позиция принадлежит адресу
        """
        owner = self.get_owner_of(token_id)
        if owner is None:
            return False
        return owner.lower() == address.lower()

    def get_positions_count(self, address: str) -> int:
        """
        Получение количества позиций у адреса.

        Args:
            address: Адрес кошелька

        Returns:
            Количество NFT позиций
        """
        try:
            erc721_contract = self.w3.eth.contract(
                address=self.position_manager_address,
                abi=ERC721_ENUMERABLE_ABI
            )
            return erc721_contract.functions.balanceOf(
                Web3.to_checksum_address(address)
            ).call()
        except Exception:
            return 0

    def get_position_token_ids(self, address: str) -> List[int]:
        """
        Получение списка всех token_id позиций для адреса.

        Использует ERC721Enumerable.tokenOfOwnerByIndex для перечисления.
        Fallback через Transfer events если Enumerable не поддерживается.

        Args:
            address: Адрес кошелька

        Returns:
            Список token_id принадлежащих адресу
        """
        token_ids = []
        address_checksum = Web3.to_checksum_address(address)

        try:
            erc721_contract = self.w3.eth.contract(
                address=self.position_manager_address,
                abi=ERC721_ENUMERABLE_ABI
            )

            # Получаем количество NFT
            balance = erc721_contract.functions.balanceOf(address_checksum).call()
            logger.info(f"[V3] Wallet {address_checksum[:8]}... has {balance} NFTs")

            if balance == 0:
                return []

            # Пробуем ERC721Enumerable
            try:
                for i in range(balance):
                    token_id = erc721_contract.functions.tokenOfOwnerByIndex(
                        address_checksum, i
                    ).call()
                    token_ids.append(token_id)
                logger.info(f"[V3] Found {len(token_ids)} positions via ERC721Enumerable")
                return token_ids
            except Exception as enum_error:
                logger.warning(f"[V3] tokenOfOwnerByIndex not supported: {enum_error}")
                logger.info(f"[V3] Falling back to Transfer event scanning...")

            # Fallback: Scan Transfer events
            token_ids = self._scan_transfer_events(address_checksum, balance)

        except Exception as e:
            logger.error(f"[V3] Error scanning wallet positions: {e}", exc_info=True)

        return token_ids

    def _scan_transfer_events(self, address: str, expected_count: int) -> List[int]:
        """
        Scan Transfer events to find all token IDs owned by address.

        This is a fallback when ERC721Enumerable is not supported.

        Args:
            address: Wallet address (checksum)
            expected_count: Expected number of tokens (from balanceOf)

        Returns:
            List of token IDs currently owned by address
        """
        token_ids = []

        try:
            # Get Transfer event signature
            transfer_topic = self.w3.keccak(text='Transfer(address,address,uint256)').hex()

            # Pad address to 32 bytes for topic filter
            address_padded = '0x' + address[2:].lower().zfill(64)

            # Scan for transfers TO this address
            current_block = self.w3.eth.block_number
            from_block = max(0, current_block - 100000)

            logger.debug(f"[V3] Scanning Transfer events from block {from_block} to {current_block}...")

            logs = self.w3.eth.get_logs({
                'address': self.position_manager_address,
                'topics': [
                    transfer_topic,
                    None,  # from (any)
                    address_padded  # to (our address)
                ],
                'fromBlock': from_block,
                'toBlock': 'latest'
            })

            logger.debug(f"[V3] Found {len(logs)} Transfer events TO address")

            # Extract candidate token IDs
            candidate_ids = set()
            for log in logs:
                if len(log['topics']) >= 4:
                    token_id = int(log['topics'][3].hex(), 16)
                else:
                    token_id = int(log['data'].hex(), 16)
                candidate_ids.add(token_id)

            logger.debug(f"[V3] Found {len(candidate_ids)} candidate token IDs")

            # Verify current ownership
            erc721_contract = self.w3.eth.contract(
                address=self.position_manager_address,
                abi=ERC721_ENUMERABLE_ABI
            )

            for token_id in candidate_ids:
                try:
                    owner = erc721_contract.functions.ownerOf(token_id).call()
                    if owner.lower() == address.lower():
                        token_ids.append(token_id)
                except Exception:
                    pass

            logger.info(f"[V3] Verified {len(token_ids)} tokens still owned (expected {expected_count})")

        except Exception as e:
            logger.error(f"[V3] Error scanning Transfer events: {e}", exc_info=True)

        return token_ids

    def scan_wallet_positions(self, address: str) -> List[dict]:
        """
        Сканирование кошелька и получение информации обо всех позициях.

        Args:
            address: Адрес кошелька

        Returns:
            Список словарей с информацией о каждой позиции
        """
        positions = []
        token_ids = self.get_position_token_ids(address)

        for token_id in token_ids:
            try:
                position_data = self.get_position(token_id)
                position_data['token_id'] = token_id
                positions.append(position_data)
            except Exception as e:
                logger.error(f"Error getting position {token_id}: {e}")

        return positions
