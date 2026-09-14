"""Network latency emulation via the Chrome DevTools Protocol.

Applies realistic latency/jitter/bandwidth conditions to browser pages using
CDP ``Network.emulateNetworkConditions`` (Chromium only). Note that CDP cannot
drop individual packets; ``packet_loss_rate`` is exposed for reporting and for
custom transports (e.g. an intercepting mitmproxy) but is not enforced here.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import Dict, Optional

from playwright.async_api import Page

from .models import LatencyProfile

logger = logging.getLogger(__name__)

# Mbps -> bytes per second
_MBPS_TO_BPS = 1_000_000 / 8

PRESET_PROFILES: Dict[str, LatencyProfile] = {
    "none": LatencyProfile("none", 0, 0, 0, 0.0, 0.0, 0.0),
    "residential_fiber": LatencyProfile("residential_fiber", 5, 15, 2, 0.0, 300.0, 100.0),
    "residential_cable": LatencyProfile("residential_cable", 15, 40, 5, 0.0, 100.0, 20.0),
    "residential_dsl": LatencyProfile("residential_dsl", 25, 60, 10, 0.001, 25.0, 5.0),
    "mobile_4g": LatencyProfile("mobile_4g", 40, 90, 15, 0.002, 20.0, 10.0),
    "mobile_3g": LatencyProfile("mobile_3g", 150, 350, 60, 0.01, 1.5, 0.75),
}


class NetworkEmulator:
    """Emulates network conditions for realistic residential/mobile testing."""

    def __init__(self) -> None:
        self._profiles: Dict[str, LatencyProfile] = dict(PRESET_PROFILES)
        self._active_sessions: Dict[int, object] = {}  # id(page) -> CDPSession

    # ------------------------------------------------------------------ #
    # Profile management
    # ------------------------------------------------------------------ #
    def create_latency_profile(self, profile_name: str, config: Dict[str, float]) -> LatencyProfile:
        """Create (and register) a custom latency profile."""
        profile = LatencyProfile(name=profile_name, **config)
        self._profiles[profile_name] = profile
        return profile

    def get_preset_profile(self, profile_type: str) -> LatencyProfile:
        """Return a preset latency profile by name (e.g. 'residential_cable')."""
        if profile_type not in self._profiles:
            raise KeyError(
                f"Unknown latency profile '{profile_type}'. "
                f"Available: {sorted(self._profiles)}"
            )
        return self._profiles[profile_type]

    def calculate_realistic_delay(self, profile: LatencyProfile) -> float:
        """Sample a realistic one-off delay (ms) including jitter."""
        if profile.max_latency_ms == 0:
            return 0.0
        base = random.uniform(profile.min_latency_ms, profile.max_latency_ms)
        if profile.jitter_ms:
            base += random.gauss(0, profile.jitter_ms / 2)
        return max(0.0, base)

    # ------------------------------------------------------------------ #
    # CDP-based emulation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _conditions_payload(profile: LatencyProfile, latency_ms: float) -> Dict[str, object]:
        return {
            "offline": False,
            "latency": latency_ms,
            "downloadThroughput": profile.download_speed_mbps * _MBPS_TO_BPS,
            "uploadThroughput": profile.upload_speed_mbps * _MBPS_TO_BPS,
        }

    async def apply_latency_profile(self, page: Page, profile: LatencyProfile) -> None:
        """Apply a latency profile to a page via a CDP session."""
        latency = self.calculate_realistic_delay(profile)
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Network.emulateNetworkConditions", self._conditions_payload(profile, latency))
        self._active_sessions[id(page)] = cdp
        logger.debug(
            "Applied profile '%s' to page (latency=%.1fms, down=%.1fMbps)",
            profile.name, latency, profile.download_speed_mbps,
        )

    async def inject_random_latency(self, min_ms: int, max_ms: int) -> None:
        """Re-apply a randomised latency in [min_ms, max_ms] to all active pages."""
        if max_ms <= 0:
            return
        latency = float(random.randint(min_ms, max_ms))
        for cdp in list(self._active_sessions.values()):
            try:
                await cdp.send(
                    "Network.emulateNetworkConditions",
                    {"offline": False, "latency": latency,
                     "downloadThroughput": -1, "uploadThroughput": -1},
                )
            except Exception:  # pragma: no cover - page may be closed
                logger.debug("Failed to inject latency on closed page", exc_info=True)
        await asyncio.sleep(0)

    async def clear_emulation(self, page: Page) -> None:
        """Remove network emulation from a page."""
        cdp = self._active_sessions.pop(id(page), None)
        if cdp is None:
            return
        try:
            await cdp.send(
                "Network.emulateNetworkConditions",
                {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
            )
            await cdp.detach()
        except Exception:  # pragma: no cover
            pass
