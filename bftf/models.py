"""Core data models for the Browser Fingerprint Testing Framework (BFTF).

All models are plain dataclasses (no external dependencies) so they can be
serialized to JSON for reproducible research artefacts.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ProxyType(str, Enum):
    """Supported proxy categories."""

    RESIDENTIAL = "residential"
    DATACENTER = "datacenter"
    MOBILE = "mobile"


@dataclass
class BrowserFingerprint:
    """Complete, internally consistent browser fingerprint."""

    user_agent: str
    platform: str
    vendor: str
    renderer: str
    languages: List[str]
    screen: Dict[str, int]
    viewport: Dict[str, int]
    timezone: str
    webgl_vendor: str
    webgl_renderer: str
    canvas_hash: str
    audio_hash: str
    fonts: List[str]
    plugins: List[Dict[str, str]]
    hardware_concurrency: int
    device_memory: int
    max_touch_points: int

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserFingerprint":
        return cls(**data)


@dataclass
class ProxyConfig:
    """Proxy endpoint configuration."""

    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    proxy_type: ProxyType = ProxyType.RESIDENTIAL
    country: str = "US"
    city: Optional[str] = None
    isp: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def url(self) -> str:
        """Full proxy URL (credentials included). Never log this value."""
        if self.username and self.password:
            return f"http://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"http://{self.host}:{self.port}"

    def to_dict(self, redact: bool = False) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["proxy_type"] = self.proxy_type.value
        if redact:
            data["username"] = "***" if self.username else None
            data["password"] = "***" if self.password else None
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProxyConfig":
        payload = dict(data)
        if isinstance(payload.get("proxy_type"), str):
            payload["proxy_type"] = ProxyType(payload["proxy_type"])
        return cls(**payload)


@dataclass
class ProxyHealth:
    """Health metrics for a single proxy endpoint."""

    is_alive: bool = False
    response_time_ms: float = -1.0
    success_rate: float = 1.0
    last_checked: float = 0.0
    consecutive_failures: int = 0


@dataclass
class LatencyProfile:
    """Network latency profile configuration."""

    name: str
    min_latency_ms: int
    max_latency_ms: int
    jitter_ms: int = 0
    packet_loss_rate: float = 0.0
    download_speed_mbps: float = 0.0
    upload_speed_mbps: float = 0.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.min_latency_ms < 0 or self.max_latency_ms < 0:
            raise ValueError("latency values must be non-negative")
        if self.min_latency_ms > self.max_latency_ms:
            raise ValueError("min_latency_ms must be <= max_latency_ms")
        if self.jitter_ms < 0:
            raise ValueError("jitter_ms must be non-negative")
        if not 0.0 <= self.packet_loss_rate <= 1.0:
            raise ValueError("packet_loss_rate must be within [0.0, 1.0]")
        if self.download_speed_mbps < 0 or self.upload_speed_mbps < 0:
            raise ValueError("speeds must be non-negative")


@dataclass
class HumanBehaviorConfig:
    """Configuration for human behavior simulation."""

    typing_speed_wpm: int = 45
    mouse_speed_pixels_per_sec: int = 300
    scroll_behavior: str = "smooth"  # "smooth" | "instant"
    think_time_range_sec: Tuple[float, float] = (1.0, 3.0)
    error_rate: float = 0.02

    def __post_init__(self) -> None:
        if self.typing_speed_wpm <= 0:
            raise ValueError("typing_speed_wpm must be positive")
        if self.mouse_speed_pixels_per_sec <= 0:
            raise ValueError("mouse_speed_pixels_per_sec must be positive")
        if self.scroll_behavior not in ("smooth", "instant"):
            raise ValueError("scroll_behavior must be 'smooth' or 'instant'")
        if len(self.think_time_range_sec) != 2 or self.think_time_range_sec[0] > self.think_time_range_sec[1]:
            raise ValueError("think_time_range_sec must be a (min, max) tuple with min <= max")
        if not 0.0 <= self.error_rate <= 1.0:
            raise ValueError("error_rate must be within [0.0, 1.0]")

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["think_time_range_sec"] = list(self.think_time_range_sec)
        return data


@dataclass
class SessionData:
    """Complete session data for one test scenario run."""

    session_id: str
    start_time: datetime
    fingerprint: BrowserFingerprint
    proxy: Optional[ProxyConfig] = None
    latency_profile: Optional[LatencyProfile] = None
    end_time: Optional[datetime] = None
    interactions: List[Dict[str, Any]] = field(default_factory=list)
    detection_events: List[Dict[str, Any]] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    success: bool = False
    error_message: Optional[str] = None

    @property
    def duration_sec(self) -> float:
        if self.end_time is None:
            return 0.0
        return (self.end_time - self.start_time).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_sec": self.duration_sec,
            "fingerprint": self.fingerprint.to_dict(),
            "proxy": self.proxy.to_dict(redact=True) if self.proxy else None,
            "latency_profile": dataclasses.asdict(self.latency_profile) if self.latency_profile else None,
            "interactions": self.interactions,
            "detection_events": self.detection_events,
            "screenshots": self.screenshots,
            "errors": self.errors,
            "success": self.success,
            "error_message": self.error_message,
        }

    def save_to(self, filepath: str) -> None:
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)


@dataclass
class TestAction:
    """Single test action."""

    __test__ = False  # not a pytest test class despite the name

    action_type: str  # navigate, click, type, scroll, wait, screenshot
    selector: Optional[str] = None
    value: Optional[str] = None
    wait_after_ms: int = 500
    human_behavior: bool = True
    retry_on_error: bool = False
    validation: Optional[Dict[str, Any]] = None

    VALID_TYPES = ("navigate", "click", "type", "scroll", "wait", "screenshot")

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.action_type not in self.VALID_TYPES:
            raise ValueError(
                f"action_type must be one of {self.VALID_TYPES}, got '{self.action_type}'"
            )
        if self.action_type == "type" and not self.value:
            raise ValueError("'type' actions require a non-empty value")
        if self.action_type in ("click", "type") and not self.selector:
            raise ValueError(f"'{self.action_type}' actions require a selector")
        if self.wait_after_ms < 0:
            raise ValueError("wait_after_ms must be non-negative")


@dataclass
class TestScenario:
    """Individual test scenario configuration."""

    __test__ = False  # not a pytest test class despite the name

    scenario_id: str
    scenario_name: str
    target_url: str
    fingerprint_profile: str
    proxy_criteria: Dict[str, Any] = field(default_factory=dict)
    latency_profile: str = "residential_cable"
    actions: List[TestAction] = field(default_factory=list)
    expected_outcome: str = "success"
    validation_rules: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario_id must be non-empty")
        if not self.target_url.startswith(("http://", "https://")):
            raise ValueError(f"target_url must be a valid URL, got '{self.target_url}'")
        if not self.actions:
            raise ValueError("scenarios must contain at least one action")
        for action in self.actions:
            action.validate()


@dataclass
class TestConfiguration:
    """Complete test suite configuration."""

    __test__ = False  # not a pytest test class despite the name

    test_suite_name: str
    target_urls: List[str]
    fingerprint_profiles: List[str]
    proxy_criteria: Dict[str, Any] = field(default_factory=dict)
    latency_profiles: List[str] = field(default_factory=lambda: ["residential_cable"])
    human_behavior_config: HumanBehaviorConfig = field(default_factory=HumanBehaviorConfig)
    test_scenarios: List[TestScenario] = field(default_factory=list)
    max_concurrent_sessions: int = 1
    session_timeout_sec: int = 300
    retry_on_failure: bool = True
    max_retries: int = 2
    output_directory: str = "./output"
    headless: bool = True
    proxy_list_path: str = "proxies.json"

    def __post_init__(self) -> None:
        if not self.test_suite_name:
            raise ValueError("test_suite_name must be non-empty")
        if not self.target_urls:
            raise ValueError("target_urls must contain at least one URL")
        if self.max_concurrent_sessions <= 0:
            raise ValueError("max_concurrent_sessions must be positive")
        if self.session_timeout_sec <= 0:
            raise ValueError("session_timeout_sec must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")


@dataclass
class TestResult:
    """Result from a single test scenario."""

    __test__ = False  # not a pytest test class despite the name

    scenario_id: str
    session_data: SessionData
    success: bool
    detection_score: float
    performance_metrics: Dict[str, float] = field(default_factory=dict)
    validation_results: List[Dict[str, Any]] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    error_log: List[str] = field(default_factory=list)


@dataclass
class TestSuiteResult:
    """Aggregated results for a full test suite."""

    __test__ = False  # not a pytest test class despite the name

    test_suite_name: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    test_results: List[TestResult] = field(default_factory=list)
    aggregate_metrics: Dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Test Suite: {self.test_suite_name}",
            f"Total: {self.total_tests}  Passed: {self.passed_tests}  Failed: {self.failed_tests}",
        ]
        for metric, value in self.aggregate_metrics.items():
            lines.append(
                f"  {metric}: {value:.3f}" if isinstance(value, float) else f"  {metric}: {value}"
            )
        return "\n".join(lines)
