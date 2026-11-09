"""Read-only cloud inventory built with OunceMCP (pilot 1, milestone M0).

Run over stdio:      python examples/inventory/server.py
Check token budget:  ouncemcp check examples/inventory/server.py
"""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from data import BY_ID, RESOURCES
from pydantic import BaseModel, Field

from ouncemcp import Detail, Effect, OunceServer

server = OunceServer(
    "inventory",
    instructions="Read-only cloud inventory. Search first, then get one resource for details.",
)

Kind = Literal["vm", "bucket", "database", "function", "load_balancer"]
Status = Literal["running", "stopped", "degraded"]


class SearchInput(BaseModel):
    region: str = Field(description="Region, e.g. ap-south-1")
    kind: Kind | None = None
    status: Status | None = None
    limit: int = Field(20, ge=1, le=50)
    cursor: str | None = Field(None, description="next_cursor from a previous call")


class ResourceSummary(BaseModel):
    id: str
    kind: str
    name: str
    status: str
    tags: Annotated[dict[str, str] | None, Detail] = None
    cost_per_month: Annotated[float | None, Detail] = None


class SearchResult(BaseModel):
    items: list[ResourceSummary]
    total: int
    next_cursor: str | None = None


class ResourceDetail(BaseModel):
    id: str
    kind: str
    name: str
    region: str
    status: str
    owner: str
    created_at: str
    cost_per_month: float
    tags: dict[str, str]
    config: Annotated[dict | None, Detail] = None
    metrics: Annotated[dict | None, Detail] = None


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except ValueError as exc:
        raise ValueError("Invalid cursor; pass next_cursor from the previous result") from exc


@server.tool(name="inventory.search", effect=Effect.READ, deadline_ms=5_000)
async def search(args: SearchInput) -> SearchResult:
    """Find cloud resources in a region, optionally filtered by kind and status.
    Use inventory.get for full details of one resource."""
    rows = [
        r
        for r in RESOURCES
        if r["region"] == args.region
        and (args.kind is None or r["kind"] == args.kind)
        and (args.status is None or r["status"] == args.status)
    ]
    start = _decode(args.cursor)
    page = rows[start : start + args.limit]
    end = start + len(page)
    return SearchResult(
        items=[ResourceSummary(**r) for r in page],
        total=len(rows),
        next_cursor=_encode(end) if end < len(rows) else None,
    )


@server.tool(name="inventory.get", effect=Effect.READ, deadline_ms=5_000)
async def get(id: str = Field(description="Resource id from inventory.search")) -> ResourceDetail:
    """Get one resource. Use response_format=detailed for config and metrics."""
    row = BY_ID.get(id)
    if row is None:
        raise ValueError(f"No resource with id '{id}'. Use inventory.search to find ids.")
    return ResourceDetail(**row)


if __name__ == "__main__":
    server.run()
