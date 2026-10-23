"""Run argv subprocesses with a timeout and a bounded capture.

Never passes `shell=True`. Callers build an argument vector. Output past
`max_bytes` is discarded while the pipe is still drained, so a chatty child
cannot fill the OS pipe and deadlock, and cannot fill memory.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from invariant_mcp.errors import InvariantError


@dataclass(frozen=True, slots=True)
class ProcessOutput:
    """Captured result of one argv subprocess."""

    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool


def resolve_executable(name: str) -> Path | None:
    """Find an operator-configured binary. Missing is `None`, not an error."""
    if not isinstance(name, str) or not name.strip() or "\x00" in name:
        return None
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    found = shutil.which(name)
    if found is None:
        return None
    return Path(found)


def run_argv(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_bytes: int,
    env: dict[str, str] | None = None,
) -> ProcessOutput:
    """Execute `argv` without a shell. Kill the process when `timeout` elapses."""
    _validate_argv(argv)
    if timeout <= 0:
        raise InvariantError("bad_timeout", "subprocess timeout must be > 0")
    if max_bytes < 1:
        raise InvariantError("bad_config", "output byte limit must be >= 1")

    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    stdout_box: dict[str, tuple[str, bool]] = {}
    stderr_box: dict[str, tuple[str, bool]] = {}
    stdout_thread = threading.Thread(
        target=_capture,
        args=(process.stdout, max_bytes, stdout_box),
        name="invariant-mcp-stdout",
    )
    stderr_thread = threading.Thread(
        target=_capture,
        args=(process.stderr, max_bytes, stderr_box),
        name="invariant-mcp-stderr",
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = process.returncode if process.returncode is not None else -1

    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()
    stdout, stdout_truncated = stdout_box.get("value", ("", False))
    stderr, stderr_truncated = stderr_box.get("value", ("", False))
    return ProcessOutput(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        timed_out=timed_out,
    )


def _validate_argv(argv: list[str]) -> None:
    if not argv:
        raise InvariantError("bad_argv", "command argv must not be empty")
    for part in argv:
        if not isinstance(part, str) or part == "" or "\x00" in part:
            raise InvariantError("bad_argv", "command argv must be non-empty strings")


def _capture(pipe: IO[str] | None, limit: int, box: dict[str, tuple[str, bool]]) -> None:
    if pipe is None:
        box["value"] = ("", False)
        return
    chunks: list[str] = []
    total = 0
    truncated = False
    try:
        while True:
            chunk = pipe.read(8192)
            if chunk == "":
                break
            if total < limit:
                remaining = limit - total
                chunks.append(chunk[:remaining])
                truncated = truncated or len(chunk) > remaining
            else:
                truncated = True
            total += len(chunk)
    except OSError:
        truncated = True
    box["value"] = ("".join(chunks), truncated)
