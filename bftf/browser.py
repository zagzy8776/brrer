"""Playwright browser control with stealth and human behavior simulation."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page
from playwright_stealth import Stealth

from .models import BrowserFingerprint, HumanBehaviorConfig, ProxyConfig

logger = logging.getLogger(__name__)

# Launch flags that reduce obvious automation signals (Chromium).
# HTTP/2 and QUIC are disabled because they can break proxy tunneling with
# HTTP CONNECT proxies (e.g. Webshare) -- HTTP/1.1 is reliable through tunnels.
_STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-http2",
    "--disable-quic",
    "--ignore-certificate-errors",
]


class BrowserController:
    """Controls Playwright browser automation with stealth and human behavior."""

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)
        self._playwright = None
        self._stealth = Stealth()
        self._browsers: set = set()

    @staticmethod
    def is_local_chrome() -> bool:
        """Whether to launch the user's local Chrome binary."""
        return os.environ.get("USE_LOCAL_CHROME", "").lower() in ("true", "1", "yes")

    @staticmethod
    def local_chrome_info() -> Dict[str, Any]:
        """Return info about the configured local Chrome path."""
        return {
            "enabled": BrowserController.is_local_chrome(),
            "chrome_path": os.environ.get("CHROME_PATH", ""),
        }

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def initialize_browser(
        self,
        fingerprint: BrowserFingerprint,
        proxy: Optional[ProxyConfig] = None,
        headless: bool = True,
    ) -> Browser:
        """Initialize a Playwright Chromium browser with stealth configuration.

        When ``USE_LOCAL_CHROME`` is set and ``CHROME_PATH`` points to a
        real Chrome binary, that binary is used via ``channel="chrome"``.
        """
        if self._playwright is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()

        launch_kwargs = dict(headless=headless, args=_STEALTH_LAUNCH_ARGS)
        if self.is_local_chrome():
            chrome_path = os.environ.get("CHROME_PATH", "")
            launch_kwargs["channel"] = "chrome"
            if chrome_path:
                launch_kwargs["executable_path"] = chrome_path
        if proxy is not None:
            if proxy.username and proxy.password:
                launch_kwargs["proxy"] = {
                    "server": f"http://{proxy.host}:{proxy.port}",
                    "username": proxy.username,
                    "password": proxy.password,
                }
            else:
                launch_kwargs["proxy"] = {"server": proxy.url}
        browser = await self._playwright.chromium.launch(**launch_kwargs)
        self._browsers.add(browser)
        logger.info(
            "Browser launched (headless=%s, proxy=%s, local_chrome=%s)",
            headless, proxy.key if proxy else "none", self.is_local_chrome(),
        )
        return browser

    async def create_context(self, browser: Browser, fingerprint: BrowserFingerprint) -> BrowserContext:
        """Create a browser context with the fingerprint applied."""
        is_mobile = fingerprint.max_touch_points > 0
        context = await browser.new_context(
            user_agent=fingerprint.user_agent,
            viewport=fingerprint.viewport,
            screen={"width": fingerprint.screen["width"], "height": fingerprint.screen["height"]},
            locale=fingerprint.languages[0],
            timezone_id=fingerprint.timezone,
            device_scale_factor=2 if is_mobile else 1,
            is_mobile=is_mobile,
            has_touch=is_mobile,
        )
        await self.inject_stealth_scripts(context)
        return context

    async def inject_stealth_scripts(self, context: BrowserContext) -> None:
        """Apply playwright-stealth evasions to the context (covers new pages)."""
        await self._stealth.apply_stealth_async(context)

    async def close_browser(self, browser: Browser) -> None:
        """Close the browser and clean up resources."""
        self._browsers.discard(browser)
        try:
            await browser.close()
        except Exception:  # pragma: no cover
            logger.debug("Browser already closed", exc_info=True)

    async def stop(self) -> None:
        """Close any stray browsers and stop the Playwright driver."""
        for browser in list(self._browsers):
            await self.close_browser(browser)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # pragma: no cover
                logger.debug("Playwright already stopped", exc_info=True)
            self._playwright = None

    # ------------------------------------------------------------------ #
    # Human behavior helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _think_time(behavior: HumanBehaviorConfig) -> float:
        """Random think-time in seconds."""
        lo, hi = behavior.think_time_range_sec
        return random.uniform(lo, hi)

    def _bezier_points(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        behavior: HumanBehaviorConfig,
    ) -> List[Tuple[float, float]]:
        """Sample a curved mouse path using a cubic Bezier with jittered control points."""
        x0, y0 = start
        x3, y3 = end
        distance = math.hypot(x3 - x0, y3 - y0)
        # Control points perpendicular to the travel direction, randomized.
        offset = max(20.0, min(distance * 0.35, 200.0))
        sign = self._rng.choice((-1.0, 1.0))
        mx, my = (x0 + x3) / 2, (y0 + y3) / 2
        dx, dy = x3 - x0, y3 - y0
        norm = math.hypot(dx, dy) or 1.0
        px, py = -dy / norm, dx / norm
        c1 = (mx + px * offset * sign + self._rng.gauss(0, 10), my + py * offset * sign + self._rng.gauss(0, 10))
        c2 = (mx + px * offset * sign * 0.5 + self._rng.gauss(0, 10), my + py * offset * sign * 0.5 + self._rng.gauss(0, 10))

        steps = max(12, int(distance / 10))
        points: List[Tuple[float, float]] = []
        for i in range(steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)  # ease-in-out
            u = 1 - t
            x = u**3 * x0 + 3 * u**2 * t * c1[0] + 3 * u * t**2 * c2[0] + t**3 * x3
            y = u**3 * y0 + 3 * u**2 * t * c1[1] + 3 * u * t**2 * c2[1] + t**3 * y3
            points.append((x, y))
        return points

    async def simulate_mouse_movement(
        self,
        page: Page,
        start: Tuple[int, int],
        end: Tuple[int, int],
        behavior: HumanBehaviorConfig,
    ) -> None:
        """Simulate realistic curved mouse movement between two points."""
        points = self._bezier_points(start, end, behavior)
        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        total_time = max(0.05, distance / behavior.mouse_speed_pixels_per_sec)
        step_delay = total_time / len(points)
        for x, y in points:
            await page.mouse.move(x, y, steps=1)
            await asyncio.sleep(step_delay)

    async def _move_to_element(self, page: Page, selector: str, behavior: HumanBehaviorConfig) -> Tuple[float, float]:
        """Move the mouse to a realistic randomized point within an element."""
        locator = page.locator(selector).first
        box = await locator.bounding_box()
        if box is None:
            raise RuntimeError(f"Element '{selector}' is not visible for mouse interaction")
        tx = box["x"] + box["width"] * (0.45 + self._rng.uniform(-0.08, 0.08))
        ty = box["y"] + box["height"] * (0.5 + self._rng.uniform(-0.1, 0.1))
        # Approximate the previous cursor position (upper-left region).
        await self.simulate_mouse_movement(page, (max(0, box["x"] - 40), max(0, box["y"] - 40)), (tx, ty), behavior)
        return tx, ty

    # ------------------------------------------------------------------ #
    # High-level human-like interactions
    # ------------------------------------------------------------------ #
    async def navigate_to_url(self, page: Page, url: str, wait_until: str = "load") -> float:
        """Navigate with human-like timing; returns load time in ms."""
        await asyncio.sleep(self._think_time(HumanBehaviorConfig()))
        started = asyncio.get_event_loop().time()
        await page.goto(url, wait_until=wait_until, timeout=60000)
        load_ms = (asyncio.get_event_loop().time() - started) * 1000.0
        logger.debug("Navigated to %s in %.0fms", url, load_ms)
        return load_ms

    async def click_element(
        self,
        page: Page,
        selector: str,
        behavior: HumanBehaviorConfig,
        use_human_behavior: bool = True,
    ) -> None:
        """Click an element with realistic mouse movement."""
        if use_human_behavior:
            tx, ty = await self._move_to_element(page, selector, behavior)
            await asyncio.sleep(self._rng.uniform(0.05, 0.18))
            await page.mouse.down()
            await asyncio.sleep(self._rng.uniform(0.03, 0.09))
            await page.mouse.up()
            _ = (tx, ty)
        else:
            await page.locator(selector).first.click()

    async def type_text(
        self,
        page: Page,
        selector: str,
        text: str,
        behavior: HumanBehaviorConfig,
        use_human_behavior: bool = True,
    ) -> None:
        """Type text with human-like speed, jitter, and occasional corrected typos."""
        locator = page.locator(selector).first
        if use_human_behavior:
            await self.click_element(page, selector, behavior)
        else:
            await locator.click()
        await locator.fill("")  # ensure a clean field

        # Mean delay per keystroke: 5 chars per word.
        base_ms = 60000.0 / (behavior.typing_speed_wpm * 5)
        await page.keyboard.type("", delay=0)
        for char in text:
            if use_human_behavior and self._rng.random() < behavior.error_rate:
                wrong = self._rng.choice("abcdefghijklmnopqrstuvwxyz")
                await page.keyboard.type(wrong, delay=base_ms)
                await asyncio.sleep(self._rng.uniform(0.15, 0.4))  # notice mistake
                await page.keyboard.press("Backspace")
                await asyncio.sleep(self._rng.uniform(0.05, 0.12))
            delay_ms = base_ms * self._rng.uniform(0.6, 1.6)
            await asyncio.sleep(delay_ms / 1000.0)
            await page.keyboard.type(char)

    async def scroll_page(
        self,
        page: Page,
        direction: str,
        behavior: HumanBehaviorConfig,
        amount_px: int = 600,
    ) -> None:
        """Scroll the page with human-like patterns (smooth or instant)."""
        sign = -1 if direction == "up" else 1
        if behavior.scroll_behavior == "instant":
            await page.mouse.wheel(0, sign * amount_px)
            await asyncio.sleep(self._rng.uniform(0.1, 0.3))
            return
        remaining = amount_px
        while remaining > 0:
            step = min(remaining, self._rng.randint(80, 200))
            await page.mouse.wheel(0, sign * step)
            remaining -= step
            await asyncio.sleep(self._rng.uniform(0.03, 0.12))
        await asyncio.sleep(self._rng.uniform(0.2, 0.6))
