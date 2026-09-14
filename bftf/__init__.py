"""BFTF - Browser Fingerprint Testing Framework.

A modular cybersecurity research framework for testing web service responses
to varied browser fingerprint profiles, residential proxy configurations, and
network latency profiles, using Playwright with stealth and human behavior
simulation.
"""
from .activities import (
    AccountInventoryManager,
    AccountRecord,
    BehaviorProfile,
    GeneralSurferProfile,
    MapsReviewerProfile,
    RandomizationEngine,
    WarmupAccountResult,
    WarmupContext,
    WarmupOrchestrator,
    WarmupRunResult,
    BEHAVIOR_PROFILES,
)
from .browser import BrowserController
from .fingerprints import FingerprintManager
from .models import (
    BrowserFingerprint,
    HumanBehaviorConfig,
    LatencyProfile,
    ProxyConfig,
    ProxyHealth,
    ProxyType,
    SessionData,
    TestAction,
    TestConfiguration,
    TestResult,
    TestScenario,
    TestSuiteResult,
)
from .network import NetworkEmulator
from .orchestrator import TestOrchestrator
from .proxies import ProxyManager, ProxyPoolError, load_dotenv_credentials
from .sessions import SessionManager
from .workflows import (
    BranchRule,
    PageClassifier,
    PageState,
    RegistrationWorkflow,
    UserProfile,
    WorkflowOrchestrator,
    WorkflowResult,
    WorkflowSnapshot,
    WorkflowState,
    WorkflowStateStore,
    WorkflowStep,
    load_user_profiles,
)

__version__ = "0.2.0"

__all__ = [
    "AccountInventoryManager",
    "AccountRecord",
    "BehaviorProfile",
    "BEHAVIOR_PROFILES",
    "BrowserController",
    "FingerprintManager",
    "GeneralSurferProfile",
    "MapsReviewerProfile",
    "RandomizationEngine",
    "WarmupAccountResult",
    "WarmupContext",
    "WarmupOrchestrator",
    "WarmupRunResult",
    "NetworkEmulator",
    "ProxyManager",
    "ProxyPoolError",
    "SessionManager",
    "TestOrchestrator",
    "load_dotenv_credentials",
    "BrowserFingerprint",
    "HumanBehaviorConfig",
    "LatencyProfile",
    "ProxyConfig",
    "ProxyHealth",
    "ProxyType",
    "SessionData",
    "TestAction",
    "TestConfiguration",
    "TestResult",
    "TestScenario",
    "TestSuiteResult",
    "BranchRule",
    "PageClassifier",
    "PageState",
    "RegistrationWorkflow",
    "UserProfile",
    "WorkflowOrchestrator",
    "WorkflowResult",
    "WorkflowSnapshot",
    "WorkflowState",
    "WorkflowStateStore",
    "WorkflowStep",
    "load_user_profiles",
]
