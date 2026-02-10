"""
OKX DEX Aggregator Integration

Модуль для автоматической продажи токенов через OKX DEX API.
Используется при закрытии позиций для конвертации полученных токенов в стейблкоины.
"""

import hashlib
import hmac
import base64
import time
import json
import logging
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from decimal import Decimal
import requests
from web3 import Web3
from .utils import NonceManager

logger = logging.getLogger(__name__)

# Токены которые НЕ нужно продавать (стейблкоины и нативные токены)
STABLE_TOKENS = {
    # BNB Chain (56)
    "0x55d398326f99059ff775485246999027b3197955": "USDT",  # BSC USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",  # BSC USDC
    "0xe9e7cea3dedca5984780bafc599bd69add087d56": "BUSD",  # BUSD
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",  # Wrapped BNB
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": "BNB",   # Native BNB

    # Ethereum (1)
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",  # ETH USDT
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",  # ETH USDC
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",  # WETH

    # Base (8453)
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",  # Base USDC
    "0x4200000000000000000000000000000000000006": "WETH",  # Base WETH
}

# Предпочтительные токены для продажи (куда конвертировать)
PREFERRED_OUTPUT = {
    56: "0x55d398326f99059ff775485246999027b3197955",   # BSC -> USDT
    1: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",    # ETH -> USDC
    8453: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", # Base -> USDC
}


@dataclass
class SwapQuote:
    """Результат котировки свопа."""
    from_token: str
    to_token: str
    from_amount: int
    to_amount: int
    to_amount_usd: float
    price_impact: float
    gas_estimate: int
    route: List[str]


@dataclass
class SwapResult:
    """Результат выполнения свопа."""
    success: bool
    tx_hash: Optional[str]
    from_token: str
    to_token: str
    from_amount: int
    to_amount: int
    to_amount_usd: float
    gas_used: int
    error: Optional[str] = None


class OKXDexSwap:
    """
    Клиент для работы с OKX DEX Aggregator API.

    Использование:
        swapper = OKXDexSwap(
            api_key="your_key",
            secret_key="your_secret",
            passphrase="your_passphrase",
            project_id="your_project_id"  # опционально
        )

        # Получить котировку
        quote = swapper.get_quote(
            chain_id=56,
            from_token="0x...",
            to_token="0x55d398326f99059ff775485246999027b3197955",
            amount=1000000000000000000  # 1 токен с 18 decimals
        )

        # Выполнить своп
        result = swapper.execute_swap(
            chain_id=56,
            from_token="0x...",
            to_token="0x...",
            amount=1000000000000000000,
            wallet_address="0x...",
            private_key="0x...",
            w3=web3_instance
        )
    """

    BASE_URL = "https://web3.okx.com/api/v6/dex/aggregator"

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str,
        project_id: str = "",
        nonce_manager: 'NonceManager' = None
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.project_id = project_id
        self.nonce_manager = nonce_manager
        self.session = requests.Session()

    def _get_timestamp(self) -> str:
        """Получить timestamp в формате ISO 8601."""
        return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())

    def _sign(self, timestamp: str, method: str, request_path: str, query_string: str = "") -> str:
        """Создать подпись запроса HMAC-SHA256."""
        message = timestamp + method + request_path
        if query_string:
            message += "?" + query_string

        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        )
        return base64.b64encode(signature.digest()).decode('utf-8')

    def _get_headers(self, method: str, request_path: str, query_string: str = "") -> Dict[str, str]:
        """Получить заголовки с аутентификацией."""
        timestamp = self._get_timestamp()
        signature = self._sign(timestamp, method, request_path, query_string)

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

        if self.project_id:
            headers["OK-ACCESS-PROJECT"] = self.project_id

        return headers

    def _request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Выполнить GET запрос к API."""
        request_path = f"/api/v6/dex/aggregator/{endpoint}"
        query_string = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)

        headers = self._get_headers("GET", request_path, query_string)
        url = f"{self.BASE_URL}/{endpoint}"

        logger.debug(f"OKX DEX Request: {url}?{query_string}")

        response = self.session.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        logger.debug(f"OKX DEX Response: {data}")

        if data.get("code") != "0":
            raise Exception(f"OKX DEX API Error: {data.get('msg', 'Unknown error')}")

        return data

    def is_stable_token(self, token_address: str) -> bool:
        """Проверить, является ли токен стейблкоином или нативным токеном."""
        return token_address.lower() in STABLE_TOKENS

    def get_output_token(self, chain_id: int) -> str:
        """Получить предпочтительный токен для продажи (стейблкоин)."""
        return PREFERRED_OUTPUT.get(chain_id, PREFERRED_OUTPUT[56])

    def get_quote(
        self,
        chain_id: int,
        from_token: str,
        to_token: str,
        amount: int,
        slippage: float = 0.5
    ) -> Optional[SwapQuote]:
        """
        Получить котировку для свопа.

        Args:
            chain_id: ID сети (56=BNB, 1=ETH, 8453=Base)
            from_token: Адрес токена для продажи
            to_token: Адрес токена для покупки
            amount: Количество в минимальных единицах (wei)
            slippage: Проскальзывание в процентах (0.5 = 0.5%)

        Returns:
            SwapQuote или None если котировка недоступна
        """
        try:
            params = {
                "chainIndex": str(chain_id),
                "fromTokenAddress": from_token,
                "toTokenAddress": to_token,
                "amount": str(amount),
                "slippagePercent": str(slippage)
            }

            response = self._request("quote", params)
            data = response.get("data", [{}])[0]

            router_result = data.get("routerResult", {})

            return SwapQuote(
                from_token=from_token,
                to_token=to_token,
                from_amount=amount,
                to_amount=int(router_result.get("toTokenAmount", 0)),
                to_amount_usd=float(router_result.get("toTokenUsdValue", 0)),
                price_impact=float(router_result.get("priceImpactPercent", 0)),
                gas_estimate=int(router_result.get("estimateGasFee", 0)),
                route=[p.get("dexName", "") for p in router_result.get("dexRouterList", [])]
            )
        except Exception as e:
            logger.error(f"Failed to get quote: {e}")
            return None

    def get_swap_data(
        self,
        chain_id: int,
        from_token: str,
        to_token: str,
        amount: int,
        wallet_address: str,
        slippage: float = 0.5
    ) -> Optional[Dict[str, Any]]:
        """
        Получить данные для транзакции свопа.

        Returns:
            Словарь с параметрами транзакции (to, data, value, gas) или None
        """
        try:
            params = {
                "chainIndex": str(chain_id),
                "fromTokenAddress": from_token,
                "toTokenAddress": to_token,
                "amount": str(amount),
                "slippagePercent": str(slippage),
                "userWalletAddress": wallet_address
            }

            response = self._request("swap", params)
            data = response.get("data", [{}])[0]

            tx = data.get("tx", {})
            router_result = data.get("routerResult", {})

            return {
                "to": tx.get("to"),
                "data": tx.get("data"),
                "value": int(tx.get("value", 0)),
                "gas": int(tx.get("gas", 300000)),
                "to_amount": int(router_result.get("toTokenAmount", 0)),
                "min_receive": int(tx.get("minReceiveAmount", 0))
            }
        except Exception as e:
            logger.error(f"Failed to get swap data: {e}")
            return None

    def check_and_approve(
        self,
        w3: Web3,
        chain_id: int,
        token_address: str,
        amount: int,
        wallet_address: str,
        private_key: str
    ) -> bool:
        """
        Проверить и при необходимости одобрить токен для свопа.

        Returns:
            True если одобрение успешно или не требуется
        """
        try:
            # Получить адрес spender (роутер OKX DEX)
            params = {
                "chainIndex": str(chain_id),
                "tokenContractAddress": token_address,
                "approveAmount": str(amount)
            }

            response = self._request("approve-transaction", params)
            data = response.get("data", [{}])[0]

            # Проверить текущий allowance
            dex_address = data.get("dexContractAddress")
            if not dex_address:
                logger.warning("Could not get DEX contract address")
                return False

            # ERC20 ABI для allowance и approve
            erc20_abi = [
                {
                    "constant": True,
                    "inputs": [
                        {"name": "owner", "type": "address"},
                        {"name": "spender", "type": "address"}
                    ],
                    "name": "allowance",
                    "outputs": [{"name": "", "type": "uint256"}],
                    "type": "function"
                },
                {
                    "constant": False,
                    "inputs": [
                        {"name": "spender", "type": "address"},
                        {"name": "amount", "type": "uint256"}
                    ],
                    "name": "approve",
                    "outputs": [{"name": "", "type": "bool"}],
                    "type": "function"
                }
            ]

            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=erc20_abi
            )

            current_allowance = token_contract.functions.allowance(
                Web3.to_checksum_address(wallet_address),
                Web3.to_checksum_address(dex_address)
            ).call()

            if current_allowance >= amount:
                logger.info(f"Token already approved: {current_allowance} >= {amount}")
                return True

            # Выполнить approve
            logger.info(f"Approving token {token_address} for OKX DEX...")

            # Используем максимальное значение для approve
            max_uint256 = 2**256 - 1

            nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                    w3.eth.get_transaction_count(Web3.to_checksum_address(wallet_address), 'pending')

            try:
                tx = token_contract.functions.approve(
                    Web3.to_checksum_address(dex_address),
                    max_uint256
                ).build_transaction({
                    'from': Web3.to_checksum_address(wallet_address),
                    'nonce': nonce,
                    'gas': 100000,
                    'gasPrice': w3.eth.gas_price
                })

                signed_tx = w3.eth.account.sign_transaction(tx, private_key)
                tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

                logger.info(f"Approve TX sent: {tx_hash.hex()}")

                # Ждём подтверждения
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

                if self.nonce_manager:
                    if receipt.status == 1:
                        self.nonce_manager.confirm_transaction(nonce)
                    else:
                        self.nonce_manager.release_nonce(nonce)

                if receipt.status == 1:
                    logger.info("Token approved successfully")
                    return True
                else:
                    logger.error("Approve transaction failed")
                    return False

            except Exception as e:
                if self.nonce_manager:
                    self.nonce_manager.release_nonce(nonce)
                raise

        except Exception as e:
            logger.error(f"Failed to approve token: {e}")
            return False

    def execute_swap(
        self,
        w3: Web3,
        chain_id: int,
        from_token: str,
        to_token: str,
        amount: int,
        wallet_address: str,
        private_key: str,
        slippage: float = 0.5
    ) -> SwapResult:
        """
        Выполнить своп токена.

        Args:
            w3: Web3 instance
            chain_id: ID сети
            from_token: Адрес токена для продажи
            to_token: Адрес токена для покупки
            amount: Количество в минимальных единицах
            wallet_address: Адрес кошелька
            private_key: Приватный ключ
            slippage: Проскальзывание в %

        Returns:
            SwapResult с результатом операции
        """
        try:
            # Проверить что токен не стейбл (не нужно продавать)
            if self.is_stable_token(from_token):
                return SwapResult(
                    success=False,
                    tx_hash=None,
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=0,
                    error="Token is a stablecoin, no swap needed"
                )

            # Проверить баланс
            is_native = from_token.lower() == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

            if is_native:
                balance = w3.eth.get_balance(Web3.to_checksum_address(wallet_address))
            else:
                erc20_abi = [{"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
                token_contract = w3.eth.contract(address=Web3.to_checksum_address(from_token), abi=erc20_abi)
                balance = token_contract.functions.balanceOf(Web3.to_checksum_address(wallet_address)).call()

            if balance < amount:
                logger.warning(f"Insufficient balance: {balance} < {amount}")
                amount = balance  # Используем доступный баланс

            if amount == 0:
                return SwapResult(
                    success=False,
                    tx_hash=None,
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=0,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=0,
                    error="Zero balance"
                )

            # Approve если не нативный токен
            if not is_native:
                if not self.check_and_approve(w3, chain_id, from_token, amount, wallet_address, private_key):
                    return SwapResult(
                        success=False,
                        tx_hash=None,
                        from_token=from_token,
                        to_token=to_token,
                        from_amount=amount,
                        to_amount=0,
                        to_amount_usd=0,
                        gas_used=0,
                        error="Failed to approve token"
                    )

            # Получить данные для свопа
            swap_data = self.get_swap_data(
                chain_id, from_token, to_token, amount, wallet_address, slippage
            )

            if not swap_data:
                return SwapResult(
                    success=False,
                    tx_hash=None,
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=0,
                    error="Failed to get swap data"
                )

            # Построить транзакцию
            nonce = self.nonce_manager.get_next_nonce() if self.nonce_manager else \
                    w3.eth.get_transaction_count(Web3.to_checksum_address(wallet_address), 'pending')

            try:
                tx = {
                    'from': Web3.to_checksum_address(wallet_address),
                    'to': Web3.to_checksum_address(swap_data['to']),
                    'data': swap_data['data'],
                    'value': swap_data['value'] if is_native else 0,
                    'nonce': nonce,
                    'gas': swap_data['gas'],
                    'gasPrice': w3.eth.gas_price
                }

                # Подписать и отправить
                signed_tx = w3.eth.account.sign_transaction(tx, private_key)
                tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

                logger.info(f"Swap TX sent: {tx_hash.hex()}")

                # Ждём подтверждения
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

                if self.nonce_manager:
                    if receipt.status == 1:
                        self.nonce_manager.confirm_transaction(nonce)
                    else:
                        self.nonce_manager.release_nonce(nonce)
            except Exception as e:
                if self.nonce_manager:
                    self.nonce_manager.release_nonce(nonce)
                raise

            if receipt.status == 1:
                # Получить котировку для расчёта USD value
                quote = self.get_quote(chain_id, from_token, to_token, amount, slippage)
                to_amount_usd = quote.to_amount_usd if quote else 0

                return SwapResult(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount,
                    to_amount=swap_data['to_amount'],
                    to_amount_usd=to_amount_usd,
                    gas_used=receipt.gasUsed
                )
            else:
                return SwapResult(
                    success=False,
                    tx_hash=tx_hash.hex(),
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=receipt.gasUsed,
                    error="Swap transaction failed"
                )

        except Exception as e:
            logger.error(f"Swap failed: {e}")
            return SwapResult(
                success=False,
                tx_hash=None,
                from_token=from_token,
                to_token=to_token,
                from_amount=amount,
                to_amount=0,
                to_amount_usd=0,
                gas_used=0,
                error=str(e)
            )


def sell_tokens_after_close(
    w3: Web3,
    chain_id: int,
    tokens: List[Dict[str, Any]],
    wallet_address: str,
    private_key: str,
    okx_api_key: str,
    okx_secret_key: str,
    okx_passphrase: str,
    okx_project_id: str = "",
    slippage: float = 1.0
) -> Dict[str, Any]:
    """
    Продать токены после закрытия позиции.

    Args:
        w3: Web3 instance
        chain_id: ID сети
        tokens: Список токенов [{address, amount, decimals, symbol}]
        wallet_address: Адрес кошелька
        private_key: Приватный ключ
        okx_*: Ключи OKX DEX API
        slippage: Проскальзывание в %

    Returns:
        {
            "total_usd": float,
            "swaps": [SwapResult],
            "skipped": [token_address]
        }
    """
    swapper = OKXDexSwap(okx_api_key, okx_secret_key, okx_passphrase, okx_project_id)

    output_token = swapper.get_output_token(chain_id)
    results = {
        "total_usd": 0.0,
        "swaps": [],
        "skipped": []
    }

    for token in tokens:
        token_address = token.get("address", "").lower()
        amount = token.get("amount", 0)

        if amount <= 0:
            continue

        # Пропустить стейблкоины
        if swapper.is_stable_token(token_address):
            logger.info(f"Skipping stable token: {token.get('symbol', token_address)}")
            results["skipped"].append(token_address)

            # Добавить к total_usd если это стейбл (1:1)
            decimals = token.get("decimals", 18)
            results["total_usd"] += amount / (10 ** decimals)
            continue

        # Выполнить своп
        logger.info(f"Selling {token.get('symbol', token_address)}: {amount}")

        result = swapper.execute_swap(
            w3=w3,
            chain_id=chain_id,
            from_token=token_address,
            to_token=output_token,
            amount=amount,
            wallet_address=wallet_address,
            private_key=private_key,
            slippage=slippage
        )

        results["swaps"].append(result)

        if result.success:
            results["total_usd"] += result.to_amount_usd
            logger.info(f"Sold {token.get('symbol', '')}: ${result.to_amount_usd:.2f}")
        else:
            logger.error(f"Failed to sell {token.get('symbol', '')}: {result.error}")

    return results
