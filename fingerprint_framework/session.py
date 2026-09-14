from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
import asyncio
import logging
import json

from .proxy import WebshareProxyClient, Proxy, ProxyError, ProxyNotFoundError, ProxyAuthenticationError
from .browser import StealthBrowser, BrowserError, NavigationError
from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class FingerprintResult:
    url: str
    timestamp: datetime
    proxy: Optional[Proxy] = None
    success: bool = False
    errors: List[str] = field(default_factory=list)
    fingerprint_data: Dict[str, Any] = field(default_factory=dict)
    screenshots: List[str] = field(default_factory=list)
    console_logs: List[str] = field(default_factory=list)
    network_requests: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "timestamp": self.timestamp.isoformat(),
            "proxy": {
                "host": self.proxy.host,
                "port": self.proxy.port,
                "country_code": self.proxy.country_code,
                "isp": self.proxy.isp,
            } if self.proxy else None,
            "success": self.success,
            "errors": self.errors,
            "fingerprint_data": self.fingerprint_data,
            "screenshots": self.screenshots,
            "console_logs": self.console_logs,
            "network_requests": self.network_requests,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class SessionManager:
    def __init__(
        self,
        webshare_api_key: Optional[str] = None,
        headless: bool = settings.headless,
        browser_timeout: int = settings.browser_timeout,
        navigation_timeout: int = settings.navigation_timeout,
        test_url: str = settings.fingerprint_test_url,
    ):
        self.webshare_api_key = webshare_api_key or settings.webshare_api_key
        self.headless = headless
        self.browser_timeout = browser_timeout
        self.navigation_timeout = navigation_timeout
        self.test_url = test_url
        self._proxy_client: Optional[WebshareProxyClient] = None

    async def __aenter__(self) -> "SessionManager":
        self._proxy_client = WebshareProxyClient(api_key=self.webshare_api_key)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._proxy_client:
            await self._proxy_client.close()

    async def get_proxy(self, country_code: Optional[str] = None) -> Proxy:
        if not self._proxy_client:
            self._proxy_client = WebshareProxyClient(api_key=self.webshare_api_key)

        logger.info("Fetching residential proxy from Webshare...")
        proxy = await self._proxy_client.get_random_proxy(country_code=country_code)

        logger.info(f"Testing proxy: {proxy}")
        if not await self._proxy_client.test_proxy(proxy):
            raise ProxyError(f"Proxy test failed: {proxy}")

        logger.info(f"Proxy verified: {proxy.host}:{proxy.port} ({proxy.country_code})")
        return proxy

    async def run_fingerprint_test(
        self,
        proxy: Optional[Proxy] = None,
        country_code: Optional[str] = None,
        capture_screenshot: bool = True,
        extract_fingerprint: bool = True,
    ) -> FingerprintResult:
        result = FingerprintResult(
            url=self.test_url,
            timestamp=datetime.utcnow(),
            proxy=proxy,
        )

        if not proxy:
            try:
                proxy = await self.get_proxy(country_code=country_code)
                result.proxy = proxy
            except (ProxyNotFoundError, ProxyAuthenticationError, ProxyError) as e:
                result.errors.append(f"Proxy acquisition failed: {e}")
                logger.error(f"Proxy acquisition failed: {e}")
                return result

        browser = StealthBrowser(
            headless=self.headless,
            timeout=self.browser_timeout,
            navigation_timeout=self.navigation_timeout,
        )

        try:
            await browser.launch(proxy=proxy)
            page = await browser.create_stealth_page()

            page.on("console", lambda msg: result.console_logs.append(f"[{msg.type}] {msg.text}"))
            page.on("request", lambda req: result.network_requests.append({
                "url": req.url,
                "method": req.method,
                "headers": req.headers,
            }))
            page.on("response", lambda resp: result.network_requests[-1].update({
                "status": resp.status,
                "status_text": resp.status_text,
            }) if result.network_requests else None)

            await browser.navigate(page, self.test_url)

            if capture_screenshot:
                screenshot_path = f"fingerprint_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                result.screenshots.append(screenshot_path)
                logger.info(f"Screenshot saved: {screenshot_path}")

            if extract_fingerprint:
                fingerprint_data = await self._extract_fingerprint(page)
                result.fingerprint_data = fingerprint_data

            result.success = True
            logger.info("Fingerprint test completed successfully")

        except (BrowserError, NavigationError, Exception) as e:
            error_msg = f"Session error: {e}"
            result.errors.append(error_msg)
            logger.error(error_msg)

        finally:
            await browser.close()

        return result

    async def _extract_fingerprint(self, page) -> Dict[str, Any]:
        fingerprint_script = """
        () => {
            const fp = {};
            
            fp.userAgent = navigator.userAgent;
            fp.platform = navigator.platform;
            fp.languages = navigator.languages;
            fp.hardwareConcurrency = navigator.hardwareConcurrency;
            fp.deviceMemory = navigator.deviceMemory;
            fp.maxTouchPoints = navigator.maxTouchPoints;
            fp.cookieEnabled = navigator.cookieEnabled;
            fp.doNotTrack = navigator.doNotTrack;
            fp.webdriver = navigator.webdriver;
            
            fp.screenWidth = screen.width;
            fp.screenHeight = screen.height;
            fp.colorDepth = screen.colorDepth;
            fp.pixelDepth = screen.pixelDepth;
            fp.availWidth = screen.availWidth;
            fp.availHeight = screen.availHeight;
            
            fp.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
            fp.timezoneOffset = new Date().getTimezoneOffset();
            
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillText('Random text for fingerprinting', 2, 2);
            fp.canvasHash = canvas.toDataURL().slice(-50);
            
            try {
                const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                if (gl) {
                    fp.webglVendor = gl.getParameter(gl.VENDOR);
                    fp.webglRenderer = gl.getParameter(gl.RENDERER);
                    fp.webglVersion = gl.getParameter(gl.VERSION);
                    fp.webglShadingLanguage = gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
                }
            } catch (e) {
                fp.webglError = e.message;
            }
            
            fp.plugins = Array.from(navigator.plugins).map(p => ({
                name: p.name,
                description: p.description,
                filename: p.filename,
            }));
            
            fp.mimeTypes = Array.from(navigator.mimeTypes).map(m => ({
                type: m.type,
                description: m.description,
                suffixes: m.suffixes,
            }));
            
            return fp;
        }
        """

        try:
            fingerprint_data = await page.evaluate(fingerprint_script)
            return fingerprint_data
        except Exception as e:
            logger.warning(f"Fingerprint extraction failed: {e}")
            return {"error": str(e)}

    async def run_multiple_tests(
        self,
        count: int = 5,
        country_code: Optional[str] = None,
        delay_between: float = 2.0,
    ) -> List[FingerprintResult]:
        results = []

        for i in range(count):
            logger.info(f"Running test {i + 1}/{count}")
            result = await self.run_fingerprint_test(country_code=country_code)
            results.append(result)

            if i < count - 1:
                await asyncio.sleep(delay_between)

        return results