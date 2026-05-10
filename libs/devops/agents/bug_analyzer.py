"""Bug-Analyzer agent — reads logs / code and produces a structured BugReport.

This is an LLM agent that sits at the inner-loop upstream.  It receives raw
incident signals (logs, alerts, error traces) and produces a ``BugReport``
that the rest of the pipeline consumes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from libs.agent import Agent, ProviderAdapter

from ..types import BugReport, Severity

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are Bug-Analyzer, a senior SRE and software engineer.

Your job:
1. Read the provided logs, stack traces, and source code snippets.
2. Produce a structured JSON bug report.

Output ONLY a JSON object with these fields:
{
  "title": "short descriptive title",
  "summary": "1-3 sentence summary of the bug",
  "severity": "critical|high|medium|low",
  "affected_service": "service name",
  "affected_files": ["file1.py", "file2.py"],
  "log_snippets": ["relevant log line 1", "relevant log line 2"],
  "stack_trace": "full stack trace if available",
  "root_cause_hypothesis": "your best hypothesis about the root cause"
}

Rules:
- Be precise and factual.  Do not speculate beyond the evidence.
- severity: critical = data loss / security breach / full outage;
  high = partial outage / degraded performance affecting many users;
  medium = bug affecting some users / non-critical path;
  low = cosmetic / minor issue.
- If information is missing, say so explicitly in the relevant field.
"""


class BugAnalyzerAgent:
    """Wraps the LLM Agent to analyse bugs from raw signals.

    Example::

        analyzer = BugAnalyzerAgent(provider=my_provider)
        report = analyzer.analyze(
            logs="ERROR 2024-01-15 ...",
            source_code="def process_payment(...): ...",
        )
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
            name="Bug-Analyzer",
            prompt=_SYSTEM_PROMPT,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    def analyze(
        self,
        *,
        logs: str = "",
        source_code: str = "",
        alert_payload: str = "",
        trace_id: str = "",
    ) -> BugReport:
        """Analyze raw signals and return a structured BugReport.

        Args:
            logs: Raw log output / error messages.
            source_code: Relevant source code snippets.
            alert_payload: Alert / monitoring payload if available.
            trace_id: Pipeline trace ID for correlation.

        Returns:
            A populated BugReport dataclass.
        """
        user_message = self._build_user_message(logs, source_code, alert_payload)
        session = self._agent.create_session()
        response = self._agent.chat(user_message, session)

        report = self._parse_response(response, trace_id)
        logger.info(
            "BugAnalyzerAgent produced report: title=%s severity=%s",
            report.title,
            report.severity.value,
        )
        return report

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _build_user_message(
        logs: str, source_code: str, alert_payload: str,
    ) -> str:
        parts: list[str] = []
        if logs:
            parts.append(f"## Logs\n```\n{logs}\n```")
        if source_code:
            parts.append(f"## Source Code\n```\n{source_code}\n```")
        if alert_payload:
            parts.append(f"## Alert Payload\n```\n{alert_payload}\n```")
        if not parts:
            parts.append("No input signals provided.  Please describe the issue.")
        return "\n\n".join(parts)

    @staticmethod
    def _parse_response(response: str, trace_id: str) -> BugReport:
        """Parse the LLM JSON response into a BugReport."""
        # Strip markdown fences if present
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("BugAnalyzerAgent: failed to parse JSON, returning raw text as summary")
            return BugReport(
                trace_id=trace_id,
                title="Unparseable bug report",
                summary=response[:500],
            )

        severity_map = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
        }

        return BugReport(
            trace_id=trace_id,
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            severity=severity_map.get(data.get("severity", "medium"), Severity.MEDIUM),
            affected_service=data.get("affected_service", ""),
            affected_files=data.get("affected_files", []),
            log_snippets=data.get("log_snippets", []),
            stack_trace=data.get("stack_trace", ""),
            root_cause_hypothesis=data.get("root_cause_hypothesis", ""),
        )
