"""Seam for resolving a write whose outcome is unknown (spec EXE-3; mechanism is EXE-4).

OunceMCP has no idempotency store yet. A ``WriteResolver`` is how one will plug in: when a
write times out, the resolver is asked whether the write took effect. If it can tell, the
call returns the real result. If it cannot, the caller gets ``ambiguous_write``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

RESOLVE_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class WriteAttempt:
    """What a resolver is told about the write that timed out."""

    tool: str
    principal: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    reason: str = "write_status_unknown"


class WriteResolver(Protocol):
    async def resolve(self, attempt: WriteAttempt) -> BaseModel | Mapping[str, Any] | None:
        """Return the write's result if it is known to have been applied, else ``None``."""
        ...
