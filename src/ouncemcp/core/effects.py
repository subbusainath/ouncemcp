"""Declared side effects of a capability (spec CAP-3).

Effects are mapped to MCP tool annotations so hosts can display them, but the
annotations are hints only and are never used for access decisions (spec SEC-3).
"""

from __future__ import annotations

from enum import Enum


class Effect(str, Enum):
    """What a tool does to the outside world. Every tool must declare one."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    def annotations(self) -> dict[str, bool]:
        """Return the MCP tool annotations that describe this effect."""
        if self is Effect.READ:
            return {"readOnlyHint": True, "destructiveHint": False}
        if self is Effect.WRITE:
            return {"readOnlyHint": False, "destructiveHint": False}
        return {"readOnlyHint": False, "destructiveHint": True}
