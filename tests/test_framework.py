"""
Unit tests for the Fingerprint Testing Framework.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fingerprint_framework import (
    Proxy,
    WebshareProxyClient,
    StealthBrowser,
    SessionManager,
    FingerprintResult,
    settings,
    ProxyError,
    ProxyAuthenticationError,
    ProxyNotFoundError,
    BrowserLaunchError,
    NavigationError,
)


class TestProxy:
    def test_proxy_creation(self):
        proxy = Proxy(
            host="proxy.example.com",
            port=8080,
            username="user",
            password="pass",
            country_code="US",
            isp="Test ISP",
        )
        assert proxy.host == "proxy.example.com"
        assert proxy.port == 8080
        assert proxy.username == "user"
        assert proxy.password == "pass"
        assert proxy.country_code == "US"
        assert proxy.isp == "Test ISP"

    def test_proxy_url_property(self):
        proxy = Proxy(host="proxy.example.com", port=8080, username="user", password="pass")
        assert proxy.proxy_url == "http://user:pass@proxy.example.com:8080"

    def test_playwright_proxy_property(self):
        proxy = Proxy(host="proxy.example.com", port=8080, username="user", password="pass")
        pw_proxy = proxy.playwright_proxy
        assert pw_proxy["server"] == "http://proxy.example.com:8080"
        assert pw_proxy["username"] == "user"
        assert pw_proxy["password"] == "pass"


class TestSettings:
    def test_settings_defaults(self):
        assert settings.headless is True
        assert settings.browser_timeout == 60000
        assert settings.navigation_timeout == 30000
        assert settings.viewport_width == 1920
        assert settings.viewport_height == 1080
        assert "sannysoft.com" in settings.fingerprint_test_url


class TestFingerprintResult:
    def test_result_creation(self):
        result = FingerprintResult(
            url="https://example.com",
            timestamp="2024-01-01T00:00:00",
        )
        assert result.url == "https://example.com"
        assert result.success is False
        assert result.errors == []
        assert result.fingerprint_data == {}

    def test_result_to_dict(self):
        proxy = Proxy(host="proxy.com", port=8080, username="u", password="p")
        result = FingerprintResult(
            url="https://example.com",
            timestamp="2024-01-01T00:00:00",
            proxy=proxy,
            success=True,
        )
        data = result.to_dict()
        assert data["url"] == "https://example.com"
        assert data["success"] is True
        assert data["proxy"]["host"] == "proxy.com"

    def test_result_to_json(self):
        result = FingerprintResult(
            url="https://example.com",
            timestamp="2024-01-01T00:00:00",
        )
        json_str = result.to_json()
        assert "example.com" in json_str


class TestWebshareProxyClient:
    @pytest.mark.asyncio
    async def test_client_requires_api_key(self):
        with pytest.raises(ProxyAuthenticationError):
            WebshareProxyClient(api_key="")

    @pytest.mark.asyncio
    async def test_list_proxies_success(self, mock_httpx_response):
        client = WebshareProxyClient(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_httpx_response({
            "results": [{
                "proxy_address": "proxy.example.com",
                "ports": {"http": 8080},
                "username": "user",
                "password": "pass",
                "country_code": "US",
                "isp": "Test ISP",
            }]
        }))

        data = await client.list_proxies()
        assert "results" in data
        assert len(data["results"]) == 1

    @pytest.mark.asyncio
    async def test_get_random_proxy_no_results(self, mock_httpx_response):
        client = WebshareProxyClient(api_key="test-key")
        client._client = AsyncMock()
        client._client.request = AsyncMock(return_value=mock_httpx_response({"results": []}))

        with pytest.raises(ProxyNotFoundError):
            await client.get_random_proxy()


class TestStealthBrowser:
    @pytest.mark.asyncio
    async def test_browser_initialization(self):
        browser = StealthBrowser(headless=True, timeout=30000)
        assert browser.headless is True
        assert browser.timeout == 30000
        assert browser._browser is None

    @pytest.mark.asyncio
    async def test_browser_launch_failure(self):
        browser = StealthBrowser(headless=True)
        with patch("fingerprint_framework.browser.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.return_value.chromium.launch.side_effect = Exception("Launch failed")
            
            with pytest.raises(BrowserLaunchError):
                await browser.launch()


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_session_manager_initialization(self):
        manager = SessionManager(headless=True, webshare_api_key="test-key")
        assert manager.headless is True
        assert manager.webshare_api_key == "test-key"
        assert manager._proxy_client is None

    @pytest.mark.asyncio
    async def test_run_fingerprint_test_without_proxy(self):
        manager = SessionManager(headless=True, webshare_api_key="test-key")
        
        with patch.object(manager, "get_proxy", side_effect=ProxyNotFoundError("No proxies")):
            result = await manager.run_fingerprint_test()
            assert result.success is False
            assert len(result.errors) > 0
            assert "Proxy acquisition failed" in result.errors[0]


@pytest.fixture
def mock_httpx_response():
    def _make_response(json_data):
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status = MagicMock()
        mock_resp.status_code = 200
        return mock_resp
    return _make_response