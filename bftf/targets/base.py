"""Base container for target-specific registration workflow configurations.

A ``TargetWorkflowConfiguration`` bundles everything the
``WorkflowOrchestrator`` needs to execute a registration sequence against one
specific target:

- ``workflow_id`` / ``description``: identity and research notes.
- ``steps``: ordered ``WorkflowStep`` state machine (Step B ``depends_on``
  Step A).
- ``fingerprint_profile`` / ``latency_profile`` / ``proxy_criteria``:
  research context applied to every attempt's session.
- ``success_criteria``: human-readable definition of COMPLETED, mirrored by
  machine-checkable ``completion_validation`` rules evaluated after the final
  step.
- ``expected_data_tokens``: the ``{{tokens}}`` the configuration consumes,
  so execution scripts can validate a ``UserProfile`` up front.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..workflows import WorkflowStep


@dataclass
class TargetWorkflowConfiguration:
    """Declarative registration sequence for a single target service."""

    workflow_id: str
    description: str
    steps: List[WorkflowStep]
    fingerprint_profile: str = "residential_windows"
    latency_profile: str = "residential_cable"
    proxy_criteria: Dict[str, Any] = field(default_factory=dict)
    success_criteria: str = ""
    completion_validation: List[Dict[str, Any]] = field(default_factory=list)
    expected_data_tokens: List[str] = field(default_factory=list)
    target_name: str = ""
    entry_url: str = ""
    research_notes: str = ""

    def __post_init__(self) -> None:
        if not self.workflow_id:
            raise ValueError("workflow_id must be non-empty")
        if not self.steps:
            raise ValueError("Target configuration must define at least one step")
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Target configuration step ids must be unique")

    @property
    def step_ids(self) -> List[str]:
        """Ordered step ids forming the happy-path state machine."""
        return [s.step_id for s in self.steps]

    def validate_profile_tokens(self, tokens: Dict[str, str]) -> List[str]:
        """Return the subset of ``expected_data_tokens`` missing from ``tokens``."""
        return [t for t in self.expected_data_tokens if not tokens.get(t)]

    def summary(self) -> str:
        """One-page human-readable description for research logs."""
        lines = [
            f"Target: {self.target_name or self.workflow_id}",
            f"Description: {self.description}",
            f"Entry: {self.entry_url}",
            f"Steps ({len(self.steps)}): " + " -> ".join(self.step_ids),
            f"Success criteria: {self.success_criteria}",
            f"Expected tokens: {', '.join(self.expected_data_tokens)}",
        ]
        if self.research_notes:
            lines.append(f"Notes: {self.research_notes}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize (steps as step_id/name/depends_on) for reproducible artefacts."""
        return {
            "workflow_id": self.workflow_id,
            "target_name": self.target_name,
            "description": self.description,
            "entry_url": self.entry_url,
            "fingerprint_profile": self.fingerprint_profile,
            "latency_profile": self.latency_profile,
            "proxy_criteria": dict(self.proxy_criteria),
            "steps": [
                {
                    "step_id": s.step_id,
                    "name": s.name,
                    "depends_on": list(s.depends_on),
                    "max_retries": s.max_retries,
                    "branches": [b.name for b in s.branches],
                }
                for s in self.steps
            ],
            "success_criteria": self.success_criteria,
            "completion_validation": list(self.completion_validation),
            "expected_data_tokens": list(self.expected_data_tokens),
            "research_notes": self.research_notes,
        }

    def describe_step(self, step_id: str) -> Optional[str]:
        """Human-readable one-liner for a step; None when unknown."""
        for step in self.steps:
            if step.step_id == step_id:
                deps = f" (after: {', '.join(step.depends_on)})" if step.depends_on else ""
                branches = (
                    f" [branches: {', '.join(b.name for b in step.branches)}]"
                    if step.branches
                    else ""
                )
                return f"{step.step_id}: {step.name}{deps}{branches}"
        return None
