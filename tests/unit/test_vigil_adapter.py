"""Vigil adapter against the real Vigil services."""

from __future__ import annotations

from pathlib import Path

import pytest

from invariant_mcp.adapters.vigil import PolicyRequest, VigilAdapter
from invariant_mcp.errors import InvariantError
from tests.support import copy_example, settings_for


def test_validate_example_policy(tmp_path: Path) -> None:
    policy_dir = copy_example("vigil", tmp_path)
    result = VigilAdapter(settings_for(policy_dir)).check(
        PolicyRequest(
            policy="vigil.yaml",
            events=None,
            decisions="artifacts/decisions.jsonl",
            audit="artifacts/audit.jsonl",
        )
    )
    assert result.mode == "validate"
    assert result.vigil_version == "0.1.0"
    assert result.policy.project == "support-agent"
    assert result.policy.environment == "production"
    assert result.allowed_count is None
    assert "circuit_breaker" in " ".join(result.policy.controls)
    assert result.policy_uri == "invariant://vigil/policy/vigil.yaml"


def test_dry_run_example_events(tmp_path: Path) -> None:
    policy_dir = copy_example("vigil", tmp_path)
    result = VigilAdapter(settings_for(policy_dir)).check(
        PolicyRequest(
            policy="vigil.yaml",
            events="events.jsonl",
            decisions="artifacts/decisions.jsonl",
            audit="artifacts/audit.jsonl",
        )
    )
    assert result.mode == "dry_run"
    counts = (result.allowed_count or 0) + (result.blocked_count or 0) + (result.routed_count or 0)
    assert counts == 3
    assert (result.blocked_count or 0) >= 1
    assert {item.reason_code for item in result.decisions} >= {
        "model_not_allowed",
        "tool_content_blocked",
    }
    assert (policy_dir / "artifacts" / "decisions.jsonl").is_file()
    assert (policy_dir / "artifacts" / "audit.jsonl").is_file()
    assert result.audit_uri == "invariant://vigil/audit/artifacts/audit.jsonl"
    audit = (policy_dir / "artifacts" / "audit.jsonl").read_text(encoding="utf-8")
    assert "customer_message" not in audit
    assert "Authorization" not in audit


def test_invalid_policy_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "vigil.yaml").write_text("version: 99\n", encoding="utf-8")
    with pytest.raises(InvariantError, match="invalid_policy"):
        VigilAdapter(settings_for(tmp_path)).check(
            PolicyRequest(
                policy="vigil.yaml",
                events=None,
                decisions="artifacts/decisions.jsonl",
                audit="artifacts/audit.jsonl",
            )
        )


def test_events_outside_the_workspace_are_rejected(tmp_path: Path) -> None:
    policy_dir = copy_example("vigil", tmp_path)
    with pytest.raises(InvariantError, match="path_escape"):
        VigilAdapter(settings_for(policy_dir)).check(
            PolicyRequest(
                policy="vigil.yaml",
                events="../../etc/passwd",
                decisions="artifacts/decisions.jsonl",
                audit="artifacts/audit.jsonl",
            )
        )
