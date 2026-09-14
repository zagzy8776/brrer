"""Phase 2: Complex workflow orchestration.

State-machine based execution of long-form, multi-step sequences (e.g.
authenticated registration flows on services you own or are authorized to
test) with:

- Step dependencies: 'Step B' only runs after 'Step A' completes successfully.
- Dynamic data injection: external JSON user profiles injected into human-like
  typing/click actions via ``{{token}}`` placeholders.
- State persistence & recovery: every state transition is snapshotted to disk;
  interrupted workflows (CAPTCHA, network timeouts) are either recovered in a
  structured way or gracefully logged for analysis.
- Conditional logic: unexpected pages (e.g. 'Verification Required') are
  classified, logged as specific detection events, and HALT the workflow
  instead of crashing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .models import BrowserFingerprint, ProxyConfig, TestAction
from .orchestrator import TestOrchestrator

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# State definitions
# --------------------------------------------------------------------------- #
class WorkflowState(str, Enum):
    """Lifecycle states of a workflow execution."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    HALTED = "halted"      # graceful stop: interdiction / conditional branch
    FAILED = "failed"      # exhausted recovery attempts or unrecoverable error


class PageState(str, Enum):
    """Classification of the currently rendered page."""

    NORMAL = "normal"
    CAPTCHA = "captcha"
    VERIFICATION_REQUIRED = "verification_required"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


_CAPTCHA_MARKERS = ("captcha", "recaptcha", "hcaptcha", "cf-turnstile")
_VERIFICATION_MARKERS = (
    "verification required", "verify your account", "verify your email",
    "check your email", "email verification", "confirm your email",
    "two-factor", "2fa", "one-time code", "otp code", "sms code",
)
_NETWORK_MARKERS = (
    "err_connection_reset", "err_timed_out", "err_internet_disconnected",
    "temporarily unavailable", "bad gateway", "service unavailable",
    "gateway time-out", "connection timed out",
)

# Unique sentinels for branch evaluation. Plain strings ("halt"/"continue")
# are NOT safe here: they could collide with a real step id, so a goto to a
# step literally named "continue" must not be treated as "keep going".
_HALT: Any = object()
_CONTINUE: Any = object()


# --------------------------------------------------------------------------- #
# Dynamic data injection
# --------------------------------------------------------------------------- #
@dataclass
class UserProfile:
    """A pre-defined user profile injected into workflow actions."""

    profile_id: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    username: str = ""
    password: str = ""
    extra: Dict[str, str] = field(default_factory=dict)

    def to_dict(self, redact: bool = False) -> Dict[str, Any]:
        data = {
            "profile_id": self.profile_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "username": self.username,
            "extra": dict(self.extra),
        }
        data["password"] = "***" if redact and self.password else self.password
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        payload = dict(data)
        payload["extra"] = {str(k): str(v) for k, v in (payload.get("extra") or {}).items()}
        return cls(**payload)


def load_user_profiles(path: str) -> List[UserProfile]:
    """Load user profiles from a JSON file: ``{"profiles": [ ... ]}``."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data["profiles"] if isinstance(data, dict) else data
    profiles = [UserProfile.from_dict(entry) for entry in entries]
    if not profiles:
        raise ValueError(f"No user profiles found in '{path}'")
    for profile in profiles:
        if not profile.profile_id:
            raise ValueError("Every user profile requires a non-empty profile_id")
    return profiles


# --------------------------------------------------------------------------- #
# Step and branch definitions
# --------------------------------------------------------------------------- #
@dataclass
class BranchRule:
    """Conditional branching rule evaluated before a step executes.

    match_type: 'selector' | 'url_contains' | 'title_contains' | 'page_marker'
    behavior:   'halt' (graceful stop + event) | 'goto' | 'continue'
    """

    name: str
    match_type: str
    match_value: str
    behavior: str = "halt"
    goto_step: Optional[str] = None
    event_type: Optional[str] = None  # detection event logged when matched

    def __post_init__(self) -> None:
        if self.match_type not in ("selector", "url_contains", "title_contains", "page_marker"):
            raise ValueError(f"Unknown match_type '{self.match_type}'")
        if self.behavior not in ("halt", "goto", "continue"):
            raise ValueError(f"Unknown behavior '{self.behavior}'")
        if self.behavior == "goto" and not self.goto_step:
            raise ValueError("'goto' branches require goto_step")


def match_rule(rule: BranchRule, *, url: str, title: str, content: str) -> bool:
    """Pure matcher: evaluate a branch rule against extracted page info."""
    if rule.match_type == "url_contains":
        return rule.match_value.lower() in url.lower()
    if rule.match_type == "title_contains":
        return rule.match_value.lower() in title.lower()
    if rule.match_type == "page_marker":
        return rule.match_value.lower() in content.lower()
    # 'selector' matching requires a live page; handled by the evaluator.
    return False


@dataclass
class WorkflowStep:
    """One state in the workflow machine."""

    step_id: str
    name: str
    actions: List[TestAction]
    depends_on: List[str] = field(default_factory=list)
    max_retries: int = 2
    recovery_url: Optional[str] = None  # page to re-navigate to during recovery
    branches: List[BranchRule] = field(default_factory=list)
    validation: List[Dict[str, Any]] = field(default_factory=list)  # rule dicts

    def __post_init__(self) -> None:
        if not self.step_id:
            raise ValueError("step_id must be non-empty")
        if not self.actions:
            raise ValueError(f"Step '{self.step_id}' must contain at least one action")


@dataclass
class WorkflowSnapshot:
    """Persisted workflow state for interruption recovery and analysis."""

    workflow_id: str
    session_id: str
    state: WorkflowState
    step_statuses: Dict[str, str]
    profile_id: Optional[str] = None
    current_step_id: Optional[str] = None
    recovery_attempts: int = 0
    halted_reason: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "step_statuses": dict(self.step_statuses),
            "profile_id": self.profile_id,
            "current_step_id": self.current_step_id,
            "recovery_attempts": self.recovery_attempts,
            "halted_reason": self.halted_reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowSnapshot":
        payload = dict(data)
        payload["state"] = WorkflowState(payload["state"])
        return cls(**payload)


class WorkflowStateStore:
    """Persists workflow snapshots under ``<output_dir>/workflows/``."""

    def __init__(self, output_dir: str) -> None:
        self.directory = Path(output_dir) / "workflows"
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: WorkflowSnapshot) -> str:
        snapshot.updated_at = datetime.now().isoformat()
        path = self.directory / f"{snapshot.workflow_id}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot.to_dict(), fh, indent=2)
        return str(path)

    def load(self, workflow_id: str) -> Optional[WorkflowSnapshot]:
        path = self.directory / f"{workflow_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return WorkflowSnapshot.from_dict(json.load(fh))


@dataclass
class WorkflowResult:
    """Outcome of a workflow execution."""

    workflow_id: str
    session_id: str
    final_state: WorkflowState
    completed_steps: List[str] = field(default_factory=list)
    failed_step: Optional[str] = None
    halted_reason: Optional[str] = None
    detection_events: List[Dict[str, Any]] = field(default_factory=list)
    snapshot_path: Optional[str] = None
    duration_sec: float = 0.0

    @property
    def success(self) -> bool:
        return self.final_state == WorkflowState.COMPLETED


# --------------------------------------------------------------------------- #
# Page classification
# --------------------------------------------------------------------------- #
class PageClassifier:
    """Classifies the live page into a PageState for conditional logic."""

    @staticmethod
    async def _extract(page: Page) -> Dict[str, str]:
        url = page.url
        try:
            title = await page.title()
        except Exception:
            title = ""
        try:
            content = (await page.content()).lower()
        except Exception:
            content = ""
        return {"url": url, "title": title, "content": content}

    @classmethod
    async def classify(cls, page: Page) -> tuple:
        """Return ``(PageState, evidence_list)`` for the current page."""
        try:
            info = await cls._extract(page)
        except Exception as exc:  # page crashed / navigation destroyed context
            logger.debug("Page classification failed: %s", exc)
            return PageState.UNKNOWN, [str(exc)]

        evidence: List[str] = []
        for marker in _CAPTCHA_MARKERS:
            if marker in info["content"]:
                evidence.append(marker)
        if evidence:
            return PageState.CAPTCHA, evidence

        for marker in _VERIFICATION_MARKERS:
            if marker in info["content"]:
                evidence.append(marker)
        if evidence:
            return PageState.VERIFICATION_REQUIRED, evidence

        for marker in _NETWORK_MARKERS:
            if marker in info["content"] or marker in info["url"].lower():
                evidence.append(marker)
        if evidence:
            return PageState.NETWORK_ERROR, evidence

        return PageState.NORMAL, []


# --------------------------------------------------------------------------- #
# Registration workflow (state machine)
# --------------------------------------------------------------------------- #
class RegistrationWorkflow:
    """State-machine execution of a multi-step registration-style sequence.

    The workflow runs its steps in definition order, enforcing ``depends_on``:
    a step only executes once every step it depends on is COMPLETED. Each
    transition is snapshotted to disk. Interruptions are handled by the
    recovery policy; interdiction pages (CAPTCHA, verification) halt the
    workflow gracefully with a specific logged event.
    """

    def __init__(
        self,
        orchestrator: TestOrchestrator,
        workflow_id: str,
        steps: List[WorkflowStep],
        fingerprint_profile: str = "residential_windows",
        latency_profile: str = "residential_cable",
        proxy_criteria: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        recovery_backoff_sec: float = 2.0,
    ) -> None:
        self.orchestrator = orchestrator
        self.workflow_id = workflow_id
        self.steps = list(steps)
        self.fingerprint_profile = fingerprint_profile
        self.latency_profile = latency_profile
        self.proxy_criteria = proxy_criteria or {}
        self.session_id = session_id
        self.recovery_backoff_sec = recovery_backoff_sec

        self.state = WorkflowState.NOT_STARTED
        self.step_statuses: Dict[str, str] = {s.step_id: "pending" for s in self.steps}
        self._profile: Optional[UserProfile] = None
        self._recovery_attempts = 0
        self._halted_reason: Optional[str] = None
        self._detection_events: List[Dict[str, Any]] = []
        self.store = WorkflowStateStore(orchestrator.sessions.output_dir)
        self._validate_steps()

    # ------------------------------------------------------------------ #
    # Construction-time validation
    # ------------------------------------------------------------------ #
    def _validate_steps(self) -> None:
        """Ensure unique ids, resolvable dependencies, and no cycles."""
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Workflow step ids must be unique")
        position = {sid: i for i, sid in enumerate(ids)}
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in position:
                    raise ValueError(f"Step '{step.step_id}' depends on unknown step '{dep}'")
                if position[dep] >= position[step.step_id]:
                    raise ValueError(
                        f"Step '{step.step_id}' depends on '{dep}' which is not defined earlier "
                        "(dependencies must reference previously defined steps)"
                    )

    # ------------------------------------------------------------------ #
    # Dynamic data injection
    # ------------------------------------------------------------------ #
    def inject_user_profile(self, profile: UserProfile) -> None:
        """Set the user profile whose values are injected into ``{{token}}``s."""
        if not isinstance(profile, UserProfile):
            raise TypeError("inject_user_profile expects a UserProfile instance")
        self._profile = profile

    def _resolve(self, value: Optional[str]) -> Optional[str]:
        """Substitute ``{{field}}`` and ``{{extra:key}}`` tokens in a string."""
        if value is None or "{{" not in value:
            return value
        if self._profile is None:
            raise ValueError(
                f"Action value '{value}' contains injection tokens but no user profile "
                "has been injected (call inject_user_profile first)"
            )
        resolved = value
        p = self._profile
        tokens = {
            "profile_id": p.profile_id,
            "first_name": p.first_name,
            "last_name": p.last_name,
            "full_name": f"{p.first_name} {p.last_name}".strip(),
            "email": p.email,
            "username": p.username,
            "password": p.password,
        }
        for key, val in tokens.items():
            resolved = resolved.replace("{{" + key + "}}", val)
        for key, val in p.extra.items():
            resolved = resolved.replace("{{extra:" + key + "}}", val)
        if "{{" in resolved:
            raise ValueError(f"Unresolved injection token(s) in '{value}' -> '{resolved}'")
        return resolved

    def _resolved_actions(self, step: WorkflowStep) -> List[TestAction]:
        """Return a copy of the step's actions with profile tokens injected."""
        actions = []
        for action in step.actions:
            actions.append(replace(
                action,
                selector=self._resolve(action.selector),
                value=self._resolve(action.value),
            ))
        return actions

    # ------------------------------------------------------------------ #
    # State persistence
    # ------------------------------------------------------------------ #
    def _snapshot(self, session_id: str) -> WorkflowSnapshot:
        return WorkflowSnapshot(
            workflow_id=self.workflow_id,
            session_id=session_id,
            state=self.state,
            step_statuses=dict(self.step_statuses),
            profile_id=self._profile.profile_id if self._profile else None,
            recovery_attempts=self._recovery_attempts,
            halted_reason=self._halted_reason,
        )

    def _persist(self, session_id: str) -> str:
        return self.store.save(self._snapshot(session_id))

    def resume_snapshot(self, session_id: Optional[str] = None) -> Optional[WorkflowSnapshot]:
        """Load the last persisted snapshot for this workflow, if any."""
        return self.store.load(self.workflow_id)

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    async def run(self, headless: bool = True, resume: bool = False) -> WorkflowResult:
        """Execute the workflow end-to-end; returns a WorkflowResult.

        A fresh browser session is created with a new fingerprint and proxy
        (matching the orchestrator's subsystems) and fully logged through the
        SessionManager. With ``resume=True`` the last snapshot is loaded and
        previously COMPLETED steps are skipped.
        """
        started = time.perf_counter()
        orchestrator = self.orchestrator

        fingerprint: BrowserFingerprint = orchestrator.fingerprints.generate_fingerprint(
            profile=self.fingerprint_profile
        )
        proxy: Optional[ProxyConfig] = None
        if orchestrator.proxies is not None:
            proxy = await orchestrator.proxies.get_proxy(criteria=self.proxy_criteria)
        latency_profile = orchestrator.network.get_preset_profile(self.latency_profile)

        session_id = self.session_id or f"{self.workflow_id}_{int(time.time())}"
        orchestrator.sessions.start_session(
            session_id, fingerprint, proxy=None,
            latency_profile=latency_profile, proxy_resolved=proxy,
        )
        orchestrator.sessions.record_interaction(session_id, "workflow_start", {
            "workflow_id": self.workflow_id,
            "profile_id": self._profile.profile_id if self._profile else None,
            "resume": resume,
            "fingerprint_platform": fingerprint.platform,
            "proxy": proxy.key if proxy else None,
            "latency_profile": latency_profile.name,
        })

        self.state = WorkflowState.RUNNING
        self._halted_reason = None
        if not resume:
            self.step_statuses = {s.step_id: "pending" for s in self.steps}
            self._recovery_attempts = 0
        snapshot_path = self._persist(session_id)

        browser = await orchestrator.browser.initialize_browser(fingerprint, proxy, headless=headless)
        try:
            context = await orchestrator.browser.create_context(browser, fingerprint)
            page = await context.new_page()
            await orchestrator.network.apply_latency_profile(page, latency_profile)

            if resume:
                self._restore_progress(session_id)
            result = await self._execute_machine(page, session_id)
        except Exception as exc:  # never crash: log and persist failed state
            self.state = WorkflowState.FAILED
            self._halted_reason = f"{type(exc).__name__}: {exc}"
            orchestrator.sessions.record_error(session_id, self._halted_reason)
            logger.exception("Workflow %s crashed", self.workflow_id)
            snapshot_path = self._persist(session_id)
            result = self._build_result(session_id, snapshot_path)
        finally:
            await orchestrator.browser.close_browser(browser)
            success = self.state == WorkflowState.COMPLETED
            orchestrator.sessions.end_session(session_id, success, self._halted_reason)

        result.duration_sec = round(time.perf_counter() - started, 3)
        return result

    def _restore_progress(self, session_id: str) -> None:
        """Skip steps already COMPLETED according to the persisted snapshot."""
        snapshot = self.store.load(self.workflow_id)
        if snapshot is None:
            return
        for sid, status in snapshot.step_statuses.items():
            if sid in self.step_statuses:
                self.step_statuses[sid] = status
        self._recovery_attempts = snapshot.recovery_attempts
        self.orchestrator.sessions.record_interaction(session_id, "workflow_resume", {
            "completed_steps": [s for s, st in self.step_statuses.items() if st == "completed"],
        })
        logger.info("Resuming workflow %s: %s", self.workflow_id, self.step_statuses)

    # ------------------------------------------------------------------ #
    # State machine
    # ------------------------------------------------------------------ #
    async def _execute_machine(self, page: Page, session_id: str) -> WorkflowResult:
        orchestrator = self.orchestrator
        snapshot_path = self._persist(session_id)
        index = {s.step_id: i for i, s in enumerate(self.steps)}

        i = 0
        jumps: set = set()  # (from_step, to_step) guard against goto loops
        while i < len(self.steps):
            step = self.steps[i]
            status = self.step_statuses.get(step.step_id)
            if status in ("completed", "skipped_by_branch"):
                i += 1
                continue

            # Dependency gate: Step B requires Step A COMPLETED.
            unmet = [d for d in step.depends_on if self.step_statuses.get(d) != "completed"]
            if unmet:
                self.state = WorkflowState.FAILED
                self._halted_reason = f"Step '{step.step_id}' has unmet dependencies: {unmet}"
                orchestrator.sessions.record_error(session_id, self._halted_reason)
                snapshot_path = self._persist(session_id)
                return self._build_result(session_id, snapshot_path, failed_step=step.step_id)

            # Conditional branching: detect unexpected pages before acting.
            branch = await self._evaluate_branches(page, step, session_id)
            if branch is _HALT:
                snapshot_path = self._persist(session_id)
                return self._build_result(session_id, snapshot_path, failed_step=step.step_id)
            if branch is not _CONTINUE:  # 'goto' -> jump to the named step
                if branch not in index:
                    self.state = WorkflowState.FAILED
                    h = f"branches to unknown step '{branch}'"
                    self._halted_reason = f"Step '{step.step_id}' {h}"
                    orchestrator.sessions.record_error(session_id, self._halted_reason)
                    snapshot_path = self._persist(session_id)
                    return self._build_result(session_id, snapshot_path, failed_step=step.step_id)
                jump = (step.step_id, branch)
                if jump in jumps:
                    self.state = WorkflowState.FAILED
                    self._halted_reason = f"Branch loop: {jump[0]} -> {jump[1]}"
                    orchestrator.sessions.record_error(session_id, self._halted_reason)
                    snapshot_path = self._persist(session_id)
                    return self._build_result(session_id, snapshot_path, failed_step=step.step_id)
                jumps.add(jump)
                i = self._goto_step(branch, current_index=i, session_id=session_id)
                snapshot_path = self._persist(session_id)
                continue

            # Interruption screening before step execution.
            page_state, evidence = await PageClassifier.classify(page)
            if page_state in (PageState.CAPTCHA, PageState.VERIFICATION_REQUIRED):
                return await self._halt_on_interdiction(page, session_id, step, page_state, evidence)
            if page_state == PageState.NETWORK_ERROR:
                recovered = await self._recover(page, session_id, step, "network_error_before_step")
                if not recovered:
                    snapshot_path = self._persist(session_id)
                    return self._build_result(session_id, snapshot_path, failed_step=step.step_id)

            self.state = WorkflowState.RUNNING
            self.step_statuses[step.step_id] = "running"
            snapshot_path = self._persist(session_id)

            if await self._run_step_with_recovery(page, step, session_id):
                self.step_statuses[step.step_id] = "completed"
                orchestrator.sessions.record_interaction(session_id, "step_completed", {
                    "step_id": step.step_id, "name": step.name,
                })
                snapshot_path = self._persist(session_id)
            else:
                self.step_statuses[step.step_id] = "failed"
                self.state = WorkflowState.FAILED
                self._halted_reason = f"Step '{step.step_id}' failed after recovery attempts"
                orchestrator.sessions.record_error(session_id, self._halted_reason)
                snapshot_path = self._persist(session_id)
                return self._build_result(session_id, snapshot_path, failed_step=step.step_id)
            i += 1

        self.state = WorkflowState.COMPLETED
        snapshot_path = self._persist(session_id)
        orchestrator.sessions.record_interaction(session_id, "workflow_completed", {
            "workflow_id": self.workflow_id,
        })
        return self._build_result(session_id, snapshot_path)

    def _goto_step(self, target_step_id: str, current_index: int, session_id: str) -> int:
        """Branch 'goto': skip pending steps up to target, return its index."""
        target = next(i for i, s in enumerate(self.steps) if s.step_id == target_step_id)
        current_id = self.steps[current_index].step_id
        self.step_statuses[current_id] = "skipped_by_branch"
        skipped = [current_id]
        if target > current_index:
            for step in self.steps[current_index + 1:target]:
                if self.step_statuses.get(step.step_id) == "pending":
                    self.step_statuses[step.step_id] = "skipped_by_branch"
                    skipped.append(step.step_id)
        self.orchestrator.sessions.record_interaction(session_id, "branch_goto", {
            "target": target_step_id, "skipped": skipped,
        })
        logger.info("Branch goto %s -> %s (skipped %s)", current_id, target_step_id, skipped)
        return target

    # ------------------------------------------------------------------ #
    # Step execution, recovery, and interdiction handling
    # ------------------------------------------------------------------ #
    async def _run_step_with_recovery(self, page: Page, step: WorkflowStep, session_id: str) -> bool:
        """Execute a step; on transient failures attempt structured recovery."""
        orchestrator = self.orchestrator
        actions = self._resolved_actions(step)
        last_error: Optional[str] = None

        for attempt in range(step.max_retries + 1):
            try:
                for action in actions:
                    await orchestrator.execute_action(page, action, session_id)

                if step.validation:
                    results = await orchestrator.evaluate_rules(page, step.validation)
                    if not all(r["passed"] for r in results):
                        raise RuntimeError(
                            f"Step validation failed: "
                            f"{[r['rule'] for r in results if not r['passed']]}"
                        )
                return True

            except PlaywrightTimeoutError as exc:
                last_error = f"timeout: {exc}"
                self.state = WorkflowState.RECOVERING
                if not await self._recover(page, session_id, step, "timeout", attempt):
                    return False
            except Exception as exc:
                # Interdiction during a step: never retry through it - halt.
                page_state, evidence = await PageClassifier.classify(page)
                if page_state in (PageState.CAPTCHA, PageState.VERIFICATION_REQUIRED):
                    await self._halt_on_interdiction(page, session_id, step, page_state, evidence)
                    return False
                last_error = f"{type(exc).__name__}: {exc}"
                self.state = WorkflowState.RECOVERING
                if not await self._recover(page, session_id, step, "action_error", attempt):
                    orchestrator.sessions.record_error(session_id, last_error)
                    return False

        orchestrator.sessions.record_error(
            session_id, f"Step '{step.step_id}' exhausted retries: {last_error}"
        )
        return False

    async def _recover(self, page: Page, session_id: str, step: WorkflowStep,
                       reason: str, attempt: int = 0) -> bool:
        """Structured recovery: backoff, reload / re-navigate, then retry.

        Returns True if another execution attempt should be made.
        """
        orchestrator = self.orchestrator
        self._recovery_attempts += 1
        backoff = self.recovery_backoff_sec * (2 ** attempt)
        orchestrator.sessions.record_interaction(session_id, "recovery", {
            "step_id": step.step_id, "reason": reason,
            "attempt": attempt + 1, "backoff_sec": round(backoff, 2),
        })
        logger.info("Recovering step '%s' (%s), attempt %d", step.step_id, reason, attempt + 1)
        await asyncio.sleep(backoff)
        try:
            if step.recovery_url:
                await page.goto(step.recovery_url, wait_until="load", timeout=30000)
            else:
                await page.reload(wait_until="load", timeout=30000)
            self.state = WorkflowState.RUNNING
            return True
        except Exception as exc:
            orchestrator.sessions.record_error(
                session_id, f"Recovery navigation failed for '{step.step_id}': {exc}"
            )
            self.state = WorkflowState.FAILED
            self._halted_reason = f"Recovery failed for step '{step.step_id}': {reason}"
            return False

    async def _halt_on_interdiction(self, page: Page, session_id: str, step: WorkflowStep,
                                    page_state: PageState, evidence: List[str]) -> WorkflowResult:
        """Gracefully halt on CAPTCHA / verification pages and log the event."""
        orchestrator = self.orchestrator
        event_type = "verification_required" if page_state == PageState.VERIFICATION_REQUIRED else "captcha"
        self.state = WorkflowState.HALTED
        self._halted_reason = f"{event_type} detected before/during step '{step.step_id}'"
        orchestrator.sessions.record_detection_event(session_id, event_type, {
            "workflow_id": self.workflow_id,
            "step_id": step.step_id,
            "evidence": evidence,
            "url": page.url,
            "action": "halted",  # interdiction is logged, never bypassed
        })
        orchestrator.sessions.record_interaction(session_id, "workflow_halted", {
            "workflow_id": self.workflow_id, "reason": event_type,
            "step_id": step.step_id,
        })
        logger.warning("Workflow %s HALTED at step '%s': %s", self.workflow_id, step.step_id, event_type)
        snapshot_path = self._persist(session_id)
        return self._build_result(session_id, snapshot_path, failed_step=step.step_id)

    async def _evaluate_branches(self, page: Page, step: WorkflowStep, session_id: str):
        """Evaluate the step's branch rules.

        Returns ``_HALT`` for halt, ``_CONTINUE`` to proceed normally,
        or a step id string for 'goto'.
        """
        if not step.branches:
            return _CONTINUE
        info = await PageClassifier._extract(page)
        for rule in step.branches:
            matched = match_rule(rule, url=info["url"], title=info["title"], content=info["content"])
            if not matched and rule.match_type == "selector":
                try:
                    matched = await page.locator(rule.match_value).first.is_visible()
                except Exception:
                    matched = False
            if not matched:
                continue

            event_type = rule.event_type or rule.name
            self.orchestrator.sessions.record_detection_event(session_id, event_type, {
                "workflow_id": self.workflow_id,
                "step_id": step.step_id,
                "branch": rule.name,
                "match_type": rule.match_type,
                "match_value": rule.match_value,
                "url": info["url"],
            })
            if rule.behavior == "halt":
                self.state = WorkflowState.HALTED
                self._halted_reason = f"Branch '{rule.name}' matched on step '{step.step_id}'"
                logger.warning("Branch '%s' matched -> halting workflow", rule.name)
                return _HALT
            if rule.behavior == "goto":
                return rule.goto_step
            return _CONTINUE
        return _CONTINUE

    # ------------------------------------------------------------------ #
    # Result assembly
    # ------------------------------------------------------------------ #
    def _build_result(self, session_id: str, snapshot_path: str,
                      failed_step: Optional[str] = None) -> WorkflowResult:
        session = self.orchestrator.sessions.get_session(session_id)
        return WorkflowResult(
            workflow_id=self.workflow_id,
            session_id=session_id,
            final_state=self.state,
            completed_steps=[s for s, st in self.step_statuses.items() if st == "completed"],
            failed_step=failed_step,
            halted_reason=self._halted_reason,
            detection_events=list(session.detection_events) if session else list(self._detection_events),
            snapshot_path=snapshot_path,
        )


class WorkflowOrchestrator(TestOrchestrator):
    """TestOrchestrator extended with state-machine workflow execution."""

    def create_registration_workflow(
        self,
        workflow_id: str,
        steps: List[WorkflowStep],
        fingerprint_profile: str = "residential_windows",
        latency_profile: str = "residential_cable",
        proxy_criteria: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        recovery_backoff_sec: float = 2.0,
    ) -> RegistrationWorkflow:
        """Build a RegistrationWorkflow bound to this orchestrator's subsystems."""
        return RegistrationWorkflow(
            orchestrator=self,
            workflow_id=workflow_id,
            steps=steps,
            fingerprint_profile=fingerprint_profile,
            latency_profile=latency_profile,
            proxy_criteria=proxy_criteria,
            session_id=session_id,
            recovery_backoff_sec=recovery_backoff_sec,
        )

    async def execute_registration_workflow(
        self,
        workflow: RegistrationWorkflow,
        profile: UserProfile,
        headless: bool = True,
        resume: bool = False,
    ) -> WorkflowResult:
        """Inject a user profile into the workflow and execute it."""
        workflow.inject_user_profile(profile)
        return await workflow.run(headless=headless, resume=resume)






