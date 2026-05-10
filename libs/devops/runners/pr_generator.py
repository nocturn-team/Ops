"""PR-Generator — deterministic Git/PR operations.

This is a PURE CODE component that sits between the inner loop and outer loop.
It creates a branch, commits the approved patch, and opens a PR (or prepares
the data for a GitOps workflow).

No LLM involvement — all operations are deterministic.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..types import BugReport, FixPlan, Patch, PRInfo, _now

logger = logging.getLogger(__name__)


class PRGenerator:
    """Generates Pull Requests from approved patches.

    Example::

        pr_gen = PRGenerator(repo_root="/path/to/repo")
        pr_info = pr_gen.create_pr(patch, bug_report, plan)
    """

    def __init__(
        self,
        *,
        repo_root: str | Path,
        remote: str = "origin",
        base_branch: str = "main",
        timeout_seconds: int = 60,
    ) -> None:
        """
        Args:
            repo_root: Path to the git repository root.
            remote: Git remote name.
            base_branch: Base branch for the PR.
            timeout_seconds: Max seconds per git command.
        """
        self.repo_root = Path(repo_root)
        self.remote = remote
        self.base_branch = base_branch
        self.timeout = timeout_seconds

    def create_pr(
        self,
        patch: Patch,
        bug_report: BugReport,
        plan: FixPlan,
    ) -> PRInfo:
        """Create a branch, commit changes, and prepare PR metadata.

        This method performs git operations but does NOT push or create the
        actual PR on the remote (that requires platform-specific API calls
        or a GitOps tool).  It returns ``PRInfo`` with all the data needed
        for the CI/CD pipeline to proceed.

        Args:
            patch: The approved Patch with file changes.
            bug_report: The BugReport for commit message context.
            plan: The FixPlan for commit message context.

        Returns:
            PRInfo with branch name, commit SHA, etc.
        """
        trace_id = patch.trace_id
        branch_name = f"fix/{trace_id}"

        info = PRInfo(
            trace_id=trace_id,
            branch_name=branch_name,
        )

        try:
            # 1. Create and checkout branch
            self._run_git("checkout", "-b", branch_name)
            logger.info("Created branch: %s", branch_name)

            # 2. Apply file changes
            for file_path, content in patch.files_changed.items():
                target = self.repo_root / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                self._run_git("add", file_path)

            # 3. Commit
            commit_msg = self._build_commit_message(bug_report, plan, patch)
            self._run_git("commit", "-m", commit_msg)

            # 4. Get commit SHA
            sha = self._run_git("rev-parse", "HEAD").strip()
            info.commit_sha = sha
            info.image_tag = f"sha-{sha[:12]}"

            # 5. Push branch (if remote is reachable)
            try:
                self._run_git("push", "-u", self.remote, branch_name)
                logger.info("Pushed branch %s to %s", branch_name, self.remote)
            except subprocess.CalledProcessError:
                logger.warning(
                    "Failed to push branch %s — will need manual push", branch_name
                )

            # 6. Create PR via CLI (if `gh` is available)
            pr_url, pr_number = self._create_github_pr(
                branch_name, bug_report, plan, patch,
            )
            info.pr_url = pr_url
            info.pr_number = pr_number

        except Exception as e:
            logger.exception("PR generation failed")
            info.pr_url = f"ERROR: {e}"
            # Try to clean up: go back to base branch
            try:
                self._run_git("checkout", self.base_branch)
            except Exception:
                pass

        return info

    # -- internal ----------------------------------------------------------

    def _run_git(self, *args: str) -> str:
        """Run a git command in the repo root."""
        cmd = ["git", *args]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            cwd=self.repo_root,
        )
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, cmd, proc.stdout, proc.stderr,
            )
        return proc.stdout

    @staticmethod
    def _build_commit_message(
        report: BugReport, plan: FixPlan, patch: Patch,
    ) -> str:
        return (
            f"fix({report.affected_service}): {report.title}\n\n"
            f"Trace-ID: {report.trace_id}\n"
            f"Severity: {report.severity.value}\n"
            f"Strategy: {plan.strategy}\n\n"
            f"{patch.description}\n\n"
            f"Files changed: {', '.join(patch.files_changed.keys())}\n"
            f"Risk level: {plan.estimated_risk.value}"
        )

    def _create_github_pr(
        self,
        branch: str,
        report: BugReport,
        plan: FixPlan,
        patch: Patch,
    ) -> tuple[str, int]:
        """Attempt to create a PR using the `gh` CLI tool."""
        title = f"fix({report.affected_service}): {report.title}"
        body = (
            f"## Bug Fix — {report.trace_id}\n\n"
            f"**Severity:** {report.severity.value}\n"
            f"**Service:** {report.affected_service}\n\n"
            f"### Summary\n{report.summary}\n\n"
            f"### Root Cause\n{report.root_cause_hypothesis}\n\n"
            f"### Fix Strategy\n{plan.strategy}\n\n"
            f"### Changes\n{patch.description}\n\n"
            f"**Files:** {', '.join(patch.files_changed.keys())}\n"
            f"**Risk:** {plan.estimated_risk.value}\n\n"
            f"---\n*Auto-generated by DevOps Agent (trace: {report.trace_id})*"
        )
        try:
            proc = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--title", title,
                    "--body", body,
                    "--base", self.base_branch,
                    "--head", branch,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.repo_root,
            )
            if proc.returncode == 0:
                pr_url = proc.stdout.strip()
                # Extract PR number from URL
                try:
                    pr_number = int(pr_url.rstrip("/").split("/")[-1])
                except (ValueError, IndexError):
                    pr_number = 0
                return pr_url, pr_number
            else:
                logger.warning("gh pr create failed: %s", proc.stderr)
                return "", 0
        except FileNotFoundError:
            logger.info("gh CLI not found — PR must be created manually")
            return "", 0
        except Exception as e:
            logger.warning("PR creation error: %s", e)
            return "", 0
