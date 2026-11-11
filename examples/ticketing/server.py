"""Protected ticketing workflow built with OunceMCP (pilot 2, milestone M2).

Shows safe writes: every write is idempotent, closing asks the user first with a
preview of the exact ticket, deleting also needs the tickets:admin scope, and each
caller only ever sees their own tenant's tickets.

Run over stdio:      python examples/ticketing/server.py
Check token budget:  ouncemcp check examples/ticketing/server.py
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from upstream import TicketAPI

from ouncemcp import (
    Cursor,
    Detail,
    Effect,
    NotFound,
    OunceServer,
    Paginated,
    PermissionDenied,
    ToolContext,
)
from ouncemcp.security import require_scopes

Status = Literal["open", "closed"]


class TicketOut(BaseModel):
    id: str
    title: str
    status: str
    assignee: str | None = None
    body: Annotated[str | None, Detail] = None


class SearchInput(BaseModel):
    status: Status | None = None
    limit: int = Field(20, ge=1, le=50)
    cursor: str | None = Field(None, description="next_cursor from the previous call")


class SearchResult(BaseModel):
    items: Annotated[list[TicketOut], Paginated]
    next_cursor: Cursor = None


LOCAL_TENANT = "acme"


def tenant(ctx: ToolContext) -> str:
    """The caller's tenant from the verified token. A local run without a token (stdio)
    acts for the demo tenant; an authenticated caller without a tenant is refused."""
    identity = ctx.identity
    if identity.tenant:
        return identity.tenant
    if not identity.authenticated:
        return LOCAL_TENANT
    raise PermissionDenied("Your token has no tenant.", hint="Sign in with a team account.")


def out(ticket: object) -> TicketOut:
    return TicketOut.model_validate(ticket, from_attributes=True)


class CreateInput(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    body: str = Field("", max_length=4000)


async def _ticket(ctx: ToolContext, ticket_id: str) -> object:
    ticket = await ctx.deps.get(tenant(ctx), ticket_id)
    if ticket is None:
        raise NotFound(f"No ticket {ticket_id}.", hint="Find ids with tickets.search.")
    return ticket


async def close_preview(ticket_id: str, ctx: ToolContext) -> str:
    ticket = await _ticket(ctx, ticket_id)
    return f"Close ticket {ticket_id}: '{ticket.title}'? The requester is notified."  # type: ignore[attr-defined]


async def delete_preview(ticket_id: str, ctx: ToolContext) -> str:
    ticket = await _ticket(ctx, ticket_id)
    return f"Permanently delete ticket {ticket_id}: '{ticket.title}'? This cannot be undone."  # type: ignore[attr-defined]


class Deleted(BaseModel):
    id: str
    deleted: bool = True


def build(api: TicketAPI | None = None, *, write_deadline_ms: int = 10_000) -> OunceServer:
    """The pilot server. Tests pass their own API and a short write deadline."""
    server = OunceServer(
        "ticketing",
        instructions="Support tickets for the caller's team. Search before creating one.",
        deps=api or TicketAPI().seed(),
        stdio_policy="enforce",
    )

    @server.tool(name="tickets.search", effect=Effect.READ)
    async def search(args: SearchInput, ctx: ToolContext) -> SearchResult:
        """Find your team's tickets, optionally by status. Use tickets.create only if none match."""
        rows = [out(t) for t in await ctx.deps.search(tenant(ctx), args.status)]
        page = ctx.page(rows, cursor=args.cursor, limit=args.limit)
        return SearchResult(items=page.items, next_cursor=page.next_cursor)

    @server.tool(
        name="tickets.create",
        effect=Effect.WRITE,
        idempotency=True,
        deadline_ms=write_deadline_ms,
    )
    async def create(args: CreateInput, ctx: ToolContext) -> TicketOut:
        """Open a ticket. Use tickets.search first. Side effect: notifies the support team."""
        return out(await ctx.deps.create(tenant(ctx), args.title, args.body))

    @server.tool(
        name="tickets.close",
        effect=Effect.WRITE,
        approval="required",
        preview=close_preview,
        idempotency=True,
        deadline_ms=write_deadline_ms,
    )
    async def close(ticket_id: str, ctx: ToolContext) -> TicketOut:
        """Close a ticket after the user confirms. Side effect: notifies the requester."""
        return out(await ctx.deps.close(await _ticket(ctx, ticket_id)))

    @server.tool(
        name="tickets.delete",
        effect=Effect.DESTRUCTIVE,
        approval="required",
        preview=delete_preview,
        idempotency=True,
        deadline_ms=write_deadline_ms,
        policy=require_scopes("tickets:admin"),
    )
    async def delete(ticket_id: str, ctx: ToolContext) -> Deleted:
        """Delete a ticket permanently after the user confirms. Admins only."""
        await ctx.deps.delete(await _ticket(ctx, ticket_id))
        return Deleted(id=ticket_id)

    return server


server = build()

if __name__ == "__main__":
    server.run()
