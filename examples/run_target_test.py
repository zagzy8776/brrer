"""Phase 3: execute a target-specific registration workflow.

Imports the declarative ``GOOGLE_REGISTRATION_TARGET`` configuration and runs
it through ``WorkflowOrchestrator`` -- the script contains no selectors and no
automation logic of its own.

Two modes:
    python examples/run_target_test.py          # dry run: validate config only
    python examples/run_target_test.py --live   # live browser execution

Research use only: run against flows you are authorized to test, at
conservative rates. On this hardened target the expected outcome is a
graceful HALT (verification_required / captcha_detected), not COMPLETED.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bftf import (  # noqa: E402
    BrowserController,
    FingerprintManager,
    NetworkEmulator,
    SessionManager,
    UserProfile,
    WorkflowOrchestrator,
)
from bftf.targets import GOOGLE_REGISTRATION_TARGET  # noqa: E402


def build_demo_profile() -> UserProfile:
    """Synthetic research profile (never real credentials)."""
    return UserProfile(
        profile_id="phase3_demo_001",
        first_name="Ada",
        last_name="Lovelace",
        email="ada.lovelace@example.test",
        username="ada.lovelace.phase3.demo",
        password="R3search-Demo-Pass-9x!",
        extra={
            "birth_day": "10",
            "birth_year": "1815",
            "phone_number": "+15555550100",  # reserved fictional range
        },
    )


def dry_run() -> int:
    """Validate the configuration without launching a browser."""
    cfg = GOOGLE_REGISTRATION_TARGET
    print(cfg.summary())
    print()
    profile = build_demo_profile()
    tokens = {
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "username": profile.username,
        "password": profile.password,
        **{f"extra:{k}": v for k, v in profile.extra.items()},
    }
    missing = cfg.validate_profile_tokens(tokens)
    if missing:
        print(f"ERROR: profile is missing tokens: {missing}")
        return 1
    from bftf.workflows import RegistrationWorkflow  # noqa: E402

    orchestrator = WorkflowOrchestrator(
        fingerprint_manager=FingerprintManager(seed=7),
        proxy_manager=None,
        browser_controller=BrowserController(seed=7),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager("./output"),
    )
    workflow = orchestrator.create_registration_workflow(
        cfg.workflow_id,
        cfg.steps,
        fingerprint_profile=cfg.fingerprint_profile,
        latency_profile=cfg.latency_profile,
        proxy_criteria=cfg.proxy_criteria,
    )
    assert isinstance(workflow, RegistrationWorkflow)
    print(f"Dry run OK: {len(cfg.steps)} steps validated, "
          f"profile tokens resolved, workflow '{cfg.workflow_id}' constructed.")
    print(f"Success criteria: {cfg.success_criteria[:120]}...")
    return 0


async def live_run(headless: bool = True) -> int:
    """Execute the target configuration in a real (stealth) browser."""
    cfg = GOOGLE_REGISTRATION_TARGET
    print(cfg.summary())
    print()
    orchestrator = WorkflowOrchestrator(
        fingerprint_manager=FingerprintManager(seed=7),
        proxy_manager=None,  # direct connection; add ProxyManager for proxy runs
        browser_controller=BrowserController(seed=7),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager("./output"),
    )
    workflow = orchestrator.create_registration_workflow(
        cfg.workflow_id,
        cfg.steps,
        fingerprint_profile=cfg.fingerprint_profile,
        latency_profile=cfg.latency_profile,
        proxy_criteria=cfg.proxy_criteria,
    )
    profile = build_demo_profile()
    print(f"Executing '{cfg.workflow_id}' with profile '{profile.profile_id}' ...")
    print("Expected outcome on this hardened target: graceful HALT "
          "(verification_required / captcha_detected), not COMPLETED.")
    result = await orchestrator.execute_registration_workflow(
        workflow, profile, headless=headless,
    )
    print(f"Final state: {result.final_state.value}")
    print(f"Completed steps: {result.completed_steps}")
    if result.failed_step:
        print(f"Stopped at step: {result.failed_step}")
    if result.halted_reason:
        print(f"Reason: {result.halted_reason}")
    print(f"Detection events: {len(result.detection_events)}")
    for event in result.detection_events:
        print(f"  - {event.get('event_type')}: {event.get('details', {})}")
    print(f"Snapshot: {result.snapshot_path}")
    await orchestrator.cleanup()
    return 0 if result.final_state.value in ("completed", "halted") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="launch a real browser and execute the workflow")
    parser.add_argument("--headed", action="store_true",
                        help="run the live browser visibly (implies --live)")
    args = parser.parse_args()
    if args.headed:
        return asyncio.run(live_run(headless=False))
    if args.live:
        return asyncio.run(live_run())
    return dry_run()


if __name__ == "__main__":
    raise SystemExit(main())
