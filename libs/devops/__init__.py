"""DevOps Dual-Loop Convergence Architecture.

A self-healing DevOps pipeline with:
- **Inner Loop (Correctness)**: LLM agents + TDD + AI Review + Sandbox
- **Outer Loop (Availability)**: Pure-code canary deploy + SLO monitoring + auto-rollback

Usage::

    from libs.devops import Orchestrator
    from libs.devops.agents import *
    from libs.devops.runners import SandboxRunner, PRGenerator
    from libs.devops.deploy import ReleaseController, SREGuard
"""

from .circuit_breaker import CircuitBreaker
from .context import SharedContext
from .orchestrator import Orchestrator
from .types import (
    BugReport,
    BuildResult,
    CanaryMetrics,
    CanaryStage,
    CodeReviewResult,
    EscalationRequest,
    FixPlan,
    Patch,
    PipelineRun,
    PipelineState,
    PlanReviewResult,
    PRInfo,
    RedTest,
    ReproductionResult,
    ReviewVerdict,
    RiskLevel,
    RolloutDecision,
    RolloutStatus,
    SandboxResult,
    Severity,
)

__all__ = [
    # Core
    "Orchestrator",
    "SharedContext",
    "CircuitBreaker",
    # Types
    "BugReport",
    "BuildResult",
    "CanaryMetrics",
    "CanaryStage",
    "CodeReviewResult",
    "EscalationRequest",
    "FixPlan",
    "Patch",
    "PipelineRun",
    "PipelineState",
    "PlanReviewResult",
    "PRInfo",
    "RedTest",
    "ReproductionResult",
    "ReviewVerdict",
    "RiskLevel",
    "RolloutDecision",
    "RolloutStatus",
    "SandboxResult",
    "Severity",
]
