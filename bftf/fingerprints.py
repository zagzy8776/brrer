"""Fingerprint generation, validation, and persistence.

Generates internally consistent browser fingerprints for the supported device
profiles (desktop Windows/macOS/Linux and mobile Android/iOS) so that user
agent, platform, screen/viewport, WebGL, and hardware attributes always agree.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import BrowserFingerprint

logger = logging.getLogger(__name__)

# Recent stable Chromium major versions for realistic UA strings.
_CHROME_MAJORS = [126, 127, 128, 129, 130, 131]

DESKTOP_FONTS_WINDOWS = [
    "Arial", "Arial Black", "Calibri", "Cambria", "Comic Sans MS", "Consolas",
    "Courier New", "Georgia", "Impact", "Segoe UI", "Tahoma", "Times New Roman",
    "Trebuchet MS", "Verdana",
]
DESKTOP_FONTS_MACOS = [
    "American Typewriter", "Andale Mono", "Arial", "Avenir", "Avenir Next",
    "Courier New", "Geneva", "Georgia", "Helvetica", "Helvetica Neue",
    "Menlo", "Monaco", "Palatino", "SF Pro Text", "Times New Roman",
]
DESKTOP_FONTS_LINUX = [
    "Cantarell", "DejaVu Sans", "DejaVu Serif", "FreeMono", "Liberation Mono",
    "Liberation Sans", "Liberation Serif", "Noto Sans", "Ubuntu", "Ubuntu Mono",
]
MOBILE_FONTS_ANDROID = ["Roboto", "Noto Sans", "Noto Serif", "Droid Sans", "monospace"]
MOBILE_FONTS_IOS = ["Academy Engraved LET", "Helvetica", "Helvetica Neue", "Arial", "Courier New"]

_PROFILE_DB: Dict[str, Dict[str, Any]] = {
    "residential_windows": {
        "os_variants": ["Windows NT 10.0; Win64; x64"],
        "platform": "Win32",
        "vendor": "Google Inc.",
        "screens": [(1920, 1080), (1366, 768), (1536, 864), (2560, 1440), (1440, 900), (1600, 900)],
        "webgl": [
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00003EA0) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics (0x0000164E) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ],
        "fonts": DESKTOP_FONTS_WINDOWS,
        "hardware_range": (4, 16),
        "memory_choices": [4, 8, 8],
        "touch_points": 0,
        "is_mobile": False,
        "timezones": ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"],
        "languages": [["en-US", "en"]],
    },
    "residential_macos": {
        "os_variants": ["Macintosh; Intel Mac OS X 10_15_7"],
        "platform": "MacIntel",
        "vendor": "Google Inc.",
        "screens": [(1440, 900), (1512, 982), (1680, 1050), (1920, 1080), (2560, 1440)],
        "webgl": [
            ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
            ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2 Pro, Unspecified Version)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics 645, OpenGL 4.1)"),
        ],
        "fonts": DESKTOP_FONTS_MACOS,
        "hardware_range": (4, 12),
        "memory_choices": [8, 8],
        "touch_points": 0,
        "is_mobile": False,
        "timezones": ["America/New_York", "America/Los_Angeles", "Europe/London"],
        "languages": [["en-US", "en"]],
    },
    "residential_linux": {
        "os_variants": ["X11; Linux x86_64"],
        "platform": "Linux x86_64",
        "vendor": "Google Inc.",
        "screens": [(1366, 768), (1600, 900), (1920, 1080), (2560, 1440)],
        "webgl": [
            ("Google Inc. (Mesa)", "ANGLE (Mesa, llvmpipe (LLVM 15.0.7, 256 bits), OpenGL 4.5)"),
            ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 (radeonsi navi23 LLVM 15.0.7), OpenGL 4.6)"),
        ],
        "fonts": DESKTOP_FONTS_LINUX,
        "hardware_range": (4, 16),
        "memory_choices": [4, 8],
        "touch_points": 0,
        "is_mobile": False,
        "timezones": ["America/New_York", "Europe/Berlin", "Europe/London", "America/Los_Angeles"],
        "languages": [["en-US", "en"], ["en-GB", "en"]],
    },
    "mobile_android": {
        "os_variants": ["Linux; Android 13; Pixel 7", "Linux; Android 14; Pixel 8", "Linux; Android 13; SM-G991B"],
        "platform": "Linux armv8l",
        "vendor": "Google Inc.",
        "screens": [(360, 800), (384, 854), (412, 915), (360, 780)],
        "webgl": [
            ("Google Inc. (Qualcomm)", "ANGLE (Qualcomm, Adreno (TM) 730, OpenGL ES 3.2)"),
            ("Google Inc. (ARM)", "ANGLE (ARM, Mali-G715-Immortalis, OpenGL ES 3.2)"),
        ],
        "fonts": MOBILE_FONTS_ANDROID,
        "hardware_range": (6, 8),
        "memory_choices": [4, 8],
        "touch_points": 5,
        "is_mobile": True,
        "timezones": ["America/New_York", "America/Chicago", "America/Los_Angeles"],
        "languages": [["en-US", "en"]],
    },
    "mobile_ios": {
        "os_variants": ["iPhone; CPU iPhone OS 17_5 like Mac OS X", "iPhone; CPU iPhone OS 16_6 like Mac OS X"],
        "platform": "iPhone",
        "vendor": "Apple Computer, Inc.",
        "screens": [(390, 844), (393, 852), (414, 896), (375, 812)],
        "webgl": [
            ("Apple Inc.", "Apple GPU"),
            ("Apple Inc.", "Apple A16 GPU"),
        ],
        "fonts": MOBILE_FONTS_IOS,
        "hardware_range": (4, 6),
        "memory_choices": [4],
        "touch_points": 5,
        "is_mobile": True,
        "timezones": ["America/New_York", "America/Los_Angeles"],
        "languages": [["en-US", "en"]],
    },
}

_UA_TEMPLATES = {
    "residential_windows": "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{cv}.0.0.0 Safari/537.36",
    "residential_macos": "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{cv}.0.0.0 Safari/537.36",
    "residential_linux": "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{cv}.0.0.0 Safari/537.36",
    "mobile_android": "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{cv}.0.0.0 Mobile Safari/537.36",
    "mobile_ios": "Mozilla/5.0 ({os}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
}


class FingerprintManager:
    """Manages fingerprint generation, validation, and persistence."""

    def __init__(self, fingerprint_db_path: Optional[str] = None, seed: Optional[int] = None) -> None:
        """
        Args:
            fingerprint_db_path: optional path to a JSON file with extra profiles.
            seed: optional RNG seed for reproducible fingerprint generation.
        """
        self._rng = random.Random(seed)
        self._profiles: Dict[str, Dict[str, Any]] = dict(_PROFILE_DB)
        self._seed = seed
        if fingerprint_db_path:
            self._load_extra_profiles(fingerprint_db_path)

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #
    def generate_fingerprint(
        self,
        profile: str = "residential_windows",
        consistency_level: str = "high",
    ) -> BrowserFingerprint:
        """Generate a realistic browser fingerprint matching a device profile.

        Args:
            profile: profile name (e.g. 'residential_windows', 'mobile_android').
            consistency_level: 'low' | 'medium' | 'high' (validation strictness).
        """
        if profile not in self._profiles:
            raise KeyError(f"Unknown fingerprint profile '{profile}'. Available: {sorted(self._profiles)}")
        if consistency_level not in ("low", "medium", "high"):
            raise ValueError("consistency_level must be 'low', 'medium', or 'high'")

        cfg = self._profiles[profile]
        chrome_major = self._rng.choice(_CHROME_MAJORS)
        user_agent = _UA_TEMPLATES[profile].format(
            os=self._rng.choice(cfg["os_variants"]), cv=chrome_major
        )
        platform = cfg["platform"]

        screen_w, screen_h = self._rng.choice(cfg["screens"])
        screen = {"width": screen_w, "height": screen_h}
        if cfg["is_mobile"]:
            viewport = {"width": screen_w, "height": screen_h}
        else:
            # Chrome window chrome subtracts vertical space; sometimes smaller windows.
            if self._rng.random() < 0.3:
                vw = self._rng.randint(max(720, screen_w // 2), screen_w)
                vh = self._rng.randint(max(540, screen_h // 2), screen_h)
            else:
                vw, vh = screen_w, screen_h - self._rng.randint(70, 140)
            viewport = {"width": vw, "height": vh}

        webgl_vendor, webgl_renderer = self._rng.choice(cfg["webgl"])

        hash_seed = f"{user_agent}|{screen_w}x{screen_h}|{time.time_ns()}"
        canvas_hash = hashlib.sha256(f"canvas:{hash_seed}".encode()).hexdigest()[:32]
        audio_hash = hashlib.sha256(f"audio:{hash_seed}".encode()).hexdigest()[:32]

        plugins: List[Dict[str, str]] = []
        if not cfg["is_mobile"]:
            plugins = [
                {"name": "PDF Viewer", "description": "Portable Document Format", "type": "application/pdf"},
                {"name": "Chrome PDF Viewer", "description": "Portable Document Format", "type": "application/pdf"},
            ]

        fingerprint = BrowserFingerprint(
            user_agent=user_agent,
            platform=platform,
            vendor=cfg["vendor"],
            renderer=webgl_renderer.split(",")[0].strip(),
            languages=list(self._rng.choice(cfg["languages"])),
            screen=screen,
            viewport=viewport,
            timezone=self._rng.choice(cfg["timezones"]),
            webgl_vendor=webgl_vendor,
            webgl_renderer=webgl_renderer,
            canvas_hash=canvas_hash,
            audio_hash=audio_hash,
            fonts=list(cfg["fonts"]),
            plugins=plugins,
            hardware_concurrency=self._rng.randint(*cfg["hardware_range"]),
            device_memory=self._rng.choice(cfg["memory_choices"]),
            max_touch_points=cfg["touch_points"],
        )

        if consistency_level in ("medium", "high") and not self.validate_fingerprint(fingerprint):
            issues = self.get_validation_issues(fingerprint)
            raise RuntimeError(f"Generated fingerprint failed validation: {issues}")
        return fingerprint

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def get_validation_issues(self, fingerprint: BrowserFingerprint) -> List[str]:
        """Return a list of consistency problems (empty list = valid)."""
        issues: List[str] = []
        ua = fingerprint.user_agent
        platform = fingerprint.platform

        expected = {
            "Win32": ("Windows",),
            "MacIntel": ("Macintosh", "Mac OS X"),
            "Linux x86_64": ("X11", "Linux"),
            "Linux armv8l": ("Android",),
            "iPhone": ("iPhone",),
        }.get(platform)
        if expected is None:
            issues.append(f"unknown platform '{platform}'")
        elif not any(token in ua for token in expected):
            issues.append(f"platform '{platform}' does not match user agent")

        if fingerprint.viewport["width"] > fingerprint.screen["width"]:
            issues.append("viewport wider than screen")
        if fingerprint.viewport["height"] > fingerprint.screen["height"]:
            issues.append("viewport taller than screen")

        if fingerprint.device_memory < 1 or fingerprint.device_memory > 8:
            issues.append("device_memory out of realistic range [1, 8]")
        if fingerprint.hardware_concurrency < 1:
            issues.append("hardware_concurrency must be >= 1")

        if not fingerprint.languages or not fingerprint.languages[0]:
            issues.append("languages must be non-empty")
        elif "-" in fingerprint.languages[0] and len(fingerprint.languages[0].split("-")[0]) != 2:
            issues.append(f"invalid locale '{fingerprint.languages[0]}'")

        is_mobile = platform in ("Linux armv8l", "iPhone")
        if is_mobile and fingerprint.max_touch_points < 1:
            issues.append("mobile profiles require touch points >= 1")
        if not is_mobile and fingerprint.max_touch_points != 0:
            issues.append("desktop profiles should have max_touch_points == 0")
        if is_mobile and fingerprint.viewport != fingerprint.screen:
            issues.append("mobile viewport must match screen")

        if not fingerprint.webgl_vendor or not fingerprint.webgl_renderer:
            issues.append("WebGL vendor/renderer must be non-empty")
        if not fingerprint.canvas_hash or not fingerprint.audio_hash:
            issues.append("canvas/audio hashes must be non-empty")
        return issues

    def validate_fingerprint(self, fingerprint: BrowserFingerprint) -> bool:
        """Validate fingerprint for consistency and realism."""
        return not self.get_validation_issues(fingerprint)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def save_fingerprint_to_file(self, fingerprint: BrowserFingerprint, filepath: str) -> None:
        """Persist a fingerprint to JSON for reproducible test runs."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(fingerprint.to_dict(), fh, indent=2)

    def load_fingerprint_from_file(self, filepath: str) -> BrowserFingerprint:
        """Load a previously generated fingerprint from JSON."""
        with open(filepath, "r", encoding="utf-8") as fh:
            return BrowserFingerprint.from_dict(json.load(fh))

    def _load_extra_profiles(self, db_path: str) -> None:
        try:
            with open(db_path, "r", encoding="utf-8") as fh:
                extra = json.load(fh)
            for name, cfg in extra.items():
                base = dict(_PROFILE_DB[cfg.get("base", "residential_windows")])
                base.update({k: v for k, v in cfg.items() if k != "base"})
                self._profiles[name] = base
        except FileNotFoundError:
            logger.warning("Fingerprint DB '%s' not found; using built-in profiles", db_path)

