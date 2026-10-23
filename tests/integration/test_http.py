"""Streamable HTTP smoke test. Same server object, second transport."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from mcp import Client


def test_streamable_http_lists_tools(tmp_path: Path) -> None:
    port = _free_port()
    env = {key: value for key, value in os.environ.items() if isinstance(value, str)}
    env["INVARIANT_WORKSPACE"] = str(tmp_path)
    env["INVARIANT_LOG_LEVEL"] = "WARNING"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "invariant_mcp",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace",
            str(tmp_path),
            "--log-level",
            "WARNING",
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    logs: list[str] = []
    threading.Thread(target=_drain, args=(process, logs), daemon=True).start()
    try:
        _wait_until_listening(port, process, logs)
        asyncio.run(_list_tools(port))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def _list_tools(port: int) -> None:
    async with Client(f"http://127.0.0.1:{port}/mcp") as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert "list_scenarios" in names
        catalog = await client.read_resource("invariant://maul/scenarios")
        assert "force_500" in catalog.contents[0].text
        scenarios = await client.call_tool("list_scenarios", {})
        assert scenarios.is_error is False
        assert scenarios.structured_content is not None
        assert len(scenarios.structured_content["scenarios"]) == 3


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _drain(process: subprocess.Popen[str], logs: list[str]) -> None:
    if process.stderr is None:
        return
    logs.append(process.stderr.read())


def _wait_until_listening(port: int, process: subprocess.Popen[str], logs: list[str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            time.sleep(0.2)
            msg = f"HTTP server exited {process.returncode}: {''.join(logs)}"
            raise AssertionError(msg)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    msg = f"HTTP server did not listen on {port}. stderr={''.join(logs)}"
    raise AssertionError(msg)
