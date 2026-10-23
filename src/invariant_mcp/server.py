"""Build the Invariant MCP server."""

from __future__ import annotations

from typing import Literal, cast

from mcp.server import MCPServer

from invariant_mcp import __version__
from invariant_mcp.config import Settings
from invariant_mcp.prompts import register_prompts
from invariant_mcp.resources import register_resources
from invariant_mcp.tools import register_tools

INSTRUCTIONS = """\
You are connected to Invariant, a local MCP server for three sibling tools.
Holds measures whether an agent solved its tasks.
Maul injects dependency failures and writes a reliability report.
Vigil checks runtime policy: budgets, model allow-lists, circuit breakers, and tool-content guards.

Call run_eval to execute a Holds suite.
Call compare_baseline to gate a Holds report against an accepted baseline.
Call check_policy to validate a Vigil policy, and to dry-run recorded events
when an events file is provided.
Call list_scenarios to read Maul's fault catalog and which scenarios a maul.yaml enables.
Call run_failure_probe to run an agent through an isolated Maul proxy.

Paths are relative to the operator-configured workspace.
Read full artifacts with invariant:// resources.
Report the numbers the tools and resources return.
When Maul is not installed, run_failure_probe returns status unavailable
and does not invent a report.
No tool deploys a Vigil policy or promotes a Holds baseline.\
"""

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def create_server(settings: Settings) -> MCPServer:
    """Register tools, resources, and prompts on a new server bound to `settings`."""
    mcp = MCPServer(
        name="invariant",
        title="Invariant",
        version=__version__,
        website_url="https://getinvariant.sh/",
        instructions=INSTRUCTIONS,
        log_level=cast(LogLevel, settings.log_level),
    )
    register_tools(mcp, settings)
    register_resources(mcp, settings)
    register_prompts(mcp)
    return mcp


def __getattr__(name: str) -> MCPServer:
    """Lazy `mcp` object for `mcp dev` / the Inspector.

    Importing this module must not require a workspace. The CLI builds its own
    server so `--workspace` still overrides the environment. Set
    INVARIANT_WORKSPACE before `mcp dev`; that command imports `mcp`.
    """
    if name == "mcp":
        server = create_server(Settings.from_env())
        globals()["mcp"] = server
        return server
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
