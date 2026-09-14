"""Phase 4: Automated Account Trust & Reputation Management ("warm-up profiles").

Sustained, non-repetitive post-authentication browsing that establishes
human-like behavior patterns. Designed to work in harmony with the existing
subsystems:

- ``BrowserController`` -- all interactions go through the human-like
  Bezier-curve mouse, WPM typing, and smooth scrolling primitives.
- ``ProxyManager`` -- each account declares ``proxy_criteria``; the proxy
  actually used is resolved per session and logged (key only, never the URL).
- ``SessionManager`` -- every warm-up session is started/ended/logged with
  its matched fingerprint + proxy + latency context, including detection
  events and a detection score.
- ``FingerprintManager`` -- each account declares a ``fingerprint_profile``
  (or a persisted ``fingerprint_file``) so sessions reuse the account's
  matched identity.

Contents:

- ``AccountRecord`` / ``AccountInventoryManager`` -- load credentials and
  fingerprint/proxy metadata from an external ``accounts_inventory.json``.
- ``RandomizationEngine`` -- seeded randomness for timings, search terms,
  and activity ordering so no two warm-up sessions look identical.
- ``BehaviorProfile`` library -- ``MapsReviewerProfile`` and
  ``GeneralSurferProfile`` complex activity sequences executed while
  "logged in".
- ``WarmupAccountResult`` / ``WarmupRunResult`` -- per-account and
  inventory-wide outcome containers.
- ``WarmupOrchestrator`` -- ``WorkflowOrchestrator`` extension that runs
  warm-up activities across an inventory sequentially with guaranteed
  per-account browser teardown.

Research use only: run exclusively against services you own or are
authorized to test, at conservative rates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .fingerprints import FingerprintManager
from .models import BrowserFingerprint, HumanBehaviorConfig, ProxyConfig
from .proxies import ProxyManager
from .workflows import PageClassifier, WorkflowOrchestrator

logger = logging.getLogger(__name__)

 # --------------------------------------------------------------------------- #
# Account inventory
# --------------------------------------------------------------------------- #
@dataclass
class AccountRecord:
    """One warmed-up account: credentials bound to a matched identity."""

    account_id: str
    username: str
    password: str = ""
    fingerprint_profile: str = "residential_windows"
    fingerprint_file: Optional[str] = None
    proxy_criteria: Dict[str, Any] = field(default_factory=dict)
    proxy: Optional[ProxyConfig] = None
    latency_profile: str = "residential_cable"
    behavior_profile: str = "general_surfer"
    city: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id must be non-empty")
        if not self.username:
            raise ValueError(f"Account '{self.account_id}' requires a username")
        if isinstance(self.proxy, dict):
            self.proxy = ProxyConfig.from_dict(self.proxy)

    def to_dict(self, redact: bool = True) -> Dict[str, Any]:
        """Serialize for logs/artefacts (password redacted by default)."""
        return {
            "account_id": self.account_id,
            "username": self.username,
            "password": "***" if redact and self.password else self.password,
            "fingerprint_profile": self.fingerprint_profile,
            "fingerprint_file": self.fingerprint_file,
            "proxy_criteria": dict(self.proxy_criteria),
            "proxy": self.proxy.to_dict(redact=True) if self.proxy else None,
            "latency_profile": self.latency_profile,
            "behavior_profile": self.behavior_profile,
            "city": self.city,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountRecord":
        payload = dict(data)
        payload["extra"] = {str(k): str(v) for k, v in (payload.get("extra") or {}).items()}
        payload.setdefault("proxy_criteria", {})
        return cls(**payload)


def _expand_env(value: Optional[str]) -> Optional[str]:
    """Expand a single ${ENV_VAR} placeholder (mirrors ProxyManager)."""
    if value and isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1])
    return value

class AccountInventoryManager:
    """Loads credentials + identity metadata from an inventory JSON file."""

    def __init__(self, inventory_path: str) -> None:
        self.inventory_path = inventory_path
        self._accounts: List[AccountRecord] = self._load(inventory_path)
        logger.info("Loaded %d accounts from %s", len(self._accounts), inventory_path)

    def _load(self, path: str) -> List[AccountRecord]:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data["accounts"] if isinstance(data, dict) else data
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"No accounts found in '{path}'")
        accounts: List[AccountRecord] = []
        seen: set = set()
        for entry in entries:
            entry = dict(entry)
            entry["password"] = _expand_env(entry.get("password")) or ""
            proxy = entry.get("proxy")
            if isinstance(proxy, dict):
                proxy = dict(proxy)
                proxy["host"] = _expand_env(proxy.get("host"))
                port = _expand_env(proxy.get("port"))
                if isinstance(port, str):
                    port = int(port)
                proxy["port"] = port
                proxy["username"] = _expand_env(proxy.get("username"))
                proxy["password"] = _expand_env(proxy.get("password"))
                entry["proxy"] = proxy
            record = AccountRecord.from_dict(entry)
            if record.account_id in seen:
                raise ValueError(f"Duplicate account_id '{record.account_id}'")
            seen.add(record.account_id)
            accounts.append(record)
        return accounts

    def __len__(self) -> int:
        return len(self._accounts)

    def __iter__(self):
        return iter(self._accounts)

    @property
    def accounts(self) -> List[AccountRecord]:
        return list(self._accounts)

    @property
    def account_ids(self) -> List[str]:
        return [a.account_id for a in self._accounts]

    def get_account(self, account_id: str) -> AccountRecord:
        for account in self._accounts:
            if account.account_id == account_id:
                return account
        raise KeyError(f"Unknown account_id '{account_id}'")

    def accounts_for_profile(self, behavior_profile: str) -> List[AccountRecord]:
        return [a for a in self._accounts if a.behavior_profile == behavior_profile]

    def summary(self) -> str:
        lines = [f"Inventory '{self.inventory_path}': {len(self._accounts)} accounts"]
        for a in self._accounts:
            lines.append(
                f"  - {a.account_id} ({a.username}): behavior={a.behavior_profile} "
                f"fingerprint={a.fingerprint_profile} city={a.city or 'n/a'}"
            )
        return "\n".join(lines)

 # --------------------------------------------------------------------------- #
# Randomization engine
# --------------------------------------------------------------------------- #
class RandomizationEngine:
    """Seeded randomness so no two warm-up sessions look identical."""

    MAPS_QUERIES = [
        "coffee shop", "pizza near me", "pharmacy", "gas station",
        "italian restaurant", "car wash", "bookstore", "gym",
        "sushi restaurant", "hardware store", "bakery", "barber shop",
    ]
    NEWS_SITES = [
        "https://example.com",
        "https://example.org",
        "https://example.net",
    ]

    def __init__(self, seed: Optional[int] = None) -> None:
        self.seed = seed
        self._rng = random.Random(seed)

    def reseed(self, seed: Optional[int]) -> None:
        self.seed = seed
        self._rng.seed(seed)

    def pause(self, lo_s: float = 0.8, hi_s: float = 3.5) -> float:
        """Sample a human-like pause duration in seconds."""
        return self._rng.uniform(lo_s, hi_s)

    def think_steps(self, lo: int = 2, hi: int = 5) -> int:
        return self._rng.randint(lo, hi)

    def shuffled(self, items: Sequence[Any]) -> List[Any]:
        order = list(items)
        self._rng.shuffle(order)
        return order

    def choice(self, items: Sequence[Any]) -> Any:
        return self._rng.choice(list(items))

    def maps_query(self, city: Optional[str] = None) -> str:
        base = self._rng.choice(self.MAPS_QUERIES)
        if city and self._rng.random() < 0.7:
            return f"{base} in {city}"
        return base

    def news_site(self) -> str:
        return self._rng.choice(self.NEWS_SITES)

    def scroll_plan(self, passes: int = 3) -> List[Dict[str, Any]]:
        plan = []
        for _ in range(passes):
            plan.append({
                "direction": self._rng.choice(["down", "down", "down", "up"]),
                "amount_px": self._rng.randint(300, 1200),
                "pauses": round(self._rng.uniform(0.4, 2.0), 2),
            })
        return plan

    def jitter_behavior(self, base: HumanBehaviorConfig) -> HumanBehaviorConfig:
        """Return a copy of the behavior config with jittered typing speed."""
        from dataclasses import replace
        wpm = max(20, min(140, int(base.typing_speed_wpm * self._rng.uniform(0.7, 1.3))))
        return replace(base, typing_speed_wpm=wpm)

 # --------------------------------------------------------------------------- #
# Warm-up execution context + behavior profiles
# --------------------------------------------------------------------------- #
@dataclass
class WarmupContext:
    """Everything a behavior profile needs to act human-like on a page."""

    page: Any
    browser: Any
    session_id: str
    account: AccountRecord
    fingerprint: BrowserFingerprint
    proxy: Optional[ProxyConfig]
    behavior: HumanBehaviorConfig
    random: RandomizationEngine
    dry_run: bool = False
    sleep_scale: float = 1.0  # 0.0 in tests: sample timings but skip real sleeps


class BehaviorProfile:
    """Base class for a warm-up activity persona. Subclass and override."""

    name: str = "base"
    description: str = ""

    async def run(self, ctx: WarmupContext, orchestrator: "WarmupOrchestrator") -> Dict[str, Any]:
        raise NotImplementedError

    # -- safe primitives (all route through BrowserController) --------- #
    async def _goto(self, orchestrator: "WarmupOrchestrator", ctx: WarmupContext,
                    url: str) -> None:
        if ctx.dry_run:
            orchestrator.sessions.record_interaction(ctx.session_id, "warmup_navigate", {"url": url})
            return
        await orchestrator.browser.navigate_to_url(ctx.page, url)

    async def _scroll_plan(self, orchestrator: "WarmupOrchestrator", ctx: WarmupContext,
                           passes: int = 3) -> int:
        total = 0
        for leg in ctx.random.scroll_plan(passes=passes):
            if not ctx.dry_run:
                await orchestrator.browser.scroll_page(
                    ctx.page, leg["direction"], ctx.behavior, amount_px=leg["amount_px"])
                if ctx.sleep_scale > 0:
                    await asyncio.sleep(leg["pauses"] * 0.5 * ctx.sleep_scale)
            total += leg["amount_px"]
        orchestrator.sessions.record_interaction(ctx.session_id, "warmup_scroll", {
            "passes": passes, "total_px": total})
        return total

    async def _pause(self, ctx: WarmupContext, lo: float = 0.8, hi: float = 3.0) -> float:
        duration = ctx.random.pause(lo, hi)
        if not ctx.dry_run and ctx.sleep_scale > 0:
            await asyncio.sleep(min(duration, 3.0) * ctx.sleep_scale)
        return round(duration, 2)

    async def _check_interdiction(self, orchestrator: "WarmupOrchestrator",
                                  ctx: WarmupContext) -> Optional[str]:
        """Classify the page; log + report CAPTCHA/verification, never bypass."""
        if ctx.dry_run:
            return None
        state, evidence = await PageClassifier.classify(ctx.page)
        if state.value in ("captcha", "verification_required"):
            orchestrator.sessions.record_detection_event(ctx.session_id, state.value, {
                "account_id": ctx.account.account_id,
                "behavior_profile": self.name,
                "evidence": evidence,
                "url": getattr(ctx.page, "url", ""),
            })
            return state.value
        return None

class MapsReviewerProfile(BehaviorProfile):
    """Local-discovery persona: search nearby businesses, inspect results."""

    name = "maps_reviewer"
    description = ("Navigate to Google Maps, search for local businesses biased "
                   "by the proxy/account city, page through results, and scroll.")

    MAPS_URL = "https://www.google.com/maps"
    SEARCH_BOX = "input[name='q'], input#searchboxinput, input[aria-label*='Search' i]"
    RESULT_CARDS = ("div[role='article'], div[role='feed'] div[role='article'], "
                    "a[href*='/maps/place/']")

    async def run(self, ctx: WarmupContext, orchestrator: "WarmupOrchestrator",
                *args: Any, **kwargs: Any) -> Dict[str, Any]:
        searches = ctx.random.think_steps(2, 4)
        summary = {"profile": self.name, "searches": [], "result_clicks": 0,
                   "scroll_px": 0, "halts": []}
        city = ctx.account.city or (ctx.proxy.city if ctx.proxy else None)
        for n in range(searches):
            query = ctx.random.maps_query(city)
            summary["searches"].append(query)
            if ctx.dry_run:
                orchestrator.sessions.record_interaction(ctx.session_id, "warmup_search", {
                    "engine": "maps", "query": query, "round": n + 1})
            else:
                await self._maps_search(orchestrator, ctx, query)
            summary["scroll_px"] += await self._scroll_plan(
                orchestrator, ctx, passes=ctx.random.think_steps(2, 4))
            summary["result_clicks"] += await self._browse_results(orchestrator, ctx)
            halt = await self._check_interdiction(orchestrator, ctx)
            if halt:
                summary["halts"].append(halt)
                break
            await self._pause(ctx)
        return summary

    async def _maps_search(self, orch, ctx: WarmupContext, query: str) -> None:
        await self._goto(orch, ctx, self.MAPS_URL)
        await self._pause(ctx, 1.0, 2.5)
        try:
            box = ctx.page.locator(self.SEARCH_BOX).first
            await box.click(timeout=8000)
            await box.fill("")
            behavior = ctx.random.jitter_behavior(ctx.behavior)
            await orch.browser.type_text(ctx.page, self.SEARCH_BOX, query, behavior)
            await ctx.page.keyboard.press("Enter")
            await self._pause(ctx, 1.5, 3.0)
        except Exception as exc:
            orch.sessions.record_error(ctx.session_id, f"maps search failed: {exc}")
        orch.sessions.record_interaction(ctx.session_id, "warmup_search", {
            "engine": "maps", "query": query})

    async def _browse_results(self, orch, ctx: WarmupContext) -> int:
        clicks = 0
        if ctx.dry_run:
            clicks = ctx.random.think_steps(0, 2)
            orch.sessions.record_interaction(ctx.session_id, "warmup_result_clicks", {
                "engine": "maps", "clicks": clicks, "simulated": True})
            return clicks
        try:
            cards = ctx.page.locator(self.RESULT_CARDS)
            count = await cards.count()
        except Exception:
            return 0
        for idx in ctx.random.shuffled(range(min(count, 5)))[: ctx.random.think_steps(1, 3)]:
            try:
                await orch.browser.click_element(
                    ctx.page, f"({self.RESULT_CARDS}) >> nth={idx}",
                    ctx.behavior)
                clicks += 1
                await self._pause(ctx, 1.0, 2.5)
                if ctx.random.choice([True, False]):
                    await orch.browser.scroll_page(ctx.page, "down", ctx.behavior,
                                                   amount_px=ctx.random.choice([400, 600, 900]))
            except Exception as exc:
                orch.sessions.record_error(ctx.session_id, f"maps click failed: {exc}")
                break
        orch.sessions.record_interaction(ctx.session_id, "warmup_result_clicks", {
            "engine": "maps", "clicks": clicks})
        return clicks


class GeneralSurferProfile(BehaviorProfile):
    """News-reading persona: visit articles, scroll variably, follow links."""

    name = "general_surfer"
    description = ("Navigate to news sites, read articles with varying scroll "
                   "speeds, and click random internal links.")
    ARTICLE_LINKS = "main a[href], article a[href], a[href^='/']"

    async def run(self, ctx: WarmupContext, orchestrator: "WarmupOrchestrator") -> Dict[str, Any]:
        visits = ctx.random.think_steps(2, 4)
        summary = {"profile": self.name, "visits": [], "link_clicks": 0,
                   "scroll_px": 0, "halts": []}
        for n in range(visits):
            url = ctx.random.news_site()
            summary["visits"].append(url)
            await self._goto(orchestrator, ctx, url)
            await self._pause(ctx, 1.0, 2.5)
            summary["scroll_px"] += await self._scroll_plan(
                orchestrator, ctx, passes=ctx.random.think_steps(2, 5))
            summary["link_clicks"] += await self._follow_link(orchestrator, ctx)
            halt = await self._check_interdiction(orchestrator, ctx)
            if halt:
                summary["halts"].append(halt)
                break
            await self._pause(ctx)
        return summary

    async def _follow_link(self, orchestrator, ctx: WarmupContext) -> int:
        if ctx.dry_run:
            orchestrator.sessions.record_interaction(ctx.session_id, "warmup_link_click", {
                "strategy": "random_internal", "simulated": True})
            return 1
        try:
            links = ctx.page.locator(self.ARTICLE_LINKS)
            count = await links.count()
        except Exception:
            return 0
        if count == 0:
            return 0
        try:
            idx = ctx.random.choice(range(min(count, 10)))
            await orchestrator.browser.click_element(
                ctx.page, f"({self.ARTICLE_LINKS}) >> nth={idx}", ctx.behavior)
            await self._pause(ctx, 1.5, 3.0)
            await self._scroll_plan(orchestrator, ctx, passes=ctx.random.think_steps(1, 3))
            orchestrator.sessions.record_interaction(ctx.session_id, "warmup_link_click", {
                "link_index": idx, "candidate_count": min(count, 10)})
            return 1
        except Exception as exc:
            orchestrator.sessions.record_error(ctx.session_id, f"surfer link click failed: {exc}")
            return 0


BEHAVIOR_PROFILES: Dict[str, BehaviorProfile] = {
    "maps_reviewer": MapsReviewerProfile(),
    "general_surfer": GeneralSurferProfile(),
}

# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class WarmupAccountResult:
    """Outcome of warming up a single account."""

    account_id: str
    session_id: str
    behavior_profile: str
    success: bool
    fingerprint_key: str = ""
    proxy_key: Optional[str] = None
    latency_profile: str = ""
    activities: Dict[str, Any] = field(default_factory=dict)
    detection_events: List[Dict[str, Any]] = field(default_factory=list)
    detection_score: float = 0.0
    error: Optional[str] = None
    duration_sec: float = 0.0

    def summary(self) -> str:
        status = "OK" if self.success else f"FAILED ({self.error})"
        return (f"[{self.account_id}] {self.behavior_profile}: {status} "
                f"score={self.detection_score:.2f} "
                f"detections={len(self.detection_events)} "
                f"duration={self.duration_sec:.1f}s")


@dataclass
class WarmupRunResult:
    """Aggregated outcome of warming a whole inventory sequentially."""

    run_id: str
    account_results: List[WarmupAccountResult] = field(default_factory=list)
    duration_sec: float = 0.0

    @property
    def succeeded(self) -> List[WarmupAccountResult]:
        return [r for r in self.account_results if r.success]

    @property
    def failed(self) -> List[WarmupAccountResult]:
        return [r for r in self.account_results if not r.success]

    @property
    def success_rate(self) -> float:
        if not self.account_results:
            return 0.0
        return len(self.succeeded) / len(self.account_results)

    def summary(self) -> str:
        lines = [f"Warm-up run '{self.run_id}': "
                 f"{len(self.succeeded)}/{len(self.account_results)} succeeded "
                 f"({self.success_rate:.0%}) in {self.duration_sec:.1f}s"]
        lines.extend(f"  {r.summary()}" for r in self.account_results)
        return "\n".join(lines)

# --------------------------------------------------------------------------- #
# Warm-up orchestrator (WorkflowOrchestrator extension)
# --------------------------------------------------------------------------- #
class WarmupOrchestrator(WorkflowOrchestrator):
    """Runs warm-up BehaviorProfiles across an account inventory.

    Each account gets its own sequential session with matched fingerprint
    + proxy, fully logged via SessionManager, with guaranteed per-account
    browser teardown (even on failure).
    """

    def __init__(self, *args: Any, seed: Optional[int] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.random = RandomizationEngine(seed=seed)
        self._seed = seed
        self._run_counter = 0

    # -- behavior registry ------------------------------------------- #
    @staticmethod
    def available_profiles() -> List[str]:
        return sorted(BEHAVIOR_PROFILES)

    @staticmethod
    def get_behavior_profile(name: str) -> BehaviorProfile:
        try:
            return BEHAVIOR_PROFILES[name]
        except KeyError:
            raise ValueError(
                f"Unknown behavior profile '{name}'. "
                f"Available: {sorted(BEHAVIOR_PROFILES)}")

    # -- identity resolution ----------------------------------------- #
    def resolve_fingerprint(self, account: AccountRecord) -> BrowserFingerprint:
        """Matched fingerprint: persisted file wins, else profile generate."""
        if account.fingerprint_file:
            try:
                return self.fingerprints.load_fingerprint_from_file(
                    account.fingerprint_file)
            except FileNotFoundError:
                logger.warning("Fingerprint file '%s' missing for account '%s'; "
                               "generating from profile '%s'",
                               account.fingerprint_file, account.account_id,
                               account.fingerprint_profile)
        return self.fingerprints.generate_fingerprint(profile=account.fingerprint_profile)

    async def resolve_proxy(self, account: AccountRecord) -> Optional[ProxyConfig]:
        """Matched proxy: inline config wins, else pool criteria lookup."""
        if account.proxy is not None:
            return account.proxy
        if self.proxies is None:
            return None
        if not account.proxy_criteria:
            return await self.proxies.get_proxy(criteria={})
        return await self.proxies.get_proxy(criteria=account.proxy_criteria)

    # -- single-account warm-up -------------------------------------- #
    async def warmup_account(
        self,
        account: AccountRecord,
        headless: bool = True,
        dry_run: bool = False,
        max_activities: Optional[int] = None,
    ) -> WarmupAccountResult:
        """Warm one account: session + behavior run + teardown.

        Browser teardown is guaranteed via try/finally, and the session
        is always ended (success or failure) so the audit trail is
        complete. Interdictions (CAPTCHA/verification) are logged as
        detection events but do NOT fail the account unless the
        behavior itself raises.
        """
        started = time.perf_counter()
        self._run_counter += 1
        behavior_name = account.behavior_profile
        # Per-account randomness: derive from the master seed + counter so
        # sessions differ even when run back-to-back.
        seed = None if self._seed is None else self._seed + self._run_counter
        rng = RandomizationEngine(seed=seed)

        fingerprint = self.resolve_fingerprint(account)
        try:
            latency = self.network.get_preset_profile(account.latency_profile)
        except Exception as exc:
            logger.warning("Latency profile '%s' unknown for '%s': %s",
                           account.latency_profile, account.account_id, exc)
            latency = self.network.get_preset_profile("residential_cable")
        session_id = f"warmup_{account.account_id}_{int(time.time())}"
        behavior = rng.jitter_behavior(self.behavior)

        proxy: Optional[ProxyConfig] = None
        try:
            proxy = await self.resolve_proxy(account)
        except Exception as exc:
            logger.warning("Proxy resolution failed for '%s': %s", account.account_id, exc)
            proxy = None

        self.sessions.start_session(
            session_id, fingerprint, proxy=None,
            latency_profile=latency, proxy_resolved=proxy,
        )
        self.sessions.record_interaction(session_id, "warmup_start", {
            "account_id": account.account_id,
            "username": account.username,
            "behavior_profile": behavior_name,
            "fingerprint_platform": fingerprint.platform,
            "latency_profile": latency.name,
            "dry_run": dry_run,
        })
        self.sessions.record_interaction(session_id, "warmup_proxy", {
            "account_id": account.account_id,
            "proxy": proxy.key if proxy else None,
        })
        browser = None
        page = None
        activities: Dict[str, Any] = {}
        error: Optional[str] = None
        try:
            profile = self.get_behavior_profile(behavior_name)
            if not dry_run:
                browser = await self.browser.initialize_browser(
                    fingerprint, proxy, headless=headless)
                context = await self.browser.create_context(browser, fingerprint)
                page = await context.new_page()
                await self.network.apply_latency_profile(page, latency)
            ctx = WarmupContext(
                page=page, browser=browser, session_id=session_id,
                account=account, fingerprint=fingerprint, proxy=proxy,
                behavior=behavior, random=rng, dry_run=dry_run)
            activities = await profile.run(ctx, self)
            if max_activities is not None:
                activities["capped_at"] = max_activities
                for key in ("searches", "visits"):
                    if isinstance(activities.get(key), list):
                        activities[key] = activities[key][:max_activities]
            self.sessions.record_interaction(session_id, "warmup_completed", {
                "account_id": account.account_id, "summary": activities})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.sessions.record_error(session_id, error)
            logger.exception("Warm-up failed for account '%s'", account.account_id)
        finally:
            if browser is not None:
                try:
                    await self.browser.close_browser(browser)
                except Exception as exc:
                    logger.debug("Teardown close failed: %s", exc)
            success = error is None
            session = self.sessions.end_session(session_id, success, error)
            score = self.sessions.calculate_detection_score(session)

        duration = round(time.perf_counter() - started, 3)
        return WarmupAccountResult(
            account_id=account.account_id, session_id=session_id,
            behavior_profile=behavior_name, success=success,
            fingerprint_key=fingerprint.platform,
            proxy_key=proxy.key if proxy else None,
            latency_profile=latency.name, activities=activities,
            detection_events=list(session.detection_events),
            detection_score=score, error=error, duration_sec=duration)

    # -- inventory-wide sequential run ------------------------------- #
    async def warmup_inventory(
        self,
        inventory: AccountInventoryManager,
        headless: bool = True,
        dry_run: bool = False,
        account_ids: Optional[Sequence[str]] = None,
        cooldown_sec: float = 1.0,
        run_id: Optional[str] = None,
    ) -> WarmupRunResult:
        """Warm accounts sequentially with teardown between each one.

        Sequential (never concurrent) by design: one live browser identity
        at a time keeps fingerprint/proxy attribution clean. A randomized
        cooldown separates accounts so inter-session timing is not uniform.
        Every account outcome is logged; a single failure never aborts the
        run -- it is recorded in the aggregate result.
        """
        started = time.perf_counter()
        run_id = run_id or f"warmup_run_{int(time.time())}"
        targets = ([inventory.get_account(aid) for aid in account_ids]
                   if account_ids is not None else inventory.accounts)
        result = WarmupRunResult(run_id=run_id)
        logger.info("Warm-up run '%s': %d account(s)", run_id, len(targets))
        for pos, account in enumerate(targets):
            account_result = await self.warmup_account(
                account, headless=headless, dry_run=dry_run)
            result.account_results.append(account_result)
            logger.info("  [%d/%d] %s", pos + 1, len(targets),
                        account_result.summary())
            if pos < len(targets) - 1 and cooldown_sec > 0 and not dry_run:
                await asyncio.sleep(cooldown_sec * random.uniform(0.7, 1.3))
        result.duration_sec = round(time.perf_counter() - started, 3)
        return result

