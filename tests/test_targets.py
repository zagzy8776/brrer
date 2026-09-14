"""Tests for Phase 3 target-specific registration configurations."""
import re

import pytest

from bftf.models import TestAction
from bftf.targets import GOOGLE_REGISTRATION_TARGET, TargetWorkflowConfiguration
from bftf.targets.google_registration_target import (
    ENTRY_URL,
    EXPECTED_DATA_TOKENS,
    STEPS,
)
from bftf.workflows import RegistrationWorkflow, UserProfile


def test_target_is_configuration_object():
    assert isinstance(GOOGLE_REGISTRATION_TARGET, TargetWorkflowConfiguration)
    assert GOOGLE_REGISTRATION_TARGET.workflow_id == "google_registration"


def test_step_sequence_and_dependencies():
    ids = GOOGLE_REGISTRATION_TARGET.step_ids
    assert ids == [
        "landing",
        "choose_account_type",
        "enter_name",
        "enter_credentials",
        "enter_birthday_gender",
        "phone_entry",
        "confirmation_capture",
    ]
    by_id = {s.step_id: s for s in STEPS}
    for step in STEPS[1:]:
        assert step.depends_on, f"step '{step.step_id}' must declare depends_on"
        for dep in step.depends_on:
            assert dep in by_id


def test_data_mapping_tokens_present():
    """Every {{token}} used by actions must be declared in expected_data_tokens."""
    token_re = re.compile(r"\{\{([^}]+)\}\}")
    used = set()
    for step in STEPS:
        for action in step.actions:
            for text in (action.selector, action.value):
                if text:
                    used.update(token_re.findall(text))
    assert used, "expected at least one data token in the configuration"
    assert used <= set(EXPECTED_DATA_TOKENS), f"undeclared tokens: {used - set(EXPECTED_DATA_TOKENS)}"
    for required in ("first_name", "last_name", "username", "password"):
        assert required in used


def test_phone_verification_halt_rule_present():
    """Phone-verification BranchRules must halt with verification_required."""
    found = 0
    for step in STEPS:
        for rule in step.branches:
            if rule.event_type == "verification_required":
                assert rule.behavior == "halt"
                found += 1
    assert found >= len(STEPS), "every step must carry a phone-verification halt rule"


def test_selectors_are_resilient():
    """Interaction selectors should carry comma-separated fallbacks."""
    interaction_steps = [
        "choose_account_type", "enter_name", "enter_credentials",
        "enter_birthday_gender", "phone_entry",
    ]
    for step in STEPS:
        if step.step_id not in interaction_steps:
            continue
        selectors = [
            a.selector for a in step.actions if a.action_type in ("click", "type")
        ]
        assert selectors
        assert all("," in s for s in selectors), (
            f"step '{step.step_id}' selectors need fallback alternatives"
        )


def test_actions_validate():
    for step in STEPS:
        for action in step.actions:
            assert isinstance(action, TestAction)
            action.validate()


def test_success_criteria_defined():
    cfg = GOOGLE_REGISTRATION_TARGET
    assert cfg.success_criteria
    assert cfg.completion_validation
    assert ENTRY_URL.startswith("https://accounts.google.com")


def test_workflow_constructs_from_config():
    """The declarative config must build a valid RegistrationWorkflow."""
    from bftf import (  # local import: needs no browser
        BrowserController,
        FingerprintManager,
        NetworkEmulator,
        SessionManager,
        WorkflowOrchestrator,
    )

    orchestrator = WorkflowOrchestrator(
        fingerprint_manager=FingerprintManager(seed=7),
        proxy_manager=None,
        browser_controller=BrowserController(seed=7),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager("./output"),
    )
    workflow = orchestrator.create_registration_workflow(
        GOOGLE_REGISTRATION_TARGET.workflow_id,
        GOOGLE_REGISTRATION_TARGET.steps,
        fingerprint_profile=GOOGLE_REGISTRATION_TARGET.fingerprint_profile,
        latency_profile=GOOGLE_REGISTRATION_TARGET.latency_profile,
        proxy_criteria=GOOGLE_REGISTRATION_TARGET.proxy_criteria,
    )
    assert isinstance(workflow, RegistrationWorkflow)

    # All declared tokens must resolve against a complete profile.
    profile = UserProfile(
        profile_id="t",
        first_name="Ada",
        last_name="Lovelace",
        username="ada.test",
        password="pw",
        extra={"birth_day": "10", "birth_year": "1815", "phone_number": "+1"},
    )
    workflow.inject_user_profile(profile)
    for step in GOOGLE_REGISTRATION_TARGET.steps:
        for action in workflow._resolved_actions(step):
            for text in (action.selector, action.value):
                assert "{{" not in (text or "")


def test_missing_tokens_reported():
    missing = GOOGLE_REGISTRATION_TARGET.validate_profile_tokens({"first_name": "A"})
    assert "last_name" in missing and "username" in missing
