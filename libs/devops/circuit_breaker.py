"""Circuit breaker / fuse matrix for the DevOps pipeline.

Implements the fuse matrix from the architecture spec.  Each node in the
pipeline has a maximum retry count; exceeding it triggers escalation to
human intervention.

The CircuitBreaker is a pure-code component — no LLM involvement.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .types import EscalationRequest, PipelineState, _now

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Fuse blown — escalate to human
    HALF_OPEN = "half_open" # Tentative retry (optional)


@dataclass
class BreakerConfig:
    """Configuration for a single circuit breaker node."""

    node: str
    max_failures: int = 2
    description: str = ""


# Default fuse matrix from the architecture spec
DEFAULT_FUSE_MATRIX: list[BreakerConfig] = [
    BreakerConfig(
        node="bug_recurrence",
        max_failures=3,
        description="Reproduction failure >= 3 times",
    ),
    BreakerConfig(
        node="plan_review",
        max_failures=2,
        description="Plan rejected >= 2 times",
    ),
    BreakerConfig(
        node="code_reviewer",
        max_failures=2,
        description="Code review rejected >= 2 times",
    ),
    BreakerConfig(
        node="sandbox_runner",
        max_failures=2,
        description="Test/Lint/Mutation failure >= 2 times",
    ),
    BreakerConfig(
        node="canary_5",
        max_failures=1,
        description="Error rate > 0.1% or P99 exceeds threshold at 5%",
    ),
    BreakerConfig(
        node="canary_25",
        max_failures=1,
        description="Core business metric anomaly at 25%",
    ),
    BreakerConfig(
        node="high_risk_change",
        max_failures=0,     # Always requires human review
        description="Involves payment / auth / DB schema changes",
    ),
]


@dataclass
class _NodeState:
    """Internal tracking for a single breaker node."""

    failures: int = 0
    state: BreakerState = BreakerState.CLOSED
    last_failure_reason: str = ""


class CircuitBreaker:
    """Orchestrator-level circuit breaker manager.

    Tracks failure counts per node and decides when to escalate.

    Example::

        cb = CircuitBreaker()
        # Record a failure
        escalation = cb.record_failure("plan_review", "Plan lacks rollback strategy")
        if escalation:
            # Fuse blown — halt pipeline and notify human
            ...
    """

    def __init__(
        self,
        matrix: list[BreakerConfig] | None = None,
        on_escalation: Callable[[EscalationRequest], None] | None = None,
    ) -> None:
        self._matrix = {c.node: c for c in (matrix or DEFAULT_FUSE_MATRIX)}
        self._states: dict[str, _NodeState] = {
            node: _NodeState() for node in self._matrix
        }
        self._on_escalation = on_escalation

    def record_failure(
        self,
        node: str,
        reason: str = "",
        trace_id: str = "",
    ) -> EscalationRequest | None:
        """Record a failure at *node*.  Returns an EscalationRequest if the
        breaker trips (fuse blown), otherwise None.

        Args:
            node: The pipeline node that failed.
            reason: Human-readable failure reason.
            trace_id: Pipeline trace ID for correlation.

        Returns:
            EscalationRequest if fuse is blown; None if retries remain.
        """
        config = self._matrix.get(node)
        if config is None:
            logger.warning("Unknown breaker node: %s", node)
            return None

        state = self._states[node]
        state.failures += 1
        state.last_failure_reason = reason

        logger.info(
            "CircuitBreaker [%s]: failure %d/%d — %s",
            node,
            state.failures,
            config.max_failures,
            reason,
        )

        if state.failures >= config.max_failures:
            state.state = BreakerState.OPEN
            escalation = EscalationRequest(
                trace_id=trace_id,
                reason=f"Circuit breaker tripped at '{node}': {reason} "
                       f"(failures={state.failures}/{config.max_failures})",
                failed_stage=self._node_to_pipeline_state(node),
                context_summary=config.description,
                created_at=_now(),
            )
            logger.warning("FUSE BLOWN at '%s': %s", node, escalation.reason)
            if self._on_escalation:
                self._on_escalation(escalation)
            return escalation

        return None

    def record_success(self, node: str) -> None:
        """Record a success at *node*, resetting its failure counter."""
        state = self._states.get(node)
        if state:
            state.failures = 0
            state.state = BreakerState.CLOSED
            state.last_failure_reason = ""

    def is_open(self, node: str) -> bool:
        """Check if a node's breaker is in the OPEN (tripped) state."""
        state = self._states.get(node)
        return state is not None and state.state == BreakerState.OPEN

    def reset(self, node: str | None = None) -> None:
        """Reset one or all breakers (e.g. after human resolution)."""
        if node:
            state = self._states.get(node)
            if state:
                state.failures = 0
                state.state = BreakerState.CLOSED
                state.last_failure_reason = ""
        else:
            for state in self._states.values():
                state.failures = 0
                state.state = BreakerState.CLOSED
                state.last_failure_reason = ""

    def get_status(self) -> dict[str, dict]:
        """Return a summary dict of all breaker states."""
        result = {}
        for node, state in self._states.items():
            config = self._matrix[node]
            result[node] = {
                "failures": state.failures,
                "max_failures": config.max_failures,
                "state": state.state.value,
                "last_failure_reason": state.last_failure_reason,
            }
        return result

    @staticmethod
    def _node_to_pipeline_state(node: str) -> PipelineState:
        """Map a breaker node name to the corresponding PipelineState."""
        mapping = {
            "bug_recurrence": PipelineState.BUG_RECURRENCE,
            "plan_review": PipelineState.PLAN_REVIEW,
            "code_reviewer": PipelineState.INNER_LOOP,
            "sandbox_runner": PipelineState.INNER_LOOP,
            "canary_5": PipelineState.OUTER_LOOP,
            "canary_25": PipelineState.OUTER_LOOP,
            "high_risk_change": PipelineState.PR_GENERATION,
        }
        return mapping.get(node, PipelineState.FAILED)
