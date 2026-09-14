"""Tests for Phase 2 workflow orchestration (browser interactions faked)."""
import json

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bftf import (
    BranchRule,
    FingerprintManager,
    NetworkEmulator,
    SessionManager,
    TestAction,
    UserProfile,
    WorkflowOrchestrator,
    WorkflowSnapshot,
    WorkflowState,
    WorkflowStep,
    load_user_profiles,
)
from bftf.workflows import WorkflowStateStore, match_rule


# --------------------------------------------------------------------------- #
# Fake browser harness
# --------------------------------------------------------------------------- #
class FakeKeyboard:
    def __init__(self, page):
        self._page = page
        self.pressed: list = []

    async def press(self, key):
        self.pressed.append(key)


class FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        return 2

    async def click(self, timeout=None):
        self._page.clicks.append(self.selector)

    async def fill(self, text):
        self._page.fills.append((self.selector, text))


class FakePage:
    def __init__(self, content="<html>signup page</html>", url="https://target.test/signup"):
        self._content = content
        self.url = url
        self.reloads = 0
        self.gotos: list = []
        self.clicks: list = []
        self.fills: list = []
        self.keyboard = FakeKeyboard(self)

    async def title(self):
        return "Signup"

    async def content(self):
        return self._content

    async def reload(self, wait_until=None, timeout=None):
        self.reloads += 1

    async def goto(self, url, wait_until=None, timeout=None):
        self.gotos.append(url)

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakeContext:
    def __init__(self, page):
        self._page = page

    async def new_page(self):
        return self._page


class FakeBrowser:
    async def close(self):
        pass


class FakeMouse:
    async def down(self):
        pass

    async def up(self):
        pass

    async def move(self, x, y, steps=None):
        pass

    async def wheel(self, dx, dy):
        pass


class FakeController:
    def __init__(self, page):
        self.page = page
        self.mouse = FakeMouse()

    async def _bounding_box(self, selector):
        return {"x": 0.0, "y": 0.0, "width": 10.0, "height": 10.0}

    async def navigate_to_url(self, page, url, wait_until="load"):
        await page.goto(url, wait_until=wait_until)
        return 0.0

    async def click_element(self, page, selector, behavior, use_human_behavior=True):
        self.page.clicks.append(selector)

    async def type_text(self, page, selector, text, behavior, use_human_behavior=True):
        self.page.fills.append((selector, text))

    async def scroll_page(self, page, direction, behavior, amount_px=600):
        return None

    async def initialize_browser(self, fingerprint, proxy=None, headless=True):
        return FakeBrowser()

    async def create_context(self, browser, fingerprint):
        return FakeContext(self.page)

    async def close_browser(self, browser):
        pass

    async def stop(self):
        pass


class FakeNetwork:
    def __init__(self):
        self._real = NetworkEmulator()

    def get_preset_profile(self, name):
        return self._real.get_preset_profile(name)

    async def apply_latency_profile(self, page, profile):
        pass


def make_orchestrator(page, tmp_path) -> WorkflowOrchestrator:
    orch = WorkflowOrchestrator(
        fingerprint_manager=FingerprintManager(seed=5),
        proxy_manager=None,
        browser_controller=FakeController(page),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager(str(tmp_path / "out")),
    )
    orch.network = FakeNetwork()  # CDP latency emulation needs a real browser
    return orch


@pytest.fixture
def profile():
    return UserProfile(
        profile_id="u1", first_name="Ada", last_name="Lovelace",
        email="ada@example.test", username="ada_42", password="s3cret!",
        extra={"phone": "555-0100"},
    )


def wait_action(ms=0):
    return TestAction(action_type="wait", wait_after_ms=ms)


# --------------------------------------------------------------------------- #
# Pure logic tests
# --------------------------------------------------------------------------- #
def test_load_user_profiles(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": [
        {"profile_id": "u1", "first_name": "Ada", "email": "a@x.test",
         "username": "ada", "password": "pw", "extra": {"phone": "555"}},
    ]}), encoding="utf-8")
    profiles = load_user_profiles(str(path))
    assert len(profiles) == 1
    assert profiles[0].extra["phone"] == "555"
    assert profiles[0].to_dict(redact=True)["password"] == "***"


def test_load_user_profiles_rejects_missing_id(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"profiles": [{"first_name": "NoId"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_user_profiles(str(path))


def test_token_resolution(tmp_path, profile):
    orch = make_orchestrator(FakePage(), tmp_path)
    wf = orch.create_registration_workflow(
        "wf_tokens", [WorkflowStep("s1", "only", [wait_action()])], recovery_backoff_sec=0.01
    )
    wf.inject_user_profile(profile)
    assert wf._resolve("{{email}}") == profile.email
    assert wf._resolve("{{full_name}}") == "Ada Lovelace"
    assert wf._resolve("{{extra:phone}}") == "555-0100"
    assert wf._resolve("no tokens") == "no tokens"
    assert wf._resolve(None) is None
    with pytest.raises(ValueError):
        wf._resolve("{{unknown_field}}")


def test_resolve_requires_profile(tmp_path):
    orch = make_orchestrator(FakePage(), tmp_path)
    wf = orch.create_registration_workflow(
        "wf_np", [WorkflowStep("s1", "only", [wait_action()])], recovery_backoff_sec=0.01
    )
    with pytest.raises(ValueError):
        wf._resolve("{{username}}")


def test_injected_values_reach_actions(tmp_path, profile):
    orch = make_orchestrator(FakePage(), tmp_path)
    wf = orch.create_registration_workflow(
        "wf_inject", [WorkflowStep("s1", "only", [
            TestAction(action_type="type", selector="#email", value="{{email}}", wait_after_ms=0),
        ])], recovery_backoff_sec=0.01
    )
    wf.inject_user_profile(profile)
    resolved = wf._resolved_actions(wf.steps[0])
    assert resolved[0].value == "ada@example.test"
    # Original definitions stay untouched (idempotent re-resolution).
    assert wf.steps[0].actions[0].value == "{{email}}"


def test_step_validation(tmp_path):
    orch = make_orchestrator(FakePage(), tmp_path)
    with pytest.raises(ValueError):  # duplicate ids
        orch.create_registration_workflow("w", [
            WorkflowStep("s1", "a", [wait_action()]),
            WorkflowStep("s1", "b", [wait_action()]),
        ])
    with pytest.raises(ValueError):  # unknown dependency
        orch.create_registration_workflow("w", [
            WorkflowStep("s1", "a", [wait_action()], depends_on=["ghost"]),
        ])
    with pytest.raises(ValueError):  # forward dependency
        orch.create_registration_workflow("w", [
            WorkflowStep("s1", "a", [wait_action()], depends_on=["s2"]),
            WorkflowStep("s2", "b", [wait_action()]),
        ])


def test_snapshot_store_roundtrip(tmp_path):
    store = WorkflowStateStore(str(tmp_path))
    snap = WorkflowSnapshot("w1", "sess1", WorkflowState.HALTED,
                            {"s1": "completed", "s2": "pending"},
                            profile_id="u1", halted_reason="captcha")
    path = store.save(snap)
    loaded = store.load("w1")
    assert loaded.state == WorkflowState.HALTED
    assert loaded.step_statuses["s1"] == "completed"
    assert loaded.halted_reason == "captcha"
    assert "workflows" in path


def test_match_rule_pure():
    rule = BranchRule("r", "page_marker", "verification required", behavior="halt")
    assert match_rule(rule, url="https://x.test", title="T", content="please verification required now")
    assert not match_rule(rule, url="https://x.test", title="T", content="all normal")


# --------------------------------------------------------------------------- #
# State machine integration tests (fake browser)
# --------------------------------------------------------------------------- #
async def test_workflow_completion(tmp_path, profile):
    page = FakePage()
    orch = make_orchestrator(page, tmp_path)
    wf = orch.create_registration_workflow(
        "wf_ok", [
            WorkflowStep("step_a", "Enter data", [wait_action()]),
            WorkflowStep("step_b", "Submit", [wait_action()], depends_on=["step_a"]),
        ],
        recovery_backoff_sec=0.01,
    )
    result = await orch.execute_registration_workflow(wf, profile)
    assert result.success
    assert result.final_state == WorkflowState.COMPLETED
    assert result.completed_steps == ["step_a", "step_b"]
    # Session logging with fingerprint + proxy association
    session = orch.sessions.get_session(result.session_id)
    assert session.success is True
    assert session.fingerprint.platform == "Win32"
    assert any(i["type"] == "workflow_start" for i in session.interactions)
    assert any(i["type"] == "step_completed" for i in session.interactions)
    snapshot = wf.store.load("wf_ok")
    assert snapshot.state == WorkflowState.COMPLETED
    assert snapshot.profile_id == "u1"


async def test_halt_on_verification_page(tmp_path, profile):
    page = FakePage(content="<html>Verification required: check your email for a code</html>")
    orch = make_orchestrator(page, tmp_path)
    wf = orch.create_registration_workflow(
        "wf_verify", [
            WorkflowStep("step_a", "Enter data", [wait_action()]),
            WorkflowStep("step_b", "Submit", [wait_action()], depends_on=["step_a"]),
        ],
        recovery_backoff_sec=0.01,
    )
    result = await orch.execute_registration_workflow(wf, profile)
    assert result.final_state == WorkflowState.HALTED
    assert not result.success
    session = orch.sessions.get_session(result.session_id)
    event_types = [e["event_type"] for e in session.detection_events]
    assert "verification_required" in event_types
    assert session.success is False
    snapshot = wf.store.load("wf_verify")
    assert snapshot.state == WorkflowState.HALTED
    assert snapshot.halted_reason and "verification" in snapshot.halted_reason


async def test_halt_on_captcha(tmp_path, profile):
    page = FakePage(content="<html><div class='px-captcha'></div>prove you are human</html>")
    orch = make_orchestrator(page, tmp_path)
    wf = orch.create_registration_workflow(
        "wf_captcha", [WorkflowStep("step_a", "Enter data", [wait_action()])],
        recovery_backoff_sec=0.01,
    )
    result = await orch.execute_registration_workflow(wf, profile)
    assert result.final_state == WorkflowState.HALTED
    session = orch.sessions.get_session(result.session_id)
    assert "captcha" in [e["event_type"] for e in session.detection_events]


async def test_recovery_from_timeout(tmp_path, profile, monkeypatch):
    page = FakePage()
    orch = make_orchestrator(page, tmp_path)
    attempts = {"n": 0}

    async def flaky_execute_action(p, action, scenario_id):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise PlaywrightTimeoutError("navigation timed out")

    monkeypatch.setattr(orch, "execute_action", flaky_execute_action)
    wf = orch.create_registration_workflow(
        "wf_timeout", [WorkflowStep("step_a", "Flaky", [wait_action()])],
        recovery_backoff_sec=0.01,
    )
    result = await orch.execute_registration_workflow(wf, profile)
    assert result.success  # recovered on the second attempt
    assert page.reloads == 1
    session = orch.sessions.get_session(result.session_id)
    recoveries = [i for i in session.interactions if i["type"] == "recovery"]
    assert len(recoveries) == 1
    assert recoveries[0]["reason"] == "timeout"


async def test_failed_state_after_exhausted_retries(tmp_path, profile, monkeypatch):
    page = FakePage()
    orch = make_orchestrator(page, tmp_path)

    async def always_fail(p, action, scenario_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(orch, "execute_action", always_fail)
    wf = orch.create_registration_workflow(
        "wf_fail", [
            WorkflowStep("step_a", "Broken", [wait_action()], max_retries=1),
            WorkflowStep("step_b", "Never runs", [wait_action()], depends_on=["step_a"]),
        ],
        recovery_backoff_sec=0.01,
    )
    result = await orch.execute_registration_workflow(wf, profile)
    assert result.final_state == WorkflowState.FAILED
    assert result.failed_step == "step_a"
    assert result.completed_steps == []
    session = orch.sessions.get_session(result.session_id)
    assert session.success is False
    assert session.errors  # failure logged gracefully, not raised


async def test_branch_goto_skips_steps(tmp_path, profile, monkeypatch):
    page = FakePage(content="<html>special offer page</html>", url="https://target.test/promo")
    orch = make_orchestrator(page, tmp_path)

    async def no_exec(p, action, scenario_id):
        pass

    monkeypatch.setattr(orch, "execute_action", no_exec)
    wf = orch.create_registration_workflow(
        "wf_branch", [
            WorkflowStep("intro", "Intro", [wait_action()],
                         branches=[BranchRule("promo", "url_contains", "/promo",
                                              behavior="goto", goto_step="main")]),
            WorkflowStep("optional", "Optional", [wait_action()]),
            WorkflowStep("main", "Main", [wait_action()]),
        ],
        recovery_backoff_sec=0.01,
    )
    result = await orch.execute_registration_workflow(wf, profile)
    assert result.success
    assert wf.step_statuses["intro"] == "skipped_by_branch"
    assert wf.step_statuses["optional"] == "skipped_by_branch"
    assert wf.step_statuses["main"] == "completed"



