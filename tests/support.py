"""Shared fixtures for adapter and server tests."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from invariant_mcp.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]

_MANAGED_ENV = (
    "INVARIANT_WORKSPACE",
    "INVARIANT_MAUL_BIN",
    "INVARIANT_MAX_OUTPUT_BYTES",
    "INVARIANT_MAX_RESOURCE_BYTES",
    "INVARIANT_SUBPROCESS_TIMEOUT_SECONDS",
    "INVARIANT_MAX_EVAL_SECONDS",
    "INVARIANT_MAX_ATTEMPTS_RETURNED",
    "INVARIANT_MAX_DECISIONS_RETURNED",
    "INVARIANT_LOG_LEVEL",
)


def settings_for(root: Path, **env: str) -> Settings:
    """Build settings for a temp workspace, ignoring the developer environment."""
    overrides = {"INVARIANT_MAUL_BIN": str(root / "missing-maul")}
    overrides.update(env)
    previous = {key: os.environ.get(key) for key in _MANAGED_ENV}
    for key in _MANAGED_ENV:
        os.environ.pop(key, None)
    os.environ.update(overrides)
    try:
        return Settings.from_env(workspace=str(root), log_level="WARNING")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def copy_example(name: str, destination: Path) -> Path:
    """Copy examples/<name> into `destination` / name."""
    target = destination / name
    shutil.copytree(REPO_ROOT / "examples" / name, target)
    return target


def agent_command() -> str:
    """Shell string Holds will pass to `sh -c`. Quoted so paths with spaces work."""
    import shlex

    return f"{shlex.quote(sys.executable)} agent.py"
