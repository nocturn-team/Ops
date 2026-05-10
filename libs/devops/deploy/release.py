"""Release Controller — canary deployment orchestration.

This is a PURE CODE component (no LLM involvement).  It drives the
progressive rollout through traffic stages (5% -> 25% -> 50% -> 100%),
coordinating with the SRE-Guard at each stage.

In production, this would interface with Argo Rollouts, Flagger, or
a Service Mesh API.  Here we define the abstraction and a reference
implementation that delegates actual traffic shifting to pluggable backends.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Protocol, runtime_checkable

from ..types import (
    BuildResult,
    CanaryMetrics,
    CanaryStage,
    RolloutDecision,
    RolloutStatus,
    _now,
)

logger = logging.getLogger(__name__)

# Ordered canary stages
_CANARY_STAGES = [
    CanaryStage.STAGE_5,
    CanaryStage.STAGE_25,
    CanaryStage.STAGE_50,
    CanaryStage.STAGE_100,
]


@runtime_checkable
class DeployBackend(Protocol):
    """Protocol for the actual traffic-shifting backend.

    Implementations should integrate with Argo Rollouts, Flagger,
    Kubernetes Ingress, or a Service Mesh.
    """

    def set_traffic_weight(self, service: str, image_tag: str, weight_pct: int) -> bool:
        """Set the canary traffic weight.  Returns True on success."""
        ...

    def rollback(self, service: str) -> bool:
        """Immediately rollback to the previous stable version."""
        ...

    def promote(self, service: str) -> bool:
        """Promote the canary to full production."""
        ...


class NoOpDeployBackend:
    """Dummy backend for testing / dry-run mode."""

    def set_traffic_weight(self, service: str, image_tag: str, weight_pct: int) -> bool:
        logger.info("[DRY-RUN] Set %s traffic to %d%% (image: %s)", service, weight_pct, image_tag)
        return True

    def rollback(self, service: str) -> bool:
        logger.info("[DRY-RUN] Rolling back %s", service)
        return True

    def promote(self, service: str) -> bool:
        logger.info("[DRY-RUN] Promoting %s to full traffic", service)
        return True


class ReleaseController:
    """Orchestrates the canary deployment process.

    Drives the outer loop: for each stage it sets traffic weight, waits
    for the observation window, queries the SRE-Guard for a decision,
    and either promotes or rolls back.

    Example::

        from libs.devops.deploy.sre_guard import SREGuard

        guard = SREGuard(prometheus_url="http://prometheus:9090")
        controller = ReleaseController(
            backend=NoOpDeployBackend(),
            metrics_fn=guard.collect_metrics,
            decide_fn=guard.evaluate,
        )
        status = controller.rollout(build_result, service="payment-svc")
    """

    def __init__(
        self,
        *,
        backend: DeployBackend | None = None,
        metrics_fn: Callable[[str, CanaryStage], CanaryMetrics] | None = None,
        decide_fn: Callable[[CanaryMetrics], RolloutDecision] | None = None,
        observation_window_sec: int = 300,
    ) -> None:
        """
        Args:
            backend: Traffic-shifting backend implementation.
            metrics_fn: Callable to collect metrics for a service at a given stage.
            decide_fn: Callable to evaluate metrics and return a rollout decision.
            observation_window_sec: Seconds to wait between stages.
        """
        self._backend = backend or NoOpDeployBackend()
        self._metrics_fn = metrics_fn or self._default_metrics
        self._decide_fn = decide_fn or self._default_decide
        self._observation_window = observation_window_sec

    def rollout(
        self,
        build: BuildResult,
        *,
        service: str,
        trace_id: str = "",
    ) -> RolloutStatus:
        """Execute a full canary rollout.

        Args:
            build: The CI build result with the image tag to deploy.
            service: Target service name.
            trace_id: Pipeline trace ID.

        Returns:
            RolloutStatus documenting the outcome.
        """
        status = RolloutStatus(trace_id=trace_id)

        for stage in _CANARY_STAGES:
            weight = int(stage.value.replace("%", ""))
            status.current_stage = stage

            logger.info("Canary %s: setting traffic to %d%%", service, weight)
            ok = self._backend.set_traffic_weight(service, build.image_tag, weight)
            if not ok:
                status.decision = RolloutDecision.ROLLBACK
                status.rollback_reason = f"Failed to set traffic weight at {stage.value}"
                self._backend.rollback(service)
                status.finished_at = _now()
                return status

            # Observation window
            logger.info(
                "Canary %s: observing for %ds at %s",
                service,
                self._observation_window,
                stage.value,
            )
            time.sleep(self._observation_window)

            # Collect and evaluate metrics
            metrics = self._metrics_fn(service, stage)
            metrics.stage = stage
            status.metrics_history.append(metrics)

            decision = self._decide_fn(metrics)
            logger.info("Canary %s at %s: decision=%s", service, stage.value, decision.value)

            if decision == RolloutDecision.ROLLBACK:
                status.decision = RolloutDecision.ROLLBACK
                status.rollback_reason = (
                    f"Metrics exceeded threshold at {stage.value}: "
                    f"error_rate={metrics.error_rate:.3f}% "
                    f"p99={metrics.p99_latency_ms:.1f}ms"
                )
                self._backend.rollback(service)
                status.finished_at = _now()
                logger.warning("ROLLBACK at %s: %s", stage.value, status.rollback_reason)
                return status

            if decision == RolloutDecision.HOLD:
                # Wait another observation window and re-check
                logger.info("HOLD at %s — waiting another cycle", stage.value)
                time.sleep(self._observation_window)
                metrics2 = self._metrics_fn(service, stage)
                decision2 = self._decide_fn(metrics2)
                if decision2 != RolloutDecision.PROMOTE:
                    status.decision = RolloutDecision.ROLLBACK
                    status.rollback_reason = f"Still not healthy after HOLD at {stage.value}"
                    self._backend.rollback(service)
                    status.finished_at = _now()
                    return status

        # All stages passed — promote
        self._backend.promote(service)
        status.decision = RolloutDecision.PROMOTE
        status.completed = True
        status.finished_at = _now()
        logger.info("Canary rollout COMPLETED for %s", service)
        return status

    # -- default implementations -------------------------------------------

    @staticmethod
    def _default_metrics(service: str, stage: CanaryStage) -> CanaryMetrics:
        """Default metrics collector — returns healthy metrics (for testing)."""
        return CanaryMetrics(
            stage=stage,
            error_rate=0.0,
            p99_latency_ms=50.0,
            success_rate=100.0,
        )

    @staticmethod
    def _default_decide(metrics: CanaryMetrics) -> RolloutDecision:
        """Default decision logic based on metrics thresholds."""
        if metrics.error_rate > metrics.error_rate_threshold:
            return RolloutDecision.ROLLBACK
        if metrics.p99_latency_ms > metrics.p99_threshold_ms:
            return RolloutDecision.ROLLBACK
        if not metrics.custom_slo_ok:
            return RolloutDecision.ROLLBACK
        return RolloutDecision.PROMOTE
