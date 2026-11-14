"""Declared side effects of a capability (spec CAP-3).

Effects are mapped to MCP tool annotations so hosts can display them, but the
annotations are hints only and are never used for access decisions (spec SEC-3).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

PARALLEL_SAFE_META = "ouncemcp/parallelSafe"
"""``_meta`` key that marks a tool as safe to call in parallel with other calls (EXE-5).

MCP has no standard annotation for this, so OunceMCP uses a namespaced ``_meta`` key,
which the MCP specification reserves for implementation metadata. Hosts that do not know
the key ignore it, and it costs no model tokens (``_meta`` is not counted, BUD-7).

``true`` means the tool only reads, does not depend on the order of other calls, and
can run concurrently with them. Read tools are marked ``true`` unless registered with
``parallel_safe=False``; write and destructive tools are always ``false``.
"""


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


def is_parallel_safe(tool: Any) -> bool:
    """Whether a tool definition (an MCP ``Tool`` or its JSON) is marked parallel-safe."""
    meta = tool.get("_meta") if isinstance(tool, Mapping) else getattr(tool, "meta", None)
    return isinstance(meta, Mapping) and meta.get(PARALLEL_SAFE_META) is True
