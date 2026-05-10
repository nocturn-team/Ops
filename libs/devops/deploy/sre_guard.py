"""SRE-Guard — Prometheus + Alertmanager + auto-rollback.

This is a PURE CODE component (no LLM involvement in real-time decisions).
It collects metrics from Prometheus, evaluates SLOs, and triggers
auto-rollback via the Release Controller when thresholds are breached.

LLM is only involved post-incident: reading logs and producing reports.
The SRE-Guard never waits for LLM decisions during live traffic.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..types import CanaryMetrics, CanaryStage, RolloutDecision, _now

logger = logging.getLogger(__name__)


class SREGuard:
    """Monitors service health and makes millisecond-level rollback decisions.

    In production, this queries Prometheus for real metrics.  The class
    also provides ``evaluate()`` as a pure function for the Release
    Controller to call.

    Example::

        guard = SREGuard(prometheus_url="http://prometheus:9090")
        metrics = guard.collect_metrics("payment-svc", CanaryStage.STAGE_5)
        decision = guard.evaluate(metrics)
    """

    def __init__(
        self,
        *,
        prometheus_url: str = "http://localhost:9090",
        error_rate_threshold: float = 0.1,       # 0.1%
        p99_threshold_ms: float = 500.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        """
        Args:
            prometheus_url: Prometheus server base URL.
            error_rate_threshold: Max acceptable error rate (percentage).
            p99_threshold_ms: Max acceptable P99 latency (milliseconds).
            timeout_seconds: HTTP request timeout.
        """
        self.prometheus_url = prometheus_url.rstrip("/")
        self.error_rate_threshold = error_rate_threshold
        self.p99_threshold_ms = p99_threshold_ms
        self._client = httpx.Client(timeout=timeout_seconds)

    def collect_metrics(
        self,
        service: str,
        stage: CanaryStage,
    ) -> CanaryMetrics:
        """Collect current metrics for a service from Prometheus.

        Args:
            service: Service name (used in PromQL label selectors).
            stage: Current canary stage.

        Returns:
            CanaryMetrics snapshot.
        """
        metrics = CanaryMetrics(
            stage=stage,
            error_rate_threshold=self.error_rate_threshold,
            p99_threshold_ms=self.p99_threshold_ms,
        )

        try:
            # Query error rate
            error_rate = self._query_prometheus(
                f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[5m]))'
                f' / sum(rate(http_requests_total{{service="{service}"}}[5m])) * 100'
            )
            metrics.error_rate = error_rate

            # Query P99 latency
            p99 = self._query_prometheus(
                f'histogram_quantile(0.99, '
                f'sum(rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m])) '
                f'by (le)) * 1000'
            )
            metrics.p99_latency_ms = p99

            # Query success rate
            success_rate = self._query_prometheus(
                f'sum(rate(http_requests_total{{service="{service}",status=~"2.."}}[5m]))'
                f' / sum(rate(http_requests_total{{service="{service}"}}[5m])) * 100'
            )
            metrics.success_rate = success_rate

        except Exception as e:
            logger.warning("Failed to collect metrics for %s: %s", service, e)
            # On collection failure, assume unhealthy to trigger rollback
            metrics.error_rate = 100.0
            metrics.custom_slo_ok = False

        return metrics

    def evaluate(self, metrics: CanaryMetrics) -> RolloutDecision:
        """Evaluate metrics against SLO thresholds.

        This is a PURE FUNCTION — no side effects, no LLM, no network calls.
        Millisecond-level decision.

        Args:
            metrics: Current metrics snapshot.

        Returns:
            RolloutDecision: PROMOTE, ROLLBACK, or HOLD.
        """
        reasons: list[str] = []

        if metrics.error_rate > metrics.error_rate_threshold:
            reasons.append(
                f"Error rate {metrics.error_rate:.3f}% > {metrics.error_rate_threshold:.3f}%"
            )

        if metrics.p99_latency_ms > metrics.p99_threshold_ms:
            reasons.append(
                f"P99 latency {metrics.p99_latency_ms:.1f}ms > {metrics.p99_threshold_ms:.1f}ms"
            )

        if not metrics.custom_slo_ok:
            reasons.append("Custom SLO check failed")

        if reasons:
            logger.warning("SRE-Guard ROLLBACK: %s", "; ".join(reasons))
            return RolloutDecision.ROLLBACK

        return RolloutDecision.PROMOTE

    def check_alertmanager(
        self,
        alertmanager_url: str = "http://localhost:9093",
        service: str = "",
    ) -> list[dict[str, Any]]:
        """Check Alertmanager for active alerts related to a service.

        Args:
            alertmanager_url: Alertmanager base URL.
            service: Filter alerts by service label.

        Returns:
            List of active alert dicts.
        """
        try:
            resp = self._client.get(
                f"{alertmanager_url}/api/v2/alerts",
                params={"filter": f'service="{service}"'} if service else {},
            )
            resp.raise_for_status()
            alerts = resp.json()
            active = [
                a for a in alerts
                if a.get("status", {}).get("state") == "active"
            ]
            return active
        except Exception as e:
            logger.warning("Alertmanager check failed: %s", e)
            return []

    # -- internal ----------------------------------------------------------

    def _query_prometheus(self, query: str) -> float:
        """Execute an instant PromQL query and return the scalar value."""
        try:
            resp = self._client.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": query},
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                logger.warning("Prometheus query failed: %s", data)
                return 0.0

            results = data.get("data", {}).get("result", [])
            if not results:
                return 0.0

            # Return the first result's value
            value = results[0].get("value", [None, "0"])
            return float(value[1])

        except httpx.HTTPError as e:
            logger.warning("Prometheus HTTP error: %s", e)
            raise
        except (ValueError, IndexError, TypeError) as e:
            logger.warning("Prometheus response parsing error: %s", e)
            return 0.0
