"""DevOps Agent — Dual-Loop Convergence Architecture

Example pipeline demonstrating the full workflow:
  Bug Analysis -> Reproduction -> Red Tests -> Planning ->
  Plan Review -> Inner Loop (TDD) -> PR -> CI -> Canary Deploy

Usage:
    # 方式一：环境变量（推荐）
    export OPENAI_API_KEY="sk-..."
    export OPENAI_BASE_URL="https://api.deepseek.com/v1"   # 可选，默认 OpenAI
    export OPENAI_MODEL="deepseek-chat"                     # 可选，默认 gpt-4o
    python main.py

    # 方式二：命令行参数
    python main.py --api-key sk-... --base-url https://api.deepseek.com/v1 --model deepseek-chat
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# ---------------------------------------------------------------------------
# Configure logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DevOps Agent — Dual-Loop Pipeline")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY", ""),
        help="LLM API key (env: OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="LLM API base URL (env: OPENAI_BASE_URL)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        help="LLM model name (env: OPENAI_MODEL)",
    )
    parser.add_argument(
        "--project-root",
        default=os.environ.get("PROJECT_ROOT", "."),
        help="Project root for sandbox (env: PROJECT_ROOT)",
    )
    parser.add_argument(
        "--prometheus-url",
        default=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
        help="Prometheus URL for SRE-Guard (env: PROMETHEUS_URL)",
    )
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        default=True,
        help="Skip outer loop (canary deploy)",
    )
    return parser.parse_args()


def main() -> None:
    """Run the DevOps pipeline with example input."""

    args = parse_args()

    if not args.api_key:
        logger.error("API key is required.  Use --api-key or set OPENAI_API_KEY.")
        sys.exit(1)

    from libs.devops import Orchestrator
    from libs.devops.deploy import ReleaseController, SREGuard

    # ---------------------------------------------------------------------------
    # Escalation handler
    # ---------------------------------------------------------------------------

    def on_escalation(req):
        logger.critical(
            "HUMAN ESCALATION REQUIRED\n"
            "  Trace: %s\n"
            "  Stage: %s\n"
            "  Reason: %s",
            req.trace_id,
            req.failed_stage.value,
            req.reason,
        )

    # ---------------------------------------------------------------------------
    # Build Orchestrator — 只需 3 个参数即可启动
    # ---------------------------------------------------------------------------

    sre_guard = SREGuard(prometheus_url=args.prometheus_url)
    release_controller = ReleaseController(
        metrics_fn=sre_guard.collect_metrics,
        decide_fn=sre_guard.evaluate,
        observation_window_sec=int(os.environ.get("CANARY_WINDOW_SEC", "5")),
    )

    orchestrator = Orchestrator(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        project_root=args.project_root,
        release_controller=release_controller,
        sre_guard=sre_guard,
        on_escalation=on_escalation,
    )

    # ---------------------------------------------------------------------------
    # Example: Run the pipeline
    # ---------------------------------------------------------------------------

    example_logs = """\
2024-01-15 14:32:11 ERROR [payment-svc] Unhandled exception in process_refund:
Traceback (most recent call last):
  File "/app/services/payment.py", line 142, in process_refund
    amount = calculate_refund(order)
  File "/app/services/payment.py", line 87, in calculate_refund
    return order.total * refund_rate
TypeError: unsupported operand type(s) for *: 'Decimal' and 'float'

2024-01-15 14:32:11 ERROR [payment-svc] Request failed: POST /api/v1/refund
  status=500 duration=23ms user_id=12345 order_id=ORD-98765
"""

    example_source = """\
# /app/services/payment.py (lines 80-95)
from decimal import Decimal

REFUND_RATES = {
    "full": 1.0,
    "partial": 0.5,
    "restocking": 0.85,
}

def calculate_refund(order, refund_type="full"):
    refund_rate = REFUND_RATES.get(refund_type, 1.0)
    return order.total * refund_rate  # BUG: Decimal * float

def process_refund(order, refund_type="full"):
    amount = calculate_refund(order, refund_type)
    # ... rest of refund processing
"""

    logger.info("=" * 60)
    logger.info("  DevOps Agent — Dual-Loop Pipeline")
    logger.info("  API:   %s", args.base_url)
    logger.info("  Model: %s", args.model)
    logger.info("=" * 60)

    ctx = orchestrator.run(
        logs=example_logs,
        source_code=example_source,
        skip_deploy=args.skip_deploy,
    )

    # ---------------------------------------------------------------------------
    # Print results
    # ---------------------------------------------------------------------------

    run = ctx.run
    logger.info("=" * 60)
    logger.info("  Pipeline Result")
    logger.info("=" * 60)
    logger.info("  Trace ID:     %s", run.trace_id)
    logger.info("  Final State:  %s", run.state.value)

    if run.bug_report:
        logger.info("  Bug Title:    %s", run.bug_report.title)
        logger.info("  Severity:     %s", run.bug_report.severity.value)

    if run.reproduction:
        logger.info("  Reproduced:   %s", run.reproduction.reproduced)

    logger.info("  Red Tests:    %d", len(run.red_tests))

    if run.fix_plan:
        logger.info("  Fix Strategy: %s", run.fix_plan.strategy[:80])
        logger.info("  Risk Level:   %s", run.fix_plan.estimated_risk.value)

    logger.info("  Patches:      %d", len(run.patches))
    logger.info("  Reviews:      %d", len(run.code_reviews))
    logger.info("  Sandbox Runs: %d", len(run.sandbox_results))

    if run.pr_info:
        logger.info("  PR URL:       %s", run.pr_info.pr_url)

    if run.escalation:
        logger.info("  ESCALATED:    %s", run.escalation.reason)

    # Circuit breaker status
    breaker_status = orchestrator.get_breaker_status()
    logger.info("  Breaker Status:")
    for node, status in breaker_status.items():
        if status["failures"] > 0:
            logger.info(
                "    %s: %d/%d failures (%s)",
                node,
                status["failures"],
                status["max_failures"],
                status["state"],
            )


if __name__ == "__main__":
    main()
