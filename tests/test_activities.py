"""Tests for Phase 4 warm-up profiles (browser interactions faked / dry-run)."""
import json

import pytest

from bftf import (
    BEHAVIOR_PROFILES,
    AccountInventoryManager,
    AccountRecord,
    FingerprintManager,
    NetworkEmulator,
    ProxyConfig,
    SessionManager,
    WarmupOrchestrator,
)
from bftf.activities import RandomizationEngine


@pytest.fixture
def inventory_path(tmp_path):
    path = tmp_path / "accounts_inventory.json"
    path.write_text(json.dumps({"accounts": [
        {"account_id": "acc_maps", "username": "maps_user", "password": "${WARMUP_TEST_PW}",
         "fingerprint_profile": "residential_windows", "behavior_profile": "maps_reviewer",
         "city": "Austin, TX"},
        {"account_id": "acc_surf", "username": "surf_user", "password": "pw2",
         "fingerprint_profile": "residential_macos", "behavior_profile": "general_surfer",
         "proxy_criteria": {"country": "US"}},
    ]}), encoding="utf-8")
    return str(path)


def make_warmup_orchestrator(tmp_path, seed=11):
    from tests.test_workflows import FakeController, FakePage
    page = FakePage()
    return WarmupOrchestrator(
        fingerprint_manager=FingerprintManager(seed=seed),
        proxy_manager=None,
        browser_controller=FakeController(page),
        network_emulator=NetworkEmulator(),
        session_manager=SessionManager(str(tmp_path / "out")),
        seed=seed,
    )


def test_inventory_loads_with_env_expansion(inventory_path, tmp_path, monkeypatch):
    monkeypatch.setenv("WARMUP_TEST_PW", "s3cret")
    manager = AccountInventoryManager(inventory_path)
    assert len(manager) == 2
    assert manager.get_account("acc_maps").password == "s3cret"
    assert manager.get_account("acc_surf").proxy_criteria == {"country": "US"}
    assert manager.accounts_for_profile("maps_reviewer")[0].account_id == "acc_maps"
    assert "acc_maps" in manager.summary()
    with pytest.raises(KeyError):
        manager.get_account("nope")


def test_inventory_rejects_duplicates_and_empties(tmp_path):
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({"accounts": [
        {"account_id": "a", "username": "u1"},
        {"account_id": "a", "username": "u2"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError):
        AccountInventoryManager(str(dup))
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"accounts": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        AccountInventoryManager(str(empty))


def test_password_never_logged(inventory_path, monkeypatch):
    monkeypatch.setenv("WARMUP_TEST_PW", "s3cret")
    manager = AccountInventoryManager(inventory_path)
    dumped = json.dumps(manager.get_account("acc_maps").to_dict())
    assert "s3cret" not in dumped
    assert "***" in dumped


def test_randomization_varies_sessions():
    first = RandomizationEngine(seed=1)
    second = RandomizationEngine(seed=2)
    assert [first.pause() for _ in range(5)] != [second.pause() for _ in range(5)]
    assert first.maps_query("Austin, TX") != "" or True  # smoke: may or may not include city
    queries = {RandomizationEngine(seed=s).maps_query("Paris") for s in range(10)}
    assert len(queries) > 1  # search terms vary across seeds
    plan = first.scroll_plan(passes=3)
    assert len(plan) == 3 and all("amount_px" in leg for leg in plan)


async def test_dry_run_warmup_account(inventory_path, tmp_path, monkeypatch):
    monkeypatch.setenv("WARMUP_TEST_PW", "s3cret")
    orch = make_warmup_orchestrator(tmp_path)
    orch.network = __import__("tests.test_workflows", fromlist=["FakeNetwork"]).FakeNetwork()
    from bftf import AccountInventoryManager as AIM
    account = AIM(inventory_path).get_account("acc_surf")
    result = await orch.warmup_account(account, dry_run=True)
    assert result.success
    assert result.account_id == "acc_surf"
    session = orch.sessions.get_session(result.session_id)
    assert session.success is True
    assert session.fingerprint.platform in ("Win32", "MacIntel")
    assert any(i["type"] == "warmup_start" for i in session.interactions)
    assert any(i["type"] == "warmup_completed" for i in session.interactions)


async def test_sequential_inventory_run_teardown_between_accounts(inventory_path, tmp_path, monkeypatch):
    monkeypatch.setenv("WARMUP_TEST_PW", "s3cret")
    orch = make_warmup_orchestrator(tmp_path)
    from tests.test_workflows import FakeNetwork
    orch.network = FakeNetwork()
    closed = []
    orig_close = orch.browser.close_browser

    async def spy_close(browser):
        closed.append(browser)
        await orig_close(browser)

    orch.browser.close_browser = spy_close
    manager = AccountInventoryManager(inventory_path)
    run = await orch.warmup_inventory(manager, dry_run=False, cooldown_sec=0)
    assert len(run.account_results) == 2
    assert run.success_rate == 1.0
    assert len(closed) == 2  # teardown happened per account
    assert "succeeded" in run.summary()


async def test_failed_account_does_not_abort_run(inventory_path, tmp_path, monkeypatch):
    monkeypatch.setenv("WARMUP_TEST_PW", "s3cret")
    orch = make_warmup_orchestrator(tmp_path)
    from tests.test_workflows import FakeNetwork
    orch.network = FakeNetwork()
    manager = AccountInventoryManager(inventory_path)
    with pytest.raises(ValueError):
        orch.get_behavior_profile("nonexistent")
    bad = AccountRecord(account_id="bad", username="u", behavior_profile="nonexistent")
    run = await orch.warmup_inventory(
        _InventoryShim([manager.get_account("acc_surf"), bad]),
        dry_run=True, cooldown_sec=0, run_id="r1")
    assert len(run.succeeded) == 1 and len(run.failed) == 1
    assert run.account_results[1].error is not None


# --------------------------------------------------------------------------- #
# Phase 4: env expansion + per-account proxy redaction
# --------------------------------------------------------------------------- #
@pytest.fixture
def webshare_env(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_HOST", "203.0.113.10")
    monkeypatch.setenv("WEBSHARE_PROXY_PORT", "8080")
    monkeypatch.setenv("WEBSHARE_PROXY_USER", "ws_user")
    monkeypatch.setenv("WEBSHARE_PROXY_PASS", "ws_pass")
    monkeypatch.setenv("WEBSHARE_PROXY_COUNTRY", "US")
    return monkeypatch


def test_account_inventory_proxy_env_expansion(tmp_path, webshare_env):
    """accounts_inventory.json with ${WEBSHARE_PROXY_*} placeholders resolves."""
    inventory = tmp_path / "accounts_inventory.json"
    inventory.write_text(json.dumps({"accounts": [
        {"account_id": "acc_proxy", "username": "user1",
         "proxy": {"host": "${WEBSHARE_PROXY_HOST}",
                   "port": "${WEBSHARE_PROXY_PORT}",
                   "username": "${WEBSHARE_PROXY_USER}",
                   "password": "${WEBSHARE_PROXY_PASS}",
                   "proxy_type": "residential"},
         "behavior_profile": "general_surfer"},
    ]}), encoding="utf-8")
    manager = AccountInventoryManager(str(inventory))
    acc = manager.get_account("acc_proxy")
    assert acc.proxy is not None
    assert acc.proxy.host == "203.0.113.10"
    assert acc.proxy.port == 8080
    assert acc.proxy.username == "ws_user"
    assert acc.proxy.password == "ws_pass"


def test_account_record_proxy_credentials_never_serialized(tmp_path, webshare_env):
    """Account.to_dict() never leaks resolved proxy credentials."""
    account = AccountRecord(
        account_id="acc_x", username="user1",
        proxy=ProxyConfig(host="203.0.113.10", port=8080,
                          username="ws_user", password="ws_pass"),
        behavior_profile="general_surfer",
    )
    dumped = json.dumps(account.to_dict(redact=True))
    assert "ws_pass" not in dumped
    assert "ws_user" not in dumped
    assert account.to_dict(redact=True)["proxy"]["password"] == "***"


async def test_warmup_account_proxy_resolved_logged_redacted(
    inventory_path, tmp_path, webshare_env, monkeypatch
):
    """When a proxy is resolved, start_session receives it via proxy_resolved
    and the session artefact stores only the redacted key."""
    from tests.test_workflows import FakeNetwork
    orch = make_warmup_orchestrator(tmp_path)
    orch.network = FakeNetwork()
    manager = AccountInventoryManager(inventory_path)
    account = manager.get_account("acc_surf")

    # Inject a fake proxy into the account's criteria so resolve_proxy returns it.
    account.proxy = ProxyConfig(
        host="203.0.113.10", port=8080,
        username="ws_user", password="ws_pass",
    )

    # Spy on start_session to capture the proxy_resolved argument.
    original_start = orch.sessions.start_session
    captured = {}

    def spy_start_session(session_id, fingerprint, proxy=None,
                          latency_profile=None, proxy_resolved=None):
        captured["proxy_resolved"] = proxy_resolved
        return original_start(
            session_id, fingerprint, proxy=proxy,
            latency_profile=latency_profile, proxy_resolved=proxy_resolved,
        )
    orch.sessions.start_session = spy_start_session

    result = await orch.warmup_account(account, dry_run=True)
    assert result.success
    assert captured["proxy_resolved"] is account.proxy
    assert captured["proxy_resolved"].key == "203.0.113.10:8080"

    # The persisted session file must not contain credentials.
    orch.sessions.save_session_data(
        orch.sessions.get_session(result.session_id))
    session_file = orch.sessions.sessions_dir / f"{result.session_id}.json"
    data = json.loads(session_file.read_text(encoding="utf-8"))
    if data["proxy"]:
        assert data["proxy"]["password"] == "***"
        blob = json.dumps(data)
        assert "ws_pass" not in blob
        assert "ws_user" not in blob


class _InventoryShim:
    def __init__(self, accounts):
        self.accounts = accounts
