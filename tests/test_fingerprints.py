"""Tests for fingerprint generation and validation."""
import pytest

from bftf.fingerprints import FingerprintManager
from bftf.models import BrowserFingerprint

ALL_PROFILES = [
    "residential_windows",
    "residential_macos",
    "residential_linux",
    "mobile_android",
    "mobile_ios",
]


@pytest.fixture
def manager() -> FingerprintManager:
    return FingerprintManager(seed=42)


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_generate_valid_fingerprint(manager, profile):
    fp = manager.generate_fingerprint(profile=profile)
    assert isinstance(fp, BrowserFingerprint)
    assert manager.validate_fingerprint(fp), manager.get_validation_issues(fp)


def test_fingerprint_is_chrome_ua(manager):
    fp = manager.generate_fingerprint("residential_windows")
    assert "Chrome/" in fp.user_agent
    assert "Windows" in fp.user_agent
    assert fp.platform == "Win32"


def test_mobile_viewport_matches_screen(manager):
    fp = manager.generate_fingerprint("mobile_android")
    assert fp.viewport == fp.screen
    assert fp.max_touch_points >= 1


def test_desktop_touch_points_zero(manager):
    fp = manager.generate_fingerprint("residential_macos")
    assert fp.max_touch_points == 0


def test_hashes_are_hex(manager):
    fp = manager.generate_fingerprint("residential_linux")
    int(fp.canvas_hash, 16)
    int(fp.audio_hash, 16)


def test_validation_detects_inconsistencies(manager):
    fp = manager.generate_fingerprint("residential_windows")
    fp.platform = "MacIntel"  # now inconsistent with Windows UA
    assert not manager.validate_fingerprint(fp)
    issues = manager.get_validation_issues(fp)
    assert any("user agent" in i for i in issues)


def test_validation_rejects_oversized_viewport(manager):
    fp = manager.generate_fingerprint("residential_windows")
    fp.viewport = {"width": fp.screen["width"] + 1, "height": fp.screen["height"]}
    assert not manager.validate_fingerprint(fp)


def test_save_load_roundtrip(manager, tmp_path):
    fp = manager.generate_fingerprint("residential_windows")
    path = tmp_path / "fp.json"
    manager.save_fingerprint_to_file(fp, str(path))
    loaded = manager.load_fingerprint_from_file(str(path))
    assert loaded == fp


def test_unknown_profile_raises(manager):
    with pytest.raises(KeyError):
        manager.generate_fingerprint("does_not_exist")


def test_invalid_consistency_level(manager):
    with pytest.raises(ValueError):
        manager.generate_fingerprint("residential_windows", consistency_level="ultra")
