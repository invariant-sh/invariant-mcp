"""CLI parsing. These tests must not start a server."""

from __future__ import annotations

import pytest

from invariant_mcp.cli import main


def test_help_exits_before_serving() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0


def test_version_exits_before_serving() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0


def test_missing_workspace_exits_2(tmp_path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit) as caught:
        main(["--workspace", str(missing)])
    assert caught.value.code == 2


def test_sse_transport_is_not_offered() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--transport", "sse"])
    assert caught.value.code == 2
