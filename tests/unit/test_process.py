"""Bounded argv subprocesses."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from invariant_mcp.errors import InvariantError
from invariant_mcp.safety.process import run_argv


def test_output_is_capped_and_shell_is_not_used(tmp_path: Path) -> None:
    completed = run_argv(
        [sys.executable, "-c", "print('x' * 5000)"],
        cwd=tmp_path,
        timeout=10,
        max_bytes=100,
    )
    assert completed.returncode == 0
    assert completed.stdout_truncated
    assert len(completed.stdout) == 100
    assert completed.timed_out is False


def test_timeout_kills_the_process(tmp_path: Path) -> None:
    completed = run_argv(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout=0.3,
        max_bytes=1000,
    )
    assert completed.timed_out
    assert completed.returncode != 0


def test_empty_argv_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="bad_argv"):
        run_argv([], cwd=tmp_path, timeout=1, max_bytes=100)
