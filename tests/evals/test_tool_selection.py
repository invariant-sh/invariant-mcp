"""Does the published tool metadata point at the right tool?

The default mode is deterministic and needs no API key. It fails when the
descriptions are too vague for a lexical reader to tell the tools apart.

Set OPENAI_API_KEY, or INVARIANT_MCP_EVAL_BASE_URL for a local
OpenAI-compatible server, to also ask a model. That test is skipped otherwise.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

import pytest
from mcp import Client

from invariant_mcp.config import Settings
from invariant_mcp.server import create_server
from tests.evals.rank import ToolView, rank, select_tool
from tests.support import REPO_ROOT

CASES: tuple[tuple[str, str], ...] = (
    (
        "Run the Holds evaluation suite and tell me whether the agent solved the tasks.",
        "run_eval",
    ),
    (
        "Compare this Holds candidate report with the accepted baseline and gate it on regression.",
        "compare_baseline",
    ),
    (
        "Validate the Vigil policy and dry-run events for budget and circuit-breaker decisions.",
        "check_policy",
    ),
    (
        "Run a Maul failure probe that injects force_500 and write reliability_report.json.",
        "run_failure_probe",
    ),
    (
        "Which fault scenarios can Maul inject, and which ones does this maul.yaml enable?",
        "list_scenarios",
    ),
)


def test_descriptions_select_the_expected_tool() -> None:
    tools = _views()
    assert {tool.name for tool in tools} == {expected for _, expected in CASES}
    for request, expected in CASES:
        ordered = rank(request, tools)
        winner, best = ordered[0]
        runner_up, second = ordered[1]
        assert winner == expected, f"{request!r} ranked {ordered}"
        assert best > second, f"{request!r} tied between {winner} and {runner_up}: {ordered}"
        assert select_tool(request, tools) == expected


@pytest.mark.llm
def test_model_selects_the_expected_tool() -> None:
    if not _llm_configured():
        pytest.skip(
            "set OPENAI_API_KEY or INVARIANT_MCP_EVAL_BASE_URL to run the live tool-selection eval"
        )
    tools = _openai_tools()
    for request, expected in CASES:
        chosen = _complete(request, tools)
        assert chosen == expected, f"{request!r} chose {chosen}"


def _views() -> list[ToolView]:
    import asyncio

    server = create_server(Settings.from_env(workspace=str(REPO_ROOT), log_level="ERROR"))

    async def _list() -> list[ToolView]:
        async with Client(server) as client:
            listed = await client.list_tools()
        views: list[ToolView] = []
        for tool in listed.tools:
            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {}) or {}
            parts = [tool.name, getattr(tool, "title", "") or "", tool.description or ""]
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            for name, spec in properties.items():
                parts.append(name)
                if isinstance(spec, dict):
                    parts.append(str(spec.get("description", "")))
            views.append(ToolView(name=tool.name, text="\n".join(parts)))
        return views

    return asyncio.run(_list())


def _llm_configured() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("INVARIANT_MCP_EVAL_API_KEY")
        or os.environ.get("INVARIANT_MCP_EVAL_BASE_URL")
    )


def _openai_tools() -> list[dict[str, Any]]:
    import asyncio

    server = create_server(Settings.from_env(workspace=str(REPO_ROOT), log_level="ERROR"))

    async def _list() -> list[dict[str, Any]]:
        async with Client(server) as client:
            listed = await client.list_tools()
        tools: list[dict[str, Any]] = []
        for tool in listed.tools:
            schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {}) or {}
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": schema,
                    },
                }
            )
        return tools

    return asyncio.run(_list())


def _complete(request: str, tools: list[dict[str, Any]]) -> str:
    base = os.environ.get("INVARIANT_MCP_EVAL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("INVARIANT_MCP_EVAL_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("INVARIANT_MCP_EVAL_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "temperature": 0,
        "tool_choice": "required",
        "messages": [
            {
                "role": "system",
                "content": "Choose the single Invariant tool that should handle the user request.",
            },
            {"role": "user", "content": request},
        ],
        "tools": tools,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    http_request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(http_request, timeout=60) as response:
        body = json.loads(response.read().decode())
    message = body["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        msg = f"model returned no tool call: {json.dumps(message)[:500]}"
        raise AssertionError(msg)
    return str(tool_calls[0]["function"]["name"])
