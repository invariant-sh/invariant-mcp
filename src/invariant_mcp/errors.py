"""Errors the MCP layer shows to a client."""

from __future__ import annotations


class InvariantError(Exception):
    """Anticipated failure with a stable code prefix.

    Tool handlers turn this into an MCP tool error. The code is the part a
    caller can branch on (`path_escape`, `maul_unavailable`, `invalid_suite`).
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
