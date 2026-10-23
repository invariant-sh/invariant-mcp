"""Stdio integration: a real MCP client launches this server as a subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml
from holds.adapters.runtime import SystemClock
from holds.adapters.storage.filesystem import FilesystemArtifactStore
from holds.application.baseline import BaselineService
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from tests.support import agent_command, copy_example


def _params(workspace: Path) -> StdioServerParameters:
    env = {key: value for key, value in os.environ.items() if isinstance(value, str)}
    env["INVARIANT_WORKSPACE"] = str(workspace)
    env["INVARIANT_LOG_LEVEL"] = "WARNING"
    env["INVARIANT_MAUL_BIN"] = str(workspace / "missing-maul")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "invariant_mcp", "--workspace", str(workspace), "--log-level", "WARNING"],
        env=env,
        cwd=str(workspace),
    )


def _prepare(root: Path) -> None:
    holds = copy_example("holds", root)
    document = yaml.safe_load((holds / "holds.yaml").read_text(encoding="utf-8"))
    document["agent"]["command"] = agent_command()
    (holds / "holds.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    copy_example("vigil", root)
    copy_example("maul", root)


def test_stdio_client_runs_the_tool_surface(tmp_path: Path) -> None:
    _prepare(tmp_path)
    asyncio.run(_exercise(tmp_path))


async def _exercise(tmp_path: Path) -> None:
    async with Client(_params(tmp_path)) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert names == {
            "run_eval",
            "compare_baseline",
            "check_policy",
            "run_failure_probe",
            "list_scenarios",
        }
        run_eval = next(tool for tool in listed.tools if tool.name == "run_eval")
        output_schema = getattr(run_eval, "output_schema", None) or getattr(
            run_eval, "outputSchema", None
        )
        assert output_schema

        evaluated = await client.call_tool(
            "run_eval",
            {"suite": "holds/holds.yaml", "report": "artifacts/holds_report.json"},
        )
        assert evaluated.is_error is False
        assert evaluated.structured_content is not None
        assert evaluated.structured_content["threshold_passed"] is True
        assert evaluated.structured_content["report_uri"].endswith("artifacts/holds_report.json")

        BaselineService(store=FilesystemArtifactStore(), clock=SystemClock()).promote(
            run_path=tmp_path / "artifacts" / "holds_report.json",
            baseline_path=tmp_path / "artifacts" / "baseline.json",
            reviewer="stdio-test",
            reason="accepted",
        )
        compared = await client.call_tool(
            "compare_baseline",
            {
                "candidate": "artifacts/holds_report.json",
                "baseline": "artifacts/baseline.json",
            },
        )
        assert compared.is_error is False
        assert compared.structured_content is not None
        assert compared.structured_content["passed"] is True

        report = await client.read_resource("invariant://holds/report/artifacts/holds_report.json")
        body = json.loads(report.contents[0].text)
        assert body["summary"]["pass_rate"] == 1.0

        escaped = await client.call_tool("run_eval", {"suite": "../holds.yaml"})
        assert escaped.is_error is True
        assert "path_escape" in escaped.content[0].text

        checked = await client.call_tool(
            "check_policy",
            {"policy": "vigil/vigil.yaml", "events": "vigil/events.jsonl"},
        )
        assert checked.is_error is False
        assert checked.structured_content is not None
        assert checked.structured_content["mode"] == "dry_run"
        assert checked.structured_content["blocked_count"] >= 1

        audit = await client.read_resource("invariant://vigil/audit/artifacts/audit.jsonl")
        assert (
            "tool_content_blocked" in audit.contents[0].text
            or "model_not_allowed" in audit.contents[0].text
        )

        scenarios = await client.call_tool("list_scenarios", {"config": "maul/maul.yaml"})
        assert scenarios.is_error is False
        assert scenarios.structured_content is not None
        assert scenarios.structured_content["validation_source"] == "local_mirror"
        assert scenarios.structured_content["configured_scenarios"] == ["force_500"]

        probe = await client.call_tool(
            "run_failure_probe",
            {
                "config": "maul/maul.yaml",
                "agent": ["python3", "maul/probe_agent.py"],
                "report": "artifacts/reliability_report.json",
            },
        )
        assert probe.is_error is False
        assert probe.structured_content is not None
        assert probe.structured_content["status"] == "unavailable"
        assert probe.structured_content["summary"] is None

        prompt = await client.get_prompt(
            "gate_a_change",
            {"suite": "holds/holds.yaml", "baseline": "artifacts/baseline.json"},
        )
        text = prompt.messages[0].content.text
        assert "run_eval" in text
        assert "compare_baseline" in text

        index = await client.read_resource("invariant://workspace/index")
        listed_uris = index.contents[0].text
        assert "invariant://holds/report/artifacts/holds_report.json" in listed_uris
        assert "invariant://vigil/policy/vigil/vigil.yaml" in listed_uris
