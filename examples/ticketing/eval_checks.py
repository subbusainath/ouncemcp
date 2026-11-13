"""State checks for the ticketing eval suite: judged on the ticket store itself."""

from __future__ import annotations

from typing import Any


def t1_closed_once(server: Any, run: Any) -> bool:
    api = server.deps
    return api.tickets["T-1"].status == "closed" and api.writes == ["close T-1"]


def nothing_written(server: Any, run: Any) -> bool:
    return server.deps.writes == [] and server.deps.tickets["T-1"].status == "open"


def one_ticket_created(server: Any, run: Any) -> bool:
    api = server.deps
    created = [w for w in api.writes if w.startswith("create ")]
    return len(created) == 1 and api.tickets["T-5"].title == "Monitor flickers"
