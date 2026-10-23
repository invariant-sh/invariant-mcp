"""Structured tool results. The return annotations are the MCP output schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AttemptOutcome(BaseModel):
    """One Holds attempt, without stdout or raw model output."""

    task_id: str
    attempt_id: str
    status: str
    passed: bool
    duration_ms: int
    error: str | None = None


class TaskOutcome(BaseModel):
    """Aggregated Holds metrics for one task."""

    task_id: str
    attempts: int
    passed: int
    pass_rate: float
    schema_valid_rate: float | None = None
    consistency_rate: float | None = None
    threshold_passed: bool
    threshold_failures: list[str] = Field(default_factory=list)


class EvalRun(BaseModel):
    """Summary of a Holds run. The full report lives at `report_uri`."""

    holds_version: str
    threshold_passed: bool
    run_id: str
    suite_hash: str
    pass_rate: float
    schema_valid_rate: float | None = None
    attempts: int
    passed_attempts: int
    threshold_failures: list[str] = Field(default_factory=list)
    tasks: list[TaskOutcome]
    attempt_outcomes: list[AttemptOutcome]
    attempt_outcomes_truncated: bool = False
    report_path: str = Field(description="Workspace-relative JSON report path.")
    report_uri: str
    markdown_path: str
    outputs_included_in_report: bool = False
    notice: str


class ComparisonDeltaModel(BaseModel):
    """One metric in a Holds baseline comparison."""

    name: str
    candidate: float | None = None
    baseline: float | None = None
    delta_points: float | None = None
    allowed_regression_points: float | None = None
    passed: bool
    message: str


class CompareOutcome(BaseModel):
    """Result of `holds compare`. A failed gate is still a completed call."""

    holds_version: str
    compatible: bool
    passed: bool
    deltas: list[ComparisonDeltaModel] = Field(default_factory=list)
    incompatibility_reasons: list[str] = Field(default_factory=list)


class PolicySummary(BaseModel):
    """Fields a caller needs in order to read a Vigil decision."""

    version: int
    project: str
    environment: str
    content_hash: str | None = None
    status: str
    max_llm_calls_per_workflow: int | None = None
    max_cost_usd_display: str | None = None
    allowed_models: list[str]
    fallback_model: str | None = None
    controls: list[str]
    audit_emit: bool


class DecisionSummary(BaseModel):
    """One dry-run decision. Prompt text is never included."""

    request_id: str
    workflow_id: str
    action: str
    reason_code: str
    message: str
    requested_model: str
    effective_model: str | None = None


class PolicyCheck(BaseModel):
    """Validation, or validation plus a dry-run when events were supplied."""

    vigil_version: str
    mode: Literal["validate", "dry_run"]
    policy: PolicySummary
    policy_path: str
    policy_uri: str
    allowed_count: int | None = None
    blocked_count: int | None = None
    routed_count: int | None = None
    decisions_path: str | None = None
    decisions_uri: str | None = None
    audit_path: str | None = None
    audit_uri: str | None = None
    decisions: list[DecisionSummary] = Field(default_factory=list)
    decisions_truncated: bool = False
    notice: str


class ScenarioInfo(BaseModel):
    """One fault Maul's v0.1 engine can inject."""

    name: str
    mode: Literal["short_circuit", "mutate_after"]
    calls_upstream: bool
    http_status: int | None = None
    description: str


class ScenarioList(BaseModel):
    """Catalog, plus an optional reading of one maul.yaml."""

    source: str
    binary_present: bool
    scenarios: list[ScenarioInfo]
    config_path: str | None = None
    configured_scenarios: list[str] | None = None
    unrecognized_configured: list[str] = Field(default_factory=list)
    config_valid: bool | None = None
    validation_source: Literal["maul_binary", "local_mirror"] | None = None
    config_message: str | None = None


class ReliabilitySummary(BaseModel):
    """Counters copied from a Maul reliability report. Requests are not inlined."""

    schema_version: str | None = None
    run_id: str | None = None
    total_proxy_requests: int | None = None
    billable_llm_calls: int | None = None
    faults_injected: int | None = None
    successful_requests: int | None = None
    failed_requests: int | None = None
    budget_rejections: int | None = None
    recovery_events: int | None = None
    unrecovered_sessions: int | None = None
    recovered_sessions: int | None = None
    unattributed_requests: int | None = None
    observed_cost_display: str | None = None


class ProbeResult(BaseModel):
    """Outcome of `maul test`. `unavailable` means the binary is not installed."""

    status: Literal[
        "completed",
        "threshold_failed",
        "agent_failed",
        "timed_out",
        "unavailable",
        "invalid_config",
        "failed",
    ]
    message: str
    maul_binary: str | None = None
    config_path: str | None = None
    report_path: str | None = None
    report_uri: str | None = None
    exit_code: int | None = None
    summary: ReliabilitySummary | None = None
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
