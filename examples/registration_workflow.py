"""Phase 2 smoke test: live multi-step registration-style workflow.

Runs against https://example.com (safe, static) to prove the state
machine executes real browser steps end-to-end:

    Step A (fill) -> Step B (done, depends_on=['fill'])

Usage:
    python examples/registration_workflow.py
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
    UserProfile,
    WorkflowOrchestrator,
    WorkflowStep,
)


async def run_registration_workflow() -> None:
    """Execute a live two-step registration-style workflow."""
    orchestrator = WorkflowOrchestrator(
        fingerprint_manager=FingerprintManager(seed=7),
        proxy_manager=None,  # direct connection; supply a ProxyManager for proxy research
        browser_controller=BrowserController(seed=7),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager("./output"),
    )

    # Step B ('done') only runs after Step A ('fill') completes.
    steps = [
        WorkflowStep(
            step_id="fill",
            name="Load and read the signup page",
            actions=[
                TestAction(action_type="navigate", value="https://example.com",
                           wait_after_ms=1500, human_behavior=False),
                TestAction(action_type="wait", value="500", human_behavior=False),
                TestAction(action_type="scroll", value="down", human_behavior=False),
            ],
        ),
        WorkflowStep(
            step_id="done",
            name="Capture confirmation evidence",
            actions=[
                TestAction(action_type="screenshot", human_behavior=False),
            ],
            depends_on=["fill"],
        ),
    ]
    profile = UserProfile(
        profile_id="smoke_001",
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.test",
        username="ada_smoke",
        password="s3cret-pass!",
    )

    workflow = orchestrator.create_registration_workflow("registration_smoke", steps)
    result = await orchestrator.execute_registration_workflow(workflow, profile)
    print(f"Workflow {result.workflow_id}: {result.final_state.value}")
    print(f"Completed steps: {result.completed_steps}")
    print(f"Detection events: {len(result.detection_events)}")
    print(f"Snapshot: {result.snapshot_path}")
    await orchestrator.cleanup()


if __name__ == "__main__":
    asyncio.run(run_registration_workflow())
