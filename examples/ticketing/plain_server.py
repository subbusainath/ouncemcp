"""The same ticketing workflow as a plain FastMCP server, for comparison.

Descriptions match the OunceMCP version. What is missing is what the framework adds:
no tenant isolation without hand-written checks, no approval before closing or
deleting, no protection against a retried write running twice, and no result budget.
"""

from __future__ import annotations

from fastmcp import FastMCP
from upstream import TicketAPI

api = TicketAPI().seed()
mcp = FastMCP("ticketing-plain")


@mcp.tool(annotations={"readOnlyHint": True})
async def search_tickets(status: str | None = None) -> list[dict]:
    """Find your team's tickets, optionally by status. Use create_ticket only if none match."""
    return [vars(t) for t in await api.search("acme", status)]


@mcp.tool()
async def create_ticket(title: str, body: str = "") -> dict:
    """Open a ticket. Side effect: notifies the support team."""
    return vars(await api.create("acme", title, body))


@mcp.tool()
async def close_ticket(ticket_id: str) -> dict:
    """Close a ticket. Side effect: notifies the requester."""
    ticket = await api.get("acme", ticket_id)
    if ticket is None:
        raise ValueError(f"No ticket {ticket_id}")
    return vars(await api.close(ticket))


@mcp.tool(annotations={"destructiveHint": True})
async def delete_ticket(ticket_id: str) -> dict:
    """Delete a ticket permanently."""
    ticket = await api.get("acme", ticket_id)
    if ticket is None:
        raise ValueError(f"No ticket {ticket_id}")
    await api.delete(ticket)
    return {"id": ticket_id, "deleted": True}


if __name__ == "__main__":
    mcp.run()
