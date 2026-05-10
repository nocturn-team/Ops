"""Planner agent — creates a FixPlan with bounded scope.

The Planner reads the BugReport, Red Tests, and reproduction data, then
produces a structured FixPlan that constrains the modification scope
(max_lines, files_to_modify) so downstream agents can't go wild.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from libs.agent import Agent, ProviderAdapter

from ..types import BugReport, FixPlan, RedTest, ReproductionResult, RiskLevel

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are Planner, a senior software architect who creates precise fix plans.

Given a BugReport, reproduction data, and Red Tests, produce a FixPlan that:
1. Identifies exactly which files need modification.
2. Describes the strategy step by step.
3. Estimates the risk level.
4. Sets a maximum line budget.

Output ONLY a JSON object:
{
  "strategy": "high-level approach description",
  "files_to_modify": ["path/to/file1.py", "path/to/file2.py"],
  "max_lines_changed": 50,
  "estimated_risk": "low|medium|high",
  "steps": [
    "Step 1: ...",
    "Step 2: ..."
  ],
  "rationale": "why this approach is correct and minimal"
}

Rules:
- Minimise blast radius: change as few files and lines as possible.
- estimated_risk = "high" if changes touch payment, authentication, database
  schemas, or security-sensitive code.
- max_lines_changed should be realistic — pad by ~20% for safety.
- Steps should be concrete enough for a TDD-Executor to implement without
  further clarification.
- Never propose deleting tests or relaxing assertions.
"""


class PlannerAgent:
    """Creates a bounded FixPlan from bug context.

    Example::

        planner = PlannerAgent(provider=my_provider)
        plan = planner.plan(bug_report, red_tests, reproduction)
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
            name="Planner",
            prompt=_SYSTEM_PROMPT,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    def plan(
        self,
        bug_report: BugReport,
        red_tests: list[RedTest] | None = None,
        reproduction: ReproductionResult | None = None,
    ) -> FixPlan:
        """Generate a FixPlan for the given bug.

        Args:
            bug_report: The structured BugReport.
            red_tests: Red Tests that define correctness.
            reproduction: Reproduction result for context.

        Returns:
            A FixPlan dataclass.
        """
        session = self._agent.create_session()
        user_msg = self._build_user_message(bug_report, red_tests, reproduction)
        response = self._agent.chat(user_msg, session)

        plan = self._parse_response(response, bug_report.trace_id)
        logger.info(
            "PlannerAgent: strategy=%s files=%d risk=%s",
            plan.strategy[:60],
            len(plan.files_to_modify),
            plan.estimated_risk.value,
        )
        return plan

    def revise(
        self,
        plan: FixPlan,
        feedback: str,
        session: Any = None,
    ) -> FixPlan:
        """Revise a plan based on review feedback.

        Args:
            plan: The rejected FixPlan.
            feedback: Feedback from Plan-Review.
            session: Optional existing session to continue conversation.

        Returns:
            A revised FixPlan.
        """
        if session is None:
            session = self._agent.create_session()

        user_msg = (
            f"Your previous plan was rejected with this feedback:\n\n{feedback}\n\n"
            f"Previous plan:\n```json\n{json.dumps(self._plan_to_dict(plan), indent=2)}\n```\n\n"
            "Please revise the plan to address the concerns."
        )
        response = self._agent.chat(user_msg, session)
        return self._parse_response(response, plan.trace_id)

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _build_user_message(
        report: BugReport,
        red_tests: list[RedTest] | None,
        reproduction: ReproductionResult | None,
    ) -> str:
        parts = [
            f"## Bug Report\n"
            f"**Title:** {report.title}\n"
            f"**Summary:** {report.summary}\n"
            f"**Severity:** {report.severity.value}\n"
            f"**Service:** {report.affected_service}\n"
            f"**Affected files:** {', '.join(report.affected_files)}\n"
            f"**Root cause hypothesis:** {report.root_cause_hypothesis}\n"
        ]
        if report.stack_trace:
            parts.append(f"## Stack Trace\n```\n{report.stack_trace}\n```")
        if reproduction and reproduction.reproduced:
            parts.append(
                f"## Reproduction\n"
                f"**Reproduced:** Yes\n"
                f"```python\n{reproduction.script_content}\n```"
            )
        if red_tests:
            parts.append("## Red Tests (must pass after fix)")
            for t in red_tests:
                parts.append(f"### {t.test_name}\n```python\n{t.test_content}\n```")
        return "\n\n".join(parts)

    @staticmethod
    def _plan_to_dict(plan: FixPlan) -> dict:
        return {
            "strategy": plan.strategy,
            "files_to_modify": plan.files_to_modify,
            "max_lines_changed": plan.max_lines_changed,
            "estimated_risk": plan.estimated_risk.value,
            "steps": plan.steps,
            "rationale": plan.rationale,
        }

    @staticmethod
    def _parse_response(response: str, trace_id: str) -> FixPlan:
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("PlannerAgent: failed to parse JSON")
            return FixPlan(trace_id=trace_id, strategy=response[:200])

        risk_map = {
            "low": RiskLevel.LOW,
            "medium": RiskLevel.MEDIUM,
            "high": RiskLevel.HIGH,
        }
        return FixPlan(
            trace_id=trace_id,
            strategy=data.get("strategy", ""),
            files_to_modify=data.get("files_to_modify", []),
            max_lines_changed=data.get("max_lines_changed", 50),
            estimated_risk=risk_map.get(data.get("estimated_risk", "low"), RiskLevel.LOW),
            steps=data.get("steps", []),
            rationale=data.get("rationale", ""),
        )
