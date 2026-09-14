"""Tests for session lifecycle, scoring, and persistence."""
import json

import pytest

from bftf.fingerprints import FingerprintManager
from bftf.models import LatencyProfile, ProxyConfig
from bftf.sessions import SessionManager


@pytest.fixture
def fingerprint():
    return FingerprintManager(seed=7).generate_fingerprint("residential_windows")


@pytest.fixture
def manager(tmp_path):
    return SessionManager(str(tmp_path / "out"))


def test_session_lifecycle(manager, fingerprint):
    proxy = ProxyConfig(host="1.2.3.4", port=8080)
    profile = LatencyProfile("residential_cable", 15, 40, 5)
    manager.start_session("s1", fingerprint, proxy=proxy, latency_profile=profile)
    manager.record_interaction("s1", "navigate", {"url": "https://example.com", "load_time_ms": 120.0})
    session = manager.end_session("s1", success=True)

    assert session.end_time is not None
    assert session.success is True
    assert session.duration_sec >= 0
    assert session.interactions[0]["url"] == "https://example.com"


def test_unknown_session_raises(manager, fingerprint):
    with pytest.raises(KeyError):
        manager.record_interaction("nope", "click", {})


def test_detection_score_bounds(manager, fingerprint):
    manager.start_session("s2", fingerprint)
    for event in ("captcha", "blocked", "rate_limited", "challenge"):
        manager.record_detection_event("s2", event, {"marker": event})
    manager.end_session("s2", success=False)
    session = manager.get_session("s2")
    score = manager.calculate_detection_score(session)
    assert 0.0 <= score <= 1.0
    assert score == 1.0  # heavy interdiction saturates the score


def test_clean_session_low_score(manager, fingerprint):
    manager.start_session("s3", fingerprint)
    manager.record_interaction("s3", "navigate", {"url": "https://example.com", "load_time_ms": 100.0})
    manager.end_session("s3", success=True)
    score = manager.calculate_detection_score(manager.get_session("s3"))
    assert score == 0.0


def test_score_requires_ended_session(manager, fingerprint):
    manager.start_session("s4", fingerprint)
    with pytest.raises(ValueError):
        manager.calculate_detection_score(manager.get_session("s4"))


def test_persistence(manager, fingerprint, tmp_path):
    manager.start_session("s5", fingerprint)
    manager.record_interaction("s5", "click", {"selector": "#go"})
    session = manager.end_session("s5", success=True)
    path = manager.sessions_dir / "s5.json"
    assert path.exists()

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["session_id"] == "s5"
    assert data["success"] is True
    assert data["proxy"] is None
    assert data["fingerprint"]["platform"] == "Win32"


def test_analyze_sessions(manager, fingerprint):
    for sid, ok in (("a", True), ("b", False)):
        manager.start_session(sid, fingerprint)
        if not ok:
            manager.record_detection_event(sid, "captcha", {})
        manager.end_session(sid, success=ok)
    report = manager.analyze_sessions()
    assert report["total_sessions"] == 2
    assert report["success_rate"] == 0.5
    assert report["detection_event_types"]["captcha"] == 1
    assert report["avg_detection_score"] > 0


def test_performance_metrics(manager, fingerprint):
    manager.start_session("p1", fingerprint)
    manager.record_interaction("p1", "navigate", {"load_time_ms": 100.0})
    manager.record_interaction("p1", "navigate", {"load_time_ms": 200.0})
    manager.end_session("p1", success=True)
    metrics = manager.calculate_performance_metrics(manager.get_session("p1"))
    assert metrics["avg_load_time_ms"] == pytest.approx(150.0)
    assert metrics["interaction_count"] == 2


# --------------------------------------------------------------------------- #
# Redacted proxy logging via proxy_resolved
# --------------------------------------------------------------------------- #
def test_proxy_resolved_redacted_in_session(manager, fingerprint, tmp_path):
    """When proxy_resolved is set, only host:port appears in the serialised
    artefact — credentials must never leak."""
    proxy = ProxyConfig(
        host="1.2.3.4", port=8080,
        username="leak_user", password="leak_pass",
    )
    session = manager.start_session(
        "redact", fingerprint, proxy_resolved=proxy,
    )
    assert session.proxy is proxy
    # Serialise to disk and read back.
    manager.end_session("redact", success=True)
    with open(manager.sessions_dir / "redact.json", encoding="utf-8") as fh:
        data = json.load(fh)
    proxy_data = data["proxy"]
    assert proxy_data["host"] == "1.2.3.4"
    assert proxy_data["port"] == 8080
    assert proxy_data["username"] == "***"
    assert proxy_data["password"] == "***"
    blob = json.dumps(data)
    assert "leak_pass" not in blob


def test_proxy_resolved_none_when_absent(manager, fingerprint):
    session = manager.start_session("none_proxy", fingerprint)
    assert session.proxy is None
