"""Confine every filesystem path to one operator-chosen root."""

from __future__ import annotations

from pathlib import Path

from mcp.shared.path_security import PathEscapeError, safe_join

from invariant_mcp.errors import InvariantError


class Workspace:
    """A directory tree the server is allowed to read and write.

    The root is the security boundary. It is set by the operator
    (`--workspace` / `INVARIANT_WORKSPACE`), not by a client-advertised MCP
    root. `safe_join` rejects `..`, absolute injection, NUL bytes, and
    symlinks whose target leaves the root.
    """

    def __init__(self, root: Path) -> None:
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            msg = f"workspace does not exist or is not a directory: {root}"
            raise InvariantError("workspace_missing", msg)
        self.root = resolved

    def confine(self, raw: str, *, create_parent: bool = False) -> Path:
        """Resolve `raw` and require the real path to stay inside the root."""
        resolved = self._resolve(raw)
        if create_parent:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved = self._resolve(raw)
        return resolved

    def relative(self, path: Path) -> str:
        """Return a workspace-relative POSIX path."""
        return path.resolve().relative_to(self.root).as_posix()

    def _resolve(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise InvariantError("empty_path", "a non-empty workspace path is required")
        text = raw.strip()
        if "\x00" in text:
            raise InvariantError("path_escape", "path escapes the workspace")
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                relative = candidate.resolve().relative_to(self.root)
            except ValueError as exc:
                raise InvariantError(
                    "path_escape",
                    f"path escapes the workspace: {text!r}",
                ) from exc
            text = relative.as_posix()
        try:
            return safe_join(self.root, text)
        except PathEscapeError as exc:
            raise InvariantError(
                "path_escape",
                f"path escapes the workspace: {text!r}",
            ) from exc
