"""Orchestrator — deterministic state machine driving the dual-loop pipeline.

The Orchestrator is the central coordinator.  It:
- Owns the state machine transitions
- Dispatches work to agents and runners
- Enforces circuit breaker logic
- Persists state through SharedContext
- Never makes LLM calls itself — only delegates to typed agents

Key design principle: the Orchestrator is DETERMINISTIC CODE.
All non-determinism is encapsulated in the LLM agents.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .agents import (
    BugAnalyzerAgent,
    BugRecurrenceAgent,
    CodeReviewerAgent,
    PlannerAgent,
    PlanReviewAgent,
    TDDExecutorAgent,
    TestDriverAgent,
)
from .circuit_breaker import CircuitBreaker
from .context import SharedContext
from .deploy.release import ReleaseController
from .deploy.sre_guard import SREGuard
from .runners.pr_generator import PRGenerator
from .runners.sandbox import SandboxRunner
from .types import (
    BuildResult,
    EscalationRequest,
    PipelineRun,
    PipelineState,
    ReviewVerdict,
    RiskLevel,
    RolloutDecision,
    _now,
)

logger = logging.getLogger(__name__)

# Maximum inner-loop iterations (TDD-Executor -> Code-Review -> Sandbox)
_MAX_INNER_LOOP_ITERATIONS = 3


class Orchestrator:
    """State-machine orchestrator for the DevOps dual-loop pipeline.

    Drives the full lifecycle: bug analysis -> reproduction -> test writing ->
    planning -> plan review -> inner loop (TDD) -> PR -> CI -> canary deploy.

    Supports two construction modes:

    **Simple mode** — pass ``api_key`` / ``base_url`` / ``model``, all LLM
    agents are auto-created::

        orch = Orchestrator(
            api_key="sk-...",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            project_root="/path/to/project",
        )
        result = orch.run(logs="ERROR ...")

    **Advanced mode** — pass pre-built agent instances for full control::

        orch = Orchestrator(
            bug_analyzer=BugAnalyzerAgent(provider=my_provider),
            ...
            sandbox=SandboxRunner(project_root="/path/to/project"),
        )
    """

    def __init__(
        self,
        *,
        # ── Simple mode: LLM config (auto-creates all agents) ──
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        project_root: str = ".",
        # ── Advanced mode: pre-built agents ──
        bug_analyzer: BugAnalyzerAgent | None = None,
        bug_recurrence: BugRecurrenceAgent | None = None,
        test_driver: TestDriverAgent | None = None,
        planner: PlannerAgent | None = None,
        plan_reviewer: PlanReviewAgent | None = None,
        tdd_executor: TDDExecutorAgent | None = None,
        code_reviewer: CodeReviewerAgent | None = None,
        sandbox: SandboxRunner | None = None,
        # ── Optional components ──
        pr_generator: PRGenerator | None = None,
        release_controller: ReleaseController | None = None,
        sre_guard: SREGuard | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        on_escalation: Callable[[EscalationRequest], None] | None = None,
        state_dir: str = ".devops_state",
    ) -> None:
        # Build shared provider if using simple mode
        provider = None
        if api_key and not all([
            bug_analyzer, bug_recurrence, test_driver, planner,
            plan_reviewer, tdd_executor, code_reviewer,
        ]):
            from libs.agent import OpenAICompatibleAdapter
            provider = OpenAICompatibleAdapter(
                api_key=api_key, base_url=base_url, model=model,
            )

        # LLM agents — use provided instances or auto-create from config
        llm_kwargs: dict[str, Any] = {}
        if provider is not None:
            llm_kwargs["provider"] = provider
        elif api_key:
            llm_kwargs.update(api_key=api_key, base_url=base_url, model=model)

        self._bug_analyzer = bug_analyzer or BugAnalyzerAgent(**llm_kwargs)
        self._bug_recurrence = bug_recurrence or BugRecurrenceAgent(**llm_kwargs)
        self._test_driver = test_driver or TestDriverAgent(**llm_kwargs)
        self._planner = planner or PlannerAgent(**llm_kwargs)
        self._plan_reviewer = plan_reviewer or PlanReviewAgent(**llm_kwargs)
        self._tdd_executor = tdd_executor or TDDExecutorAgent(**llm_kwargs)
        self._code_reviewer = code_reviewer or CodeReviewerAgent(**llm_kwargs)

        # Deterministic components
        self._sandbox = sandbox or SandboxRunner(project_root=project_root)
        self._pr_generator = pr_generator
        self._release_controller = release_controller
        self._sre_guard = sre_guard
        self._breaker = circuit_breaker or CircuitBreaker(on_escalation=on_escalation)
        self._state_dir = state_dir

    def run(
        self,
        *,
        logs: str = "",
        source_code: str = "",
        alert_payload: str = "",
        source_files: dict[str, str] | None = None,
        service_name: str = "",
        skip_deploy: bool = True,
    ) -> SharedContext:
        """Execute the full pipeline.

        Args:
            logs: Raw log / error output.
            source_code: Relevant source code snippets.
            alert_payload: Alert payload from monitoring.
            source_files: Current file contents (path -> content) for patching.
            service_name: Target service name for deployment.
            skip_deploy: If True, stop after PR (skip outer loop).

        Returns:
            SharedContext containing the full pipeline state.
        """
        ctx = SharedContext()
        run = ctx.run

        try:
            # ── Stage 1: Bug Analysis ──
            self._stage_bug_analysis(ctx, logs, source_code, alert_payload)
            if run.state == PipelineState.HUMAN_ESCALATION:
                return ctx

            # ── Stage 2: Bug Recurrence ──
            self._stage_bug_recurrence(ctx)
            if run.state == PipelineState.HUMAN_ESCALATION:
                return ctx

            # ── Stage 3: Test Driving ──
            self._stage_test_driving(ctx)

            # ── Stage 4: Planning ──
            self._stage_planning(ctx)
            if run.state == PipelineState.HUMAN_ESCALATION:
                return ctx

            # ── Stage 5: Plan Review ──
            self._stage_plan_review(ctx)
            if run.state == PipelineState.HUMAN_ESCALATION:
                return ctx

            # ── Stage 6: Inner Loop (Correctness Loop) ──
            self._stage_inner_loop(ctx, source_files)
            if run.state == PipelineState.HUMAN_ESCALATION:
                return ctx

            # ── Stage 7: PR Generation ──
            self._stage_pr_generation(ctx)

            # ── Stage 8: CI Build (simulated) ──
            self._stage_ci_build(ctx)

            # ── Stage 9: Outer Loop (Availability Loop) ──
            if not skip_deploy and self._release_controller:
                self._stage_outer_loop(ctx, service_name)
            else:
                logger.info("Skipping outer loop (deploy)")
                ctx.transition(PipelineState.COMPLETED)

        except Exception as e:
            logger.exception("Pipeline failed with unexpected error")
            run.record_error(f"Unexpected error: {e}")
            ctx.transition(PipelineState.FAILED)

        # Persist final state
        self._save_state(ctx)
        return ctx

    # =====================================================================
    # Pipeline Stages
    # =====================================================================

    def _stage_bug_analysis(
        self,
        ctx: SharedContext,
        logs: str,
        source_code: str,
        alert_payload: str,
    ) -> None:
        """Stage 1: Bug Analysis."""
        ctx.transition(PipelineState.BUG_ANALYSIS)
        logger.info("[%s] Stage: Bug Analysis", ctx.trace_id)

        report = self._bug_analyzer.analyze(
            logs=logs,
            source_code=source_code,
            alert_payload=alert_payload,
            trace_id=ctx.trace_id,
        )
        ctx.run.bug_report = report
        logger.info(
            "[%s] BugReport: title=%s severity=%s",
            ctx.trace_id,
            report.title,
            report.severity.value,
        )

    def _stage_bug_recurrence(self, ctx: SharedContext) -> None:
        """Stage 2: Bug Recurrence — attempt to reproduce."""
        ctx.transition(PipelineState.BUG_RECURRENCE)
        logger.info("[%s] Stage: Bug Recurrence", ctx.trace_id)

        report = ctx.run.bug_report
        assert report is not None, "BugReport must exist before recurrence"

        reproduction = self._bug_recurrence.reproduce(
            report,
            run_fn=self._sandbox.run_script,
        )
        ctx.run.reproduction = reproduction

        if not reproduction.reproduced:
            escalation = self._breaker.record_failure(
                "bug_recurrence",
                reason=f"Failed to reproduce after {reproduction.attempts} attempts",
                trace_id=ctx.trace_id,
            )
            if escalation:
                self._escalate(ctx, escalation)
                return
            logger.warning("[%s] Bug not reproduced but breaker not tripped", ctx.trace_id)
        else:
            self._breaker.record_success("bug_recurrence")

    def _stage_test_driving(self, ctx: SharedContext) -> None:
        """Stage 3: Write Red Tests."""
        ctx.transition(PipelineState.TEST_DRIVING)
        logger.info("[%s] Stage: Test Driving", ctx.trace_id)

        report = ctx.run.bug_report
        assert report is not None

        tests = self._test_driver.write_tests(report, ctx.run.reproduction)
        ctx.run.red_tests = tests
        logger.info("[%s] Red Tests: %d test(s) generated", ctx.trace_id, len(tests))

    def _stage_planning(self, ctx: SharedContext) -> None:
        """Stage 4: Create FixPlan."""
        ctx.transition(PipelineState.PLANNING)
        logger.info("[%s] Stage: Planning", ctx.trace_id)

        report = ctx.run.bug_report
        assert report is not None

        plan = self._planner.plan(
            report,
            red_tests=ctx.run.red_tests,
            reproduction=ctx.run.reproduction,
        )
        ctx.run.fix_plan = plan

        # Check for high-risk changes
        if plan.estimated_risk == RiskLevel.HIGH:
            logger.warning("[%s] HIGH RISK change detected", ctx.trace_id)
            escalation = self._breaker.record_failure(
                "high_risk_change",
                reason="Plan involves high-risk areas (payment/auth/DB schema)",
                trace_id=ctx.trace_id,
            )
            if escalation:
                self._escalate(ctx, escalation)

    def _stage_plan_review(self, ctx: SharedContext) -> None:
        """Stage 5: Adversarial Plan Review."""
        ctx.transition(PipelineState.PLAN_REVIEW)
        logger.info("[%s] Stage: Plan Review", ctx.trace_id)

        plan = ctx.run.fix_plan
        report = ctx.run.bug_report
        assert plan is not None and report is not None

        max_review_attempts = 2

        for attempt in range(1, max_review_attempts + 1):
            review = self._plan_reviewer.review(
                plan,
                report,
                red_tests=ctx.run.red_tests,
                attempt=attempt,
            )
            ctx.run.plan_reviews.append(review)

            if review.verdict == ReviewVerdict.APPROVE:
                self._breaker.record_success("plan_review")
                logger.info("[%s] Plan APPROVED on attempt %d", ctx.trace_id, attempt)
                return

            # Plan rejected or needs changes
            logger.warning(
                "[%s] Plan review: %s (attempt %d) — concerns: %s",
                ctx.trace_id,
                review.verdict.value,
                attempt,
                review.concerns,
            )

            if review.verdict == ReviewVerdict.REJECT or attempt == max_review_attempts:
                escalation = self._breaker.record_failure(
                    "plan_review",
                    reason=f"Plan {review.verdict.value}: {'; '.join(review.concerns)}",
                    trace_id=ctx.trace_id,
                )
                if escalation:
                    self._escalate(ctx, escalation)
                    return

            # Revise the plan
            feedback = "\n".join(review.concerns + review.suggestions)
            plan = self._planner.revise(plan, feedback)
            ctx.run.fix_plan = plan

    def _stage_inner_loop(
        self,
        ctx: SharedContext,
        source_files: dict[str, str] | None,
    ) -> None:
        """Stage 6: Inner Loop — TDD-Executor -> Code-Reviewer -> Sandbox.

        This is the Correctness Loop.  It iterates up to
        _MAX_INNER_LOOP_ITERATIONS times.  Each iteration:
        1. TDD-Executor writes/revises a Patch
        2. Code-Reviewer reviews the Patch
        3. Sandbox Runner tests the Patch

        Exit condition: Sandbox returns all_passed == True.
        Fuse: More than 2 sandbox or code-review failures -> escalate.
        """
        ctx.transition(PipelineState.INNER_LOOP)
        logger.info("[%s] Stage: Inner Loop (Correctness)", ctx.trace_id)

        plan = ctx.run.fix_plan
        red_tests = ctx.run.red_tests
        assert plan is not None

        last_review = None
        last_sandbox = None

        for iteration in range(1, _MAX_INNER_LOOP_ITERATIONS + 1):
            logger.info("[%s] Inner loop iteration %d/%d", ctx.trace_id, iteration, _MAX_INNER_LOOP_ITERATIONS)

            # ── TDD-Executor: write or revise patch ──
            if iteration == 1:
                patch = self._tdd_executor.write_patch(
                    plan, red_tests, iteration=iteration, source_files=source_files,
                )
            else:
                patch = self._tdd_executor.revise_patch(
                    plan, red_tests, review=last_review, sandbox=last_sandbox,
                    iteration=iteration,
                )
            ctx.run.patches.append(patch)

            # ── Code-Reviewer: AI review ──
            review = self._code_reviewer.review(
                patch, plan, red_tests=red_tests, iteration=iteration,
            )
            ctx.run.code_reviews.append(review)

            if review.verdict == ReviewVerdict.REJECT:
                escalation = self._breaker.record_failure(
                    "code_reviewer",
                    reason=f"Code review rejected: {'; '.join(review.issues)}",
                    trace_id=ctx.trace_id,
                )
                if escalation:
                    self._escalate(ctx, escalation)
                    return
                last_review = review
                continue

            if review.verdict == ReviewVerdict.REQUEST_CHANGES:
                last_review = review
                # Don't count as a hard failure, but feed back
                logger.info("[%s] Code review: request_changes, will iterate", ctx.trace_id)

            if review.verdict == ReviewVerdict.APPROVE:
                self._breaker.record_success("code_reviewer")

            # ── Sandbox Runner: deterministic testing ──
            sandbox_result = self._sandbox.run(patch, iteration=iteration)
            ctx.run.sandbox_results.append(sandbox_result)

            if sandbox_result.all_passed:
                self._breaker.record_success("sandbox_runner")
                logger.info("[%s] Inner loop CONVERGED at iteration %d", ctx.trace_id, iteration)
                return

            # Sandbox failed
            escalation = self._breaker.record_failure(
                "sandbox_runner",
                reason=f"Sandbox failed: {sandbox_result.output[:200]}",
                trace_id=ctx.trace_id,
            )
            if escalation:
                self._escalate(ctx, escalation)
                return

            last_sandbox = sandbox_result
            last_review = review

        # Exhausted iterations without convergence
        logger.warning("[%s] Inner loop exhausted %d iterations", ctx.trace_id, _MAX_INNER_LOOP_ITERATIONS)
        self._escalate(ctx, EscalationRequest(
            trace_id=ctx.trace_id,
            reason=f"Inner loop failed to converge after {_MAX_INNER_LOOP_ITERATIONS} iterations",
            failed_stage=PipelineState.INNER_LOOP,
        ))

    def _stage_pr_generation(self, ctx: SharedContext) -> None:
        """Stage 7: Generate PR."""
        ctx.transition(PipelineState.PR_GENERATION)
        logger.info("[%s] Stage: PR Generation", ctx.trace_id)

        if not self._pr_generator:
            logger.info("[%s] No PR generator configured; skipping", ctx.trace_id)
            return

        report = ctx.run.bug_report
        plan = ctx.run.fix_plan
        assert report is not None and plan is not None

        # Use the last (approved) patch
        if not ctx.run.patches:
            logger.warning("[%s] No patches to submit", ctx.trace_id)
            return

        patch = ctx.run.patches[-1]
        pr_info = self._pr_generator.create_pr(patch, report, plan)
        ctx.run.pr_info = pr_info
        logger.info("[%s] PR created: %s", ctx.trace_id, pr_info.pr_url or pr_info.branch_name)

    def _stage_ci_build(self, ctx: SharedContext) -> None:
        """Stage 8: CI Build (simulated).

        In a real system, this would trigger a CI pipeline and wait for
        the immutable image to be built.  Here we simulate it.
        """
        ctx.transition(PipelineState.CI_BUILD)
        logger.info("[%s] Stage: CI Build", ctx.trace_id)

        pr_info = ctx.run.pr_info
        image_tag = pr_info.image_tag if pr_info else f"sha-{ctx.trace_id[:12]}"

        build = BuildResult(
            trace_id=ctx.trace_id,
            image_tag=image_tag,
            registry_url=f"registry.example.com/services/{image_tag}",
            build_ok=True,
            opa_check_passed=True,
        )

        # OPA Schema check — high-risk changes would be flagged here
        plan = ctx.run.fix_plan
        if plan and plan.estimated_risk == RiskLevel.HIGH:
            logger.warning("[%s] OPA: high-risk change — requires human approval", ctx.trace_id)
            build.opa_check_passed = False
            build.opa_violations.append("High-risk change requires human approval")

        ctx.run.build_result = build

    def _stage_outer_loop(self, ctx: SharedContext, service: str) -> None:
        """Stage 9: Outer Loop — Canary Deployment.

        Pure code — no LLM involvement in real-time decisions.
        """
        ctx.transition(PipelineState.OUTER_LOOP)
        logger.info("[%s] Stage: Outer Loop (Availability)", ctx.trace_id)

        build = ctx.run.build_result
        assert build is not None and self._release_controller is not None

        if not build.build_ok:
            logger.warning("[%s] Build failed — skipping deploy", ctx.trace_id)
            ctx.transition(PipelineState.FAILED)
            return

        status = self._release_controller.rollout(
            build,
            service=service or "unknown-service",
            trace_id=ctx.trace_id,
        )
        ctx.run.rollout_status = status

        if status.decision == RolloutDecision.ROLLBACK:
            logger.warning("[%s] Canary ROLLED BACK: %s", ctx.trace_id, status.rollback_reason)
            ctx.transition(PipelineState.ROLLED_BACK)

            # Record in circuit breaker
            stage_node = (
                "canary_5" if status.current_stage.value == "5%"
                else "canary_25"
            )
            self._breaker.record_failure(
                stage_node,
                reason=status.rollback_reason,
                trace_id=ctx.trace_id,
            )
        else:
            logger.info("[%s] Canary rollout COMPLETED", ctx.trace_id)
            ctx.transition(PipelineState.COMPLETED)

    # =====================================================================
    # Helpers
    # =====================================================================

    def _escalate(self, ctx: SharedContext, escalation: EscalationRequest) -> None:
        """Transition to human escalation."""
        ctx.run.escalation = escalation
        ctx.transition(PipelineState.HUMAN_ESCALATION)
        logger.warning(
            "[%s] ESCALATED TO HUMAN: %s (stage: %s)",
            ctx.trace_id,
            escalation.reason,
            escalation.failed_stage.value,
        )

    def _save_state(self, ctx: SharedContext) -> None:
        """Persist pipeline state to disk."""
        try:
            from pathlib import Path
            state_path = Path(self._state_dir) / f"{ctx.trace_id}.json"
            ctx.save(state_path)
            logger.info("[%s] State saved to %s", ctx.trace_id, state_path)
        except Exception as e:
            logger.warning("[%s] Failed to save state: %s", ctx.trace_id, e)

    def get_breaker_status(self) -> dict[str, dict]:
        """Return the current circuit breaker status."""
        return self._breaker.get_status()
