"""Read-only cloud inventory built with OunceMCP (pilot 1, milestone M0).

Run over stdio:      python examples/inventory/server.py
Check token budget:  ouncemcp check examples/inventory/server.py
"""

from __future__ import annotations

from typing import Annotated, Literal

from data import BY_ID, RESOURCES, metrics_for
from pydantic import BaseModel, Field

from ouncemcp import Cursor, Detail, Effect, NotFound, OunceServer, Paginated, ToolContext

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
    limit: int = Field(20, ge=1, le=100)
    cursor: str | None = Field(None, description="next_cursor from a previous call")


class ResourceSummary(BaseModel):
    id: str
    kind: str
    name: str
    status: str
    tags: Annotated[dict[str, str] | None, Detail] = None
    cost_per_month: Annotated[float | None, Detail] = None


class SearchResult(BaseModel):
    items: Annotated[list[ResourceSummary], Paginated]
    total: int
    next_cursor: Cursor = None


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


@server.tool(name="inventory.search", effect=Effect.READ, deadline_ms=5_000)
async def search(args: SearchInput, ctx: ToolContext) -> SearchResult:
    """Find cloud resources in a region, optionally filtered by kind and status.
    Use response_format=detailed for monthly cost and tags of every result, and
    inventory.get for config and metrics of one resource."""
    rows = [
        r
        for r in RESOURCES
        if r["region"] == args.region
        and (args.kind is None or r["kind"] == args.kind)
        and (args.status is None or r["status"] == args.status)
    ]
    # If the page is trimmed to fit the result budget, OunceMCP moves next_cursor
    # so the next call resumes right after the last item returned (spec RES-9).
    page = ctx.page(rows, cursor=args.cursor, limit=args.limit)
    return SearchResult(
        items=[ResourceSummary(**r) for r in page.items],
        total=len(rows),
        next_cursor=page.next_cursor,
    )


@server.tool(name="inventory.get", effect=Effect.READ, deadline_ms=5_000)
async def get(
    ctx: ToolContext, id: str = Field(description="Resource id from inventory.search")
) -> ResourceDetail:
    """Get one resource. Use response_format=detailed for config and metrics."""
    row = BY_ID.get(id)
    if row is None:
        raise NotFound(f"No resource with id '{id}'.", hint="Use inventory.search to find ids.")
    detail = ResourceDetail(**{k: v for k, v in row.items() if k != "metrics"})
    # Metrics are a Detail field, so concise results never return them: skip the slow
    # monitoring lookup unless the caller asked for them (spec RES-11).
    if ctx.includes("metrics"):
        detail.metrics = metrics_for(id)
    return detail


if __name__ == "__main__":
    server.run()
