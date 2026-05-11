"""Sandbox Runner — deterministic code execution for the inner loop exit gate.

This is a PURE CODE component (no LLM involvement).  It receives a Patch,
applies it to a sandbox workspace, and runs:
1. Compilation / syntax check
2. Unit tests (pytest)
3. Linting (ruff / flake8)
4. Mutation testing (mutmut / cosmic-ray)

The result is a ``SandboxResult`` that determines whether the inner loop
can exit.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..types import Patch, SandboxResult

logger = logging.getLogger(__name__)


class SandboxRunner:
    """Executes patches in an isolated sandbox and reports pass/fail.

    The sandbox creates a temporary copy of the project, applies the patch,
    and runs a deterministic test pipeline.

    Example::

        runner = SandboxRunner(project_root="/path/to/project")
        result = runner.run(patch, iteration=1)
        if result.all_passed:
            # Inner loop can exit
            ...
    """

    def __init__(
        self,
        *,
        project_root: str | Path,
        test_command: str = "python -m pytest -x -q",
        lint_command: str = "python -m ruff check .",
        mutation_command: str | None = None,
        mutation_threshold: float = 0.8,
        timeout_seconds: int = 120,
    ) -> None:
        """
        Args:
            project_root: Path to the project root to copy into sandbox.
            test_command: Shell command to run unit tests.
            lint_command: Shell command to run linting.
            mutation_command: Shell command for mutation testing (optional).
            mutation_threshold: Minimum mutation score to pass (0.0 - 1.0).
            timeout_seconds: Max seconds per subprocess call.
        """
        self.project_root = Path(project_root)
        self.test_command = test_command
        self.lint_command = lint_command
        self.mutation_command = mutation_command
        self.mutation_threshold = mutation_threshold
        self.timeout_seconds = timeout_seconds

    def run(self, patch: Patch, *, iteration: int = 1) -> SandboxResult:
        """Apply a patch to a sandbox and run the test pipeline.

        Args:
            patch: The Patch to test.
            iteration: Current inner-loop iteration.

        Returns:
            SandboxResult with detailed pass/fail for each stage.
        """
        result = SandboxResult(
            trace_id=patch.trace_id,
            iteration=iteration,
            mutation_threshold=self.mutation_threshold,
        )

        sandbox_dir = None
        try:
            # 1. Create sandbox
            sandbox_dir = self._create_sandbox()
            logger.info("Sandbox created at %s", sandbox_dir)

            # 2. Apply patch
            self._apply_patch(sandbox_dir, patch)

            # 3. Compile / syntax check
            compile_ok, compile_output = self._check_syntax(sandbox_dir, patch)
            result.compile_ok = compile_ok
            if not compile_ok:
                result.output = f"Compilation failed:\n{compile_output}"
                return result

            # 4. Run unit tests
            test_ok, test_output, passed, failed, total = self._run_tests(sandbox_dir)
            result.unit_tests_passed = test_ok
            result.unit_tests_total = total
            result.unit_tests_failed = failed

            # 5. Run linter
            lint_ok, lint_errors = self._run_linter(sandbox_dir)
            result.lint_ok = lint_ok
            result.lint_errors = lint_errors

            # 6. Run mutation testing (if configured)
            if self.mutation_command:
                mutation_score = self._run_mutation_testing(sandbox_dir)
                result.mutation_score = mutation_score
            else:
                result.mutation_score = 1.0  # Skip if not configured

            # Aggregate
            result.all_passed = (
                result.compile_ok
                and result.unit_tests_passed
                and result.lint_ok
                and result.mutation_score >= self.mutation_threshold
            )

            result.output = (
                f"Compile: {'PASS' if result.compile_ok else 'FAIL'}\n"
                f"Tests: {passed}/{total} passed ({failed} failed)\n"
                f"Lint: {'PASS' if result.lint_ok else 'FAIL'} ({len(lint_errors)} errors)\n"
                f"Mutation: {result.mutation_score:.2%} (threshold: {self.mutation_threshold:.2%})\n"
                f"{'=' * 40}\n"
                f"Overall: {'ALL PASSED' if result.all_passed else 'FAILED'}\n"
                f"\n--- Test Output ---\n{test_output[:3000]}"
            )

        except Exception as e:
            logger.exception("Sandbox execution error")
            result.output = f"Sandbox error: {e}"

        finally:
            if sandbox_dir:
                self._cleanup_sandbox(sandbox_dir)

        logger.info(
            "SandboxRunner: all_passed=%s compile=%s tests=%s lint=%s mutation=%.2f",
            result.all_passed,
            result.compile_ok,
            result.unit_tests_passed,
            result.lint_ok,
            result.mutation_score,
        )
        return result

    def run_script(self, script_content: str) -> tuple[int, str]:
        """Run an arbitrary script in a temporary directory.

        Used by Bug-Recurrence for reproduction scripts.

        Args:
            script_content: Python script content.

        Returns:
            (exit_code, output) tuple.
        """
        with tempfile.TemporaryDirectory(prefix="sandbox_script_") as tmpdir:
            script_path = Path(tmpdir) / "repro_script.py"
            script_path.write_text(script_content, encoding="utf-8")
            try:
                proc = subprocess.run(
                    ["python", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    cwd=tmpdir,
                )
                output = proc.stdout + proc.stderr
                return proc.returncode, output
            except subprocess.TimeoutExpired:
                return 1, "Script timed out"
            except Exception as e:
                return 1, f"Script execution error: {e}"

    # -- internal ----------------------------------------------------------

    def _create_sandbox(self) -> Path:
        """Copy the project into a temporary directory and install deps."""
        sandbox = Path(tempfile.mkdtemp(prefix="sandbox_"))
        shutil.copytree(
            self.project_root,
            sandbox / "project",
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".venv", "node_modules",
            ),
        )
        project_dir = sandbox / "project"
        # Install project dependencies in sandbox
        try:
            subprocess.run(
                ["uv", "sync", "--frozen"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=project_dir,
            )
        except Exception as e:
            logger.warning("Failed to install deps in sandbox: %s", e)
        return project_dir

    def _apply_patch(self, sandbox_dir: Path, patch: Patch) -> None:
        """Write patched files into the sandbox."""
        for file_path, content in patch.files_changed.items():
            target = sandbox_dir / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            logger.debug("Applied patch to %s", file_path)

    def _check_syntax(self, sandbox_dir: Path, patch: Patch) -> tuple[bool, str]:
        """Run ``py_compile`` on all changed files."""
        errors: list[str] = []
        for file_path in patch.files_changed:
            target = sandbox_dir / file_path
            if target.suffix == ".py":
                try:
                    proc = subprocess.run(
                        ["python", "-m", "py_compile", str(target)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if proc.returncode != 0:
                        errors.append(f"{file_path}: {proc.stderr}")
                except Exception as e:
                    errors.append(f"{file_path}: {e}")
        return len(errors) == 0, "\n".join(errors)

    def _run_tests(self, sandbox_dir: Path) -> tuple[bool, str, int, int, int]:
        """Run the test command and parse results."""
        try:
            proc = subprocess.run(
                self.test_command.split(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=sandbox_dir,
            )
            output = proc.stdout + proc.stderr
            # Parse basic pytest output
            passed = output.count(" passed")
            failed_count = output.count(" failed")
            # Rough extraction — could be improved with --tb=short --json
            total = max(passed + failed_count, 1)
            return proc.returncode == 0, output, passed, failed_count, total
        except subprocess.TimeoutExpired:
            return False, "Tests timed out", 0, 0, 0
        except Exception as e:
            return False, f"Test execution error: {e}", 0, 0, 0

    def _run_linter(self, sandbox_dir: Path) -> tuple[bool, list[str]]:
        """Run the lint command."""
        try:
            proc = subprocess.run(
                self.lint_command.split(),
                capture_output=True,
                text=True,
                timeout=60,
                cwd=sandbox_dir,
            )
            errors = [
                line.strip()
                for line in proc.stdout.splitlines()
                if line.strip() and not line.startswith("All checks")
            ]
            return proc.returncode == 0, errors
        except subprocess.TimeoutExpired:
            return False, ["Linter timed out"]
        except Exception as e:
            return False, [f"Linter error: {e}"]

    def _run_mutation_testing(self, sandbox_dir: Path) -> float:
        """Run mutation testing and return the mutation score."""
        if not self.mutation_command:
            return 1.0
        try:
            proc = subprocess.run(
                self.mutation_command.split(),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds * 3,  # Mutation testing is slow
                cwd=sandbox_dir,
            )
            # Parse mutation score from output (tool-specific)
            # Default: if exit code is 0, assume full score
            if proc.returncode == 0:
                return 1.0
            # Try to extract a score from output
            for line in proc.stdout.splitlines():
                if "score" in line.lower() or "%" in line:
                    import re
                    match = re.search(r"(\d+\.?\d*)%", line)
                    if match:
                        return float(match.group(1)) / 100.0
            return 0.0
        except subprocess.TimeoutExpired:
            logger.warning("Mutation testing timed out")
            return 0.0
        except Exception as e:
            logger.warning("Mutation testing error: %s", e)
            return 0.0

    @staticmethod
    def _cleanup_sandbox(sandbox_dir: Path) -> None:
        """Remove the sandbox directory."""
        try:
            # Go up one level if we're at project subdir
            root = sandbox_dir.parent if sandbox_dir.name == "project" else sandbox_dir
            shutil.rmtree(root, ignore_errors=True)
        except Exception:
            pass
