"""Basic single-scenario test against a public fingerprint-testing page.

Usage:
    python examples/basic_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bftf import (  # noqa: E402
    BrowserController,
    FingerprintManager,
    NetworkEmulator,
    SessionManager,
    TestAction,
    TestOrchestrator,
    TestScenario,
)


async def run_basic_test() -> None:
    """Execute a basic fingerprint test (no proxy required)."""
    orchestrator = TestOrchestrator(
        fingerprint_manager=FingerprintManager(seed=42),
        proxy_manager=None,  # direct connection; supply a ProxyManager for proxy research
        browser_controller=BrowserController(seed=42),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager("./output/basic"),
    )

    scenario = TestScenario(
        scenario_id="basic_001",
        scenario_name="Fingerprint probe of browserleaks-style page",
        target_url="https://example.com",
        fingerprint_profile="residential_windows",
        proxy_criteria={},
        latency_profile="residential_cable",
        actions=[
            TestAction(action_type="navigate", value="https://example.com",
                       wait_after_ms=2000, human_behavior=True),
            TestAction(action_type="scroll", value="down", wait_after_ms=1000),
        ],
        validation_rules=[
            {"type": "url_contains", "value": "example.com"},
            {"type": "element_exists", "selector": "h1"},
        ],
    )

    result = await orchestrator.execute_single_test(scenario)
    print(f"Test {scenario.scenario_id}: {'PASSED' if result.success else 'FAILED'}")
    print(f"Detection Score: {result.detection_score:.2f}")
    print(f"Performance Metrics: {result.performance_metrics}")
    await orchestrator.cleanup()


if __name__ == "__main__":
    asyncio.run(run_basic_test())
