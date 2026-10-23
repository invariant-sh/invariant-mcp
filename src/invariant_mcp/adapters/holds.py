"""Call Holds' Python services. The suite file owns the agent command."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from holds.adapters.config.loader import YamlSuiteLoader
from holds.adapters.graders import default_graders
from holds.adapters.reporting import render_run_terminal
from holds.adapters.resilience.maul import MaulResilienceRunner, NoopResilienceRunner
from holds.adapters.runner.subprocess_runner import SubprocessAgentRunner
from holds.adapters.runtime import GitRevisionProvider, SystemClock, UuidFactory
from holds.adapters.storage.filesystem import FilesystemArtifactStore
from holds.application.compare import CompareService
from holds.application.run import RunService
from holds.domain.errors import (
    HoldsError,
    IncompatibleBaselineError,
    ResilienceError,
    SuiteValidationError,
)
from holds.domain.models import ComparisonResult, RunResult, Suite, Thresholds

from holds import __version__ as holds_version
from invariant_mcp.config import Settings
from invariant_mcp.errors import InvariantError
from invariant_mcp.models import (
    AttemptOutcome,
    CompareOutcome,
    ComparisonDeltaModel,
    EvalRun,
    TaskOutcome,
)
from invariant_mcp.safety.process import resolve_executable

_TASK_LIMIT = 200
_NOTICE = "This summary omits raw agent outputs. Read the full Holds report at report_uri."


@dataclass(frozen=True, slots=True)
class EvalRequest:
    """Arguments for one Holds run."""

    suite: str
    report: str
    model: str | None
    provider: str | None
    temperature: float | None
    include_outputs: bool
    enable_maul: bool


@dataclass(frozen=True, slots=True)
class CompareRequest:
    """Arguments for one baseline comparison."""

    candidate: str
    baseline: str
    max_regression_points: float | None
    allow_incompatible: bool


class HoldsAdapter:
    """Workspace-scoped wrapper around `RunService` and `CompareService`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace = settings.workspace

    def run_eval(self, request: EvalRequest) -> EvalRun:
        """Execute a suite and write the versioned report inside the workspace."""
        suite_path = self._require_file(request.suite)
        report_path = self._workspace.confine(request.report, create_parent=True)
        suite = self._load_suite(suite_path)
        self._assert_budget(suite)
        service = RunService(
            agent_runner=SubprocessAgentRunner(),
            graders=default_graders(),
            clock=SystemClock(),
            ids=UuidFactory(),
            revision_provider=GitRevisionProvider(cwd=str(suite_path.parent)),
            resilience_runner=self._resilience(request.enable_maul),
            include_outputs=request.include_outputs,
        )
        try:
            result = service.execute(
                suite,
                env=dict(os.environ),
                model_id=request.model,
                provider=request.provider,
                temperature=request.temperature,
            )
        except ResilienceError as error:
            raise InvariantError("maul_resilience", str(error)) from error
        except HoldsError as error:
            raise InvariantError("holds_failed", str(error)) from error

        FilesystemArtifactStore().write_run(report_path, result)
        markdown_path = report_path.with_suffix(".md")
        markdown_path.write_text(render_run_terminal(result), encoding="utf-8")
        return self._eval_run(
            result,
            report_path=report_path,
            markdown_path=markdown_path,
            include_outputs=request.include_outputs,
        )

    def compare(self, request: CompareRequest) -> CompareOutcome:
        """Compare a candidate report with a baseline. Does not launch an agent."""
        candidate = self._require_file(request.candidate)
        baseline = self._require_file(request.baseline)
        service = CompareService(store=FilesystemArtifactStore())
        thresholds = (
            Thresholds(max_regression_points=request.max_regression_points)
            if request.max_regression_points is not None
            else None
        )
        try:
            result = service.compare(
                candidate_path=candidate,
                baseline_path=baseline,
                thresholds=thresholds,
                allow_incompatible=request.allow_incompatible,
            )
        except IncompatibleBaselineError as error:
            reasons = [part.strip() for part in str(error).split(";") if part.strip()]
            return CompareOutcome(
                holds_version=holds_version,
                compatible=False,
                passed=False,
                incompatibility_reasons=reasons,
            )
        except HoldsError as error:
            raise InvariantError("baseline_failed", str(error)) from error
        return _compare_outcome(result)

    def _load_suite(self, path: Path) -> Suite:
        try:
            return YamlSuiteLoader().load(path)
        except SuiteValidationError as error:
            raise InvariantError("invalid_suite", str(error)) from error

    def _assert_budget(self, suite: Suite) -> None:
        if len(suite.tasks) > _TASK_LIMIT:
            raise InvariantError(
                "eval_budget_exceeded",
                f"suite declares {len(suite.tasks)} tasks; the server cap is {_TASK_LIMIT}",
            )
        total = sum(suite.timeout_for(task) * suite.repeats_for(task) for task in suite.tasks)
        if total > self._settings.max_eval_seconds:
            raise InvariantError(
                "eval_budget_exceeded",
                "suite timeout budget is "
                f"{total:.0f}s and the server cap is {self._settings.max_eval_seconds:.0f}s",
            )

    def _resilience(self, enable_maul: bool) -> MaulResilienceRunner | NoopResilienceRunner:
        if not enable_maul:
            return NoopResilienceRunner()
        if resolve_executable(self._settings.maul_bin) is None:
            raise InvariantError(
                "maul_unavailable",
                "enable_maul is set and the maul binary was not found. "
                "Install Maul or leave enable_maul false.",
            )
        return MaulResilienceRunner(maul_bin=self._settings.maul_bin)

    def _require_file(self, raw: str) -> Path:
        path = self._workspace.confine(raw)
        if not path.is_file():
            raise InvariantError("not_found", f"file not found in the workspace: {raw!r}")
        if path.stat().st_size > self._settings.max_resource_bytes:
            raise InvariantError(
                "file_too_large",
                f"{raw!r} exceeds the {self._settings.max_resource_bytes} byte read limit",
            )
        return path

    def _eval_run(
        self,
        result: RunResult,
        *,
        report_path: Path,
        markdown_path: Path,
        include_outputs: bool,
    ) -> EvalRun:
        outcomes = [
            AttemptOutcome(
                task_id=attempt.task_id,
                attempt_id=attempt.attempt_id,
                status=attempt.status,
                passed=attempt.passed,
                duration_ms=attempt.duration_ms,
                error=attempt.error,
            )
            for attempt in result.attempts
        ]
        limit = self._settings.max_attempts_returned
        truncated = len(outcomes) > limit
        notice = _NOTICE
        if include_outputs:
            notice = f"{notice} The report file includes raw agent outputs."
        return EvalRun(
            holds_version=holds_version,
            threshold_passed=result.summary.threshold_passed,
            run_id=result.run_id,
            suite_hash=result.suite_hash,
            pass_rate=result.summary.pass_rate,
            schema_valid_rate=result.summary.schema_valid_rate,
            attempts=result.summary.attempts,
            passed_attempts=result.summary.passed_attempts,
            threshold_failures=list(result.summary.threshold_failures),
            tasks=[
                TaskOutcome(
                    task_id=task.task_id,
                    attempts=task.attempts,
                    passed=task.passed,
                    pass_rate=task.pass_rate,
                    schema_valid_rate=task.schema_valid_rate,
                    consistency_rate=task.consistency_rate,
                    threshold_passed=task.threshold_passed,
                    threshold_failures=list(task.threshold_failures),
                )
                for task in result.task_summaries
            ],
            attempt_outcomes=outcomes[:limit],
            attempt_outcomes_truncated=truncated,
            report_path=self._workspace.relative(report_path),
            report_uri=f"invariant://holds/report/{self._workspace.relative(report_path)}",
            markdown_path=self._workspace.relative(markdown_path),
            outputs_included_in_report=include_outputs,
            notice=notice,
        )


def _compare_outcome(result: ComparisonResult) -> CompareOutcome:
    return CompareOutcome(
        holds_version=holds_version,
        compatible=result.compatible,
        passed=result.passed,
        deltas=[
            ComparisonDeltaModel(
                name=delta.name,
                candidate=delta.candidate,
                baseline=delta.baseline,
                delta_points=delta.delta_points,
                allowed_regression_points=delta.allowed_regression_points,
                passed=delta.passed,
                message=delta.message,
            )
            for delta in result.deltas
        ],
        incompatibility_reasons=list(result.incompatibility_reasons),
    )
