"""Session lifecycle tracking, data collection, and result analysis."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import BrowserFingerprint, LatencyProfile, ProxyConfig, SessionData

logger = logging.getLogger(__name__)

# Weights applied to individual detection event types when scoring.
_DETECTION_WEIGHTS: Dict[str, float] = {
    "captcha": 0.40,
    "blocked": 0.50,
    "challenge": 0.35,
    "rate_limited": 0.25,
    "fingerprint_mismatch": 0.20,
    "unusual_traffic": 0.20,
    "access_denied": 0.45,
}
_DEFAULT_WEIGHT = 0.15


def _tally(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


class SessionManager:
    """Manages test session lifecycle and data collection."""

    def __init__(self, output_dir: str) -> None:
        self.output_dir = Path(output_dir)
        self.sessions_dir = self.output_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: Dict[str, SessionData] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start_session(
        self,
        session_id: str,
        fingerprint: BrowserFingerprint,
        proxy: Optional[ProxyConfig] = None,
        latency_profile: Optional[LatencyProfile] = None,
        proxy_resolved: Optional[ProxyConfig] = None,
    ) -> SessionData:
        """Start a new tracked test session.

        Args:
            session_id: unique identifier for the session.
            fingerprint: the browser fingerprint applied to this session.
            proxy: optional proxy criteria/filter (pre-resolution).
            latency_profile: optional latency profile.
            proxy_resolved: the fully resolved proxy (credentials expanded)
                assigned to the session.  Only ``proxy_resolved.key``
                (host:port) is logged so credentials never leak into
                session artefacts or log streams.
        """
        effective_proxy = proxy_resolved if proxy_resolved is not None else proxy
        session = SessionData(
            session_id=session_id,
            start_time=datetime.now(),
            fingerprint=fingerprint,
            proxy=effective_proxy,
            latency_profile=latency_profile,
        )
        self._sessions[session_id] = session
        logger.info(
            "Session '%s' started (proxy=%s)",
            session_id, effective_proxy.key if effective_proxy else "none",
        )
        return session

    def end_session(self, session_id: str, success: bool, error_message: Optional[str] = None) -> SessionData:
        """End a session, finalize its data, and persist it to disk."""
        session = self._require(session_id)
        session.end_time = datetime.now()
        session.success = success
        session.error_message = error_message
        self.save_session_data(session)
        return session

    def get_session(self, session_id: str) -> Optional[SessionData]:
        return self._sessions.get(session_id)

    def _require(self, session_id: str) -> SessionData:
        if session_id not in self._sessions:
            raise KeyError(f"Unknown session '{session_id}'")
        return self._sessions[session_id]

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def record_interaction(self, session_id: str, interaction_type: str, data: Dict[str, Any]) -> None:
        """Record an interaction event (action, navigation, timing...)."""
        session = self._require(session_id)
        session.interactions.append({
            "type": interaction_type,
            "timestamp": datetime.now().isoformat(),
            **data,
        })

    def record_detection_event(self, session_id: str, event_type: str, details: Dict[str, Any]) -> None:
        """Record a detection or blocking event observed during the session."""
        session = self._require(session_id)
        session.detection_events.append({
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            **details,
        })
        logger.info("Detection event on '%s': %s", session_id, event_type)

    def record_error(self, session_id: str, message: str) -> None:
        self._require(session_id).errors.append(message)

    def record_screenshot(self, session_id: str, filepath: str) -> None:
        self._require(session_id).screenshots.append(filepath)

    # ------------------------------------------------------------------ #
    # Analysis
    # ------------------------------------------------------------------ #
    @staticmethod
    def calculate_detection_score(session_data: SessionData) -> float:
        """Calculate detection likelihood in [0.0, 1.0] from session data."""
        if session_data.end_time is None:
            raise ValueError("Session must be ended before scoring")
        score = 0.0
        for event in session_data.detection_events:
            score += _DETECTION_WEIGHTS.get(event.get("event_type"), _DEFAULT_WEIGHT)
        if not session_data.success:
            score += 0.1
        if session_data.errors:
            score += 0.05 * min(len(session_data.errors), 5)
        return min(1.0, round(score, 4))

    @staticmethod
    def calculate_performance_metrics(session_data: SessionData) -> Dict[str, float]:
        """Derive basic performance metrics from recorded interactions."""
        load_times = [
            i.get("load_time_ms") for i in session_data.interactions
            if i.get("load_time_ms") is not None
        ]
        action_times = [
            i.get("action_time_ms") for i in session_data.interactions
            if i.get("action_time_ms") is not None
        ]
        return {
            "duration_sec": session_data.duration_sec,
            "interaction_count": float(len(session_data.interactions)),
            "avg_load_time_ms": sum(load_times) / len(load_times) if load_times else 0.0,
            "avg_action_time_ms": sum(action_times) / len(action_times) if action_times else 0.0,
        }

    def analyze_sessions(self, session_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Analyze multiple sessions and generate an aggregate report."""
        ids = session_ids or list(self._sessions)
        sessions = [self._require(sid) for sid in ids]
        if not sessions:
            return {"total_sessions": 0}
        scores = [self.calculate_detection_score(s) for s in sessions]
        return {
            "total_sessions": len(sessions),
            "success_rate": sum(1 for s in sessions if s.success) / len(sessions),
            "avg_detection_score": sum(scores) / len(scores),
            "max_detection_score": max(scores),
            "total_detection_events": sum(len(s.detection_events) for s in sessions),
            "avg_duration_sec": sum(s.duration_sec for s in sessions) / len(sessions),
            "detection_event_types": _tally(
                e.get("event_type", "unknown") for s in sessions for e in s.detection_events
            ),
        }

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def save_session_data(self, session: SessionData, filepath: Optional[str] = None) -> str:
        """Persist session data to a JSON file; returns the path written."""
        path = Path(filepath) if filepath else self.sessions_dir / f"{session.session_id}.json"
        session.save_to(str(path))
        return str(path)
