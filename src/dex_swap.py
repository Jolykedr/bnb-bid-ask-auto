"""
DEX Swap Module

Модуль для обмена токенов через Uniswap/PancakeSwap Router.
Работает напрямую с блокчейном без внешних API.
"""

import logging
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from decimal import Decimal
from web3 import Web3

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

# Router адреса для разных сетей (V2)
ROUTER_V2_ADDRESSES = {
    # BNB Chain - PancakeSwap V2
    56: {
        "router": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
        "weth": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
        "usdt": "0x55d398326f99059fF775485246999027B3197955",
        "name": "PancakeSwap V2"
    },
    # Ethereum - Uniswap V2
    1: {
        "router": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
        "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "usdt": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "name": "Uniswap V2"
    },
    # Base - Uniswap V2
    8453: {
        "router": "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24",
        "weth": "0x4200000000000000000000000000000000000006",
        "usdt": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # USDC on Base
        "name": "Uniswap V2"
    },
    # BNB Testnet
    97: {
        "router": "0xD99D1c33F9fC3444f8101754aBC46c52416550D1",
        "weth": "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd",  # WBNB testnet
        "usdt": "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd",  # BUSD testnet
        "name": "PancakeSwap V2 Testnet"
    }
}

# Router адреса для V3
ROUTER_V3_ADDRESSES = {
    # BNB Chain - PancakeSwap V3 SmartRouter
    56: {
        "router": "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",  # PancakeSwap SmartRouter
        "quoter": "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997",  # PancakeSwap Quoter V2
        "weth": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "usdt": "0x55d398326f99059fF775485246999027B3197955",
        "name": "PancakeSwap V3",
        "fee_tiers": [100, 500, 2500, 10000]  # 0.01%, 0.05%, 0.25%, 1%
    },
    # Ethereum - Uniswap V3 SwapRouter02
    1: {
        "router": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",  # SwapRouter02
        "quoter": "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",  # Quoter V2
        "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "usdt": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "name": "Uniswap V3",
        "fee_tiers": [100, 500, 3000, 10000]
    },
    # Base - Uniswap V3
    8453: {
        "router": "0x2626664c2603336E57B271c5C0b26F421741e481",  # SwapRouter02
        "quoter": "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",
        "weth": "0x4200000000000000000000000000000000000006",
        "usdt": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "name": "Uniswap V3",
        "fee_tiers": [100, 500, 3000, 10000]
    }
}

# Для обратной совместимости
ROUTER_ADDRESSES = ROUTER_V2_ADDRESSES

# Uniswap V2 Router ABI (минимальный для свопов)
ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForTokens",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForTokensSupportingFeeOnTransferTokens",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactETHForTokens",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint256", "name": "amountOutMin", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"}
        ],
        "name": "swapExactTokensForETH",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# Uniswap V3 / PancakeSwap V3 SwapRouter ABI
ROUTER_V3_ABI = [
    # exactInputSingle - single pool swap
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    },
    # exactInput - multi-hop swap via path
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "bytes", "name": "path", "type": "bytes"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"}
                ],
                "internalType": "struct IV3SwapRouter.ExactInputParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "exactInput",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    },
    # multicall - batch multiple calls
    {
        "inputs": [
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
            {"internalType": "bytes[]", "name": "data", "type": "bytes[]"}
        ],
        "name": "multicall",
        "outputs": [{"internalType": "bytes[]", "name": "", "type": "bytes[]"}],
        "stateMutability": "payable",
        "type": "function"
    }
]

# Quoter V2 ABI for getting quotes
QUOTER_V3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"}
        ],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

# ERC20 ABI
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
]


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


class DexSwap:
    """
    Класс для обмена токенов через DEX (Uniswap/PancakeSwap V2 и V3).

    Поддерживает:
    - Uniswap V2/V3
    - PancakeSwap V2/V3
    - Автоматический выбор лучшего пути (V3 с разными fee tiers, V2)

    Использование:
        swapper = DexSwap(w3, chain_id=56)

        # Получить котировку
        amount_out = swapper.get_quote(
            from_token="0x...",
            to_token="0x55d398326f99059ff775485246999027b3197955",
            amount_in=1000000000000000000
        )

        # Выполнить своп
        result = swapper.swap(
            from_token="0x...",
            to_token="0x...",
            amount_in=1000000000000000000,
            wallet_address="0x...",
            private_key="0x...",
            slippage=1.0
        )
    """

    def __init__(self, w3: Web3, chain_id: int = 56):
        self.w3 = w3
        self.chain_id = chain_id

        if chain_id not in ROUTER_V2_ADDRESSES:
            raise ValueError(f"Unsupported chain ID: {chain_id}")

        # V2 Router
        config_v2 = ROUTER_V2_ADDRESSES[chain_id]
        self.router_address = Web3.to_checksum_address(config_v2["router"])
        self.weth_address = Web3.to_checksum_address(config_v2["weth"])
        self.usdt_address = Web3.to_checksum_address(config_v2["usdt"])
        self.dex_name = config_v2["name"]
        self.router = w3.eth.contract(address=self.router_address, abi=ROUTER_ABI)

        # V3 Router (если доступен)
        self.v3_available = chain_id in ROUTER_V3_ADDRESSES
        if self.v3_available:
            config_v3 = ROUTER_V3_ADDRESSES[chain_id]
            self.router_v3_address = Web3.to_checksum_address(config_v3["router"])
            self.quoter_v3_address = Web3.to_checksum_address(config_v3["quoter"])
            self.fee_tiers = config_v3["fee_tiers"]
            self.dex_name_v3 = config_v3["name"]
            self.router_v3 = w3.eth.contract(address=self.router_v3_address, abi=ROUTER_V3_ABI)
            self.quoter_v3 = w3.eth.contract(address=self.quoter_v3_address, abi=QUOTER_V3_ABI)
            logger.info(f"V3 router available: {self.dex_name_v3}")

    def is_stable_token(self, token_address: str) -> bool:
        """Проверить, является ли токен стейблкоином или нативным токеном."""
        return token_address.lower() in STABLE_TOKENS

    def get_output_token(self) -> str:
        """Получить предпочтительный токен для продажи (стейблкоин)."""
        return self.usdt_address

    def get_token_balance(self, token_address: str, wallet_address: str) -> int:
        """Получить баланс токена."""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            return contract.functions.balanceOf(Web3.to_checksum_address(wallet_address)).call()
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return 0

    def get_token_decimals(self, token_address: str) -> int:
        """Получить decimals токена."""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            return contract.functions.decimals().call()
        except:
            return 18

    def get_quote(self, from_token: str, to_token: str, amount_in: int) -> int:
        """
        Получить котировку для свопа.

        Returns:
            Количество токенов на выходе (0 если путь не найден)
        """
        try:
            from_token = Web3.to_checksum_address(from_token)
            to_token = Web3.to_checksum_address(to_token)

            # Построить путь
            path = self._build_path(from_token, to_token)
            if not path:
                return 0

            amounts = self.router.functions.getAmountsOut(amount_in, path).call()
            return amounts[-1]

        except Exception as e:
            logger.error(f"Failed to get quote: {e}")
            return 0

    def _build_path(self, from_token: str, to_token: str) -> List[str]:
        """Построить путь для свопа V2 (через WETH если нужно)."""
        from_token = Web3.to_checksum_address(from_token)
        to_token = Web3.to_checksum_address(to_token)

        # Прямой путь
        direct_path = [from_token, to_token]

        try:
            # Проверить прямой путь
            self.router.functions.getAmountsOut(10**18, direct_path).call()
            return direct_path
        except:
            pass

        # Путь через WETH
        if from_token.lower() != self.weth_address.lower() and to_token.lower() != self.weth_address.lower():
            weth_path = [from_token, self.weth_address, to_token]
            try:
                self.router.functions.getAmountsOut(10**18, weth_path).call()
                return weth_path
            except:
                pass

        return []

    def get_quote_v3(self, from_token: str, to_token: str, amount_in: int, fee: int = None) -> Tuple[int, int]:
        """
        Получить котировку V3 для свопа.

        Args:
            from_token: Адрес входного токена
            to_token: Адрес выходного токена
            amount_in: Количество входного токена
            fee: Fee tier (100, 500, 2500/3000, 10000). Если None - пробуем все

        Returns:
            (amount_out, best_fee) - количество токенов на выходе и лучший fee tier
        """
        if not self.v3_available:
            return (0, 0)

        from_token = Web3.to_checksum_address(from_token)
        to_token = Web3.to_checksum_address(to_token)

        fees_to_try = [fee] if fee else self.fee_tiers
        best_out = 0
        best_fee = 0

        for fee_tier in fees_to_try:
            try:
                # Используем quoteExactInputSingle
                params = (
                    from_token,      # tokenIn
                    to_token,        # tokenOut
                    amount_in,       # amountIn
                    fee_tier,        # fee
                    0                # sqrtPriceLimitX96 (0 = no limit)
                )

                result = self.quoter_v3.functions.quoteExactInputSingle(params).call()
                amount_out = result[0]  # First return value is amountOut

                if amount_out > best_out:
                    best_out = amount_out
                    best_fee = fee_tier
                    logger.debug(f"V3 quote fee={fee_tier}: {amount_out}")

            except Exception as e:
                logger.debug(f"V3 quote failed for fee={fee_tier}: {e}")
                continue

        # Если прямой путь не работает, пробуем через WETH (multi-hop)
        if best_out == 0 and from_token.lower() != self.weth_address.lower() and to_token.lower() != self.weth_address.lower():
            for fee1 in self.fee_tiers[:2]:  # Только низкие fee для первого хопа
                for fee2 in self.fee_tiers[:2]:
                    try:
                        # Первый хоп: from -> WETH
                        params1 = (from_token, self.weth_address, amount_in, fee1, 0)
                        result1 = self.quoter_v3.functions.quoteExactInputSingle(params1).call()
                        weth_amount = result1[0]

                        if weth_amount > 0:
                            # Второй хоп: WETH -> to
                            params2 = (self.weth_address, to_token, weth_amount, fee2, 0)
                            result2 = self.quoter_v3.functions.quoteExactInputSingle(params2).call()
                            final_amount = result2[0]

                            if final_amount > best_out:
                                best_out = final_amount
                                best_fee = fee1  # Используем fee первого хопа как индикатор
                                logger.debug(f"V3 multi-hop quote: {final_amount}")

                    except Exception as e:
                        continue

        return (best_out, best_fee)

    def swap_v3(
        self,
        from_token: str,
        to_token: str,
        amount_in: int,
        wallet_address: str,
        private_key: str,
        slippage: float = 1.0,
        deadline_minutes: int = 20,
        fee: int = None
    ) -> SwapResult:
        """
        Выполнить своп через V3 Router.

        Args:
            from_token: Адрес токена для продажи
            to_token: Адрес токена для покупки
            amount_in: Количество в минимальных единицах
            wallet_address: Адрес кошелька
            private_key: Приватный ключ
            slippage: Проскальзывание в % (1.0 = 1%)
            deadline_minutes: Дедлайн в минутах
            fee: Fee tier (если None - выбирается лучший)

        Returns:
            SwapResult с результатом операции
        """
        if not self.v3_available:
            return SwapResult(
                success=False, tx_hash=None, from_token=from_token, to_token=to_token,
                from_amount=amount_in, to_amount=0, to_amount_usd=0, gas_used=0,
                error="V3 not available for this chain"
            )

        try:
            from_token = Web3.to_checksum_address(from_token)
            to_token = Web3.to_checksum_address(to_token)
            wallet_address = Web3.to_checksum_address(wallet_address)

            # Получить лучшую котировку
            expected_out, best_fee = self.get_quote_v3(from_token, to_token, amount_in, fee)

            if expected_out == 0:
                return SwapResult(
                    success=False, tx_hash=None, from_token=from_token, to_token=to_token,
                    from_amount=amount_in, to_amount=0, to_amount_usd=0, gas_used=0,
                    error="No V3 liquidity found"
                )

            logger.info(f"V3 swap: best fee tier = {best_fee/10000}%, expected out = {expected_out}")

            # Рассчитать минимум с учётом слипажа
            min_out = int(expected_out * (100 - slippage) / 100)

            # Approve для V3 Router
            if not self._check_and_approve_v3(from_token, amount_in, wallet_address, private_key):
                return SwapResult(
                    success=False, tx_hash=None, from_token=from_token, to_token=to_token,
                    from_amount=amount_in, to_amount=0, to_amount_usd=0, gas_used=0,
                    error="Failed to approve for V3"
                )

            # Deadline
            import time
            deadline = int(time.time()) + (deadline_minutes * 60)

            # Построить параметры для exactInputSingle
            swap_params = (
                from_token,       # tokenIn
                to_token,         # tokenOut
                best_fee,         # fee
                wallet_address,   # recipient
                amount_in,        # amountIn
                min_out,          # amountOutMinimum
                0                 # sqrtPriceLimitX96 (0 = no limit)
            )

            # Encode exactInputSingle call
            swap_data = self.router_v3.encodeABI(
                fn_name='exactInputSingle',
                args=[swap_params]
            )

            # Использовать multicall с deadline
            tx = self.router_v3.functions.multicall(
                deadline,
                [swap_data]
            ).build_transaction({
                'from': wallet_address,
                'nonce': self.w3.eth.get_transaction_count(wallet_address),
                'gas': 350000,
                'gasPrice': self.w3.eth.gas_price,
                'value': 0
            })

            # Подписать и отправить
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            logger.info(f"V3 Swap TX sent: {tx_hash.hex()}")

            # Ждём подтверждения
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 1:
                to_decimals = self.get_token_decimals(to_token)
                to_amount_usd = expected_out / (10 ** to_decimals)

                return SwapResult(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=expected_out,
                    to_amount_usd=to_amount_usd,
                    gas_used=receipt.gasUsed
                )
            else:
                return SwapResult(
                    success=False,
                    tx_hash=tx_hash.hex(),
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=receipt.gasUsed,
                    error="V3 swap transaction failed"
                )

        except Exception as e:
            logger.error(f"V3 swap failed: {e}")
            return SwapResult(
                success=False, tx_hash=None, from_token=from_token, to_token=to_token,
                from_amount=amount_in, to_amount=0, to_amount_usd=0, gas_used=0,
                error=str(e)
            )

    def _check_and_approve_v3(
        self,
        token_address: str,
        amount: int,
        wallet_address: str,
        private_key: str
    ) -> bool:
        """Проверить и при необходимости одобрить токен для V3 Router."""
        try:
            token_address = Web3.to_checksum_address(token_address)
            wallet_address = Web3.to_checksum_address(wallet_address)

            token_contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)

            current_allowance = token_contract.functions.allowance(
                wallet_address, self.router_v3_address
            ).call()

            if current_allowance >= amount:
                logger.info(f"Token already approved for V3: {current_allowance} >= {amount}")
                return True

            logger.info(f"Approving token for {self.dex_name_v3}...")

            max_uint256 = 2**256 - 1
            tx = token_contract.functions.approve(
                self.router_v3_address, max_uint256
            ).build_transaction({
                'from': wallet_address,
                'nonce': self.w3.eth.get_transaction_count(wallet_address),
                'gas': 100000,
                'gasPrice': self.w3.eth.gas_price
            })

            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            logger.info(f"V3 Approve TX: {tx_hash.hex()}")

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            return receipt.status == 1

        except Exception as e:
            logger.error(f"Failed to approve for V3: {e}")
            return False

    def check_and_approve(
        self,
        token_address: str,
        amount: int,
        wallet_address: str,
        private_key: str
    ) -> bool:
        """
        Проверить и при необходимости одобрить токен для свопа.
        """
        try:
            token_address = Web3.to_checksum_address(token_address)
            wallet_address = Web3.to_checksum_address(wallet_address)

            token_contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)

            # Проверить текущий allowance
            current_allowance = token_contract.functions.allowance(
                wallet_address, self.router_address
            ).call()

            if current_allowance >= amount:
                logger.info(f"Token already approved: {current_allowance} >= {amount}")
                return True

            # Approve максимальное значение
            logger.info(f"Approving token for {self.dex_name}...")

            max_uint256 = 2**256 - 1
            tx = token_contract.functions.approve(
                self.router_address, max_uint256
            ).build_transaction({
                'from': wallet_address,
                'nonce': self.w3.eth.get_transaction_count(wallet_address),
                'gas': 100000,
                'gasPrice': self.w3.eth.gas_price
            })

            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            logger.info(f"Approve TX: {tx_hash.hex()}")

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            return receipt.status == 1

        except Exception as e:
            logger.error(f"Failed to approve: {e}")
            return False

    def swap(
        self,
        from_token: str,
        to_token: str,
        amount_in: int,
        wallet_address: str,
        private_key: str,
        slippage: float = 1.0,
        deadline_minutes: int = 20,
        use_fee_on_transfer: bool = True,
        prefer_v3: bool = True
    ) -> SwapResult:
        """
        Выполнить своп токена. Сначала пробует V3, затем V2.

        Args:
            from_token: Адрес токена для продажи
            to_token: Адрес токена для покупки
            amount_in: Количество в минимальных единицах
            wallet_address: Адрес кошелька
            private_key: Приватный ключ
            slippage: Проскальзывание в % (1.0 = 1%)
            deadline_minutes: Дедлайн в минутах
            use_fee_on_transfer: Использовать метод для токенов с комиссией (V2)
            prefer_v3: Пробовать V3 сначала (по умолчанию True)

        Returns:
            SwapResult с результатом операции
        """
        try:
            from_token = Web3.to_checksum_address(from_token)
            to_token = Web3.to_checksum_address(to_token)
            wallet_address = Web3.to_checksum_address(wallet_address)

            # Проверить что токен не стейбл
            if self.is_stable_token(from_token):
                return SwapResult(
                    success=False,
                    tx_hash=None,
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=0,
                    error="Token is a stablecoin, no swap needed"
                )

            # Проверить баланс
            balance = self.get_token_balance(from_token, wallet_address)
            if balance < amount_in:
                logger.warning(f"Insufficient balance: {balance} < {amount_in}")
                amount_in = balance

            if amount_in == 0:
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

            # Пробуем V3 сначала (если доступен и prefer_v3=True)
            if prefer_v3 and self.v3_available:
                logger.info("Trying V3 swap first...")
                v3_quote, best_fee = self.get_quote_v3(from_token, to_token, amount_in)

                if v3_quote > 0:
                    logger.info(f"V3 quote found: {v3_quote} (fee tier: {best_fee/10000}%)")

                    # Сравнить с V2 котировкой
                    v2_quote = self.get_quote(from_token, to_token, amount_in)

                    # Использовать V3 если котировка лучше или V2 недоступен
                    if v3_quote >= v2_quote or v2_quote == 0:
                        logger.info(f"Using V3 (V3: {v3_quote} vs V2: {v2_quote})")
                        result = self.swap_v3(
                            from_token, to_token, amount_in,
                            wallet_address, private_key,
                            slippage, deadline_minutes, best_fee
                        )
                        if result.success:
                            return result
                        else:
                            logger.warning(f"V3 swap failed: {result.error}, trying V2...")
                    else:
                        logger.info(f"V2 has better price (V2: {v2_quote} vs V3: {v3_quote})")
                else:
                    logger.info("No V3 liquidity found, falling back to V2")

            # V2 Swap
            return self._swap_v2(
                from_token, to_token, amount_in,
                wallet_address, private_key,
                slippage, deadline_minutes, use_fee_on_transfer
            )

        except Exception as e:
            logger.error(f"Swap failed: {e}")
            return SwapResult(
                success=False,
                tx_hash=None,
                from_token=from_token,
                to_token=to_token,
                from_amount=amount_in,
                to_amount=0,
                to_amount_usd=0,
                gas_used=0,
                error=str(e)
            )

    def _swap_v2(
        self,
        from_token: str,
        to_token: str,
        amount_in: int,
        wallet_address: str,
        private_key: str,
        slippage: float = 1.0,
        deadline_minutes: int = 20,
        use_fee_on_transfer: bool = True
    ) -> SwapResult:
        """Выполнить своп через V2 Router."""
        try:
            # Построить путь
            path = self._build_path(from_token, to_token)
            if not path:
                return SwapResult(
                    success=False,
                    tx_hash=None,
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=0,
                    error="No V2 swap path found"
                )

            # Получить ожидаемое количество
            expected_out = self.get_quote(from_token, to_token, amount_in)
            if expected_out == 0:
                return SwapResult(
                    success=False,
                    tx_hash=None,
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=0,
                    error="Could not get V2 quote"
                )

            logger.info(f"V2 swap: expected out = {expected_out}")

            # Рассчитать минимум с учётом слипажа
            min_out = int(expected_out * (100 - slippage) / 100)

            # Approve
            if not self.check_and_approve(from_token, amount_in, wallet_address, private_key):
                return SwapResult(
                    success=False,
                    tx_hash=None,
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=0,
                    error="Failed to approve token"
                )

            # Deadline
            import time
            deadline = int(time.time()) + (deadline_minutes * 60)

            # Построить транзакцию
            if use_fee_on_transfer:
                tx = self.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
                    amount_in,
                    min_out,
                    path,
                    wallet_address,
                    deadline
                ).build_transaction({
                    'from': wallet_address,
                    'nonce': self.w3.eth.get_transaction_count(wallet_address),
                    'gas': 300000,
                    'gasPrice': self.w3.eth.gas_price
                })
            else:
                tx = self.router.functions.swapExactTokensForTokens(
                    amount_in,
                    min_out,
                    path,
                    wallet_address,
                    deadline
                ).build_transaction({
                    'from': wallet_address,
                    'nonce': self.w3.eth.get_transaction_count(wallet_address),
                    'gas': 300000,
                    'gasPrice': self.w3.eth.gas_price
                })

            # Подписать и отправить
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            logger.info(f"V2 Swap TX sent: {tx_hash.hex()}")

            # Ждём подтверждения
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 1:
                actual_out = expected_out

                to_decimals = self.get_token_decimals(to_token)
                to_amount_usd = actual_out / (10 ** to_decimals)

                return SwapResult(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=actual_out,
                    to_amount_usd=to_amount_usd,
                    gas_used=receipt.gasUsed
                )
            else:
                return SwapResult(
                    success=False,
                    tx_hash=tx_hash.hex(),
                    from_token=from_token,
                    to_token=to_token,
                    from_amount=amount_in,
                    to_amount=0,
                    to_amount_usd=0,
                    gas_used=receipt.gasUsed,
                    error="V2 swap transaction failed"
                )

        except Exception as e:
            logger.error(f"V2 swap failed: {e}")
            return SwapResult(
                success=False,
                tx_hash=None,
                from_token=from_token,
                to_token=to_token,
                from_amount=amount_in,
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
    slippage: float = 1.0
) -> Dict[str, Any]:
    """
    Продать токены после закрытия позиции через DEX.

    Args:
        w3: Web3 instance
        chain_id: ID сети
        tokens: Список токенов [{address, amount, decimals, symbol}]
        wallet_address: Адрес кошелька
        private_key: Приватный ключ
        slippage: Проскальзывание в %

    Returns:
        {
            "total_usd": float,
            "swaps": [SwapResult],
            "skipped": [token_address]
        }
    """
    swapper = DexSwap(w3, chain_id)
    output_token = swapper.get_output_token()

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

            # Добавить к total_usd (1:1 для стейблов)
            decimals = token.get("decimals", 18)
            results["total_usd"] += amount / (10 ** decimals)
            continue

        # Выполнить своп
        logger.info(f"Selling {token.get('symbol', token_address)}: {amount}")

        result = swapper.swap(
            from_token=token_address,
            to_token=output_token,
            amount_in=amount,
            wallet_address=wallet_address,
            private_key=private_key,
            slippage=slippage
        )

        results["swaps"].append({
            'token': token.get('symbol', token_address[:10]),
            'success': result.success,
            'usd': result.to_amount_usd,
            'tx_hash': result.tx_hash,
            'error': result.error
        })

        if result.success:
            results["total_usd"] += result.to_amount_usd
            logger.info(f"Sold {token.get('symbol', '')}: ${result.to_amount_usd:.2f}")
        else:
            logger.error(f"Failed to sell {token.get('symbol', '')}: {result.error}")

    return results
