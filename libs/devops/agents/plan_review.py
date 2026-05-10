"""Plan-Review agent — adversarial validation of FixPlans.

This agent acts as a gate between planning and execution.  It reviews a
FixPlan and either approves it or rejects it with specific concerns.
The Orchestrator uses the verdict to decide whether to enter the inner loop
or send the plan back to the Planner.
"""

from __future__ import annotations

import json
import logging

from libs.agent import Agent, ProviderAdapter

from ..types import BugReport, FixPlan, PlanReviewResult, RedTest, ReviewVerdict

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are Plan-Review, an adversarial reviewer of fix plans.

Your job is to find flaws in the proposed FixPlan BEFORE any code is written.

Evaluate the plan against these criteria:
1. **Correctness**: Does the plan address the root cause, not just symptoms?
2. **Minimality**: Is the blast radius (files, lines) as small as possible?
3. **Risk**: Are high-risk areas (payment, auth, DB schema) properly flagged?
4. **Testability**: Will the Red Tests actually validate the fix?
5. **Rollback**: Can the change be safely reverted?
6. **Completeness**: Are there missing edge cases or failure modes?

Output ONLY a JSON object:
{
  "verdict": "approve|request_changes|reject",
  "concerns": ["concern 1", "concern 2"],
  "suggestions": ["suggestion 1", "suggestion 2"]
}

Rules:
- Be critical but constructive.
- "approve" means the plan is ready for implementation.
- "request_changes" means the plan has issues but they're fixable.
- "reject" means the approach is fundamentally wrong.
- If you approve, concerns and suggestions may still be non-empty (minor notes).
"""


class PlanReviewAgent:
    """Reviews FixPlans and produces approval/rejection verdicts.

    Example::

        reviewer = PlanReviewAgent(provider=my_provider)
        result = reviewer.review(fix_plan, bug_report, red_tests)
    """

    def __init__(
        self,
        *,
        provider: ProviderAdapter | None = None,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
    ) -> None:
        self._agent = Agent(
            name="Plan-Review",
            prompt=_SYSTEM_PROMPT,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    def review(
        self,
        plan: FixPlan,
        bug_report: BugReport,
        red_tests: list[RedTest] | None = None,
        attempt: int = 1,
    ) -> PlanReviewResult:
        """Review a FixPlan.

        Args:
            plan: The FixPlan to review.
            bug_report: Original BugReport for context.
            red_tests: Red Tests for cross-reference.
            attempt: Which review attempt this is (for circuit breaker tracking).

        Returns:
            PlanReviewResult with verdict, concerns, and suggestions.
        """
        session = self._agent.create_session()
        user_msg = self._build_user_message(plan, bug_report, red_tests)
        response = self._agent.chat(user_msg, session)

        result = self._parse_response(response, plan.trace_id, attempt)
        logger.info(
            "PlanReviewAgent: verdict=%s concerns=%d attempt=%d",
            result.verdict.value,
            len(result.concerns),
            attempt,
        )
        return result

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _build_user_message(
        plan: FixPlan,
        report: BugReport,
        red_tests: list[RedTest] | None,
    ) -> str:
        parts = [
            f"## Bug Report\n"
            f"**Title:** {report.title}\n"
            f"**Summary:** {report.summary}\n"
            f"**Root cause:** {report.root_cause_hypothesis}\n",
            f"## Proposed Fix Plan\n"
            f"**Strategy:** {plan.strategy}\n"
            f"**Files to modify:** {', '.join(plan.files_to_modify)}\n"
            f"**Max lines changed:** {plan.max_lines_changed}\n"
            f"**Estimated risk:** {plan.estimated_risk.value}\n"
            f"**Rationale:** {plan.rationale}\n\n"
            f"**Steps:**\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan.steps)),
        ]
        if red_tests:
            parts.append("## Red Tests")
            for t in red_tests:
                parts.append(f"### {t.test_name}\n{t.description}")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(response: str, trace_id: str, attempt: int) -> PlanReviewResult:
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("PlanReviewAgent: failed to parse JSON")
            return PlanReviewResult(
                trace_id=trace_id,
                verdict=ReviewVerdict.REQUEST_CHANGES,
                concerns=["Failed to parse review response"],
                attempt=attempt,
            )

        verdict_map = {
            "approve": ReviewVerdict.APPROVE,
            "request_changes": ReviewVerdict.REQUEST_CHANGES,
            "reject": ReviewVerdict.REJECT,
        }
        return PlanReviewResult(
            trace_id=trace_id,
            verdict=verdict_map.get(data.get("verdict", "request_changes"), ReviewVerdict.REQUEST_CHANGES),
            concerns=data.get("concerns", []),
            suggestions=data.get("suggestions", []),
            attempt=attempt,
        )
