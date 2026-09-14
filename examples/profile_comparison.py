"""Profile comparison suite across fingerprint profiles and latency profiles.

Requires a proxies.json file (see proxies.example.json). If it is missing the
suite falls back to direct connections.

Usage:
    python examples/profile_comparison.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bftf import (  # noqa: E402
    BrowserController,
    FingerprintManager,
    HumanBehaviorConfig,
    NetworkEmulator,
    ProxyManager,
    SessionManager,
    TestAction,
    TestConfiguration,
    TestOrchestrator,
    TestScenario,
)


def build_scenarios() -> list:
    """One navigation scenario per fingerprint/latency profile combination."""
    scenarios = []
    for i, profile in enumerate(["residential_windows", "residential_macos"]):
        for j, latency in enumerate(["residential_cable", "mobile_4g"]):
            scenarios.append(TestScenario(
                scenario_id=f"cmp_{i}_{j}",
                scenario_name=f"{profile} @ {latency}",
                target_url="https://example.com",
                fingerprint_profile=profile,
                proxy_criteria={"proxy_type": "residential"} if (path := Path("proxies.json")).exists() else {},
                latency_profile=latency,
                actions=[
                    TestAction(action_type="navigate", value="https://example.com", wait_after_ms=2000),
                    TestAction(action_type="scroll", value="down", wait_after_ms=800),
                ],
                validation_rules=[{"type": "url_contains", "value": "example.com"}],
            ))
    return scenarios


async def main() -> None:
    proxy_manager = None
    if Path("proxies.json").exists():
        proxy_manager = ProxyManager("proxies.json")

    config = TestConfiguration(
        test_suite_name="Profile Comparison Suite",
        target_urls=["https://example.com"],
        fingerprint_profiles=["residential_windows", "residential_macos"],
        proxy_criteria={"proxy_type": "residential"},
        latency_profiles=["residential_cable", "mobile_4g"],
        human_behavior_config=HumanBehaviorConfig(
            typing_speed_wpm=45,
            mouse_speed_pixels_per_sec=300,
            scroll_behavior="smooth",
            think_time_range_sec=(1.0, 3.0),
            error_rate=0.02,
        ),
        test_scenarios=build_scenarios(),
        max_concurrent_sessions=1,
        session_timeout_sec=300,
        retry_on_failure=True,
        max_retries=2,
        output_directory="./output/profile_comparison",
    )

    orchestrator = TestOrchestrator(
        fingerprint_manager=FingerprintManager(seed=2026),
        proxy_manager=proxy_manager,
        browser_controller=BrowserController(seed=2026),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager(config.output_directory),
    )

    suite_result = await orchestrator.execute_test_suite(config)
    print(suite_result.summary())
    for result in suite_result.test_results:
        print(f"  {result.scenario_id}: {'PASS' if result.success else 'FAIL'} "
              f"(detection={result.detection_score:.2f})")

    analysis = orchestrator.sessions.analyze_sessions()
    print("\nSession analysis:", analysis)
    await orchestrator.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
