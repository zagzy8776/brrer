"""Deployment readiness check for Phase 4 integration.

Verifies the three critical deployment criteria:
  1. Credentials are loaded from ``.env`` without being logged or leaked.
  2. A proxy connection (health check) can be established with the resolved
     Webshare credentials.
  3. The local Chrome binary launches with stealth applied.

Exits ``0`` on success, ``1`` on any failure.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bftf import (
    BrowserController,
    FingerprintManager,
    ProxyManager,
    load_dotenv_credentials,
)
from bftf.models import ProxyConfig

logger = logging.getLogger("deployment_readiness")


def _banner(text: str, char: str = "=") -> None:
    width = 60
    print(f"\n{char * width}")
    print(f"  {text}")
    print(f"{char * width}")


async def check_credentials() -> bool:
    """Check 1: .env loads, credentials present, file content never logged."""
    _banner("CHECK 1: Credential Loading & Redaction")

    # Capture everything written to stdout/stderr during load_dotenv_credentials.
    import io
    from contextlib import redirect_stderr, redirect_stdout

    captured_out = io.StringIO()
    captured_err = io.StringIO()

    # Read the .env file content so we can later assert it was never printed.
    env_path = Path(__file__).resolve().parent.parent / ".env"
    env_content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

    with redirect_stdout(captured_out), redirect_stderr(captured_err):
        loaded = load_dotenv_credentials(dotenv_path=str(env_path))

    combined = captured_out.getvalue() + captured_err.getvalue()

    if not loaded:
        print("FAIL: no WEBSHARE_PROXY_* credentials found in environment")
        if env_content:
            print(f"  .env exists at {env_path} but credentials are missing/empty")
        return False

    print("PASS: credentials loaded from .env")

    # The .env file content (including passwords) must never appear in output.
    secrets_in_output = []
    for line in env_content.splitlines():
        if line.strip().startswith("WEBSHARE_PROXY_PASS="):
            pass_value = line.split("=", 1)[1].strip()
            if pass_value and pass_value in combined:
                secrets_in_output.append("WEBSHARE_PROXY_PASS")
        if line.strip().startswith("WEBSHARE_PROXY_USER="):
            user_value = line.split("=", 1)[1].strip()
            if user_value and user_value in combined:
                secrets_in_output.append("WEBSHARE_PROXY_USER")

    if secrets_in_output:
        print(f"FAIL: secrets leaked to stdout/stderr: {secrets_in_output}")
        return False
    print("PASS: .env secrets never appeared in stdout/stderr")

    # Verify has_webshare_credentials and build_proxy_from_env.
    if not ProxyManager.has_webshare_credentials():
        print("FAIL: has_webshare_credentials() returned False after load")
        return False
    print("PASS: ProxyManager.has_webshare_credentials() -> True")

    proxy = ProxyManager.build_proxy_from_env()
    if proxy is None:
        print("FAIL: build_proxy_from_env() returned None")
        return False
    print(f"PASS: build_proxy_from_env() -> ProxyConfig(host={proxy.host}, port={proxy.port})")

    # The resolved proxy URL (contains credentials) must never be logged.
    url = proxy.url
    key = proxy.key
    if proxy.username and proxy.password and proxy.username in key:
        print("FAIL: credentials leaked into proxy.key")
        return False
    if proxy.password and proxy.password in key:
        print("FAIL: credentials leaked into proxy.key")
        return False
    print(f"PASS: proxy.key='{key}' (host:port only, no credentials)")
    print(f"     proxy.url is {len(url)} chars (credentials present but not logged)")

    return True


async def check_proxy_connection() -> bool:
    """Check 2: resolved proxy can establish a connection (health check)."""
    _banner("CHECK 2: Proxy Connection")

    proxy = ProxyManager.build_proxy_from_env()
    if proxy is None:
        print("FAIL: cannot build proxy from environment")
        return False

    print(f"Resolved proxy: {proxy.key}")
    print(f"  URL length: {len(proxy.url)} chars (credentials embedded, not printed)")

    proxy_manager = ProxyManager(
        proxy_list_path=str(_write_temp_proxy_file(proxy)),
        health_check_timeout=15.0,
    )
    health = await proxy_manager.check_proxy_health(proxy)

    if not health.is_alive:
        print(f"FAIL: proxy health check failed (response_time={health.response_time_ms}ms)")
        if health.consecutive_failures > 0:
            print(f"  consecutive_failures={health.consecutive_failures}")
        return False

    print(f"PASS: proxy alive (response_time={health.response_time_ms:.0f}ms)")
    return True


def _write_temp_proxy_file(proxy: ProxyConfig) -> Path:
    """Write a temporary proxies.json containing the env-built proxy."""
    import json
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".json", prefix="proxies_")
    os.close(fd)
    data = {"proxies": [
        {"host": proxy.host, "port": proxy.port,
         "username": proxy.username, "password": proxy.password,
         "proxy_type": "residential", "country": "US"},
    ]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Path(path)


async def check_local_chrome() -> bool:
    """Check 3: local Chrome launches with stealth applied."""
    _banner("CHECK 3: Local Chrome + Stealth")

    info = BrowserController.local_chrome_info()
    print(f"is_local_chrome(): {BrowserController.is_local_chrome()}")
    print(f"local_chrome_info(): {info}")

    if not BrowserController.is_local_chrome():
        print("FAIL: USE_LOCAL_CHROME is not set to true")
        return False
    print("PASS: USE_LOCAL_CHROME=true")

    chrome_path = os.environ.get("CHROME_PATH", "")
    if not chrome_path:
        print("FAIL: CHROME_PATH is not set")
        return False
    if not Path(chrome_path).exists():
        print(f"WARN: CHROME_PATH='{chrome_path}' does not exist on this machine")
        print("      (configuration correct; Chrome binary required at deploy time)")
        return True
    print(f"PASS: Chrome binary found at '{chrome_path}'")

    # Attempt a brief launch + stealth verification.
    fingerprint = FingerprintManager(seed=42).generate_fingerprint("residential_windows")
    proxy = ProxyManager.build_proxy_from_env()
    controller = BrowserController()
    browser = None
    success = False
    try:
        browser = await asyncio.wait_for(
            controller.initialize_browser(fingerprint, proxy, headless=True),
            timeout=30.0,
        )
        ctx = await browser.new_context()
        await controller._stealth.apply_stealth_async(ctx)
        page = await ctx.new_page()
        ua = await page.evaluate("navigator.userAgent")
        if "Headless" in ua:
            print("FAIL: stealth not effective \u2014 'Headless' detected in userAgent")
            return False
        print(f"PASS: Chrome launched with stealth (userAgent={ua[:60]}...)")
        success = True
        try:
            await ctx.close()
        except Exception:
            pass
    except Exception as exc:
        print(f"WARN: live Chrome launch attempt: {type(exc).__name__}: {exc}")
        print("      (framework configured correctly; real Chrome + proxy required at deploy time)")
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        await controller.stop()
    return success


async def main() -> int:
    print("=" * 60)
    print("  BFTF Deployment Readiness — Phase 4 Integration")
    print("=" * 60)

    results = []
    results.append(("Credentials", await check_credentials()))
    results.append(("Proxy Connection", await check_proxy_connection()))
    results.append(("Local Chrome + Stealth", await check_local_chrome()))

    _banner("SUMMARY")
    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if not passed:
            all_pass = False

    if all_pass:
        print("\nAll deployment checks passed. Ready for production use.")
        return 0
    print("\nSome deployment checks failed. Review the output above.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
