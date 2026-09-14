"""Tests for core data model validation."""
from datetime import datetime

import pytest

from bftf.fingerprints import FingerprintManager
from bftf.models import (
    HumanBehaviorConfig,
    LatencyProfile,
    ProxyConfig,
    ProxyType,
    SessionData,
    TestAction,
    TestScenario,
)


def test_proxy_type_from_string():
    assert ProxyConfig.from_dict({"host": "h", "port": 1, "proxy_type": "mobile"}).proxy_type == ProxyType.MOBILE
    with pytest.raises(ValueError):
        ProxyConfig.from_dict({"host": "h", "port": 1, "proxy_type": "satellite"})


def test_test_action_validation():
    with pytest.raises(ValueError):
        TestAction(action_type="hover")  # invalid type
    with pytest.raises(ValueError):
        TestAction(action_type="type", selector="#x")  # missing value
    with pytest.raises(ValueError):
        TestAction(action_type="click")  # missing selector
    TestAction(action_type="navigate", value="https://example.com").validate()


def test_test_scenario_validation():
    with pytest.raises(ValueError):
        TestScenario("s1", "name", "ftp://bad", "residential_windows",
                     actions=[TestAction(action_type="wait")])
    with pytest.raises(ValueError):
        TestScenario("s1", "name", "https://ok.com", "residential_windows", actions=[])


def test_human_behavior_config_validation():
    with pytest.raises(ValueError):
        HumanBehaviorConfig(typing_speed_wpm=0)
    with pytest.raises(ValueError):
        HumanBehaviorConfig(scroll_behavior="sideways")
    with pytest.raises(ValueError):
        HumanBehaviorConfig(think_time_range_sec=(3.0, 1.0))
    cfg = HumanBehaviorConfig()
    assert cfg.to_dict()["think_time_range_sec"] == [1.0, 3.0]


def test_session_data_duration():
    fp = FingerprintManager(seed=1).generate_fingerprint("residential_windows")
    start = datetime(2026, 1, 1, 12, 0, 0)
    session = SessionData("s", start, fp)
    assert session.duration_sec == 0.0
    session.end_time = datetime(2026, 1, 1, 12, 1, 0)
    assert session.duration_sec == 60.0


def test_latency_profile_bounds():
    LatencyProfile("ok", 10, 50).validate()
    with pytest.raises(ValueError):
        LatencyProfile("neg", -5, 50).validate()
