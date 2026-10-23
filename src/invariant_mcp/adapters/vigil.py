"""Call Vigil's validate and dry-run services. Nothing here deploys policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vigil.adapters.audit.jsonl_sink import JsonlAuditSink, JsonlDecisionSink
from vigil.adapters.config.loader import YamlPolicyLoader
from vigil.adapters.events.jsonl import JsonlEventSource
from vigil.adapters.runtime import SystemClock, UuidFactory
from vigil.application.dry_run import DryRunService
from vigil.application.validate import ValidatePolicyService
from vigil.domain.errors import (
    AuditError,
    DryRunError,
    EventSourceError,
    PolicyValidationError,
    VigilError,
)
from vigil.domain.policy import Policy

from invariant_mcp.config import Settings
from invariant_mcp.errors import InvariantError
from invariant_mcp.models import DecisionSummary, PolicyCheck, PolicySummary
from invariant_mcp.safety.workspace import Workspace
from vigil import __version__ as vigil_version

_VALIDATE_NOTICE = "Policy validated. No events were evaluated and nothing was deployed."
_DRY_RUN_NOTICE = (
    "Dry-run only. Decision files are overwritten; audit.jsonl is append-only "
    "when the policy sets audit.emit. Suggestions are not deployed."
)


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """Arguments for validate, or validate-and-dry-run when `events` is set."""

    policy: str
    events: str | None
    decisions: str
    audit: str


class VigilAdapter:
    """Workspace-scoped wrapper around Vigil's policy services."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace = settings.workspace

    def check(self, request: PolicyRequest) -> PolicyCheck:
        """Validate a policy. Dry-run it when an events file is provided."""
        policy_path = self._require_file(request.policy)
        if request.events is None:
            return self._validate(policy_path_raw=request.policy, policy_path=policy_path)
        events_path = self._require_file(request.events)
        decisions_path = self._workspace.confine(request.decisions, create_parent=True)
        audit_path = self._workspace.confine(request.audit, create_parent=True)
        service = DryRunService(
            policy_loader=YamlPolicyLoader(),
            event_source=JsonlEventSource(),
            decision_sink=JsonlDecisionSink(),
            audit_sink=JsonlAuditSink(),
            clock=SystemClock(),
            ids=UuidFactory(),
        )
        try:
            result = service.execute(
                policy_path=policy_path,
                events_path=events_path,
                decisions_path=decisions_path,
                audit_path=audit_path,
            )
        except PolicyValidationError as error:
            raise InvariantError("invalid_policy", str(error)) from error
        except EventSourceError as error:
            raise InvariantError("invalid_events", str(error)) from error
        except (DryRunError, AuditError, VigilError) as error:
            raise InvariantError("dry_run_failed", str(error)) from error

        decisions = [
            DecisionSummary(
                request_id=record.request_id,
                workflow_id=record.workflow_id,
                action=record.action,
                reason_code=record.reason_code,
                message=record.message,
                requested_model=record.requested_model,
                effective_model=record.effective_model,
            )
            for record in result.records
        ]
        limit = self._settings.max_decisions_returned
        written_audit = result.audit_path
        decisions_relative = self._workspace.relative(result.decisions_path)
        audit_relative = None if written_audit is None else self._workspace.relative(written_audit)
        return PolicyCheck(
            vigil_version=vigil_version,
            mode="dry_run",
            policy=_summary(result.policy),
            policy_path=self._workspace.relative(policy_path),
            policy_uri=_uri("vigil/policy", self._workspace, policy_path),
            allowed_count=result.allowed_count,
            blocked_count=result.blocked_count,
            routed_count=result.routed_count,
            decisions_path=decisions_relative,
            decisions_uri=f"invariant://vigil/decisions/{decisions_relative}",
            audit_path=audit_relative,
            audit_uri=None
            if audit_relative is None
            else f"invariant://vigil/audit/{audit_relative}",
            decisions=decisions[:limit],
            decisions_truncated=len(decisions) > limit,
            notice=_DRY_RUN_NOTICE,
        )

    def _validate(self, *, policy_path_raw: str, policy_path: Path) -> PolicyCheck:
        del policy_path_raw
        service = ValidatePolicyService(policy_loader=YamlPolicyLoader())
        try:
            result = service.execute(policy_path)
        except PolicyValidationError as error:
            raise InvariantError("invalid_policy", str(error)) from error
        except VigilError as error:
            raise InvariantError("policy_failed", str(error)) from error
        return PolicyCheck(
            vigil_version=vigil_version,
            mode="validate",
            policy=_summary(result.policy),
            policy_path=self._workspace.relative(policy_path),
            policy_uri=_uri("vigil/policy", self._workspace, policy_path),
            notice=_VALIDATE_NOTICE,
        )

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


def _summary(policy: Policy) -> PolicySummary:
    cost = policy.budgets.max_cost_usd_per_workflow
    controls: list[str] = []
    for control in policy.controls:
        if control.type == "circuit_breaker":
            controls.append(
                f"circuit_breaker:{control.condition}:max_occurrences={control.max_occurrences}"
            )
        else:
            patterns = ",".join(control.patterns)
            controls.append(f"tool_content_guard:{control.mode}:{patterns}")
    return PolicySummary(
        version=policy.version,
        project=policy.scope.project,
        environment=policy.scope.environment,
        content_hash=policy.content_hash,
        status=policy.status,
        max_llm_calls_per_workflow=policy.budgets.max_llm_calls_per_workflow,
        max_cost_usd_display=None if cost is None else cost.display,
        allowed_models=list(policy.models.allow),
        fallback_model=policy.models.fallback_model,
        controls=controls,
        audit_emit=policy.audit.emit,
    )


def _uri(kind: str, workspace: Workspace, path: Path) -> str:
    return f"invariant://{kind}/{workspace.relative(path)}"
