"""Tests for proxy pool management (health checks are mocked)."""
import json
import os

import pytest

from bftf.models import ProxyHealth
from bftf.proxies import ProxyManager, ProxyPoolError, load_dotenv_credentials


@pytest.fixture
def proxy_file(tmp_path):
    data = {
        "proxies": [
            {"host": "10.0.0.1", "port": 8000, "username": "${PROXY_USER}",
             "password": "${PROXY_PASS}", "proxy_type": "residential",
             "country": "US", "city": "NYC", "isp": "Comcast"},
            {"host": "10.0.0.2", "port": 8000, "username": None,
             "password": None, "proxy_type": "residential",
             "country": "US", "city": None, "isp": "Verizon"},
            {"host": "10.0.0.3", "port": 8000, "username": None,
             "password": None, "proxy_type": "mobile",
             "country": "GB", "city": "London", "isp": "BT"},
        ]
    }
    path = tmp_path / "proxies.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.fixture
def pm(proxy_file, monkeypatch):
    monkeypatch.setenv("PROXY_USER", "user123")
    monkeypatch.setenv("PROXY_PASS", "secret")
    return ProxyManager(proxy_file, seed=1)


def test_env_expansion(pm):
    assert pm._proxies[0].username == "user123"
    assert pm._proxies[0].password == "secret"


def test_proxy_url_formatting(pm):
    proxy = pm._proxies[0]
    assert proxy.url == "http://user123:secret@10.0.0.1:8000"
    assert pm._proxies[1].url == "http://10.0.0.2:8000"


def test_redacted_serialization(pm):
    data = pm._proxies[0].to_dict(redact=True)
    assert data["password"] == "***"
    assert "secret" not in json.dumps(data)


def test_criteria_filtering(pm):
    us = pm._candidates({"country": "US"})
    assert {p.host for p in us} == {"10.0.0.1", "10.0.0.2"}
    mobile = pm._candidates({"proxy_type": "mobile"})
    assert [p.host for p in mobile] == ["10.0.0.3"]
    none = pm._candidates({"country": "JP"})
    assert none == []


async def test_get_proxy_uses_health_check(pm, monkeypatch):
    async def fake_health(self, proxy):
        return ProxyHealth(is_alive=True, response_time_ms=123.0, last_checked=9999999999.0)

    monkeypatch.setattr(ProxyManager, "check_proxy_health", fake_health)
    proxy = await pm.get_proxy({"country": "US"})
    assert proxy.host in {"10.0.0.1", "10.0.0.2"}
    assert proxy.country == "US"


async def test_get_proxy_no_match_raises(pm):
    with pytest.raises(ProxyPoolError):
        await pm.get_proxy({"country": "XX"})


async def test_rotate_excludes_current(pm, monkeypatch):
    async def fake_health(self, proxy):
        return ProxyHealth(is_alive=True, response_time_ms=50.0, last_checked=9999999999.0)

    monkeypatch.setattr(ProxyManager, "check_proxy_health", fake_health)
    current = pm._proxies[0]
    other = await pm.rotate_proxy(current)
    assert other.key != current.key


async def test_dead_proxies_are_removed(pm, monkeypatch):
    # Avoid real network I/O: force every health check to fail.
    async def fake_health(self, proxy):
        health = self._health.get(proxy.key, ProxyHealth())
        new = ProxyHealth(is_alive=False, consecutive_failures=health.consecutive_failures + 1)
        self._health[proxy.key] = new
        if new.consecutive_failures >= self.max_consecutive_failures:
            await self.remove_dead_proxy(proxy)
        return new

    monkeypatch.setattr(ProxyManager, "check_proxy_health", fake_health)
    dead = pm._proxies[0]
    for _ in range(pm.max_consecutive_failures):
        health = await pm.check_proxy_health(dead)
    assert not health.is_alive
    assert dead.key in pm._removed
    assert all(p.key != dead.key for p in pm._proxies)


def test_statistics(pm):
    stats = pm.get_proxy_statistics()
    assert stats["total"] == 3
    assert stats["alive"] == 0


# --------------------------------------------------------------------------- #
# Webshare credentials / dotenv loading
# --------------------------------------------------------------------------- #
def test_has_webshare_credentials_false_by_default(monkeypatch):
    for key in (
        "WEBSHARE_PROXY_HOST", "WEBSHARE_PROXY_PORT",
        "WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert ProxyManager.has_webshare_credentials() is False


def test_has_webshare_credentials_true_when_all_set(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBSHARE_PROXY_PORT", "5050")
    monkeypatch.setenv("WEBSHARE_PROXY_USER", "user")
    monkeypatch.setenv("WEBSHARE_PROXY_PASS", "pass")
    assert ProxyManager.has_webshare_credentials() is True


def test_has_webshare_credentials_partial(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_HOST", "127.0.0.1")
    monkeypatch.setenv("WEBSHARE_PROXY_PORT", "5050")
    monkeypatch.delenv("WEBSHARE_PROXY_USER", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASS", raising=False)
    assert ProxyManager.has_webshare_credentials() is False


def test_build_proxy_from_env(monkeypatch):
    monkeypatch.setenv("WEBSHARE_PROXY_HOST", "10.0.0.1")
    monkeypatch.setenv("WEBSHARE_PROXY_PORT", "8080")
    monkeypatch.setenv("WEBSHARE_PROXY_USER", "webshare_user")
    monkeypatch.setenv("WEBSHARE_PROXY_PASS", "webshare_pass")
    monkeypatch.setenv("WEBSHARE_PROXY_COUNTRY", "DE")
    proxy = ProxyManager.build_proxy_from_env()
    assert proxy is not None
    assert proxy.host == "10.0.0.1"
    assert proxy.port == 8080
    assert proxy.username == "webshare_user"
    assert proxy.password == "webshare_pass"
    assert proxy.key == "10.0.0.1:8080"
    assert "webshare_user" in proxy.url
    assert "webshare_pass" in proxy.url
    # The key must never contain credentials.
    assert "webshare_user" not in proxy.key
    assert "webshare_pass" not in proxy.key


def test_build_proxy_from_env_missing(monkeypatch):
    for key in (
        "WEBSHARE_PROXY_HOST", "WEBSHARE_PROXY_PORT",
        "WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert ProxyManager.build_proxy_from_env() is None


def test_load_dotenv_credentials_from_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WEBSHARE_PROXY_HOST=1.2.3.4\n"
        "WEBSHARE_PROXY_PORT=9999\n"
        "WEBSHARE_PROXY_USER=loaded_user\n"
        "WEBSHARE_PROXY_PASS=loaded_pass\n",
        encoding="utf-8",
    )
    for key in (
        "WEBSHARE_PROXY_HOST", "WEBSHARE_PROXY_PORT",
        "WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert load_dotenv_credentials(str(env_file)) is True
    assert ProxyManager.has_webshare_credentials() is True
    assert os.environ["WEBSHARE_PROXY_HOST"] == "1.2.3.4"


def test_load_dotenv_credentials_no_file(monkeypatch):
    for key in (
        "WEBSHARE_PROXY_HOST", "WEBSHARE_PROXY_PORT",
        "WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert load_dotenv_credentials("/nonexistent/.env") is False


# --------------------------------------------------------------------------- #
# ProxyManager dotenv_path integration
# --------------------------------------------------------------------------- #
def test_proxy_manager_with_dotenv_path(tmp_path, proxy_file, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WEBSHARE_PROXY_HOST=127.0.0.1\n"
        "WEBSHARE_PROXY_PORT=5050\n"
        "WEBSHARE_PROXY_USER=env_user\n"
        "WEBSHARE_PROXY_PASS=env_pass\n",
        encoding="utf-8",
    )
    for key in ("WEBSHARE_PROXY_HOST", "WEBSHARE_PROXY_PORT",
                "WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS"):
        monkeypatch.delenv(key, raising=False)
    ProxyManager(proxy_file, seed=1, dotenv_path=str(env_file))
    assert os.environ["WEBSHARE_PROXY_USER"] == "env_user"
    assert ProxyManager.has_webshare_credentials() is True
