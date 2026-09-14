import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Optional
import logging

from .config import settings

logger = logging.getLogger(__name__)


class ProxyError(Exception):
    pass


class ProxyAuthenticationError(ProxyError):
    pass


class ProxyConnectionError(ProxyError):
    pass


class ProxyNotFoundError(ProxyError):
    pass


class Proxy:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        country_code: Optional[str] = None,
        isp: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.country_code = country_code
        self.isp = isp

    @property
    def proxy_url(self) -> str:
        return f"http://{self.username}:{self.password}@{self.host}:{self.port}"

    @property
    def playwright_proxy(self) -> dict:
        return {
            "server": f"http://{self.host}:{self.port}",
            "username": self.username,
            "password": self.password,
        }

    def __repr__(self) -> str:
        return f"Proxy(host={self.host}, port={self.port}, country={self.country_code})"


class WebshareProxyClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.api_key = api_key or settings.webshare_api_key
        self.base_url = base_url or settings.webshare_base_url
        self.timeout = timeout or settings.proxy_timeout

        if not self.api_key:
            raise ProxyAuthenticationError("Webshare API key not configured")

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Token {self.api_key}"},
            timeout=self.timeout,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    async def _request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(method, endpoint, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise ProxyAuthenticationError("Invalid Webshare API key") from e
            elif e.response.status_code == 404:
                raise ProxyNotFoundError("Proxy endpoint not found") from e
            raise ProxyConnectionError(f"HTTP error: {e.response.status_code}") from e
        except httpx.TimeoutException as e:
            logger.warning(f"Request timeout, retrying: {e}")
            raise
        except httpx.ConnectError as e:
            logger.warning(f"Connection error, retrying: {e}")
            raise

    async def list_proxies(
        self,
        page: int = 1,
        page_size: int = 25,
        country_code: Optional[str] = None,
        proxy_type: str = "residential",
    ) -> dict:
        params = {"page": page, "page_size": page_size, "mode": proxy_type}
        if country_code:
            params["country_code"] = country_code.upper()

        response = await self._request("GET", "/proxy/list/", params=params)
        return response.json()

    async def get_random_proxy(
        self,
        country_code: Optional[str] = None,
        proxy_type: str = "residential",
    ) -> Proxy:
        data = await self.list_proxies(country_code=country_code, proxy_type=proxy_type, page_size=100)

        results = data.get("results", [])
        if not results:
            raise ProxyNotFoundError(f"No {proxy_type} proxies available")

        import random
        proxy_data = random.choice(results)

        return Proxy(
            host=proxy_data["proxy_address"],
            port=proxy_data["ports"]["http"],
            username=proxy_data["username"],
            password=proxy_data["password"],
            country_code=proxy_data.get("country_code"),
            isp=proxy_data.get("isp"),
        )

    async def test_proxy(self, proxy: Proxy) -> bool:
        test_url = "http://httpbin.org/ip"
        proxy_url = proxy.proxy_url

        async with httpx.AsyncClient(proxies=proxy_url, timeout=10) as client:
            try:
                response = await client.get(test_url)
                response.raise_for_status()
                logger.info(f"Proxy test successful: {proxy.host}:{proxy.port}")
                return True
            except Exception as e:
                logger.warning(f"Proxy test failed: {proxy.host}:{proxy.port} - {e}")
                return False