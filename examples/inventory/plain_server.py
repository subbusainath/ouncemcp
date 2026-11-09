"""The same inventory as a plain FastMCP server, for comparison.

This mirrors a common pattern: return the upstream records as they are. It is
not a strawman; descriptions match the OunceMCP version so only the design differs.
"""

from __future__ import annotations

from data import BY_ID, RESOURCES
from fastmcp import FastMCP

mcp = FastMCP("inventory-plain")


@mcp.tool(annotations={"readOnlyHint": True})
def search_resources(region: str, kind: str | None = None, status: str | None = None) -> list[dict]:
    """Find cloud resources in a region, optionally filtered by kind and status.
    Use get_resource for full details of one resource."""
    return [
        r
        for r in RESOURCES
        if r["region"] == region
        and (kind is None or r["kind"] == kind)
        and (status is None or r["status"] == status)
    ]


@mcp.tool(annotations={"readOnlyHint": True})
def get_resource(id: str) -> dict:
    """Get one resource with its config and metrics."""
    row = BY_ID.get(id)
    if row is None:
        raise ValueError(f"No resource with id '{id}'")
    return row


if __name__ == "__main__":
    mcp.run()
