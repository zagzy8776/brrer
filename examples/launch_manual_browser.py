"""Launch a live browser session through the Webshare proxy for manual interaction.

Usage:
    python examples/launch_manual_browser.py

Opens a non-headless Chrome window with a realistic fingerprint and stealth
applied. The page loads blank so you can navigate manually. Credentials are
never printed -- only the proxy key (host:port) appears in output.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bftf import (  # noqa: E402
    BrowserController,
    FingerprintManager,
    ProxyManager,
    load_dotenv_credentials,
)
from bftf.models import ProxyConfig  # noqa: E402


async def main() -> None:
    loaded = load_dotenv_credentials()
    if not loaded:
        print("ERROR: WEBSHARE_PROXY_* credentials not found in .env")
        sys.exit(1)

    proxy: ProxyConfig | None = ProxyManager.build_proxy_from_env()
    if proxy is None:
        print("ERROR: could not build proxy from environment")
        sys.exit(1)

    print(f"Launching browser through proxy: {proxy.key}")

    fingerprint = FingerprintManager(seed=42).generate_fingerprint("residential_windows")
    print(f"Fingerprint platform: {fingerprint.platform}")

    controller = BrowserController(seed=42)
    browser = await controller.initialize_browser(
        fingerprint, proxy, headless=False,
    )
    context = await browser.new_context(
        user_agent=fingerprint.user_agent,
        viewport=fingerprint.viewport,
        locale=fingerprint.languages[0],
    )
    await controller.inject_stealth_scripts(context)
    page = await context.new_page()

    ua = await page.evaluate("navigator.userAgent")
    print(f"User-Agent: {ua[:80]}...")
    print(f"Stealth effective: {'Headless' not in ua}")

    # Load a blank page -- navigate manually from here.
    await page.goto("about:blank")

    # Verify proxy by checking the IP.
    try:
        await page.goto("https://ipv4.webshare.io/", wait_until="domcontentloaded", timeout=20000)
        ip_text = await page.text_content("body")
        print(f"Proxy IP: {ip_text.strip()[:30]}")
        print("Navigate to https://accounts.google.com/signup in the browser window.")
    except Exception as exc:
        print(f"WARN: IP check failed: {type(exc).__name__}: {exc}")

    print("Browser is open. Close the Chrome window to exit.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await context.close()
        await controller.stop()


if __name__ == "__main__":
    asyncio.run(main())
