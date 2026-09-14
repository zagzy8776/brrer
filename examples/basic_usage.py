"""
Example usage of the Fingerprint Testing Framework.

This demonstrates how to use the framework programmatically.
"""

import asyncio
import logging

from fingerprint_framework import SessionManager, FingerprintResult, Proxy


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)


async def example_single_test():
    """Run a single fingerprint test with automatic proxy rotation."""
    print("=" * 60)
    print("Example: Single Fingerprint Test")
    print("=" * 60)

    async with SessionManager(headless=True) as manager:
        result: FingerprintResult = await manager.run_fingerprint_test(
            country_code="US",
            capture_screenshot=True,
            extract_fingerprint=True,
        )

        print(f"Success: {result.success}")
        print(f"Proxy: {result.proxy.host if result.proxy else 'Direct'}")
        print(f"URL: {result.url}")
        
        if result.errors:
            for err in result.errors:
                print(f"Error: {err}")

        if result.fingerprint_data:
            print(f"Fingerprint keys: {list(result.fingerprint_data.keys())}")

        if result.screenshots:
            print(f"Screenshots: {result.screenshots}")

        return result


async def example_multiple_tests():
    """Run multiple fingerprint tests with different proxies."""
    print("\n" + "=" * 60)
    print("Example: Multiple Fingerprint Tests")
    print("=" * 60)

    async with SessionManager(headless=True) as manager:
        results = await manager.run_multiple_tests(
            count=3,
            country_code="US",
            delay_between=3.0,
        )

        for i, result in enumerate(results):
            status = "PASS" if result.success else "FAIL"
            proxy_info = f"{result.proxy.host}:{result.proxy.port}" if result.proxy else "Direct"
            print(f"  [{i+1}] {status} | {proxy_info}")

        return results


async def example_custom_proxy():
    """Run test with a pre-configured proxy."""
    print("\n" + "=" * 60)
    print("Example: Custom Proxy Configuration")
    print("=" * 60)

    # Manually create a proxy (e.g., from your own proxy pool)
    custom_proxy = Proxy(
        host="your-proxy-host.com",
        port=8080,
        username="proxy-user",
        password="proxy-pass",
        country_code="US",
        isp="Custom ISP",
    )

    async with SessionManager(headless=True) as manager:
        result = await manager.run_fingerprint_test(
            proxy=custom_proxy,
            capture_screenshot=True,
        )

        print(f"Success: {result.success}")
        return result


async def example_stealth_browser_direct():
    """Use StealthBrowser directly for custom workflows."""
    print("\n" + "=" * 60)
    print("Example: Direct StealthBrowser Usage")
    print("=" * 60)

    from fingerprint_framework import StealthBrowser

    async with StealthBrowser(headless=True) as browser:
        await browser.launch()
        page = await browser.create_stealth_page()
        
        # Navigate to any site
        await browser.navigate(page, "https://example.com")
        
        # Extract custom data
        title = await page.title()
        print(f"Page title: {title}")
        
        # Take screenshot
        await page.screenshot(path="example.png", full_page=True)
        print("Screenshot saved: example.png")

        return title


async def main():
    """Run all examples."""
    try:
        await example_single_test()
        await example_multiple_tests()
        # await example_custom_proxy()  # Requires valid proxy
        await example_stealth_browser_direct()
    except Exception as e:
        logging.exception(f"Example failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())