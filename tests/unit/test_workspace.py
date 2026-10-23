"""Workspace confinement."""

from __future__ import annotations

from pathlib import Path

import pytest

from invariant_mcp.errors import InvariantError
from invariant_mcp.safety.workspace import Workspace


def test_relative_file_stays_inside(tmp_path: Path) -> None:
    target = tmp_path / "holds.yaml"
    target.write_text("version: 1\n", encoding="utf-8")
    resolved = Workspace(tmp_path).confine("holds.yaml")
    assert resolved == target.resolve()


def test_absolute_path_inside_workspace_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")
    resolved = Workspace(tmp_path).confine(str(target))
    assert resolved == target.resolve()


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="path_escape"):
        Workspace(tmp_path).confine("../etc/passwd")


def test_absolute_path_outside_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="path_escape"):
        Workspace(tmp_path).confine("/etc/passwd")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside.joinpath("secret.txt").write_text("nope", encoding="utf-8")
    link = tmp_path / "leak"
    link.symlink_to(outside)
    with pytest.raises(InvariantError, match="path_escape"):
        Workspace(tmp_path).confine("leak/secret.txt")


def test_null_byte_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="path_escape"):
        Workspace(tmp_path).confine("holds.yaml\x00")


def test_empty_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="empty_path"):
        Workspace(tmp_path).confine("   ")


def test_missing_workspace_directory(tmp_path: Path) -> None:
    with pytest.raises(InvariantError, match="workspace_missing"):
        Workspace(tmp_path / "missing")


def test_create_parent_makes_the_report_directory(tmp_path: Path) -> None:
    resolved = Workspace(tmp_path).confine("artifacts/holds_report.json", create_parent=True)
    assert resolved.parent.is_dir()
    assert resolved.parent.parent == tmp_path.resolve()
