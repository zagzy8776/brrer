"""Target-specific registration workflow definitions.

Each module under this package is a *configuration object* — it contains no
browser automation logic of its own. It declares the steps, selectors, branch
rules, data mappings, and success criteria that the framework's
``WorkflowOrchestrator`` executes. This keeps target-specific knowledge
separate from the framework's core engine (``bftf.workflows``).
"""
from .base import TargetWorkflowConfiguration
from .google_registration_target import GOOGLE_REGISTRATION_TARGET

__all__ = ["TargetWorkflowConfiguration", "GOOGLE_REGISTRATION_TARGET"]
