"""Residential proxy pool management: selection, rotation, and health checks."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

from .models import ProxyConfig, ProxyHealth, ProxyType

logger = logging.getLogger(__name__)

DEFAULT_HEALTH_CHECK_URL = "https://api.ipify.org?format=json"
HEALTH_TTL_SEC = 300.0

_WEBSHARE_ENV_KEYS = (
    "WEBSHARE_PROXY_HOST",
    "WEBSHARE_PROXY_PORT",
    "WEBSHARE_PROXY_USER",
    "WEBSHARE_PROXY_PASS",
)


class ProxyPoolError(RuntimeError):
    """Raised when no healthy proxy matches the requested criteria."""


class ProxyManager:
    """Manages a pool of proxies with rotation and health monitoring."""

    def __init__(
        self,
        proxy_list_path: str,
        health_check_url: str = DEFAULT_HEALTH_CHECK_URL,
        health_check_timeout: float = 10.0,
        max_consecutive_failures: int = 3,
        seed: Optional[int] = None,
        dotenv_path: Optional[str] = None,
    ) -> None:
        if dotenv_path is not None:
            load_dotenv(dotenv_path)
        self._proxies: List[ProxyConfig] = self._load_proxies(proxy_list_path)
        self._health: Dict[str, ProxyHealth] = {}
        self._last_used: Dict[str, float] = {}
        self._use_counts: Dict[str, int] = {p.key: 0 for p in self._proxies}
        self._removed: Dict[str, ProxyConfig] = {}
        self._lock = asyncio.Lock()
        self.health_check_url = health_check_url
        self.health_check_timeout = health_check_timeout
        self.max_consecutive_failures = max_consecutive_failures
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    @staticmethod
    def _expand(value: Optional[str]) -> Optional[str]:
        """Expand ${ENV_VAR} placeholders in credential fields."""
        if value and value.startswith("${") and value.endswith("}"):
            return os.environ.get(value[2:-1])
        return value

    def _load_proxies(self, path: str) -> List[ProxyConfig]:
        proxies: List[ProxyConfig] = []
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data["proxies"] if isinstance(data, dict) else data
        for entry in entries:
            entry = dict(entry)
            entry["username"] = self._expand(entry.get("username"))
            entry["password"] = self._expand(entry.get("password"))
            proxies.append(ProxyConfig.from_dict(entry))
        if not proxies:
            raise ProxyPoolError(f"No proxies loaded from '{path}'")
        logger.info("Loaded %d proxies from %s", len(proxies), path)
        return proxies

    # ------------------------------------------------------------------ #
    # Environment-based Webshare credentials
    # ------------------------------------------------------------------ #
    @staticmethod
    def has_webshare_credentials() -> bool:
        """Return True when all ``WEBSHARE_PROXY_*`` env vars are populated."""
        return all(os.environ.get(k) for k in _WEBSHARE_ENV_KEYS)

    @staticmethod
    def build_proxy_from_env() -> Optional[ProxyConfig]:
        """Build a ``ProxyConfig`` from ``WEBSHARE_PROXY_*`` environment variables.

        Returns ``None`` if any required credential is missing.
        """
        host = os.environ.get("WEBSHARE_PROXY_HOST")
        port = os.environ.get("WEBSHARE_PROXY_PORT")
        if not host or not port:
            return None
        return ProxyConfig(
            host=host,
            port=int(port),
            username=os.environ.get("WEBSHARE_PROXY_USER"),
            password=os.environ.get("WEBSHARE_PROXY_PASS"),
            proxy_type=ProxyType.RESIDENTIAL,
            country=os.environ.get("WEBSHARE_PROXY_COUNTRY", "US"),
        )

    # ------------------------------------------------------------------ #
    # Selection / rotation
    # ------------------------------------------------------------------ #
    def _candidates(self, criteria: Dict[str, Any], exclude: Optional[str] = None) -> List[ProxyConfig]:
        """Filter proxies by criteria (country, proxy_type, city, isp)."""
        out: List[ProxyConfig] = []
        wanted_type = criteria.get("proxy_type") or criteria.get("type")
        for p in self._proxies:
            if p.key == exclude or p.key in self._removed:
                continue
            if criteria.get("country") and p.country.upper() != str(criteria["country"]).upper():
                continue
            if wanted_type and p.proxy_type != ProxyType(wanted_type):
                continue
            if criteria.get("city") and (p.city or "").lower() != str(criteria["city"]).lower():
                continue
            if criteria.get("isp") and (p.isp or "").lower() != str(criteria["isp"]).lower():
                continue
            out.append(p)
        return out

    async def get_proxy(
        self,
        criteria: Optional[Dict[str, Any]] = None,
        exclude: Optional[str] = None,
    ) -> ProxyConfig:
        """Get a healthy proxy matching criteria (least-recently-used first).

        Args:
            criteria: filters such as country/proxy_type/city/isp.
            exclude: optional proxy key to exclude (used by rotation).
        """
        criteria = criteria or {}
        async with self._lock:
            candidates = self._candidates(criteria, exclude=exclude)
            if not candidates:
                raise ProxyPoolError(f"No proxies match criteria {criteria}")

            # Prefer candidates with fresh successful health checks.
            now = time.time()
            healthy = [
                p for p in candidates
                if (h := self._health.get(p.key)) and h.is_alive and now - h.last_checked < HEALTH_TTL_SEC
            ]
            if not healthy:
                # Lazily health-check the least-recently-used candidates until one works.
                ordered = sorted(candidates, key=lambda p: self._last_used.get(p.key, 0.0))
                for proxy in ordered[: min(3, len(ordered))]:
                    health = await self.check_proxy_health(proxy)
                    if health.is_alive:
                        healthy = [proxy]
                        break
                if not healthy:
                    raise ProxyPoolError(f"No healthy proxy matches criteria {criteria}")

            proxy = self._rng.choice(healthy)
            self._last_used[proxy.key] = time.time()
            self._use_counts[proxy.key] = self._use_counts.get(proxy.key, 0) + 1
            return proxy

    async def rotate_proxy(self, current_proxy: ProxyConfig) -> ProxyConfig:
        """Rotate to a different healthy proxy, excluding the current one."""
        return await self.get_proxy(criteria={}, exclude=current_proxy.key)

    # ------------------------------------------------------------------ #
    # Health monitoring
    # ------------------------------------------------------------------ #
    async def check_proxy_health(self, proxy: ProxyConfig) -> ProxyHealth:
        """Check whether the proxy is alive and measure its response time."""
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(proxy=proxy.url, timeout=self.health_check_timeout) as client:
                response = await client.get(self.health_check_url)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            health = self._health.get(proxy.key, ProxyHealth())
            alive = response.status_code == 200
            new = ProxyHealth(
                is_alive=alive,
                response_time_ms=round(elapsed_ms, 2),
                success_rate=health.success_rate if alive else max(0.0, health.success_rate - 0.2),
                last_checked=time.time(),
                consecutive_failures=0 if alive else health.consecutive_failures + 1,
            )
            self._health[proxy.key] = new
            if not alive:
                logger.warning("Proxy %s unhealthy (HTTP %d)", proxy.key, response.status_code)
                if new.consecutive_failures >= self.max_consecutive_failures:
                    await self.remove_dead_proxy(proxy)
            return new
        except Exception as exc:
            health = self._health.get(proxy.key, ProxyHealth())
            new = ProxyHealth(
                is_alive=False,
                response_time_ms=-1.0,
                success_rate=max(0.0, health.success_rate - 0.2),
                last_checked=time.time(),
                consecutive_failures=health.consecutive_failures + 1,
            )
            self._health[proxy.key] = new
            logger.warning("Proxy %s failed health check: %s", proxy.key, exc)
            if new.consecutive_failures >= self.max_consecutive_failures:
                await self.remove_dead_proxy(proxy)
            return new

    async def remove_dead_proxy(self, proxy: ProxyConfig) -> None:
        """Remove a non-functional proxy from the active pool."""
        async with self._lock:
            self._removed[proxy.key] = proxy
            self._proxies = [p for p in self._proxies if p.key != proxy.key]
            logger.warning("Removed dead proxy %s (%d remaining)", proxy.key, len(self._proxies))

    # ------------------------------------------------------------------ #
    # Statistics
    # ------------------------------------------------------------------ #
    def get_proxy_statistics(self) -> Dict[str, Any]:
        """Get usage statistics about the proxy pool."""
        alive = sum(1 for h in self._health.values() if h.is_alive)
        response_times = [h.response_time_ms for h in self._health.values() if h.is_alive]
        return {
            "total": len(self._proxies),
            "removed": len(self._removed),
            "health_checked": len(self._health),
            "alive": alive,
            "avg_response_time_ms": sum(response_times) / len(response_times) if response_times else 0.0,
            "use_counts": dict(sorted(self._use_counts.items(), key=lambda kv: -kv[1])),
        }


def load_dotenv_credentials(dotenv_path: Optional[str] = None) -> bool:
    """Load environment variables from a ``.env`` file and check for Webshare credentials.

    Args:
        dotenv_path: optional path to a ``.env`` file. If ``None``,
            ``python-dotenv`` searches the current working directory.

    Returns:
        ``True`` if all ``WEBSHARE_PROXY_*`` variables were populated
        (either already present in the environment or loaded from the file).
    """
    load_dotenv(dotenv_path)
    return ProxyManager.has_webshare_credentials()
