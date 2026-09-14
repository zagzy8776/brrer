# Browser Fingerprint Testing Framework (BFTF)

A modular Python/Playwright framework for cybersecurity research into **automated
browser fingerprint testing** and **network latency emulation**. It drives
Chromium sessions with the `playwright-stealth` evasions, applies internally
consistent browser fingerprints, routes traffic through configurable proxy
pools, emulates realistic network latency via the Chrome DevTools Protocol, and
scores how target pages respond (validation rules + anti-bot interdiction
markers).

## Architecture

```
TestOrchestrator ─┬─ FingerprintManager   (consistent fingerprint generation + validation)
                  ├─ ProxyManager         (pool, criteria selection, rotation, health checks)
                  ├─ BrowserController    (Playwright + stealth, human mouse/typing/scroll)
                  ├─ NetworkEmulator      (CDP latency/jitter/bandwidth emulation)
                  └─ SessionManager       (lifecycle, events, detection scoring, reports)
```

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
```

Python 3.10+ required (developed on 3.12).

## Quick start

```bash
# No proxy needed - direct connection sanity test
python examples/basic_test.py

# Phase 2: live two-step registration-style workflow (state machine)
python examples/registration_workflow.py

# Full profile/latency comparison (needs proxies.json)
copy proxies.example.json proxies.json   # then fill in your endpoints
python examples/profile_comparison.py
```

Programmatic use:

```python
import asyncio
from bftf import (
    BrowserController, FingerprintManager, NetworkEmulator,
    SessionManager, TestOrchestrator, TestAction, TestScenario,
    UserProfile, WorkflowOrchestrator, WorkflowStep,
)

async def main():
    orch = TestOrchestrator(
        fingerprint_manager=FingerprintManager(seed=42),
        proxy_manager=None,                       # or ProxyManager("proxies.json")
        browser_controller=BrowserController(),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager("./output"),
    )
    scenario = TestScenario(
        scenario_id="t1", scenario_name="probe",
        target_url="https://example.com",
        fingerprint_profile="residential_windows",
        latency_profile="residential_cable",
        actions=[TestAction(action_type="navigate", value="https://example.com")],
        validation_rules=[{"type": "url_contains", "value": "example.com"}],
    )
    result = await orch.execute_single_test(scenario)
    print(result.success, result.detection_score)

asyncio.run(main())
```

Multi-step registration workflow (Phase 2, state machine with recovery):

```python
async def register():
    orch = WorkflowOrchestrator(
        fingerprint_manager=FingerprintManager(seed=42),
        proxy_manager=None,
        browser_controller=BrowserController(),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager("./output"),
    )
    steps = [
        WorkflowStep("signup", "Enter account details", [
            TestAction(action_type="navigate", value="https://example.com"),
            TestAction(action_type="type", selector="#email",
                       value="{{email}}", human_behavior=True),
            TestAction(action_type="click", selector="#next", human_behavior=True),
        ]),
        # Runs only after 'signup' completes; halts gracefully on
        # CAPTCHA/verification pages instead of crashing.
        WorkflowStep("confirm", "Capture confirmation", [
            TestAction(action_type="screenshot", human_behavior=False),
        ], depends_on=["signup"]),
    ]
    profile = UserProfile(profile_id="u1", email="ada@example.test",
                          username="ada_42", password="s3cret!",
                          first_name="Ada", last_name="Lovelace")
    wf = orch.create_registration_workflow("reg_flow", steps)
    result = await orch.execute_registration_workflow(wf, profile)
    print(result.final_state, result.completed_steps)
```

## Components

| Module | Responsibility |
|---|---|
| `bftf/models.py` | Dataclasses: fingerprints, proxies, latency profiles, test configs/results |
| `bftf/fingerprints.py` | Profile-based fingerprint generation (Windows/macOS/Linux/Android/iOS), consistency validation, JSON persistence |
| `bftf/proxies.py` | Proxy pool from `proxies.json` (`${ENV_VAR}` credential expansion), criteria-based selection, rotation, async health checks, statistics |
| `bftf/browser.py` | Stealth browser launch, fingerprint-applied contexts, Bezier mouse movement, WPM typing with corrected typos, smooth scrolling |
| `bftf/network.py` | Latency presets (residential fiber/cable/DSL, mobile 4G/3G) applied via CDP `Network.emulateNetworkConditions` |
| `bftf/sessions.py` | Session lifecycle, interaction/detection-event recording, detection scoring, JSON reports |
| `bftf/orchestrator.py` | Suite/scenario execution, action dispatch (`navigate/click/type/scroll/wait/screenshot`), validation rules, block-indicator scanning |
| `bftf/workflows.py` | Phase 2 state-machine registration workflows: step dependencies, `{{token}}` data injection from `UserProfile`, snapshot persistence & retry recovery, conditional `BranchRule`s, halt-instead-of-crash interdiction handling |

### Fingerprint profiles
`residential_windows`, `residential_macos`, `residential_linux`,
`mobile_android`, `mobile_ios` — every generated fingerprint is validated for
internal consistency (UA ↔ platform, screen ↔ viewport, touch points, WebGL
vendor/renderer, realistic hardware values).

### Latency profiles
`none`, `residential_fiber`, `residential_cable`, `residential_dsl`,
`mobile_4g`, `mobile_3g`, plus custom profiles via
`NetworkEmulator.create_latency_profile(...)`. Latency and bandwidth are
enforced per-page through CDP; packet-loss rate is reported for downstream
analysis (CDP cannot drop individual packets).

### Validation rules
`url_contains`, `url_equals`, `title_contains`, `element_exists`,
`element_not_exists`, `element_text_contains`.

### Detection scoring
Sessions accumulate *detection events* (page content scanned for captcha /
"unusual traffic" / "access denied" markers, plus failures) and each ended
session receives a score in `[0.0, 1.0]` — higher means more likely to have
been flagged.

## Testing

```bash
python -m pytest
```

## Security & ethics

- **Proxy credentials** are read from `proxies.json` with `${ENV_VAR}` expansion;
  passwords are redacted in all serialized output. Never commit `proxies.json`.
- **Only test services you are authorized to test** and in line with their terms
  of service and applicable law; keep request rates conservative.
- Session data and screenshots are written under `./output/` — treat as
  sensitive and sanitize before sharing.
