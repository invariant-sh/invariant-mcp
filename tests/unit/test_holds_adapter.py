"""Holds adapter against the real Holds services."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from holds.adapters.runtime import SystemClock
from holds.adapters.storage.filesystem import FilesystemArtifactStore
from holds.application.baseline import BaselineService

from invariant_mcp.adapters.holds import CompareRequest, EvalRequest, HoldsAdapter
from invariant_mcp.errors import InvariantError
from tests.support import REPO_ROOT, agent_command, copy_example, settings_for


def _suite(
    root: Path, *, command: str, category: str = "refund_request", timeout: int = 15
) -> None:
    shutil.copy(REPO_ROOT / "examples" / "holds" / "agent.py", root / "agent.py")
    document = {
        "version": 1,
        "agent": {"command": command, "result_path": "artifacts/result.json"},
        "defaults": {"repeats": 1, "timeout_seconds": timeout, "seed": 7},
        "tasks": [
            {
                "id": "classify-refund-request",
                "input": {"customer_message": "I was charged twice for the subscription."},
                "graders": [
                    {"type": "exact_field", "path": "$.category", "equals": category},
                ],
                "thresholds": {"pass_rate_gte": 1.0},
            }
        ],
    }
    (root / "holds.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


def test_run_eval_writes_a_passing_report(tmp_path: Path) -> None:
    _suite(tmp_path, command=agent_command())
    result = HoldsAdapter(settings_for(tmp_path)).run_eval(
        EvalRequest(
            suite="holds.yaml",
            report="artifacts/holds_report.json",
            model=None,
            provider=None,
            temperature=None,
            include_outputs=False,
            enable_maul=False,
        )
    )
    assert result.threshold_passed is True
    assert result.pass_rate == 1.0
    assert result.holds_version == "0.1.0"
    report = tmp_path / "artifacts" / "holds_report.json"
    assert report.is_file()
    assert "output" not in report.read_text(
        encoding="utf-8"
    ) or '"output": null' in report.read_text(encoding="utf-8")
    assert (tmp_path / "artifacts" / "holds_report.md").is_file()
    assert result.report_uri == "invariant://holds/report/artifacts/holds_report.json"


def test_threshold_miss_still_returns_the_report(tmp_path: Path) -> None:
    _suite(tmp_path, command=agent_command(), category="other")
    result = HoldsAdapter(settings_for(tmp_path)).run_eval(
        EvalRequest(
            suite="holds.yaml",
            report="artifacts/holds_report.json",
            model=None,
            provider=None,
            temperature=None,
            include_outputs=False,
            enable_maul=False,
        )
    )
    assert result.threshold_passed is False
    assert result.pass_rate == 0.0
    assert (tmp_path / "artifacts" / "holds_report.json").is_file()


def test_suite_over_the_time_budget_is_rejected(tmp_path: Path) -> None:
    _suite(tmp_path, command=agent_command(), timeout=10_000)
    with pytest.raises(InvariantError, match="eval_budget_exceeded"):
        HoldsAdapter(settings_for(tmp_path)).run_eval(
            EvalRequest(
                suite="holds.yaml",
                report="artifacts/holds_report.json",
                model=None,
                provider=None,
                temperature=None,
                include_outputs=False,
                enable_maul=False,
            )
        )


def test_path_escape_is_rejected_before_holds_runs(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="path_escape"):
        HoldsAdapter(settings_for(tmp_path)).run_eval(
            EvalRequest(
                suite="../holds.yaml",
                report="artifacts/holds_report.json",
                model=None,
                provider=None,
                temperature=None,
                include_outputs=False,
                enable_maul=False,
            )
        )


def test_compare_passes_for_a_promoted_baseline(tmp_path: Path) -> None:
    _suite(tmp_path, command=agent_command())
    adapter = HoldsAdapter(settings_for(tmp_path))
    request = EvalRequest(
        suite="holds.yaml",
        report="artifacts/holds_report.json",
        model=None,
        provider=None,
        temperature=None,
        include_outputs=False,
        enable_maul=False,
    )
    adapter.run_eval(request)
    BaselineService(store=FilesystemArtifactStore(), clock=SystemClock()).promote(
        run_path=tmp_path / "artifacts" / "holds_report.json",
        baseline_path=tmp_path / "artifacts" / "baseline.json",
        reviewer="test",
        reason="accepted for the adapter test",
    )
    compared = adapter.compare(
        CompareRequest(
            candidate="artifacts/holds_report.json",
            baseline="artifacts/baseline.json",
            max_regression_points=None,
            allow_incompatible=False,
        )
    )
    assert compared.compatible is True
    assert compared.passed is True
    assert compared.deltas


def test_compare_reports_incompatible_provenance(tmp_path: Path) -> None:
    _suite(tmp_path, command=agent_command())
    adapter = HoldsAdapter(settings_for(tmp_path))
    adapter.run_eval(
        EvalRequest(
            suite="holds.yaml",
            report="artifacts/holds_report.json",
            model=None,
            provider=None,
            temperature=None,
            include_outputs=False,
            enable_maul=False,
        )
    )
    BaselineService(store=FilesystemArtifactStore(), clock=SystemClock()).promote(
        run_path=tmp_path / "artifacts" / "holds_report.json",
        baseline_path=tmp_path / "artifacts" / "baseline.json",
        reviewer="test",
        reason="accepted",
    )
    document = yaml.safe_load((tmp_path / "holds.yaml").read_text(encoding="utf-8"))
    document["tasks"][0]["id"] = "classify-billing-dispute"
    (tmp_path / "holds.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    adapter.run_eval(
        EvalRequest(
            suite="holds.yaml",
            report="artifacts/candidate.json",
            model=None,
            provider=None,
            temperature=None,
            include_outputs=False,
            enable_maul=False,
        )
    )
    compared = adapter.compare(
        CompareRequest(
            candidate="artifacts/candidate.json",
            baseline="artifacts/baseline.json",
            max_regression_points=None,
            allow_incompatible=False,
        )
    )
    assert compared.compatible is False
    assert compared.passed is False
    assert compared.incompatibility_reasons


def test_checked_in_example_suite_passes(tmp_path: Path) -> None:
    example = copy_example("holds", tmp_path)
    document = yaml.safe_load((example / "holds.yaml").read_text(encoding="utf-8"))
    document["agent"]["command"] = agent_command()
    (example / "holds.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    result = HoldsAdapter(settings_for(example)).run_eval(
        EvalRequest(
            suite="holds.yaml",
            report="artifacts/holds_report.json",
            model="deterministic-stub",
            provider="local",
            temperature=0,
            include_outputs=False,
            enable_maul=False,
        )
    )
    assert result.threshold_passed is True
