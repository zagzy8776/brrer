#!/usr/bin/env python3
"""
Main entry point for the Fingerprint Testing Framework.

Usage:
    python -m fingerprint_framework [--proxy-country US] [--headless] [--count 5]
"""

from datetime import datetime
import asyncio
import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from fingerprint_framework import (
    SessionManager,
    FingerprintResult,
    settings,
)


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("fingerprint_test.log"),
        ],
    )


async def run_single_test(
    country_code: Optional[str] = None,
    headless: bool = True,
    capture_screenshot: bool = True,
) -> FingerprintResult:
    async with SessionManager(headless=headless) as manager:
        return await manager.run_fingerprint_test(
            country_code=country_code,
            capture_screenshot=capture_screenshot,
        )


async def run_multiple_tests(
    count: int = 5,
    country_code: Optional[str] = None,
    headless: bool = True,
    delay: float = 2.0,
) -> list[FingerprintResult]:
    async with SessionManager(headless=headless) as manager:
        return await manager.run_multiple_tests(
            count=count,
            country_code=country_code,
            delay_between=delay,
        )


def save_results(results: list[FingerprintResult], output_dir: Path = Path("results")):
    output_dir.mkdir(exist_ok=True)
    timestamp = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0
    
    for i, result in enumerate(results):
        filepath = output_dir / f"fingerprint_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{i}.json"
        filepath.write_text(result.to_json())
        logging.info(f"Result saved: {filepath}")


def print_summary(results: list[FingerprintResult]):
    successful = sum(1 for r in results if r.success)
    total = len(results)
    
    print(f"\n{'='*60}")
    print(f"FINGERPRINT TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Total tests: {total}")
    print(f"Successful:  {successful}")
    print(f"Failed:      {total - successful}")
    print(f"Success rate: {successful/total*100:.1f}%")
    print(f"{'='*60}\n")
    
    for i, result in enumerate(results):
        status = "PASS" if result.success else "FAIL"
        proxy_info = f"{result.proxy.host}:{result.proxy.port}" if result.proxy else "Direct"
        print(f"  [{i+1}] {status} | {proxy_info} | {result.url}")
        if result.errors:
            for err in result.errors:
                print(f"      ERROR: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Automated Browser Fingerprint Testing Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--country", "-c",
        type=str,
        help="Proxy country code (e.g., US, GB, DE)",
        default=None,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode (default: True)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible)",
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=1,
        help="Number of tests to run (default: 1)",
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=2.0,
        help="Delay between tests in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--no-screenshot",
        action="store_true",
        help="Disable screenshot capture",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="results",
        help="Output directory for results (default: results)",
    )
    
    args = parser.parse_args()
    
    headless = not args.headed if args.headed else args.headless
    setup_logging(args.log_level)
    
    logger = logging.getLogger(__name__)
    logger.info(f"Starting fingerprint test: count={args.count}, headless={headless}, country={args.country}")
    
    try:
        if args.count == 1:
            result = asyncio.run(run_single_test(
                country_code=args.country,
                headless=headless,
                capture_screenshot=not args.no_screenshot,
            ))
            results = [result]
        else:
            results = asyncio.run(run_multiple_tests(
                count=args.count,
                country_code=args.country,
                headless=headless,
                delay=args.delay,
            ))
        
        print_summary(results)
        
        if args.output:
            save_results(results, Path(args.output))
        
        sys.exit(0 if all(r.success for r in results) else 1)
        
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)