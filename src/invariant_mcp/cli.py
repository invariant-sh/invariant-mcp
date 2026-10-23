"""stdio and streamable-HTTP entry points."""

from __future__ import annotations

import argparse
import logging
import sys

from invariant_mcp import __version__
from invariant_mcp.config import Settings
from invariant_mcp.errors import InvariantError
from invariant_mcp.server import create_server

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and serve until the client disconnects."""
    args = _parser().parse_args(argv)
    _configure_logging(args.log_level or "INFO")
    try:
        settings = Settings.from_env(workspace=args.workspace, log_level=args.log_level)
    except InvariantError as error:
        logging.getLogger("invariant_mcp").error("%s", error)
        raise SystemExit(2) from error
    _configure_logging(settings.log_level)
    if args.transport == "streamable-http" and args.host not in _LOOPBACK:
        logging.getLogger("invariant_mcp").warning(
            "streamable HTTP is bound to %s and this server does not authenticate clients",
            args.host,
        )
    server = create_server(settings)
    if args.transport == "stdio":
        server.run(transport="stdio")
        return
    server.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path=args.http_path,
        json_response=args.json_response,
        stateless_http=args.stateless,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invariant-mcp",
        description="MCP server for Holds evals, Vigil policy checks, and Maul failure probes.",
    )
    parser.add_argument("--version", action="version", version=f"invariant-mcp {__version__}")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio for local hosts (default); streamable-http for a local HTTP endpoint",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Root for every file path. Defaults to INVARIANT_WORKSPACE or the current directory.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http bind host")
    parser.add_argument("--port", type=int, default=8000, help="streamable-http bind port")
    parser.add_argument(
        "--http-path",
        default="/mcp",
        help="streamable-http endpoint path",
    )
    parser.add_argument(
        "--json-response",
        action="store_true",
        help="Answer streamable-http POSTs with one JSON body instead of an SSE stream",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="Use a fresh streamable-http session per request",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level on stderr. Defaults to INVARIANT_LOG_LEVEL or INFO.",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
