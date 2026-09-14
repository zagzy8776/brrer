"""
Automated Browser Fingerprint Testing Framework

A production-grade modular framework for testing residential proxy configurations
with anti-detection stealth capabilities using Playwright and playwright-stealth.
"""

from .config import settings, Settings
from .proxy import (
    Proxy,
    WebshareProxyClient,
    ProxyError,
    ProxyAuthenticationError,
    ProxyConnectionError,
    ProxyNotFoundError,
)
from .browser import (
    StealthBrowser,
    BrowserError,
    BrowserLaunchError,
    NavigationError,
)
from .session import (
    SessionManager,
    FingerprintResult,
)

__version__ = "1.0.0"
__author__ = "Cybersecurity Research Team"

__all__ = [
    "settings",
    "Settings",
    "Proxy",
    "WebshareProxyClient",
    "ProxyError",
    "ProxyAuthenticationError",
    "ProxyConnectionError",
    "ProxyNotFoundError",
    "StealthBrowser",
    "BrowserError",
    "BrowserLaunchError",
    "NavigationError",
    "SessionManager",
    "FingerprintResult",
]