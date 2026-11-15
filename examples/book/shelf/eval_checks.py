"""State checks for the Shelf task suite: judged on the library itself."""

from __future__ import annotations

from typing import Any


def borrowed_once(server: Any, run: Any) -> bool:
    return server.deps.writes == ["borrow b-002 by ana"]


def nothing_borrowed(server: Any, run: Any) -> bool:
    return server.deps.writes == []
