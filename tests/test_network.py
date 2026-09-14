"""Tests for the network latency emulator (profile logic, no browser needed)."""
import pytest

from bftf.network import PRESET_PROFILES, NetworkEmulator
from bftf.models import LatencyProfile


@pytest.fixture
def emulator() -> NetworkEmulator:
    return NetworkEmulator()


def test_all_presets_exist(emulator):
    expected = {"none", "residential_fiber", "residential_cable", "residential_dsl", "mobile_4g", "mobile_3g"}
    assert expected <= set(PRESET_PROFILES)
    for name in expected:
        profile = emulator.get_preset_profile(name)
        assert profile.name == name


def test_unknown_profile_raises(emulator):
    with pytest.raises(KeyError):
        emulator.get_preset_profile("dial_up")


def test_delay_within_bounds(emulator):
    profile = emulator.get_preset_profile("residential_cable")
    for _ in range(200):
        delay = emulator.calculate_realistic_delay(profile)
        assert profile.min_latency_ms - 3 * profile.jitter_ms <= delay
        assert delay <= profile.max_latency_ms + 3 * profile.jitter_ms


def test_none_profile_zero_delay(emulator):
    assert emulator.calculate_realistic_delay(emulator.get_preset_profile("none")) == 0.0


def test_create_custom_profile(emulator):
    profile = emulator.create_latency_profile(
        "lab_5g", {"min_latency_ms": 8, "max_latency_ms": 20, "jitter_ms": 3,
                   "packet_loss_rate": 0.0, "download_speed_mbps": 500, "upload_speed_mbps": 100}
    )
    assert emulator.get_preset_profile("lab_5g") is profile


def test_latency_profile_validation():
    with pytest.raises(ValueError):
        LatencyProfile("bad", 100, 50)
    with pytest.raises(ValueError):
        LatencyProfile("bad", 0, 50, packet_loss_rate=1.5)


def test_throughput_conversion():
    profile = PRESET_PROFILES["residential_cable"]
    # 100 Mbps == 12.5 MB/s
    assert profile.download_speed_mbps * 1_000_000 / 8 == pytest.approx(12_500_000)
