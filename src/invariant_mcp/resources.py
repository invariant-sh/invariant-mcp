"""Workspace artifacts addressed as invariant:// resources."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from invariant_mcp.adapters.maul import MaulAdapter
from invariant_mcp.config import Settings
from invariant_mcp.errors import InvariantError
from invariant_mcp.safety.workspace import Workspace

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        ".uv",
    }
)
_MAX_DEPTH = 8
_MAX_LISTED = 200


@dataclass(frozen=True, slots=True)
class ListedArtifact:
    """One file the index resource can point a host at."""

    kind: str
    path: str
    uri: str
    size_bytes: int


def register_resources(mcp: MCPServer, settings: Settings) -> None:
    """Register URI templates plus the workspace index and scenario catalog."""
    workspace = settings.workspace
    maul = MaulAdapter(settings)

    @mcp.resource(
        "invariant://workspace/index",
        name="workspace_index",
        title="Workspace artifact index",
        description=(
            "JSON index of Holds reports, Holds baselines, Vigil policies, "
            "Vigil events, Vigil decisions, Vigil audit logs, and Maul reliability "
            "reports discovered under the workspace."
        ),
        mime_type="application/json",
    )
    def workspace_index() -> str:
        """List artifact files the other resources can read."""
        listed = discover(workspace)
        return json.dumps([asdict(entry) for entry in listed], indent=2) + "\n"

    @mcp.resource(
        "invariant://maul/scenarios",
        name="maul_scenarios",
        title="Maul scenario catalog",
        description=(
            "JSON catalog of Maul v0.1 fault scenarios. Same data as list_scenarios "
            "without a config file."
        ),
        mime_type="application/json",
    )
    def maul_scenarios() -> str:
        """Return the compiled Maul scenario catalog."""
        catalog = maul.list_scenarios(None)
        return catalog.model_dump_json(indent=2) + "\n"

    _template(
        mcp,
        uri="invariant://holds/report/{+path}",
        name="holds_report",
        title="Holds run report",
        description="A Holds run report (schema_version 1) inside the workspace.",
        mime_type="application/json",
        read=lambda path: _read(workspace, settings, path),
    )
    _template(
        mcp,
        uri="invariant://holds/baseline/{+path}",
        name="holds_baseline",
        title="Holds baseline",
        description="An accepted Holds baseline JSON file inside the workspace.",
        mime_type="application/json",
        read=lambda path: _read(workspace, settings, path),
    )
    _template(
        mcp,
        uri="invariant://vigil/policy/{+path}",
        name="vigil_policy",
        title="Vigil policy",
        description="A Vigil policy YAML file (vigil.yaml schema version 1).",
        mime_type="application/yaml",
        read=lambda path: _read(workspace, settings, path),
    )
    _template(
        mcp,
        uri="invariant://vigil/audit/{+path}",
        name="vigil_audit",
        title="Vigil audit log",
        description="Redacted Vigil audit JSONL. Events have no prompt bodies.",
        mime_type="application/x-ndjson",
        read=lambda path: _read(workspace, settings, path),
    )
    _template(
        mcp,
        uri="invariant://vigil/events/{+path}",
        name="vigil_events",
        title="Vigil request events",
        description="JSONL request events that check_policy can dry-run. No prompt bodies.",
        mime_type="application/x-ndjson",
        read=lambda path: _read(workspace, settings, path),
    )
    _template(
        mcp,
        uri="invariant://vigil/decisions/{+path}",
        name="vigil_decisions",
        title="Vigil dry-run decisions",
        description="Decision JSONL written by a Vigil dry-run.",
        mime_type="application/x-ndjson",
        read=lambda path: _read(workspace, settings, path),
    )
    _template(
        mcp,
        uri="invariant://maul/report/{+path}",
        name="maul_report",
        title="Maul reliability report",
        description="A Maul reliability_report.json (schema 0.2 when produced by current Maul).",
        mime_type="application/json",
        read=lambda path: _read(workspace, settings, path),
    )


def discover(workspace: Workspace) -> list[ListedArtifact]:
    """Walk the workspace for known artifact names. Symlink escapes are skipped."""
    found: list[ListedArtifact] = []
    for dirpath, dirnames, filenames in os.walk(workspace.root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        relative_dir = Path(dirpath).resolve().relative_to(workspace.root)
        if len(relative_dir.parts) > _MAX_DEPTH:
            dirnames.clear()
            continue
        for filename in sorted(filenames):
            kind = classify(filename)
            if kind is None:
                continue
            relative = (relative_dir / filename).as_posix()
            try:
                path = workspace.confine(relative)
            except InvariantError:
                continue
            if not path.is_file():
                continue
            found.append(
                ListedArtifact(
                    kind=kind,
                    path=relative,
                    uri=f"invariant://{kind}/{relative}",
                    size_bytes=path.stat().st_size,
                )
            )
            if len(found) >= _MAX_LISTED:
                return found
    return found


def classify(filename: str) -> str | None:
    """Map a file name to a resource URI kind."""
    if filename in {"holds_report.json"} or filename.endswith(".holds_report.json"):
        return "holds/report"
    if filename in {"baseline.json"} or filename.endswith(".baseline.json"):
        return "holds/baseline"
    if filename in {"vigil.yaml", "policy.yaml"} or filename.endswith(".vigil.yaml"):
        return "vigil/policy"
    if filename == "audit.jsonl" or filename.endswith(".audit.jsonl"):
        return "vigil/audit"
    if filename == "events.jsonl" or filename.endswith(".events.jsonl"):
        return "vigil/events"
    if filename == "decisions.jsonl" or filename.endswith(".decisions.jsonl"):
        return "vigil/decisions"
    if filename == "reliability_report.json" or filename.endswith(".reliability_report.json"):
        return "maul/report"
    return None


def _template(
    mcp: MCPServer,
    *,
    uri: str,
    name: str,
    title: str,
    description: str,
    mime_type: str,
    read: Callable[[str], str],
) -> None:
    def resource(path: str) -> str:
        return read(path)

    resource.__name__ = name
    resource.__doc__ = description
    mcp.resource(uri, name=name, title=title, description=description, mime_type=mime_type)(
        resource
    )


def _read(workspace: Workspace, settings: Settings, path: str) -> str:
    try:
        resolved = workspace.confine(path)
    except InvariantError as error:
        raise ResourceError(str(error)) from error
    if not resolved.is_file():
        raise ResourceNotFoundError(f"no file at {path!r} in the workspace")
    size = resolved.stat().st_size
    if size > settings.max_resource_bytes:
        raise ResourceError(
            f"resource is {size} bytes, over the {settings.max_resource_bytes} byte limit"
        )
    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ResourceError("resource is not UTF-8 text") from error
    except OSError as error:
        raise ResourceError(f"unable to read resource: {error}") from error
