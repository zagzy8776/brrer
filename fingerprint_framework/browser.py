from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from playwright_stealth import Stealth
from typing import Optional, Dict, Any
import asyncio
import logging

from .config import settings
from .proxy import Proxy, ProxyError

logger = logging.getLogger(__name__)


class BrowserError(Exception):
    pass


class BrowserLaunchError(BrowserError):
    pass


class NavigationError(BrowserError):
    pass


class StealthBrowser:
    def __init__(
        self,
        headless: bool = settings.headless,
        timeout: int = settings.browser_timeout,
        navigation_timeout: int = settings.navigation_timeout,
        viewport: Optional[Dict[str, int]] = None,
        user_agent: Optional[str] = None,
        locale: Optional[str] = None,
        timezone_id: Optional[str] = None,
    ):
        self.headless = headless
        self.timeout = timeout
        self.navigation_timeout = navigation_timeout
        self.viewport = viewport or {"width": settings.viewport_width, "height": settings.viewport_height}
        self.user_agent = user_agent or settings.user_agent
        self.locale = locale or settings.locale
        self.timezone_id = timezone_id or settings.timezone_id

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        
        self._stealth = Stealth(
            navigator_user_agent_override=self.user_agent,
            navigator_platform_override="Win32",
            navigator_languages_override=("en-US", "en"),
            navigator_hardware_concurrency=8,
            webgl_vendor_override="Intel Inc.",
            webgl_renderer_override="Intel(R) Iris(R) Xe Graphics",
        )

    async def __aenter__(self) -> "StealthBrowser":
        await self.launch()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def launch(self, proxy: Optional[Proxy] = None) -> Browser:
        try:
            self._playwright = await async_playwright().start()

            launch_options = {
                "headless": self.headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-site-isolation-trials",
                    "--enable-unsafe-swiftshader",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-ipc-flooding-protection",
                ],
            }

            if proxy:
                launch_options["proxy"] = proxy.playwright_proxy

            self._browser = await self._playwright.chromium.launch(**launch_options)

            logger.info(f"Browser launched successfully (headless={self.headless})")
            return self._browser

        except Exception as e:
            logger.error(f"Failed to launch browser: {e}")
            await self.close()
            raise BrowserLaunchError(f"Browser launch failed: {e}") from e

    async def create_context(self, **overrides) -> BrowserContext:
        if not self._browser:
            raise BrowserLaunchError("Browser not launched")

        context_options = {
            "viewport": self.viewport,
            "user_agent": self.user_agent,
            "locale": self.locale,
            "timezone_id": self.timezone_id,
            "device_scale_factor": 1,
            "has_touch": False,
            "is_mobile": False,
            "java_script_enabled": True,
            "bypass_csp": True,
            "ignore_https_errors": True,
            "permissions": ["geolocation"],
            "geolocation": {"latitude": 40.7128, "longitude": -74.0060},
            "color_scheme": "light",
            "reduced_motion": "no-preference",
            "forced_colors": "none",
        }

        context_options.update(overrides)

        self._context = await self._browser.new_context(**context_options)
        self._context.set_default_timeout(self.timeout)
        self._context.set_default_navigation_timeout(self.navigation_timeout)

        return self._context

    async def create_stealth_page(self, **context_overrides) -> Page:
        if not self._context:
            await self.create_context(**context_overrides)

        page = await self._context.new_page()

        await self._stealth.apply_stealth_async(page)

        await self._apply_additional_stealth(page)

        logger.info("Stealth page created with fingerprint overrides")
        return page

    async def _apply_additional_stealth(self, page: Page):
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: 'Portable Document Format' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin', description: 'Native Client Executable' },
                ],
            });
            
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
            
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32',
            });
            
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8,
            });
            
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8,
            });
            
            Object.defineProperty(screen, 'colorDepth', {
                get: () => 24,
            });
            
            Object.defineProperty(screen, 'pixelDepth', {
                get: () => 24,
            });
            
            const originalGetContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, ...args) {
                if (type === '2d') {
                    const context = originalGetContext.apply(this, [type, ...args]);
                    const originalFillText = context.fillText;
                    context.fillText = function(...args) {
                        args[0] = args[0].replace('Random text for fingerprinting', 'Random text for fingerprinting');
                        return originalFillText.apply(this, args);
                    };
                    return context;
                }
                return originalGetContext.apply(this, [type, ...args]);
            };
            
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {
                if (type === 'image/png') {
                    return originalToDataURL.apply(this, [type, ...args]);
                }
                return originalToDataURL.apply(this, [type, ...args]);
            };
            
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel(R) Iris(R) Xe Graphics';
                if (parameter === 7937) return 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
                if (parameter === 35660) return 'WebGL 2.0 (OpenGL ES 3.0 Chromium)';
                return getParameter.apply(this, [parameter]);
            };
            
            const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel(R) Iris(R) Xe Graphics';
                if (parameter === 7937) return 'WebGL 1.0 (OpenGL ES 2.0 Chromium)';
                if (parameter === 35660) return 'WebGL 2.0 (OpenGL ES 3.0 Chromium)';
                return getParameter2.apply(this, [parameter]);
            };
        """)

    async def navigate(
        self,
        page: Page,
        url: str,
        wait_until: str = "networkidle",
        timeout: Optional[int] = None,
    ) -> Page:
        try:
            response = await page.goto(
                url,
                wait_until=wait_until,
                timeout=timeout or self.navigation_timeout,
            )
            if not response:
                raise NavigationError(f"No response from {url}")
            if response.status >= 400:
                raise NavigationError(f"HTTP {response.status} from {url}")

            logger.info(f"Navigated to {url} (status: {response.status})")
            return page

        except Exception as e:
            logger.error(f"Navigation failed to {url}: {e}")
            raise NavigationError(f"Navigation failed: {e}") from e

    async def close(self):
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser closed")

    @property
    def browser(self) -> Optional[Browser]:
        return self._browser

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()