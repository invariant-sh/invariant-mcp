"""Operator configuration. The model cannot change these values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from invariant_mcp.errors import InvariantError
from invariant_mcp.safety.workspace import Workspace

DEFAULT_MAX_OUTPUT_BYTES = 16_384
DEFAULT_MAX_RESOURCE_BYTES = 1_048_576
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_EVAL_SECONDS = 300.0
DEFAULT_MAX_ATTEMPTS = 40
DEFAULT_MAX_DECISIONS = 20

RESOURCE_BYTE_CEILING = 8_388_608
OUTPUT_BYTE_CEILING = 1_048_576
TIMEOUT_CEILING_SECONDS = 900.0
EVAL_CEILING_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class Settings:
    """Process-wide limits and the workspace root."""

    workspace: Workspace
    maul_bin: str
    max_output_bytes: int
    max_resource_bytes: int
    subprocess_timeout_seconds: float
    max_eval_seconds: float
    max_attempts_returned: int
    max_decisions_returned: int
    log_level: str

    @classmethod
    def from_env(cls, *, workspace: str | None = None, log_level: str | None = None) -> Settings:
        """Load settings from the environment, with an optional workspace override."""
        raw_root = (
            workspace if workspace is not None else os.environ.get("INVARIANT_WORKSPACE", ".")
        )
        return cls(
            workspace=Workspace(Path(raw_root)),
            maul_bin=_maul_bin(os.environ.get("INVARIANT_MAUL_BIN", "maul")),
            max_output_bytes=_bounded_int(
                "INVARIANT_MAX_OUTPUT_BYTES",
                os.environ.get("INVARIANT_MAX_OUTPUT_BYTES"),
                default=DEFAULT_MAX_OUTPUT_BYTES,
                ceiling=OUTPUT_BYTE_CEILING,
            ),
            max_resource_bytes=_bounded_int(
                "INVARIANT_MAX_RESOURCE_BYTES",
                os.environ.get("INVARIANT_MAX_RESOURCE_BYTES"),
                default=DEFAULT_MAX_RESOURCE_BYTES,
                ceiling=RESOURCE_BYTE_CEILING,
            ),
            subprocess_timeout_seconds=_bounded_float(
                "INVARIANT_SUBPROCESS_TIMEOUT_SECONDS",
                os.environ.get("INVARIANT_SUBPROCESS_TIMEOUT_SECONDS"),
                default=DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
                ceiling=TIMEOUT_CEILING_SECONDS,
            ),
            max_eval_seconds=_bounded_float(
                "INVARIANT_MAX_EVAL_SECONDS",
                os.environ.get("INVARIANT_MAX_EVAL_SECONDS"),
                default=DEFAULT_MAX_EVAL_SECONDS,
                ceiling=EVAL_CEILING_SECONDS,
            ),
            max_attempts_returned=_bounded_int(
                "INVARIANT_MAX_ATTEMPTS_RETURNED",
                os.environ.get("INVARIANT_MAX_ATTEMPTS_RETURNED"),
                default=DEFAULT_MAX_ATTEMPTS,
                ceiling=200,
            ),
            max_decisions_returned=_bounded_int(
                "INVARIANT_MAX_DECISIONS_RETURNED",
                os.environ.get("INVARIANT_MAX_DECISIONS_RETURNED"),
                default=DEFAULT_MAX_DECISIONS,
                ceiling=100,
            ),
            log_level=_log_level(log_level or os.environ.get("INVARIANT_LOG_LEVEL") or "INFO"),
        )


def _log_level(raw: str) -> str:
    level = raw.upper()
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in allowed:
        raise InvariantError("bad_config", f"log level must be one of {sorted(allowed)}")
    return level


def _maul_bin(raw: str) -> str:
    if not raw.strip() or "\x00" in raw:
        raise InvariantError("bad_config", "INVARIANT_MAUL_BIN must be a non-empty path or name")
    return raw.strip()


def _bounded_int(name: str, raw: str | None, *, default: int, ceiling: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvariantError("bad_config", f"{name} must be an integer") from exc
    if value < 1 or value > ceiling:
        raise InvariantError("bad_config", f"{name} must be between 1 and {ceiling}")
    return value


def _bounded_float(name: str, raw: str | None, *, default: float, ceiling: float) -> float:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise InvariantError("bad_config", f"{name} must be a number") from exc
    if value <= 0 or value > ceiling:
        raise InvariantError("bad_config", f"{name} must be > 0 and <= {ceiling}")
    return value
