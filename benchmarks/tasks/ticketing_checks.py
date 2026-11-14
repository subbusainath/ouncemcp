"""State checks for the ticketing benchmark, read from whichever server ran (both
expose the ticket store as ``server.deps``)."""

from __future__ import annotations

from typing import Any


def no_writes(server: Any, run: Any) -> bool:
    return server.deps.writes == []


def t1_closed_once(server: Any, run: Any) -> bool:
    api = server.deps
    return api.tickets["T-1"].status == "closed" and api.writes == ["close T-1"]


def created_once(server: Any, run: Any) -> bool:
    api = server.deps
    created = [w for w in api.writes if w.startswith("create ")]
    titles = [t.title.lower() for t in api.tickets.values()]
    return len(created) == 1 and api.writes == created and "monitor flickers" in titles


def t2_kept(server: Any, run: Any) -> bool:
    return "T-2" in server.deps.tickets and server.deps.writes == []


def t3_deleted(server: Any, run: Any) -> bool:
    return "T-3" not in server.deps.tickets and server.deps.writes == ["delete T-3"]
