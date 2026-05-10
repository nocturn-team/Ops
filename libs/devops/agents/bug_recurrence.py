"""Bug-Recurrence agent — writes reproduction scripts and verifies them in a sandbox.

This agent attempts to reproduce a bug by generating a script and executing
it.  If the script triggers the expected failure, the bug is confirmed as
reproducible and the pipeline can proceed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from libs.agent import Agent, ProviderAdapter

from ..types import BugReport, ReproductionResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are Bug-Recurrence, a specialist at writing minimal reproduction scripts.

Given a BugReport, produce a script that reliably triggers the described bug.

Output ONLY a JSON object:
{
  "script_content": "#!/usr/bin/env python3\\n...",
  "script_path": "repro/test_repro_{trace_id}.py",
  "expected_failure": "description of what the script should demonstrate"
}

Rules:
- The script must be self-contained and runnable with `python <script>`.
- Use only standard library + project dependencies.
- The script should EXIT NON-ZERO if the bug is present, EXIT ZERO if the bug is fixed.
- Include assertions that validate the buggy behaviour.
- Keep the script minimal — no unnecessary setup.
"""


class BugRecurrenceAgent:
    """Generates reproduction scripts and validates them via a sandbox runner.

    Example::

        agent = BugRecurrenceAgent(provider=my_provider)
        result = agent.reproduce(bug_report, run_fn=sandbox.run_script)
    """

    def __init__(
        self,
        *,
        provider: ProviderAdapter | None = None,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        max_attempts: int = 3,
    ) -> None:
        self._agent = Agent(
            name="Bug-Recurrence",
            prompt=_SYSTEM_PROMPT,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        self._max_attempts = max_attempts

    def reproduce(
        self,
        bug_report: BugReport,
        *,
        run_fn: Any | None = None,
    ) -> ReproductionResult:
        """Attempt to reproduce the bug.

        Args:
            bug_report: The structured BugReport to reproduce.
            run_fn: Optional callable ``(script_content: str) -> (exit_code: int, output: str)``.
                     If None, the script is generated but not executed.

        Returns:
            ReproductionResult with the script and execution outcome.
        """
        session = self._agent.create_session()
        user_msg = self._build_user_message(bug_report)

        result = ReproductionResult(
            trace_id=bug_report.trace_id,
            max_attempts=self._max_attempts,
        )

        for attempt in range(1, self._max_attempts + 1):
            result.attempts = attempt

            if attempt == 1:
                response = self._agent.chat(user_msg, session)
            else:
                retry_msg = (
                    f"Attempt {attempt - 1} failed to reproduce the bug.\n"
                    f"Previous output:\n```\n{result.output}\n```\n\n"
                    "Please revise the reproduction script."
                )
                response = self._agent.chat(retry_msg, session)

            parsed = self._parse_response(response)
            result.script_content = parsed.get("script_content", "")
            result.script_path = parsed.get("script_path", f"repro/test_repro_{bug_report.trace_id}.py")

            if run_fn is None:
                logger.info("No run_fn provided; skipping execution (attempt %d)", attempt)
                break

            exit_code, output = run_fn(result.script_content)
            result.output = output

            if exit_code != 0:
                # Non-zero exit = bug reproduced
                result.reproduced = True
                logger.info("Bug reproduced on attempt %d", attempt)
                break
            else:
                logger.info("Reproduction attempt %d: script exited 0 (bug not triggered)", attempt)

        return result

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _build_user_message(report: BugReport) -> str:
        return (
            f"## Bug Report\n"
            f"**Title:** {report.title}\n"
            f"**Summary:** {report.summary}\n"
            f"**Severity:** {report.severity.value}\n"
            f"**Service:** {report.affected_service}\n"
            f"**Files:** {', '.join(report.affected_files)}\n"
            f"**Root cause hypothesis:** {report.root_cause_hypothesis}\n\n"
            f"## Stack Trace\n```\n{report.stack_trace}\n```\n\n"
            f"## Log Snippets\n" +
            "\n".join(f"```\n{s}\n```" for s in report.log_snippets)
        )

    @staticmethod
    def _parse_response(response: str) -> dict:
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"script_content": response, "script_path": "repro/test_repro.py"}
