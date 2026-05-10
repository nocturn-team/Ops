"""Code-Reviewer agent — AI review that catches hallucinations and logic errors.

Sits inside the inner loop.  After TDD-Executor produces a Patch, the
Code-Reviewer evaluates it for correctness, hallucinations, and adherence
to the FixPlan.
"""

from __future__ import annotations

import json
import logging

from libs.agent import Agent, ProviderAdapter

from ..types import CodeReviewResult, FixPlan, Patch, RedTest, ReviewVerdict

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are Code-Reviewer, an expert at catching bugs, hallucinations, and
deviations from the plan in LLM-generated code.

You review patches produced by TDD-Executor.

Evaluate against these criteria:
1. **Correctness**: Does the patch actually fix the bug described in the plan?
2. **Hallucination**: Did the LLM invent APIs, functions, or patterns that
   don't exist in the codebase?
3. **Plan adherence**: Does the patch stay within the FixPlan's scope
   (files, lines, strategy)?
4. **Side effects**: Could the patch break existing functionality?
5. **Code quality**: Is the code clean, idiomatic, and maintainable?
6. **Test alignment**: Will the Red Tests pass with this change?

Output ONLY a JSON object:
{
  "verdict": "approve|request_changes|reject",
  "issues": ["issue 1", "issue 2"],
  "hallucination_flags": ["hallucination 1 if any"],
  "suggestions": ["suggestion 1", "suggestion 2"]
}

Rules:
- Be strict on hallucinations — flag any invented API, non-existent import,
  or fabricated behaviour.
- "approve" means the patch is ready for sandbox testing.
- "request_changes" means fixable issues exist.
- "reject" means the patch is fundamentally flawed.
- Always explain WHY an issue is problematic.
"""


class CodeReviewerAgent:
    """Reviews patches from TDD-Executor.

    Example::

        reviewer = CodeReviewerAgent(provider=my_provider)
        result = reviewer.review(patch, plan, red_tests, iteration=1)
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
            name="Code-Reviewer",
            prompt=_SYSTEM_PROMPT,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    def review(
        self,
        patch: Patch,
        plan: FixPlan,
        red_tests: list[RedTest] | None = None,
        *,
        iteration: int = 1,
    ) -> CodeReviewResult:
        """Review a patch.

        Args:
            patch: The Patch to review.
            plan: The FixPlan the patch should follow.
            red_tests: Red Tests for cross-reference.
            iteration: Current inner-loop iteration.

        Returns:
            CodeReviewResult with verdict, issues, and hallucination flags.
        """
        session = self._agent.create_session()
        user_msg = self._build_user_message(patch, plan, red_tests)
        response = self._agent.chat(user_msg, session)

        result = self._parse_response(response, patch.trace_id, iteration)
        logger.info(
            "CodeReviewerAgent: verdict=%s issues=%d hallucinations=%d iteration=%d",
            result.verdict.value,
            len(result.issues),
            len(result.hallucination_flags),
            iteration,
        )
        return result

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _build_user_message(
        patch: Patch,
        plan: FixPlan,
        red_tests: list[RedTest] | None,
    ) -> str:
        parts = [
            f"## Fix Plan\n"
            f"**Strategy:** {plan.strategy}\n"
            f"**Allowed files:** {', '.join(plan.files_to_modify)}\n"
            f"**Max lines:** {plan.max_lines_changed}\n"
            f"**Risk:** {plan.estimated_risk.value}\n",
            f"## Patch (iteration {patch.iteration})\n"
            f"**Description:** {patch.description}\n",
        ]

        if patch.diff:
            parts.append(f"### Diff\n```diff\n{patch.diff}\n```")

        if patch.files_changed:
            parts.append("### Changed Files")
            for path, content in patch.files_changed.items():
                parts.append(f"#### {path}\n```python\n{content}\n```")

        if red_tests:
            parts.append("## Red Tests (must pass)")
            for t in red_tests:
                parts.append(f"### {t.test_name}\n```python\n{t.test_content}\n```")

        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(response: str, trace_id: str, iteration: int) -> CodeReviewResult:
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("CodeReviewerAgent: failed to parse JSON")
            return CodeReviewResult(
                trace_id=trace_id,
                verdict=ReviewVerdict.REQUEST_CHANGES,
                issues=["Failed to parse review response"],
                iteration=iteration,
            )

        verdict_map = {
            "approve": ReviewVerdict.APPROVE,
            "request_changes": ReviewVerdict.REQUEST_CHANGES,
            "reject": ReviewVerdict.REJECT,
        }
        return CodeReviewResult(
            trace_id=trace_id,
            verdict=verdict_map.get(data.get("verdict", "request_changes"), ReviewVerdict.REQUEST_CHANGES),
            issues=data.get("issues", []),
            hallucination_flags=data.get("hallucination_flags", []),
            suggestions=data.get("suggestions", []),
            iteration=iteration,
        )
