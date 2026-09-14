"""Test orchestration: coordinates all subsystems to run test scenarios."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import Page

from .browser import BrowserController
from .fingerprints import FingerprintManager
from .models import (
    HumanBehaviorConfig,
    LatencyProfile,
    ProxyConfig,
    SessionData,
    TestAction,
    TestConfiguration,
    TestResult,
    TestScenario,
    TestSuiteResult,
)
from .network import NetworkEmulator
from .proxies import ProxyManager
from .sessions import SessionManager

logger = logging.getLogger(__name__)

# Page content markers that commonly indicate anti-bot interdiction.
_BLOCK_INDICATORS = {
    "captcha": ("captcha", "recaptcha", "hcaptcha", "turnstile"),
    "unusual_traffic": ("unusual traffic", "are you a robot", "verify you are human"),
    "access_denied": ("access denied", "request blocked"),
    "blocked": ("forbidden",),
}


class TestOrchestrator:
    """Main controller for test execution."""

    __test__ = False  # not a pytest test class despite the name

    def __init__(
        self,
        fingerprint_manager: FingerprintManager,
        proxy_manager: Optional[ProxyManager],
        browser_controller: BrowserController,
        network_emulator: NetworkEmulator,
        session_manager: SessionManager,
    ) -> None:
        self.fingerprints = fingerprint_manager
        self.proxies = proxy_manager
        self.browser = browser_controller
        self.network = network_emulator
        self.sessions = session_manager
        self.behavior = HumanBehaviorConfig()

    async def cleanup(self) -> None:
        """Clean up all resources: close stray browsers and stop the driver."""
        await self.browser.stop()
        logger.debug("Orchestrator cleanup complete")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def execute_single_test(self, scenario: TestScenario) -> TestResult:
        """Execute a single test scenario end-to-end."""
        return await self._run_scenario(scenario)

    async def execute_test_suite(self, test_config: TestConfiguration) -> TestSuiteResult:
        """Execute a complete test suite with multiple scenarios."""
        self.behavior = test_config.human_behavior_config
        results: List[TestResult] = []
        scenarios = test_config.test_scenarios
        logger.info("Executing suite '%s' with %d scenarios", test_config.test_suite_name, len(scenarios))

        for scenario in scenarios:
            attempts = (1 + test_config.max_retries) if test_config.retry_on_failure else 1
            result: Optional[TestResult] = None
            for attempt in range(attempts):
                try:
                    result = await self._run_scenario(
                        scenario,
                        headless=test_config.headless,
                        output_dir=test_config.output_directory,
                    )
                    if result.success:
                        break
                    logger.warning(
                        "Scenario %s attempt %d/%d failed", scenario.scenario_id, attempt + 1, attempts
                    )
                except Exception as exc:
                    logger.error("Scenario %s attempt %d raised: %s", scenario.scenario_id, attempt + 1, exc)
            if result is None:
                result = self._failure_result(scenario, "all attempts raised exceptions")
            results.append(result)

        passed = sum(1 for r in results if r.success)
        return TestSuiteResult(
            test_suite_name=test_config.test_suite_name,
            total_tests=len(results),
            passed_tests=passed,
            failed_tests=len(results) - passed,
            test_results=results,
            aggregate_metrics=self._aggregate_metrics(results),
        )

    # ------------------------------------------------------------------ #
    # Scenario execution
    # ------------------------------------------------------------------ #
    async def _run_scenario(
        self,
        scenario: TestScenario,
        headless: bool = True,
        output_dir: Optional[str] = None,
    ) -> TestResult:
        """Run one scenario: fingerprint -> proxy -> browser -> actions -> report."""
        started = time.perf_counter()
        fingerprint = self.fingerprints.generate_fingerprint(profile=scenario.fingerprint_profile)
        proxy: Optional[ProxyConfig] = None
        if self.proxies is not None:
            proxy = await self.proxies.get_proxy(criteria=scenario.proxy_criteria)
        latency_profile: LatencyProfile = self.network.get_preset_profile(scenario.latency_profile)

        self.sessions.start_session(
            session_id=scenario.scenario_id,
            fingerprint=fingerprint,
            proxy=None,
            latency_profile=latency_profile,
            proxy_resolved=proxy,
        )

        browser = await self.browser.initialize_browser(fingerprint, proxy, headless=headless)
        success = False
        validation_results: List[Dict[str, Any]] = []
        screenshots: List[str] = []
        error_message: Optional[str] = None
        try:
            context = await self.browser.create_context(browser, fingerprint)
            page = await context.new_page()
            await self.network.apply_latency_profile(page, latency_profile)

            for action in scenario.actions:
                await self.execute_action(page, action, scenario.scenario_id)

            validation_results = await self.validate_outcome(page, scenario)
            success = all(v["passed"] for v in validation_results)

            screenshot_path = await self._take_screenshot(page, scenario.scenario_id, output_dir)
            if screenshot_path:
                screenshots = [screenshot_path]
                self.sessions.record_screenshot(scenario.scenario_id, screenshot_path)
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            self.sessions.record_error(scenario.scenario_id, error_message)
            logger.exception("Scenario %s failed", scenario.scenario_id)
        finally:
            await self.browser.close_browser(browser)
            session = self.sessions.end_session(scenario.scenario_id, success, error_message)

        result = TestResult(
            scenario_id=scenario.scenario_id,
            session_data=session,
            success=success,
            detection_score=self.sessions.calculate_detection_score(session),
            performance_metrics=self.sessions.calculate_performance_metrics(session),
            validation_results=validation_results,
            screenshots=screenshots,
            error_log=session.errors,
        )
        result.performance_metrics["total_overhead_sec"] = round(time.perf_counter() - started, 3)
        return result

    @staticmethod
    def _failure_result(scenario: TestScenario, message: str) -> TestResult:
        """Create a synthetic failure result when a scenario could not run."""
        session = SessionData(
            session_id=scenario.scenario_id,
            start_time=datetime.now(),
            end_time=datetime.now(),
            fingerprint=None,  # type: ignore[arg-type]
            success=False,
            error_message=message,
        )
        session.errors.append(message)
        return TestResult(
            scenario_id=scenario.scenario_id,
            session_data=session,
            success=False,
            detection_score=1.0,
            error_log=[message],
        )

    @staticmethod
    def _aggregate_metrics(results: List[TestResult]) -> Dict[str, float]:
        """Aggregate metrics across results for the suite report."""
        if not results:
            return {}
        scores = [r.detection_score for r in results]
        loads = [
            m["avg_load_time_ms"] for r in results
            for m in [r.performance_metrics] if m.get("avg_load_time_ms")
        ]
        return {
            "avg_detection_score": round(sum(scores) / len(scores), 4),
            "max_detection_score": max(scores),
            "avg_load_time_ms": round(sum(loads) / len(loads), 2) if loads else 0.0,
            "success_rate": round(sum(1 for r in results if r.success) / len(results), 4),
        }

    # ------------------------------------------------------------------ #
    # Action execution
    # ------------------------------------------------------------------ #
    async def execute_action(self, page: Page, action: TestAction, scenario_id: str) -> None:
        """Execute a single test action with human behavior simulation."""
        behavior = self.behavior
        started = time.perf_counter()
        human = action.human_behavior

        if action.action_type == "navigate":
            url = action.value or action.selector
            load_ms = await self.browser.navigate_to_url(page, url)
            self.sessions.record_interaction(scenario_id, "navigate", {
                "url": url, "load_time_ms": round(load_ms, 2),
            })
            await self._check_block_indicators(page, scenario_id)

        elif action.action_type == "click":
            await self.browser.click_element(page, action.selector, behavior, use_human_behavior=human)
            self.sessions.record_interaction(scenario_id, "click", {"selector": action.selector})

        elif action.action_type == "type":
            await self.browser.type_text(
                page, action.selector, action.value, behavior, use_human_behavior=human
            )
            self.sessions.record_interaction(scenario_id, "type", {
                "selector": action.selector, "length": len(action.value or ""),
            })

        elif action.action_type == "scroll":
            direction = action.value or "down"
            await self.browser.scroll_page(page, direction, behavior)
            self.sessions.record_interaction(scenario_id, "scroll", {"direction": direction})

        elif action.action_type == "wait":
            await asyncio.sleep((action.wait_after_ms or 1000) / 1000.0)
            self.sessions.record_interaction(scenario_id, "wait", {"ms": action.wait_after_ms})

        elif action.action_type == "screenshot":
            path = await self._take_screenshot(page, scenario_id, None)
            self.sessions.record_interaction(scenario_id, "screenshot", {"path": path})

        self.sessions.record_interaction(scenario_id, "timing", {
            "action": action.action_type,
            "action_time_ms": round((time.perf_counter() - started) * 1000.0, 2),
        })

        if action.wait_after_ms:
            await asyncio.sleep(action.wait_after_ms / 1000.0)

    # ------------------------------------------------------------------ #
    # Validation & detection heuristics
    # ------------------------------------------------------------------ #
    async def evaluate_rules(self, page: Page, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Evaluate a list of validation rule dicts against the current page."""
        results: List[Dict[str, Any]] = []
        for rule in rules:
            rule_type = rule.get("type")
            passed = False
            detail: Any = None
            try:
                if rule_type == "url_contains":
                    passed = rule["value"] in page.url
                elif rule_type == "url_equals":
                    passed = page.url == rule["value"]
                elif rule_type == "title_contains":
                    passed = rule["value"].lower() in (await page.title()).lower()
                elif rule_type == "element_exists":
                    passed = await page.locator(rule["selector"]).first.is_visible()
                elif rule_type == "element_not_exists":
                    passed = not await page.locator(rule["selector"]).first.is_visible()
                elif rule_type == "element_text_contains":
                    detail = await page.locator(rule["selector"]).first.inner_text()
                    passed = rule["value"].lower() in detail.lower()
                else:
                    logger.warning("Unknown validation rule type '%s'", rule_type)
            except Exception as exc:
                detail = str(exc)
            results.append({"rule": rule, "passed": bool(passed), "detail": detail})
        return results

    async def validate_outcome(self, page: Page, scenario: TestScenario) -> List[Dict[str, Any]]:
        """Evaluate the scenario's validation rules against the current page."""
        return await self.evaluate_rules(page, scenario.validation_rules)

    async def _check_block_indicators(self, page: Page, scenario_id: str) -> None:
        """Scan the page content for common anti-bot interdiction markers."""
        try:
            content = (await page.content()).lower()
        except Exception:  # pragma: no cover - page may be mid-navigation
            return
        for event_type, markers in _BLOCK_INDICATORS.items():
            hits = [m for m in markers if m in content]
            if hits:
                self.sessions.record_detection_event(scenario_id, event_type, {
                    "markers": hits,
                    "url": page.url,
                })

    async def _take_screenshot(
        self, page: Page, scenario_id: str, output_dir: Optional[str]
    ) -> Optional[str]:
        """Capture a screenshot of the current page state."""
        base = Path(output_dir) if output_dir else self.sessions.output_dir
        shots_dir = base / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        path = shots_dir / f"{scenario_id}_{int(time.time())}.png"
        try:
            await page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as exc:  # pragma: no cover
            logger.debug("Screenshot failed: %s", exc)
            return None
