"""TDD-Executor agent — the inner loop's code generator.

Each iteration of the inner loop, the TDD-Executor reads the FixPlan, Red
Tests, and any previous review/test feedback, then produces a Patch (code
changes).  The Patch goes through Code-Reviewer and Sandbox before being
accepted or rejected for another iteration.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from libs.agent import Agent, ProviderAdapter

from ..types import (
    CodeReviewResult,
    FixPlan,
    Patch,
    RedTest,
    SandboxResult,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are TDD-Executor, a disciplined developer who writes minimal, correct patches.

You follow strict TDD:
1. Read the failing Red Tests.
2. Read the FixPlan.
3. Write the MINIMUM code change to make the tests pass.

Output ONLY a JSON object:
{
  "files_changed": {
    "path/to/file.py": "full file content after changes",
    "path/to/another.py": "full file content after changes"
  },
  "diff": "unified diff showing changes",
  "description": "what this patch does and why"
}

Rules:
- Only modify files listed in the FixPlan's files_to_modify.
- Stay within the max_lines_changed budget.
- Do NOT modify test files.
- Do NOT introduce new dependencies.
- Write clean, idiomatic code following existing project conventions.
- If you receive feedback from Code-Reviewer or Sandbox, address ALL issues.
- Each file's content must be the COMPLETE file (not just the changed lines).
"""


class TDDExecutorAgent:
    """Generates code patches in a TDD loop.

    Example::

        executor = TDDExecutorAgent(provider=my_provider)
        patch = executor.write_patch(plan, red_tests, iteration=1)
        # After review feedback:
        patch2 = executor.revise_patch(plan, red_tests, review, sandbox, iteration=2)
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
            name="TDD-Executor",
            prompt=_SYSTEM_PROMPT,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self._session = None

    def write_patch(
        self,
        plan: FixPlan,
        red_tests: list[RedTest],
        *,
        iteration: int = 1,
        source_files: dict[str, str] | None = None,
    ) -> Patch:
        """Generate an initial patch.

        Args:
            plan: The approved FixPlan.
            red_tests: Red Tests the patch must satisfy.
            iteration: Current iteration number.
            source_files: Current content of files to modify (path -> content).

        Returns:
            A Patch dataclass.
        """
        self._session = self._agent.create_session()
        user_msg = self._build_initial_message(plan, red_tests, source_files)
        response = self._agent.chat(user_msg, self._session)
        return self._parse_response(response, plan.trace_id, iteration)

    def revise_patch(
        self,
        plan: FixPlan,
        red_tests: list[RedTest],
        review: CodeReviewResult | None = None,
        sandbox: SandboxResult | None = None,
        *,
        iteration: int = 2,
    ) -> Patch:
        """Revise a patch based on review and/or sandbox feedback.

        Args:
            plan: The FixPlan.
            red_tests: Red Tests.
            review: Code review feedback (if any).
            sandbox: Sandbox execution results (if any).
            iteration: Current iteration number.

        Returns:
            A revised Patch.
        """
        if self._session is None:
            self._session = self._agent.create_session()

        feedback_parts: list[str] = []
        if review and review.verdict.value != "approve":
            feedback_parts.append(
                f"## Code Review Feedback (iteration {review.iteration})\n"
                f"**Verdict:** {review.verdict.value}\n"
                f"**Issues:**\n" + "\n".join(f"- {i}" for i in review.issues) + "\n"
                f"**Hallucination flags:**\n" + "\n".join(f"- {h}" for h in review.hallucination_flags) + "\n"
                f"**Suggestions:**\n" + "\n".join(f"- {s}" for s in review.suggestions)
            )
        if sandbox and not sandbox.all_passed:
            feedback_parts.append(
                f"## Sandbox Results (iteration {sandbox.iteration})\n"
                f"**Compile:** {'OK' if sandbox.compile_ok else 'FAIL'}\n"
                f"**Tests:** {sandbox.unit_tests_passed}/{sandbox.unit_tests_total} passed "
                f"({sandbox.unit_tests_failed} failed)\n"
                f"**Lint:** {'OK' if sandbox.lint_ok else 'FAIL'}\n"
                f"**Lint errors:**\n" + "\n".join(f"- {e}" for e in sandbox.lint_errors) + "\n"
                f"**Mutation score:** {sandbox.mutation_score:.2%} "
                f"(threshold: {sandbox.mutation_threshold:.2%})\n"
                f"**Output:**\n```\n{sandbox.output[:2000]}\n```"
            )

        if not feedback_parts:
            feedback_parts.append("Previous patch had issues. Please revise.")

        user_msg = "\n\n".join(feedback_parts) + "\n\nPlease produce a revised patch."
        response = self._agent.chat(user_msg, self._session)
        return self._parse_response(response, plan.trace_id, iteration)

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _build_initial_message(
        plan: FixPlan,
        red_tests: list[RedTest],
        source_files: dict[str, str] | None,
    ) -> str:
        parts = [
            f"## Fix Plan\n"
            f"**Strategy:** {plan.strategy}\n"
            f"**Files to modify:** {', '.join(plan.files_to_modify)}\n"
            f"**Max lines changed:** {plan.max_lines_changed}\n\n"
            f"**Steps:**\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(plan.steps)),
        ]
        parts.append("## Red Tests (must pass after your patch)")
        for t in red_tests:
            parts.append(f"### {t.test_name}\n```python\n{t.test_content}\n```")

        if source_files:
            parts.append("## Current Source Files")
            for path, content in source_files.items():
                parts.append(f"### {path}\n```python\n{content}\n```")

        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(response: str, trace_id: str, iteration: int) -> Patch:
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("TDDExecutorAgent: failed to parse JSON response")
            return Patch(
                trace_id=trace_id,
                description="Unparsed patch output",
                diff=response[:2000],
                iteration=iteration,
            )
        return Patch(
            trace_id=trace_id,
            files_changed=data.get("files_changed", {}),
            diff=data.get("diff", ""),
            description=data.get("description", ""),
            iteration=iteration,
        )
