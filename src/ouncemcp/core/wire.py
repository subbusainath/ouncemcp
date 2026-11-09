"""Read tool definitions the way an MCP client receives them."""

from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP


async def wire_tools(server: FastMCP) -> list[Any]:
    """List tools through an in-memory MCP session (same serialization as stdio/HTTP)."""
    async with Client(server) as client:
        return await client.list_tools()
