"""
KyberSwap Aggregator API Client

HTTP-клиент для KyberSwap Aggregator — агрегатора DEX ликвидности.
Автоматически находит оптимальный маршрут свапа через все доступные DEX'ы.

API flow:
1. GET /routes — получить котировку и маршрут
2. POST /route/build — получить encoded calldata для TX
3. Отправить TX на router contract

Docs: https://docs.kyberswap.com/kyberswap-solutions/kyberswap-aggregator
"""

import base64
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, unquote

import requests
from requests.adapters import HTTPAdapter
from web3 import Web3

logger = logging.getLogger(__name__)

# ── Chain slug mapping ──
KYBER_CHAIN_SLUGS = {
    56: "bsc",
    8453: "base",
    1: "ethereum",
}

KYBER_BASE_URL = "https://aggregator-api.kyberswap.com"
KYBER_CLIENT_ID = "bnb-ladder"

# Whitelist известных KyberSwap роутеров (защита от подмены адреса)
KYBER_KNOWN_ROUTERS = {
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5",  # MetaAggregationRouterV2 (все сети)
}


# ── Exceptions ──

class KyberSwapError(Exception):
    """Базовая ошибка KyberSwap."""
    pass


class KyberSwapAPIError(KyberSwapError):
    """API вернул ошибку (non-200 или невалидный ответ)."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"KyberSwap API error {status_code}: {message}")


class KyberSwapNoRouteError(KyberSwapError):
    """Маршрут для пары не найден."""
    pass


class KyberSwapTimeoutError(KyberSwapError):
    """Таймаут запроса к API."""
    pass


# ── Data classes ──

@dataclass
class KyberQuote:
    """Результат GET /routes — котировка свапа."""
    amount_in: int            # Входная сумма в wei
    amount_out: int           # Ожидаемый выход в wei
    amount_out_human: float   # Выход в человекочитаемом формате
    gas_usd: str              # Стоимость газа в USD (строка из API)
    route_summary: dict       # Сырой routeSummary для POST /route/build
    router_address: str       # Адрес контракта роутера (checksummed)
    price_impact: float       # Price impact в % (из API, 0 если не предоставлен)
    route_description: str    # "KyberSwap: DOG → WBNB → USDT"


@dataclass
class KyberBuildResult:
    """Результат POST /route/build — данные для TX."""
    router_address: str       # Адрес контракта (checksummed)
    encoded_data: str         # Hex-encoded calldata для TX
    gas_estimate: int         # Оценка газа (0 если не предоставлен)


# ── Proxy auth adapter ──

class _ProxyAuthAdapter(HTTPAdapter):
    """HTTPAdapter с явной Proxy-Authorization для HTTPS CONNECT tunnel.

    requests/urllib3 извлекают auth из proxy URL через urlparse,
    но если в пароле есть спецсимволы (@, :, /) — парсинг ломается
    и Proxy-Authorization не отправляется → 407.
    """

    def __init__(self, proxy_basic_auth: str = None, **kwargs):
        self._proxy_basic_auth = proxy_basic_auth
        super().__init__(**kwargs)

    def proxy_headers(self, proxy):
        headers = super().proxy_headers(proxy)
        if self._proxy_basic_auth and 'Proxy-Authorization' not in headers:
            headers['Proxy-Authorization'] = self._proxy_basic_auth
        return headers


# ── Client ──

class KyberSwapClient:
    """
    HTTP-клиент для KyberSwap Aggregator API.

    Использование:
        client = KyberSwapClient(chain_id=56)
        quote = client.get_quote(token_in, token_out, amount_in)
        build = client.build_route(quote.route_summary, sender, sender, slippage_bips=50)
        # Approve token_in на build.router_address
        # Отправить TX: {to: build.router_address, data: build.encoded_data}
    """

    def __init__(self, chain_id: int, timeout: float = 15.0, proxy: dict = None):
        if chain_id not in KYBER_CHAIN_SLUGS:
            raise KyberSwapError(f"Unsupported chain_id: {chain_id}")
        self.chain_id = chain_id
        self.chain_slug = KYBER_CHAIN_SLUGS[chain_id]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False  # Не использовать системные прокси (OS/env)
        self.session.headers.update({
            "X-Client-Id": KYBER_CLIENT_ID,
            "Accept": "application/json",
        })
        if proxy:
            self.session.proxies.update(proxy)
            # Явно задать Proxy-Authorization для HTTPS CONNECT tunnel
            auth_header = self._extract_proxy_auth(proxy)
            if auth_header:
                adapter = _ProxyAuthAdapter(proxy_basic_auth=auth_header)
                self.session.mount('http://', adapter)
                self.session.mount('https://', adapter)

    @staticmethod
    def _extract_proxy_auth(proxy: dict) -> Optional[str]:
        """Извлечь Proxy-Authorization из proxy URL.

        Парсит URL вида http://user:pass@host:port и возвращает
        'Basic base64(user:pass)'. Работает даже если urlparse
        не справляется — ищет @ как разделитель auth/host.
        """
        for proxy_url in proxy.values():
            if not proxy_url or '@' not in str(proxy_url):
                continue
            try:
                url_str = str(proxy_url)
                # Попробовать стандартный urlparse
                parsed = urlparse(url_str)
                if parsed.username:
                    username = unquote(parsed.username)
                    password = unquote(parsed.password or '')
                else:
                    # Fallback: ручной парсинг (для спецсимволов в пароле)
                    # http://user:p@ss@host:port → scheme://  +  user:p@ss  +  @host:port
                    after_scheme = url_str.split('://', 1)[-1]
                    # Последний @ — разделитель auth/host
                    at_idx = after_scheme.rfind('@')
                    if at_idx < 0:
                        continue
                    auth_part = after_scheme[:at_idx]
                    colon_idx = auth_part.find(':')
                    if colon_idx < 0:
                        continue
                    username = auth_part[:colon_idx]
                    password = auth_part[colon_idx + 1:]

                credentials = f"{username}:{password}"
                encoded = base64.b64encode(credentials.encode('utf-8')).decode('ascii')
                logger.debug(f"Extracted proxy auth for user '{username}'")
                return f"Basic {encoded}"
            except Exception as e:
                logger.warning(f"Failed to extract proxy auth: {e}")
                continue
        return None

    def get_quote(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> KyberQuote:
        """
        Получить котировку свапа.

        GET /{chain}/api/v1/routes?tokenIn=X&tokenOut=Y&amountIn=Z

        Args:
            token_in: Адрес входного токена
            token_out: Адрес выходного токена
            amount_in: Сумма входного токена в wei

        Returns:
            KyberQuote с котировкой и routeSummary

        Raises:
            KyberSwapNoRouteError: Маршрут не найден
            KyberSwapAPIError: Ошибка API
            KyberSwapTimeoutError: Таймаут
        """
        token_in = Web3.to_checksum_address(token_in)
        token_out = Web3.to_checksum_address(token_out)

        url = f"{KYBER_BASE_URL}/{self.chain_slug}/api/v1/routes"
        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "amountIn": str(amount_in),
        }

        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.Timeout:
            raise KyberSwapTimeoutError(f"Timeout getting quote ({self.timeout}s)")
        except requests.exceptions.RequestException as e:
            raise KyberSwapError(f"Request failed: {e}")

        if resp.status_code != 200:
            raise KyberSwapAPIError(resp.status_code, resp.text[:500])

        try:
            body = resp.json()
        except ValueError:
            raise KyberSwapAPIError(resp.status_code, "Invalid JSON response")

        # Проверить код ответа
        if body.get("code") != 0:
            msg = body.get("message", "Unknown error")
            raise KyberSwapNoRouteError(f"No route: {msg}")

        data = body.get("data")
        if not data:
            raise KyberSwapNoRouteError("Empty response data")

        route_summary = data.get("routeSummary")
        if not route_summary:
            raise KyberSwapNoRouteError("No routeSummary in response")

        router_address = data.get("routerAddress", "")
        if not router_address:
            raise KyberSwapError("No routerAddress in response")

        # Валидация роутера по whitelist
        self._validate_router(router_address)

        router_address = Web3.to_checksum_address(router_address)
        amount_out = int(route_summary.get("amountOut", "0"))
        gas_usd = str(route_summary.get("gasUsd", "0"))

        # Price impact (может быть в разных форматах)
        price_impact = 0.0
        extra_fee = route_summary.get("extraFee", {})
        if "priceImpact" in extra_fee:
            try:
                price_impact = float(extra_fee["priceImpact"])
            except (ValueError, TypeError):
                pass

        # Описание маршрута
        route_description = self._build_route_description(route_summary)

        # Человекочитаемый amount_out (предварительный — без decimals)
        amount_out_human = 0.0

        return KyberQuote(
            amount_in=amount_in,
            amount_out=amount_out,
            amount_out_human=amount_out_human,
            gas_usd=gas_usd,
            route_summary=route_summary,
            router_address=router_address,
            price_impact=price_impact,
            route_description=route_description,
        )

    def build_route(
        self,
        route_summary: dict,
        sender: str,
        recipient: str,
        slippage_bips: int = 50,
    ) -> KyberBuildResult:
        """
        Получить encoded calldata для свапа.

        POST /{chain}/api/v1/route/build

        Args:
            route_summary: routeSummary из get_quote()
            sender: Адрес отправителя
            recipient: Адрес получателя (обычно == sender)
            slippage_bips: Slippage в basis points (50 = 0.5%)

        Returns:
            KyberBuildResult с calldata для TX

        Raises:
            KyberSwapAPIError: Ошибка API
        """
        sender = Web3.to_checksum_address(sender)
        recipient = Web3.to_checksum_address(recipient)

        url = f"{KYBER_BASE_URL}/{self.chain_slug}/api/v1/route/build"
        payload = {
            "routeSummary": route_summary,
            "sender": sender,
            "recipient": recipient,
            "slippageTolerance": slippage_bips,
        }

        try:
            resp = self.session.post(
                url, json=payload, timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        except requests.exceptions.Timeout:
            raise KyberSwapTimeoutError(f"Timeout building route ({self.timeout}s)")
        except requests.exceptions.RequestException as e:
            raise KyberSwapError(f"Request failed: {e}")

        if resp.status_code != 200:
            raise KyberSwapAPIError(resp.status_code, resp.text[:500])

        try:
            body = resp.json()
        except ValueError:
            raise KyberSwapAPIError(resp.status_code, "Invalid JSON response")

        if body.get("code") != 0:
            msg = body.get("message", "Unknown error")
            raise KyberSwapAPIError(resp.status_code, msg)

        data = body.get("data")
        if not data:
            raise KyberSwapError("Empty build response data")

        encoded_data = data.get("data", "")
        if not encoded_data:
            raise KyberSwapError("No encoded data in build response")

        router_address = data.get("routerAddress", "")
        if router_address:
            self._validate_router(router_address)
            router_address = Web3.to_checksum_address(router_address)

        gas_estimate = 0
        try:
            gas_estimate = int(data.get("gas", "0"))
        except (ValueError, TypeError):
            pass

        return KyberBuildResult(
            router_address=router_address,
            encoded_data=encoded_data,
            gas_estimate=gas_estimate,
        )

    def _validate_router(self, router_address: str):
        """Проверить что адрес роутера в whitelist."""
        if router_address.lower() not in KYBER_KNOWN_ROUTERS:
            raise KyberSwapError(
                f"Unknown KyberSwap router: {router_address}. "
                f"Expected one of: {KYBER_KNOWN_ROUTERS}"
            )

    def _build_route_description(self, route_summary: dict) -> str:
        """Построить описание маршрута из routeSummary."""
        try:
            route = route_summary.get("route", [])
            if not route:
                return "KyberSwap"

            dex_names = set()
            for path in route:
                if isinstance(path, list):
                    for step in path:
                        pool = step.get("pool", {}) if isinstance(step, dict) else {}
                        exchange = pool.get("exchange", "")
                        if exchange:
                            dex_names.add(exchange)
                elif isinstance(path, dict):
                    pool = path.get("pool", {})
                    exchange = pool.get("exchange", "")
                    if exchange:
                        dex_names.add(exchange)

            token_in = route_summary.get("tokenIn", "?")[-6:]
            token_out = route_summary.get("tokenOut", "?")[-6:]
            dex_str = ", ".join(sorted(dex_names)) if dex_names else "auto"

            return f"KyberSwap ({dex_str})"
        except Exception:
            return "KyberSwap"
