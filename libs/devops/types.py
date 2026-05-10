"""Shared data types for the DevOps dual-loop convergence architecture.

All data transfer objects are plain dataclasses — pure data carriers with no behavior.
They flow through the Orchestrator's state machine and are persisted in SharedContext.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Bug severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PipelineState(str, Enum):
    """States of the orchestrator state machine."""
    IDLE = "idle"
    BUG_ANALYSIS = "bug_analysis"
    BUG_RECURRENCE = "bug_recurrence"
    TEST_DRIVING = "test_driving"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    INNER_LOOP = "inner_loop"          # TDD-Executor + Code-Reviewer + Sandbox
    PR_GENERATION = "pr_generation"
    CI_BUILD = "ci_build"
    OUTER_LOOP = "outer_loop"          # Canary deployment + SRE-Guard
    COMPLETED = "completed"
    HUMAN_ESCALATION = "human_escalation"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class ReviewVerdict(str, Enum):
    """Outcome of a review (Plan-Review or Code-Reviewer)."""
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class CanaryStage(str, Enum):
    """Traffic percentages during canary rollout."""
    STAGE_5 = "5%"
    STAGE_25 = "25%"
    STAGE_50 = "50%"
    STAGE_100 = "100%"


class RolloutDecision(str, Enum):
    """Decision from the SRE-Guard during a canary stage."""
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    HOLD = "hold"


class RiskLevel(str, Enum):
    """Risk classification for a change."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"       # Requires human review gate


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


def _trace_id() -> str:
    return uuid4().hex[:12]


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass
class BugReport:
    """Structured bug analysis produced by Bug-Analyzer."""

    trace_id: str = field(default_factory=_trace_id)
    title: str = ""
    summary: str = ""
    severity: Severity = Severity.MEDIUM
    affected_service: str = ""
    affected_files: list[str] = field(default_factory=list)
    log_snippets: list[str] = field(default_factory=list)
    stack_trace: str = ""
    root_cause_hypothesis: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class ReproductionResult:
    """Outcome of the Bug-Recurrence agent's attempt to reproduce the bug."""

    trace_id: str = ""
    script_path: str = ""
    script_content: str = ""
    reproduced: bool = False
    output: str = ""
    attempts: int = 0
    max_attempts: int = 3


@dataclass
class RedTest:
    """A failing test written by Test-Driver that defines the correctness boundary."""

    trace_id: str = ""
    test_file_path: str = ""
    test_content: str = ""
    test_name: str = ""
    description: str = ""


@dataclass
class FixPlan:
    """A structured plan for fixing the bug, produced by Planner."""

    trace_id: str = ""
    strategy: str = ""                          # High-level approach
    files_to_modify: list[str] = field(default_factory=list)
    max_lines_changed: int = 50                 # Budget constraint
    estimated_risk: RiskLevel = RiskLevel.LOW
    steps: list[str] = field(default_factory=list)
    rationale: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class PlanReviewResult:
    """Outcome of the Plan-Review agent's adversarial validation."""

    trace_id: str = ""
    verdict: ReviewVerdict = ReviewVerdict.APPROVE
    concerns: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    attempt: int = 0
    max_attempts: int = 2


@dataclass
class Patch:
    """A code patch produced by TDD-Executor."""

    trace_id: str = ""
    files_changed: dict[str, str] = field(default_factory=dict)   # path -> new content
    diff: str = ""
    description: str = ""
    iteration: int = 0


@dataclass
class CodeReviewResult:
    """Outcome of the Code-Reviewer's AI review of a Patch."""

    trace_id: str = ""
    verdict: ReviewVerdict = ReviewVerdict.APPROVE
    issues: list[str] = field(default_factory=list)
    hallucination_flags: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    iteration: int = 0


@dataclass
class SandboxResult:
    """Deterministic test results from the Sandbox Runner."""

    trace_id: str = ""
    compile_ok: bool = False
    unit_tests_passed: bool = False
    unit_tests_total: int = 0
    unit_tests_failed: int = 0
    lint_ok: bool = False
    lint_errors: list[str] = field(default_factory=list)
    mutation_score: float = 0.0           # 0.0 - 1.0
    mutation_threshold: float = 0.8       # Minimum acceptable score
    all_passed: bool = False
    output: str = ""
    iteration: int = 0


@dataclass
class PRInfo:
    """Information about a generated Pull Request."""

    trace_id: str = ""
    branch_name: str = ""
    pr_url: str = ""
    pr_number: int = 0
    commit_sha: str = ""
    image_tag: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class BuildResult:
    """CI build result."""

    trace_id: str = ""
    image_tag: str = ""            # immutable: sha-xxx...
    registry_url: str = ""
    build_ok: bool = False
    build_log: str = ""
    opa_check_passed: bool = False
    opa_violations: list[str] = field(default_factory=list)


@dataclass
class CanaryMetrics:
    """Metrics snapshot during a canary stage."""

    stage: CanaryStage = CanaryStage.STAGE_5
    error_rate: float = 0.0               # Percentage
    error_rate_threshold: float = 0.1     # 0.1%
    p99_latency_ms: float = 0.0
    p99_threshold_ms: float = 500.0
    success_rate: float = 100.0
    custom_slo_ok: bool = True
    observation_window_sec: int = 300     # 5 minutes
    collected_at: str = field(default_factory=_now)


@dataclass
class RolloutStatus:
    """Current state of the canary rollout."""

    trace_id: str = ""
    current_stage: CanaryStage = CanaryStage.STAGE_5
    decision: RolloutDecision = RolloutDecision.HOLD
    metrics_history: list[CanaryMetrics] = field(default_factory=list)
    rollback_reason: str = ""
    completed: bool = False
    started_at: str = field(default_factory=_now)
    finished_at: str = ""


@dataclass
class EscalationRequest:
    """Request for human intervention when circuit breaker triggers."""

    trace_id: str = ""
    reason: str = ""
    failed_stage: PipelineState = PipelineState.IDLE
    context_summary: str = ""
    created_at: str = field(default_factory=_now)


# ---------------------------------------------------------------------------
# Pipeline-level aggregate
# ---------------------------------------------------------------------------


@dataclass
class PipelineRun:
    """Full state of a single pipeline execution — persisted by the Orchestrator."""

    trace_id: str = field(default_factory=_trace_id)
    state: PipelineState = PipelineState.IDLE

    # Artifacts produced at each stage
    bug_report: BugReport | None = None
    reproduction: ReproductionResult | None = None
    red_tests: list[RedTest] = field(default_factory=list)
    fix_plan: FixPlan | None = None
    plan_reviews: list[PlanReviewResult] = field(default_factory=list)
    patches: list[Patch] = field(default_factory=list)
    code_reviews: list[CodeReviewResult] = field(default_factory=list)
    sandbox_results: list[SandboxResult] = field(default_factory=list)
    pr_info: PRInfo | None = None
    build_result: BuildResult | None = None
    rollout_status: RolloutStatus | None = None
    escalation: EscalationRequest | None = None

    # Metadata
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    error_log: list[str] = field(default_factory=list)

    def record_error(self, message: str) -> None:
        self.error_log.append(f"[{_now()}] {message}")
        self.updated_at = _now()
