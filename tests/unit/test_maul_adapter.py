"""Maul adapter. The binary is optional; the catalog and argv builder are not."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from invariant_mcp.adapters.maul import (
    SCENARIOS,
    MaulAdapter,
    ProbeRequest,
    build_test_argv,
    normalize_agent_argv,
    preview_maul_config,
)
from invariant_mcp.errors import InvariantError
from tests.support import REPO_ROOT, settings_for


def test_catalog_matches_maul_v0_1_fault_engine() -> None:
    assert tuple(spec.name for spec in SCENARIOS) == (
        "force_500",
        "force_429",
        "malformed_tool_call_json",
    )
    by_name = {spec.name: spec for spec in SCENARIOS}
    assert by_name["force_500"].calls_upstream is False
    assert by_name["force_429"].http_status == 429
    assert by_name["malformed_tool_call_json"].calls_upstream is True
    assert by_name["malformed_tool_call_json"].mode == "mutate_after"


def test_checked_in_maul_yaml_passes_the_local_mirror() -> None:
    preview = preview_maul_config((REPO_ROOT / "examples" / "maul" / "maul.yaml").read_text())
    assert preview.valid, preview.message
    assert preview.scenarios == ("force_500",)


def test_unknown_scenario_is_rejected_by_the_local_mirror() -> None:
    text = """
proxy_listen: "127.0.0.1:7777"
upstream_base_url: "https://api.openai.com"
scenarios: [force_404]
probability: 1.0
seed: 1
budget:
  max_llm_calls: 1
  max_cost_usd: 0
"""
    preview = preview_maul_config(text)
    assert preview.valid is False
    assert "unknown scenario" in preview.message


def test_list_scenarios_labels_the_local_mirror_when_maul_is_absent(tmp_path: Path) -> None:
    config = tmp_path / "maul.yaml"
    config.write_text((REPO_ROOT / "examples" / "maul" / "maul.yaml").read_text(), encoding="utf-8")
    listed = MaulAdapter(settings_for(tmp_path)).list_scenarios("maul.yaml")
    assert listed.binary_present is False
    assert listed.validation_source == "local_mirror"
    assert listed.config_valid is True
    assert listed.configured_scenarios == ["force_500"]
    assert "control plane" in listed.source


def test_probe_without_a_binary_does_not_invent_a_report(tmp_path: Path) -> None:
    config = tmp_path / "maul.yaml"
    config.write_text((REPO_ROOT / "examples" / "maul" / "maul.yaml").read_text(), encoding="utf-8")
    (tmp_path / "agent.py").write_text("print('noop')\n", encoding="utf-8")
    result = MaulAdapter(settings_for(tmp_path)).run_probe(
        ProbeRequest(
            config="maul.yaml",
            agent=["python3", "agent.py"],
            report="artifacts/reliability_report.json",
            fail_on=[],
            timeout_seconds=None,
        )
    )
    assert result.status == "unavailable"
    assert result.summary is None
    assert result.report_uri is None
    assert not (tmp_path / "artifacts" / "reliability_report.json").exists()


def test_agent_flags_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text("print('x')\n", encoding="utf-8")
    with pytest.raises(InvariantError, match="invalid_agent"):
        normalize_agent_argv(settings_for(tmp_path).workspace, ["python3", "-c", "agent.py"])


def test_agent_outside_the_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="invalid_agent"):
        normalize_agent_argv(settings_for(tmp_path).workspace, ["curl", "https://example.com"])


def test_build_test_argv_quotes_the_agent_for_mauls_shell(tmp_path: Path) -> None:
    script = tmp_path / "my agent.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    workspace = settings_for(tmp_path).workspace
    agent = normalize_agent_argv(workspace, ["python3", "my agent.py"])
    argv = build_test_argv(
        binary=Path("/usr/local/bin/maul"),
        config=tmp_path / "maul.yaml",
        agent=agent,
        report=tmp_path / "artifacts" / "reliability_report.json",
        fail_on=["resilience", "cost"],
        timeout_seconds=30,
    )
    assert argv[0] == "/usr/local/bin/maul"
    assert argv[1] == "test"
    assert "--fail-on" in argv
    agent_arg = argv[argv.index("--agent") + 1]
    assert shlex.split(agent_arg)[0] == "python3"
    assert shlex.split(agent_arg)[1].endswith("my agent.py")
    assert ";" not in shlex.split(agent_arg)[1]


def test_summary_reader_keeps_maul_counters(tmp_path: Path) -> None:
    from invariant_mcp.adapters.maul import _read_summary

    report = tmp_path / "reliability_report.json"
    report.write_text(
        """
        {
          "schema_version": "0.2",
          "run_id": "maul-1",
          "total_proxy_requests": 2,
          "billable_llm_calls": 2,
          "faults_injected": 1,
          "summary": {
            "successful_requests": 1,
            "failed_requests": 1,
            "budget_rejections": 0,
            "recovery_events": 1,
            "unrecovered_sessions": 0,
            "recovered_sessions": 1,
            "unattributed_requests": 0,
            "observed_cost_usd": {"micro_usd": 0, "display": "$0.000000"}
          }
        }
        """,
        encoding="utf-8",
    )
    summary = _read_summary(report)
    assert summary is not None
    assert summary.schema_version == "0.2"
    assert summary.faults_injected == 1
    assert summary.observed_cost_display == "$0.000000"
