"""Google account registration target configuration (Phase 3).

This module contains **no automation logic** -- it is a declarative
``TargetWorkflowConfiguration`` consumed by ``WorkflowOrchestrator``.
The orchestrator executes the steps through the existing ``BrowserController``
human-like typing/click path, ``SessionManager`` logging, and the workflow
state machine (dependencies, persistence/recovery, conditional branching).

Coverage:
    1. ``landing`` -- initial navigation to the Google signup entry point.
    2. ``choose_account_type`` -- open *Create account* and pick personal use.
    3. ``enter_name`` -- First Name / Last Name data entry.
    4. ``enter_credentials`` -- desired username + password/confirm.
    5. ``enter_birthday_gender`` -- birthday + gender fields.
    6. ``phone_entry`` -- phone-number challenge screen.
    7. ``confirmation_capture`` -- evidence screenshot + success validation.

Research posture: Google signup deploys aggressive anti-bot countermeasures
(risk scoring, phone verification, CAPTCHA/reCAPTCHA, rate limiting). Any
*Phone Verification*, *CAPTCHA*, or *unusual-traffic* page is a graceful HALT
with a specific detection event -- never a retry-through, never a bypass.

Only execute against flows you are authorized to test, at conservative rates.
"""
from __future__ import annotations

from ..models import TestAction
from ..workflows import BranchRule, WorkflowStep
from .base import TargetWorkflowConfiguration

# --------------------------------------------------------------------------- #
# Shared constants
# --------------------------------------------------------------------------- #

ENTRY_URL = (
    "https://accounts.google.com/lifecycle/steps/signup/name"
    "?continue=https://myaccount.google.com"
    "&flowName=GlifWebSignIn&flowEntry=SignUp"
)

# Every interaction selector lists comma-separated fallbacks so a minor DOM
# reorder / rename / A-B variant does not break the locator. Playwright
# treats commas as logical OR across the alternatives.

SEL_CREATE_ACCOUNT = (
    "button:has-text('Create account'), "
    "a:has-text('Create account'), "
    "span:has-text('Create account'), "
    "#createAccount, "
    "div[role='button']:has-text('Create account')"
)

SEL_CREATE_PERSONAL_USE = (
    "li:has-text('For my personal use'), "
    "div[role='option']:has-text('For my personal use'), "
    "span:has-text('For my personal use'), "
    "div:has-text('For my personal use')"
)

SEL_FIRST_NAME = (
    "input[name='firstName'], "
    "#firstName, "
    "input[aria-label*='First name' i], "
    "input[autocomplete='given-name']"
)

SEL_LAST_NAME = (
    "input[name='lastName'], "
    "#lastName, "
    "input[aria-label*='Last name' i], "
    "input[autocomplete='family-name']"
)

SEL_USERNAME = (
    "input[name='Username'], "
    "input[name='username'], "
    "#username, "
    "input[aria-label*='Username' i], "
    "input[autocomplete='username']"
)

SEL_PASSWORD = (
    "input[name='Passwd'], "
    "input[name='password'], "
    "input[type='password'], "
    "input[aria-label*='Password' i], "
    "input[autocomplete='new-password']"
)

SEL_PASSWORD_CONFIRM = (
    "input[name='ConfirmPasswd'], "
    "input[name='confirm-password'], "
    "input[aria-label*='Confirm' i], "
    "#confirm-password"
)

SEL_BIRTH_DAY = (
    "input[name='day'], "
    "#day, "
    "input[aria-label*='Day' i]"
)

SEL_BIRTH_YEAR = (
    "input[name='year'], "
    "#year, "
    "input[aria-label*='Year' i]"
)

SEL_PHONE_NUMBER = (
    "input[name='phoneNumber'], "
    "input[type='tel'], "
    "#phoneNumberId, "
    "input[aria-label*='Phone' i], "
    "input[autocomplete='tel']"
)

SEL_NEXT_BUTTON = (
    "button:has-text('Next'), "
    "div[role='button']:has-text('Next'), "
    "button:has-text('Continue'), "
    "div[role='button']:has-text('Continue'), "
    "#next, "
    "button[type='submit']"
)

def _captcha_branches() -> list:
    """CAPTCHA / reCAPTCHA interdiction: immediate HALT + event."""
    return [
        BranchRule(
            name="captcha_iframe_present",
            match_type="selector",
            match_value="iframe[src*='recaptcha'], iframe[src*='hcaptcha']",
            behavior="halt",
            event_type="captcha_detected",
        ),
        BranchRule(
            name="captcha_page_marker",
            match_type="page_marker",
            match_value="recaptcha",
            behavior="halt",
            event_type="captcha_detected",
        ),
    ]


def _phone_verification_branches() -> list:
    """Phone-verification gate: immediate HALT + ``verification_required``.

    Required conditional branch for this target: whenever the *Verify your
    phone number* / *phone verification* challenge appears, the workflow must
    stop gracefully and log ``verification_required`` rather than proceeding
    or crashing.
    """
    return [
        BranchRule(
            name="phone_verification_url",
            match_type="url_contains",
            match_value="phoneverification",
            behavior="halt",
            event_type="verification_required",
        ),
        BranchRule(
            name="phone_verify_url_alt",
            match_type="url_contains",
            match_value="verifyphone",
            behavior="halt",
            event_type="verification_required",
        ),
        BranchRule(
            name="phone_verification_marker",
            match_type="page_marker",
            match_value="verify your phone",
            behavior="halt",
            event_type="verification_required",
        ),
        BranchRule(
            name="phone_verification_marker_alt",
            match_type="page_marker",
            match_value="verify your number",
            behavior="halt",
            event_type="verification_required",
        ),
    ]


def _abuse_branches() -> list:
    """Rate-limit / unusual-traffic interdiction: immediate HALT + event."""
    return [
        BranchRule(
            name="unusual_traffic",
            match_type="page_marker",
            match_value="unusual traffic",
            behavior="halt",
            event_type="unusual_traffic",
        ),
        BranchRule(
            name="too_many_accounts",
            match_type="page_marker",
            match_value="couldn't create your account",
            behavior="halt",
            event_type="account_creation_blocked",
        ),
        BranchRule(
            name="access_denied",
            match_type="page_marker",
            match_value="access denied",
            behavior="halt",
            event_type="access_denied",
        ),
    ]


def _standard_guards() -> list:
    """Guards attached to every pre-completion step, in evaluation order."""
    return _phone_verification_branches() + _captcha_branches() + _abuse_branches()


# --------------------------------------------------------------------------- #
# Step-by-step navigation
# --------------------------------------------------------------------------- #

STEPS: list = [
    WorkflowStep(
        step_id="landing",
        name="Navigate to the Google signup landing page",
        actions=[
            TestAction(
                action_type="navigate",
                value=ENTRY_URL,
                wait_after_ms=2500,
                human_behavior=False,
            ),
            TestAction(action_type="wait", value="1500", human_behavior=False),
            TestAction(action_type="screenshot", human_behavior=False),
        ],
        branches=[
            *_phone_verification_branches(),
            *_captcha_branches(),
            *_abuse_branches(),
        ],
        validation=[{"type": "url_contains", "value": "accounts.google.com"}],
    ),
    WorkflowStep(
        step_id="choose_account_type",
        name="Open 'Create account' and select personal use",
        actions=[
            TestAction(
                action_type="click",
                selector=SEL_CREATE_ACCOUNT,
                wait_after_ms=1500,
            ),
            TestAction(action_type="wait", value="1000", human_behavior=False),
            TestAction(
                action_type="click",
                selector=SEL_CREATE_PERSONAL_USE,
                wait_after_ms=2500,
            ),
        ],
        depends_on=["landing"],
        branches=_standard_guards(),
    ),
    WorkflowStep(
        step_id="enter_name",
        name="Enter first and last name, then continue",
        actions=[
            # Data mapping: {{first_name}} -> First name field,
            # {{last_name}} -> Last name field.
            TestAction(
                action_type="type",
                selector=SEL_FIRST_NAME,
                value="{{first_name}}",
                wait_after_ms=600,
            ),
            TestAction(
                action_type="type",
                selector=SEL_LAST_NAME,
                value="{{last_name}}",
                wait_after_ms=600,
            ),
            TestAction(
                action_type="click",
                selector=SEL_NEXT_BUTTON,
                wait_after_ms=2500,
            ),
        ],
        depends_on=["choose_account_type"],
        branches=_standard_guards(),
        validation=[{"type": "url_contains", "value": "accounts.google.com"}],
    ),
    WorkflowStep(
        step_id="enter_credentials",
        name="Enter desired username and password, then continue",
        actions=[
            # Data mapping: {{username}} -> Username field;
            # {{password}} -> Password + Confirm password fields.
            TestAction(
                action_type="type",
                selector=SEL_USERNAME,
                value="{{username}}",
                wait_after_ms=600,
            ),
            TestAction(
                action_type="type",
                selector=SEL_PASSWORD,
                value="{{password}}",
                wait_after_ms=600,
            ),
            TestAction(
                action_type="type",
                selector=SEL_PASSWORD_CONFIRM,
                value="{{password}}",
                wait_after_ms=600,
            ),
            TestAction(
                action_type="click",
                selector=SEL_NEXT_BUTTON,
                wait_after_ms=2500,
            ),
        ],
        depends_on=["enter_name"],
        max_retries=3,
        branches=_standard_guards()
        + [
            BranchRule(
                name="username_taken",
                match_type="page_marker",
                match_value="username is taken",
                behavior="halt",
                event_type="username_unavailable",
            ),
            BranchRule(
                name="username_taken_alt",
                match_type="page_marker",
                match_value="that username is taken",
                behavior="halt",
                event_type="username_unavailable",
            ),
        ],
    ),
    WorkflowStep(
        step_id="enter_birthday_gender",
        name="Enter birthday and gender, then continue",
        actions=[
            # Data mapping: {{extra:birth_day}} / {{extra:birth_year}} ->
            # birthday fields (month/gender pickers vary by locale, so the
            # flow advances via Next and leaves them for review).
            TestAction(
                action_type="type",
                selector=SEL_BIRTH_DAY,
                value="{{extra:birth_day}}",
                wait_after_ms=400,
            ),
            TestAction(
                action_type="type",
                selector=SEL_BIRTH_YEAR,
                value="{{extra:birth_year}}",
                wait_after_ms=400,
            ),
            TestAction(
                action_type="click",
                selector=SEL_NEXT_BUTTON,
                wait_after_ms=2500,
            ),
        ],
        depends_on=["enter_credentials"],
        branches=_standard_guards(),
    ),
    WorkflowStep(
        step_id="phone_entry",
        name="Handle the phone-number challenge screen",
        actions=[
            # Data mapping: {{extra:phone_number}} -> phone input. Reaching
            # this screen means the verification gate has appeared; the branch
            # rules below HALT with `verification_required` once the gate's
            # markers render, so this step records the attempt either way.
            TestAction(action_type="wait", value="1500", human_behavior=False),
            TestAction(action_type="screenshot", human_behavior=False),
            TestAction(
                action_type="type",
                selector=SEL_PHONE_NUMBER,
                value="{{extra:phone_number}}",
                wait_after_ms=600,
            ),
        ],
        depends_on=["enter_birthday_gender"],
        recovery_url="https://accounts.google.com/lifecycle/steps/signup/phoneverification",
        branches=_standard_guards(),
    ),
    WorkflowStep(
        step_id="confirmation_capture",
        name="Capture final evidence and confirm completion",
        actions=[
            TestAction(action_type="wait", value="1500", human_behavior=False),
            TestAction(action_type="screenshot", human_behavior=False),
        ],
        depends_on=["phone_entry"],
        branches=[
            *_phone_verification_branches(),
            *_captcha_branches(),
            *_abuse_branches(),
        ],
        validation=[{"type": "url_contains", "value": "google.com"}],
    ),
]


# --------------------------------------------------------------------------- #
# Success criteria -- what COMPLETED means for this target
# --------------------------------------------------------------------------- #

SUCCESS_CRITERIA = (
    "COMPLETED means every state-machine step ran in dependency order "
    "(landing -> choose_account_type -> enter_name -> enter_credentials -> "
    "enter_birthday_gender -> phone_entry -> confirmation_capture) with no "
    "HALT and no FAILED state: no CAPTCHA/reCAPTCHA interdiction, no "
    "'Phone Verification' gate, no unusual-traffic or rate-limit block, and "
    "the final page still lives under google.com (post-run "
    "completion_validation: url_contains 'google.com'). Any verification "
    "gate, CAPTCHA, or block yields HALTED (graceful, event-logged) rather "
    "than COMPLETED -- HALT is the expected research outcome on this hardened "
    "target, not a crash."
)

COMPLETION_VALIDATION: list = [
    {"type": "url_contains", "value": "google.com"},
]

EXPECTED_DATA_TOKENS: list = [
    "first_name",
    "last_name",
    "username",
    "password",
    "extra:birth_day",
    "extra:birth_year",
    "extra:phone_number",
]


GOOGLE_REGISTRATION_TARGET = TargetWorkflowConfiguration(
    workflow_id="google_registration",
    target_name="Google account registration",
    description=(
        "Multi-stage Google signup sequence: landing -> Create account "
        "(personal use) -> name entry -> username/password -> birthday/gender "
        "-> phone challenge -> confirmation capture, with halt-on-verification "
        "branch rules at every step."
    ),
    entry_url=ENTRY_URL,
    steps=STEPS,
    fingerprint_profile="residential_windows",
    latency_profile="residential_cable",
    proxy_criteria={"type": "residential"},
    success_criteria=SUCCESS_CRITERIA,
    completion_validation=COMPLETION_VALIDATION,
    expected_data_tokens=EXPECTED_DATA_TOKENS,
    research_notes=(
        "Selectors carry comma-separated fallbacks for A/B DOM variants. "
        "Phone-verification and CAPTCHA branches HALT with "
        "verification_required / captcha_detected events; no bypass is ever "
        "attempted. Username-taken halts as username_unavailable so datasets "
        "distinguish contention from interdiction."
    ),
)

__all__ = [
    "GOOGLE_REGISTRATION_TARGET",
    "COMPLETION_VALIDATION",
    "ENTRY_URL",
    "EXPECTED_DATA_TOKENS",
    "STEPS",
    "SUCCESS_CRITERIA",
]
