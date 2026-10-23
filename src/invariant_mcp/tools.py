"""Model-facing tools. Descriptions are the text a client shows the model."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from invariant_mcp.adapters.holds import CompareRequest, EvalRequest, HoldsAdapter
from invariant_mcp.adapters.maul import MaulAdapter, ProbeRequest
from invariant_mcp.adapters.vigil import PolicyRequest, VigilAdapter
from invariant_mcp.config import Settings
from invariant_mcp.errors import InvariantError
from invariant_mcp.models import CompareOutcome, EvalRun, PolicyCheck, ProbeResult, ScenarioList

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_RUN_EVAL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
_CHECK_POLICY = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)
_PROBE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

_Path = Annotated[str, Field(min_length=1, max_length=4096)]


def register_tools(mcp: MCPServer, settings: Settings) -> None:
    """Register the five Invariant tools on `mcp`."""
    holds = HoldsAdapter(settings)
    vigil = VigilAdapter(settings)
    maul = MaulAdapter(settings)

    @mcp.tool(
        title="Run Holds evaluation",
        description=_RUN_EVAL_DESCRIPTION,
        annotations=_RUN_EVAL,
    )
    def run_eval(
        suite: Annotated[
            _Path, Field(description="Workspace-relative path to a Holds suite (holds.yaml).")
        ],
        report: Annotated[
            _Path,
            Field(
                description=(
                    "Workspace-relative JSON report path. Default artifacts/holds_report.json."
                )
            ),
        ] = "artifacts/holds_report.json",
        model: Annotated[
            str | None,
            Field(description="Optional model id stored as report metadata."),
        ] = None,
        provider: Annotated[
            str | None,
            Field(description="Optional provider name stored as report metadata."),
        ] = None,
        temperature: Annotated[
            float | None,
            Field(description="Optional sampling temperature stored as report metadata."),
        ] = None,
        include_outputs: Annotated[
            bool,
            Field(description="When true, Holds writes raw agent outputs into the report file."),
        ] = False,
        enable_maul: Annotated[
            bool,
            Field(
                description=(
                    "When true, Holds starts its per-attempt Maul proxy for tasks "
                    "that declare Maul scenarios. Requires the maul binary."
                )
            ),
        ] = False,
    ) -> EvalRun:
        """Run a Holds evaluation suite."""
        with tool_errors():
            return holds.run_eval(
                EvalRequest(
                    suite=suite,
                    report=report,
                    model=model,
                    provider=provider,
                    temperature=temperature,
                    include_outputs=include_outputs,
                    enable_maul=enable_maul,
                )
            )

    @mcp.tool(
        title="Compare Holds baseline",
        description=_COMPARE_DESCRIPTION,
        annotations=_READ_ONLY,
    )
    def compare_baseline(
        candidate: Annotated[_Path, Field(description="Workspace-relative Holds run report.")],
        baseline: Annotated[
            _Path, Field(description="Workspace-relative accepted Holds baseline JSON.")
        ],
        max_regression_points: Annotated[
            float | None,
            Field(
                description=(
                    "Allowed drop, in percentage points. Default is the baseline threshold."
                ),
                ge=0,
            ),
        ] = None,
        allow_incompatible: Annotated[
            bool,
            Field(description="Compare even when suite or grader provenance differs."),
        ] = False,
    ) -> CompareOutcome:
        """Compare a Holds candidate report with an accepted baseline."""
        with tool_errors():
            return holds.compare(
                CompareRequest(
                    candidate=candidate,
                    baseline=baseline,
                    max_regression_points=max_regression_points,
                    allow_incompatible=allow_incompatible,
                )
            )

    @mcp.tool(
        title="Check Vigil policy",
        description=_CHECK_POLICY_DESCRIPTION,
        annotations=_CHECK_POLICY,
    )
    def check_policy(
        policy: Annotated[
            _Path, Field(description="Workspace-relative Vigil policy (vigil.yaml).")
        ],
        events: Annotated[
            str | None,
            Field(
                description=(
                    "Optional workspace-relative JSONL of recorded requests. "
                    "Omit it to validate the policy only."
                ),
                max_length=4096,
            ),
        ] = None,
        decisions: Annotated[
            _Path,
            Field(
                description="Workspace-relative decision JSONL. Default artifacts/decisions.jsonl."
            ),
        ] = "artifacts/decisions.jsonl",
        audit: Annotated[
            _Path,
            Field(
                description="Workspace-relative audit.jsonl, written when the policy emits audit."
            ),
        ] = "artifacts/audit.jsonl",
    ) -> PolicyCheck:
        """Validate a Vigil policy and dry-run recorded events."""
        with tool_errors():
            return vigil.check(
                PolicyRequest(policy=policy, events=events, decisions=decisions, audit=audit)
            )

    @mcp.tool(
        title="Run Maul failure probe",
        description=_PROBE_DESCRIPTION,
        annotations=_PROBE,
    )
    def run_failure_probe(
        config: Annotated[_Path, Field(description="Workspace-relative maul.yaml.")],
        agent: Annotated[
            list[str],
            Field(
                description=(
                    "Argv Maul launches against the proxy. Example: ['python3', 'agent.py']. "
                    "A shell string is rejected."
                ),
                min_length=1,
            ),
        ],
        report: Annotated[
            _Path,
            Field(description="Workspace-relative reliability_report.json destination."),
        ] = "artifacts/reliability_report.json",
        fail_on: Annotated[
            list[Literal["budget", "cost", "resilience"]] | None,
            Field(description="Thresholds Maul evaluates after the run: budget, cost, resilience."),
        ] = None,
        timeout_seconds: Annotated[
            float | None,
            Field(description="Agent timeout inside maul test, in seconds.", gt=0, le=900),
        ] = None,
    ) -> ProbeResult:
        """Run a Maul failure probe."""
        with tool_errors():
            return maul.run_probe(
                ProbeRequest(
                    config=config,
                    agent=agent,
                    report=report,
                    fail_on=list(fail_on or []),
                    timeout_seconds=timeout_seconds,
                )
            )

    @mcp.tool(
        title="List Maul scenarios",
        description=_LIST_SCENARIOS_DESCRIPTION,
        annotations=_READ_ONLY,
    )
    def list_scenarios(
        config: Annotated[
            str | None,
            Field(
                description=(
                    "Optional workspace-relative maul.yaml to validate and to show "
                    "enabled scenarios."
                ),
                max_length=4096,
            ),
        ] = None,
    ) -> ScenarioList:
        """List Maul fault scenarios."""
        with tool_errors():
            return maul.list_scenarios(config)


@contextmanager
def tool_errors() -> Iterator[None]:
    """Surface anticipated failures as tool errors the model can read."""
    try:
        yield
    except InvariantError as error:
        raise ToolError(str(error)) from error


_RUN_EVAL_DESCRIPTION = """\
Run a Holds evaluation suite (holds run).

Measure whether the agent solved the tasks in a holds.yaml suite. Holds launches \
the suite's agent, grades the JSON result artifact, applies pass-rate thresholds, \
and writes a versioned report plus a markdown summary. The result includes \
threshold_passed, pass_rate, per-task summaries, and report_uri for the full JSON.

Paths are workspace-relative. A threshold miss still returns this object and still \
writes the report. Set enable_maul when the suite's tasks declare Maul scenarios \
and the operator has installed the maul binary. Grading remains the measure of \
task success.\
"""

_COMPARE_DESCRIPTION = """\
Compare a Holds candidate report with an accepted baseline (holds compare).

Gate a change on regression. The call checks suite and grader provenance, then \
compares pass_rate and schema_valid_rate with the baseline's allowed regression \
in percentage points. It returns compatible, passed, and per-metric deltas. It \
reads the candidate report and the baseline only. Promoting a new baseline stays \
in the Holds CLI so a person accepts the run.\
"""

_CHECK_POLICY_DESCRIPTION = """\
Validate a Vigil policy and dry-run recorded events (vigil policy validate, vigil policy dry-run).

Check budgets, the model allow-list, circuit breakers, and tool-content guards in \
vigil.yaml. Omit events to validate the policy only. Pass a JSONL events file of \
recorded requests (fingerprints and cost, no prompt bodies) to evaluate each \
request and write decisions plus an audit log. The result includes allowed_count, \
blocked_count, routed_count, and decision summaries. A blocked request is still a \
completed check. Activating a policy stays a human step.\
"""

_PROBE_DESCRIPTION = """\
Run a Maul failure probe against an agent (maul test).

Start an isolated Maul proxy from maul.yaml, point the agent at it, and collect \
reliability_report.json. The proxy injects the faults that config enables, \
including force_500, force_429, and malformed_tool_call_json. The result includes \
status, Maul's summary counters, and report_uri. force_500 and force_429 do not \
call upstream. If the maul binary is not installed, status is unavailable and no \
reliability report is fabricated.\
"""

_LIST_SCENARIOS_DESCRIPTION = """\
List Maul fault scenarios and which of them a maul.yaml enables.

Return the fault catalog compiled from Maul v0.1: force_500 (HTTP 500, upstream \
not called), force_429 (HTTP 429, upstream not called), and \
malformed_tool_call_json (upstream is called, then tool-call arguments are \
rewritten to invalid JSON). Pass a maul.yaml path to see the enabled scenario \
names and whether that file validates. validation_source is maul_binary when the \
binary is installed, otherwise local_mirror. Maul's HTTP control plane is not \
shipped, so this catalog is the fault-engine list rather than a live query.\
"""
