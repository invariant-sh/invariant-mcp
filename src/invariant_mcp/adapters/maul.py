"""Maul adapter.

Maul v0.1 is a Rust binary. It has no Python API, and the README marks the
`/__maul` control plane as planned, so this adapter does not pretend to query
a live proxy for the scenario list.

`maul test` runs the agent via `sh -c` (upstream, `src/main.rs`). We never
set `shell=True` ourselves. The agent argv is checked, rewritten to absolute
workspace paths, and passed as one `shlex.quote`d argument so that shell
metacharacters stay data.

The scenario catalog is compiled from `maul::fault` at commit
`0d18832a160b926a82f7503dbe1f42bf35a1e185` (`force_500`, `force_429`,
`malformed_tool_call_json`). `maul --validate` is authoritative when the
binary is installed. Otherwise a local mirror of `RawConfig` is labeled
`local_mirror`.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import yaml

from invariant_mcp.config import Settings
from invariant_mcp.errors import InvariantError
from invariant_mcp.models import ProbeResult, ReliabilitySummary, ScenarioInfo, ScenarioList
from invariant_mcp.safety.process import resolve_executable, run_argv
from invariant_mcp.safety.workspace import Workspace

MAUL_CATALOG_COMMIT = "0d18832a160b926a82f7503dbe1f42bf35a1e185"
_CATALOG_SOURCE = (
    "Compiled from Maul v0.1 fault engine constants "
    f"(commit {MAUL_CATALOG_COMMIT}). The /__maul control plane is not implemented, "
    "so this list is not queried from a running proxy."
)

_INTERPRETERS = frozenset(
    {"python", "python3", "python3.12", "python3.13", "python3.14"},
)
_FAIL_ON = frozenset({"budget", "cost", "resilience"})
_REQUIRED_FIELDS = frozenset(
    {"proxy_listen", "upstream_base_url", "scenarios", "probability", "seed", "budget"},
)
_OPTIONAL_FIELDS = frozenset({"model_prices"})
_STARTUP_GRACE_SECONDS = 20.0

ProbeStatus = Literal[
    "completed",
    "threshold_failed",
    "agent_failed",
    "timed_out",
    "unavailable",
    "invalid_config",
    "failed",
]


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """Static description of one Maul scenario. Wording follows the Maul README."""

    name: str
    mode: Literal["short_circuit", "mutate_after"]
    calls_upstream: bool
    http_status: int | None
    description: str


SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(
        name="force_500",
        mode="short_circuit",
        calls_upstream=False,
        http_status=500,
        description=(
            "Short-circuit. Maul returns HTTP 500 with an OpenAI error envelope "
            "and does not call upstream."
        ),
    ),
    ScenarioSpec(
        name="force_429",
        mode="short_circuit",
        calls_upstream=False,
        http_status=429,
        description=(
            "Short-circuit. Maul returns HTTP 429 with Retry-After: 1 and does not call upstream."
        ),
    ),
    ScenarioSpec(
        name="malformed_tool_call_json",
        mode="mutate_after",
        calls_upstream=True,
        http_status=None,
        description=(
            "Mutate-after. Maul calls upstream, then rewrites "
            "tool_calls[].function.arguments to invalid JSON on the response, "
            "for both JSON and SSE bodies."
        ),
    ),
)

_KNOWN = frozenset(spec.name for spec in SCENARIOS)


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    """Arguments for one `maul test` invocation."""

    config: str
    agent: list[str]
    report: str
    fail_on: list[str]
    timeout_seconds: float | None


@dataclass(frozen=True, slots=True)
class ConfigPreview:
    """Local reading of a Maul YAML file."""

    valid: bool
    message: str
    scenarios: tuple[str, ...] | None


class MaulAdapter:
    """Scenario catalog and `maul test` / `maul --validate`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace = settings.workspace

    def list_scenarios(self, config: str | None) -> ScenarioList:
        """Return the compiled catalog, optionally checked against a config file."""
        binary = resolve_executable(self._settings.maul_bin)
        catalog = [_info(spec) for spec in SCENARIOS]
        if config is None:
            return ScenarioList(
                source=_CATALOG_SOURCE,
                binary_present=binary is not None,
                scenarios=catalog,
            )
        path = self._require_file(config)
        preview = preview_maul_config(path.read_text(encoding="utf-8"))
        unrecognized = _unrecognized(preview.scenarios)
        if binary is None:
            return ScenarioList(
                source=_CATALOG_SOURCE,
                binary_present=False,
                scenarios=catalog,
                config_path=self._workspace.relative(path),
                configured_scenarios=None if preview.scenarios is None else list(preview.scenarios),
                unrecognized_configured=unrecognized,
                config_valid=preview.valid,
                validation_source="local_mirror",
                config_message=_local_message(preview),
            )
        validated = self._validate_with_binary(binary, path)
        return ScenarioList(
            source=_CATALOG_SOURCE,
            binary_present=True,
            scenarios=catalog,
            config_path=self._workspace.relative(path),
            configured_scenarios=None if preview.scenarios is None else list(preview.scenarios),
            unrecognized_configured=unrecognized,
            config_valid=validated.valid,
            validation_source="maul_binary",
            config_message=validated.message,
        )

    def run_probe(self, request: ProbeRequest) -> ProbeResult:
        """Run `maul test` when the binary exists. Never synthesize a report."""
        binary = resolve_executable(self._settings.maul_bin)
        if binary is None:
            return ProbeResult(
                status="unavailable",
                message=(
                    f"maul binary {self._settings.maul_bin!r} was not found. "
                    "Install Maul from https://github.com/invariant-sh/maul "
                    "(GitHub release or `cargo install --git`). "
                    "No reliability report was produced."
                ),
            )
        config_path = self._require_file(request.config)
        report_path = self._workspace.confine(request.report, create_parent=True)
        agent = normalize_agent_argv(self._workspace, request.agent)
        fail_on = _normalize_fail_on(request.fail_on)
        timeout = self._timeout(request.timeout_seconds)
        argv = build_test_argv(
            binary=binary,
            config=config_path,
            agent=agent,
            report=report_path,
            fail_on=fail_on,
            timeout_seconds=timeout,
        )
        # Maul's own agent timeout is `timeout`. We add grace for proxy startup
        # and report flush, then kill the whole `maul test` process.
        completed = run_argv(
            argv,
            cwd=self._workspace.root,
            timeout=timeout + _STARTUP_GRACE_SECONDS,
            max_bytes=self._settings.max_output_bytes,
        )
        summary = _read_summary(report_path)
        status = _classify(
            completed.returncode, completed.stdout, completed.stderr, completed.timed_out
        )
        relative_report = self._workspace.relative(report_path) if report_path.is_file() else None
        return ProbeResult(
            status=status,
            message=_probe_message(
                status, completed.returncode, completed.stderr, completed.stdout
            ),
            maul_binary=str(binary),
            config_path=self._workspace.relative(config_path),
            report_path=relative_report,
            report_uri=None
            if relative_report is None
            else f"invariant://maul/report/{relative_report}",
            exit_code=completed.returncode,
            summary=summary,
            stdout_excerpt=completed.stdout,
            stderr_excerpt=completed.stderr,
            stdout_truncated=completed.stdout_truncated,
            stderr_truncated=completed.stderr_truncated,
        )

    def _timeout(self, requested: float | None) -> float:
        cap = self._settings.subprocess_timeout_seconds
        if requested is None:
            return cap
        if requested > cap:
            raise InvariantError(
                "timeout_exceeds_cap",
                f"timeout_seconds {requested} exceeds the server cap of {cap}",
            )
        return requested

    def _require_file(self, raw: str) -> Path:
        path = self._workspace.confine(raw)
        if not path.is_file():
            raise InvariantError("not_found", f"file not found in the workspace: {raw!r}")
        size = path.stat().st_size
        if size > self._settings.max_resource_bytes:
            raise InvariantError(
                "file_too_large",
                f"{raw!r} exceeds the {self._settings.max_resource_bytes} byte read limit",
            )
        return path

    def _validate_with_binary(self, binary: Path, config: Path) -> ConfigPreview:
        completed = run_argv(
            [str(binary), "--config", str(config), "--validate"],
            cwd=self._workspace.root,
            timeout=min(30.0, self._settings.subprocess_timeout_seconds),
            max_bytes=self._settings.max_output_bytes,
        )
        detail = (completed.stderr or completed.stdout).strip()
        if completed.timed_out:
            return ConfigPreview(False, "maul --validate timed out", None)
        if completed.returncode == 0:
            return ConfigPreview(True, "maul --validate accepted the file", None)
        return ConfigPreview(
            False, detail or f"maul --validate exited {completed.returncode}", None
        )


def normalize_agent_argv(workspace: Workspace, argv: list[str]) -> list[str]:
    """Accept a workspace script. Reject shell flags and out-of-tree executables."""
    if not argv:
        raise InvariantError("invalid_agent", "agent argv must not be empty")
    if len(argv) > 16:
        raise InvariantError("invalid_agent", "agent argv is limited to 16 entries")
    for part in argv:
        if not isinstance(part, str) or not part or "\x00" in part:
            raise InvariantError("invalid_agent", "agent argv entries must be non-empty strings")
        if part.startswith("-"):
            raise InvariantError(
                "invalid_agent",
                "agent arguments cannot include flags. "
                "Pass an allowlisted interpreter and a workspace script, "
                "or a workspace executable and positional arguments.",
            )
    first, *rest = argv
    if _is_path_like(first):
        executable = _workspace_file(workspace, first, label="agent executable")
        return [str(executable), *[_positional(workspace, part) for part in rest]]
    if first not in _INTERPRETERS:
        raise InvariantError(
            "invalid_agent",
            f"{first!r} is not an allowlisted interpreter and is not a file inside the workspace",
        )
    if not rest:
        raise InvariantError(
            "invalid_agent",
            "interpreter invocations must include a script path inside the workspace",
        )
    script = _workspace_file(workspace, rest[0], label="agent script")
    return [first, str(script), *[_positional(workspace, part) for part in rest[1:]]]


def build_test_argv(
    *,
    binary: Path,
    config: Path,
    agent: list[str],
    report: Path,
    fail_on: list[str],
    timeout_seconds: float,
) -> list[str]:
    """Build the `maul test` argv. The agent string is quoted for Maul's `sh -c`."""
    argv = [
        str(binary),
        "test",
        "--config",
        str(config),
        "--agent",
        shlex.join(agent),
        "--report",
        str(report),
        "--timeout-secs",
        str(int(timeout_seconds)),
    ]
    if fail_on:
        argv.extend(["--fail-on", ",".join(fail_on)])
    return argv


def preview_maul_config(text: str) -> ConfigPreview:
    """Mirror Maul v0.1 `RawConfig` checks well enough to label a local result."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return ConfigPreview(False, f"invalid YAML: {error}", None)
    if not isinstance(document, dict):
        return ConfigPreview(False, "configuration document must be a mapping", None)
    scenarios = _scenario_names(document.get("scenarios"))
    problems = _config_problems(document, scenarios)
    if problems:
        return ConfigPreview(False, "; ".join(problems), scenarios)
    return ConfigPreview(True, "local mirror accepted the file", scenarios)


def _info(spec: ScenarioSpec) -> ScenarioInfo:
    return ScenarioInfo(
        name=spec.name,
        mode=spec.mode,
        calls_upstream=spec.calls_upstream,
        http_status=spec.http_status,
        description=spec.description,
    )


def _unrecognized(scenarios: tuple[str, ...] | None) -> list[str]:
    if scenarios is None:
        return []
    return [name for name in scenarios if name not in _KNOWN]


def _local_message(preview: ConfigPreview) -> str:
    if preview.valid:
        return (
            "local mirror of Maul v0.1 RawConfig accepted the file. "
            "Install the maul binary for an authoritative `maul --validate`."
        )
    return preview.message


def _normalize_fail_on(values: list[str]) -> list[str]:
    unknown = [item for item in values if item not in _FAIL_ON]
    if unknown:
        raise InvariantError(
            "invalid_fail_on",
            f"fail_on accepts budget, cost, resilience; got {unknown}",
        )
    return list(values)


def _is_path_like(value: str) -> bool:
    return "/" in value or value.startswith(".")


def _workspace_file(workspace: Workspace, raw: str, *, label: str) -> Path:
    path = workspace.confine(raw)
    if not path.is_file():
        raise InvariantError(
            "invalid_agent", f"{label} is not a file inside the workspace: {raw!r}"
        )
    return path


def _positional(workspace: Workspace, part: str) -> str:
    if len(part) > 512:
        raise InvariantError("invalid_agent", "agent argument is too long")
    if _is_path_like(part):
        return str(workspace.confine(part))
    return part


def _scenario_names(raw: object) -> tuple[str, ...] | None:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return None
    return tuple(raw)


def _config_problems(document: dict[str, object], scenarios: tuple[str, ...] | None) -> list[str]:
    problems: list[str] = []
    unknown = sorted(set(document) - _REQUIRED_FIELDS - _OPTIONAL_FIELDS)
    if unknown:
        problems.append(f"unknown fields: {unknown}")
    missing = sorted(_REQUIRED_FIELDS - set(document))
    if missing:
        problems.append(f"missing fields: {missing}")
    problems.extend(_string_problems(document))
    problems.extend(_probability_problems(document.get("probability")))
    problems.extend(_seed_problems(document.get("seed")))
    problems.extend(_scenario_problems(scenarios))
    problems.extend(_budget_problems(document.get("budget")))
    problems.extend(_price_problems(document.get("model_prices", {})))
    return problems


def _string_problems(document: dict[str, object]) -> list[str]:
    problems: list[str] = []
    for key in ("proxy_listen", "upstream_base_url"):
        value = document.get(key)
        if key in document and (not isinstance(value, str) or not value.strip()):
            problems.append(f"{key} must be a non-empty string")
    return problems


def _probability_problems(value: object) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ["probability must be a number between 0 and 1"]
    if value != value or value in {float("inf"), float("-inf")} or not 0.0 <= float(value) <= 1.0:
        return ["probability must be finite and between 0.0 and 1.0"]
    return []


def _seed_problems(value: object) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, int):
        return ["seed must be a non-negative integer"]
    if value < 0 or value > 2**64 - 1:
        return ["seed must fit in a u64"]
    return []


def _scenario_problems(scenarios: tuple[str, ...] | None) -> list[str]:
    if scenarios is None:
        return ["scenarios must be a list of strings"]
    problems: list[str] = []
    seen: set[str] = set()
    for name in scenarios:
        if name not in _KNOWN:
            problems.append(f"unknown scenario `{name}`")
        if name in seen:
            problems.append(f"scenario `{name}` is configured more than once")
        seen.add(name)
    return problems


def _budget_problems(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["budget must be a mapping"]
    problems: list[str] = []
    calls = value.get("max_llm_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 1:
        problems.append("budget.max_llm_calls must be an integer greater than zero")
    cost = value.get("max_cost_usd")
    if not _usd_ok(cost):
        problems.append(
            "budget.max_cost_usd must be a non-negative number with at most 6 decimal places"
        )
    extra = sorted(set(value) - {"max_llm_calls", "max_cost_usd"})
    if extra:
        problems.append(f"unknown budget fields: {extra}")
    return problems


def _price_problems(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, dict):
        return ["model_prices must be a mapping"]
    problems: list[str] = []
    for model, price in value.items():
        if not isinstance(model, str) or not model.strip() or any(char.isspace() for char in model):
            problems.append("model_prices keys must be non-empty model ids without whitespace")
            continue
        if not isinstance(price, dict):
            problems.append(f"model_prices.{model} must be a mapping")
            continue
        for field in ("input_usd_per_million", "output_usd_per_million"):
            if not _usd_ok(price.get(field)):
                problems.append(f"model_prices.{model}.{field} must be a non-negative USD amount")
    return problems


def _usd_ok(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    if amount < 0:
        return False
    exponent = amount.as_tuple().exponent
    return isinstance(exponent, int) and exponent >= -6


def _classify(returncode: int, stdout: str, stderr: str, timed_out: bool) -> ProbeStatus:
    if timed_out:
        return "timed_out"
    if returncode == 0:
        return "completed"
    blob = f"{stdout}\n{stderr}"
    if "agent command timed out" in blob:
        return "timed_out"
    if "threshold failed" in blob:
        return "threshold_failed"
    if "agent command exited unsuccessfully" in blob:
        return "agent_failed"
    if (
        "unknown scenario" in blob
        or "failed to parse config" in blob
        or "failed to read config" in blob
    ):
        return "invalid_config"
    return "failed"


def _probe_message(status: ProbeStatus, returncode: int, stderr: str, stdout: str) -> str:
    detail = (stderr or stdout).strip()
    if status == "completed":
        return "maul test finished and wrote a reliability report"
    if detail:
        return detail
    return f"maul test exited {returncode} with status {status}"


def _read_summary(path: Path) -> ReliabilitySummary | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    return ReliabilitySummary(
        schema_version=_optional_str(payload.get("schema_version")),
        run_id=_optional_str(payload.get("run_id")),
        total_proxy_requests=_optional_int(payload.get("total_proxy_requests")),
        billable_llm_calls=_optional_int(payload.get("billable_llm_calls")),
        faults_injected=_optional_int(payload.get("faults_injected")),
        successful_requests=_optional_int(summary.get("successful_requests")),
        failed_requests=_optional_int(summary.get("failed_requests")),
        budget_rejections=_optional_int(summary.get("budget_rejections")),
        recovery_events=_optional_int(summary.get("recovery_events")),
        unrecovered_sessions=_optional_int(summary.get("unrecovered_sessions")),
        recovered_sessions=_optional_int(summary.get("recovered_sessions")),
        unattributed_requests=_optional_int(summary.get("unattributed_requests")),
        observed_cost_display=_cost_display(summary.get("observed_cost_usd")),
    )


def _cost_display(value: object) -> str | None:
    if isinstance(value, dict):
        display = value.get("display")
        if isinstance(display, str):
            return display
        micro = value.get("micro_usd")
        if isinstance(micro, int) and not isinstance(micro, bool):
            return f"${micro / 1_000_000:.6f}"
    return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
