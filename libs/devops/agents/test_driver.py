"""Test-Driver agent — writes Red Tests that define the correctness boundary.

This agent reads the BugReport and reproduction result, then produces
failing tests (Red Tests) that must pass once the fix is applied.
The tests serve as the ground truth for the inner loop's TDD cycle.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from libs.agent import Agent, ProviderAdapter

from ..types import BugReport, RedTest, ReproductionResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are Test-Driver, a specialist at writing precise failing tests (Red Tests).

Given a BugReport and optionally a reproduction script, write one or more test
functions that:
1. Currently FAIL because the bug exists.
2. Will PASS once the bug is correctly fixed.

Output ONLY a JSON object:
{
  "tests": [
    {
      "test_file_path": "tests/test_fix_{trace_id}.py",
      "test_name": "test_descriptive_name",
      "test_content": "import pytest\\n...",
      "description": "what this test validates"
    }
  ]
}

Rules:
- Use pytest as the test framework.
- Each test must be self-contained and importable.
- Test names must clearly describe the expected correct behaviour.
- Include edge cases where relevant.
- Do NOT write tests that pass with the current buggy code.
- Keep tests focused and minimal — one assertion per test when possible.
"""


class TestDriverAgent:
    """Generates Red Tests from a BugReport and reproduction context.

    Example::

        driver = TestDriverAgent(provider=my_provider)
        tests = driver.write_tests(bug_report, reproduction)
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
            name="Test-Driver",
            prompt=_SYSTEM_PROMPT,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    def write_tests(
        self,
        bug_report: BugReport,
        reproduction: ReproductionResult | None = None,
    ) -> list[RedTest]:
        """Generate Red Tests for the given bug.

        Args:
            bug_report: The structured BugReport.
            reproduction: Optional reproduction result for additional context.

        Returns:
            List of RedTest dataclasses.
        """
        session = self._agent.create_session()
        user_msg = self._build_user_message(bug_report, reproduction)
        response = self._agent.chat(user_msg, session)

        tests = self._parse_response(response, bug_report.trace_id)
        logger.info("TestDriverAgent produced %d red test(s)", len(tests))
        return tests

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _build_user_message(
        report: BugReport,
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
        if reproduction and reproduction.script_content:
            parts.append(
                f"## Reproduction Script\n```python\n{reproduction.script_content}\n```\n"
                f"**Reproduced:** {reproduction.reproduced}\n"
                f"**Output:**\n```\n{reproduction.output}\n```"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(response: str, trace_id: str) -> list[RedTest]:
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
            tests_data = data.get("tests", [])
        except json.JSONDecodeError:
            logger.warning("TestDriverAgent: failed to parse JSON response")
            return [
                RedTest(
                    trace_id=trace_id,
                    test_file_path=f"tests/test_fix_{trace_id}.py",
                    test_content=response,
                    test_name="test_unparsed",
                    description="Raw unparsed test output",
                )
            ]

        result: list[RedTest] = []
        for t in tests_data:
            result.append(
                RedTest(
                    trace_id=trace_id,
                    test_file_path=t.get("test_file_path", f"tests/test_fix_{trace_id}.py"),
                    test_content=t.get("test_content", ""),
                    test_name=t.get("test_name", "test_unknown"),
                    description=t.get("description", ""),
                )
            )
        return result
