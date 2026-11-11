"""DX-7 step 1: OunceServer.wrap puts an existing FastMCP server under OunceMCP."""

from __future__ import annotations

import pytest
from conftest import EXAMPLE
from fastmcp import Client, FastMCP

from ouncemcp import Budget, Effect, OunceServer
from ouncemcp.cli.loader import load_server
from ouncemcp.trace import MemorySink


def plain_inventory() -> FastMCP:
    """A fresh copy each time: wrap() adds middleware to the server it is given."""
    return load_server(f"{EXAMPLE / 'plain_server.py'}:mcp")  # type: ignore[return-value]


def notes_server() -> FastMCP:
    mcp = FastMCP("notes")
    notes: dict[str, str] = {}

    @mcp.tool()
    def add_note(title: str, body: str) -> dict:
        """Add a note."""
        notes[title] = body
        return {"title": title}

    @mcp.tool()
    def get_note(title: str) -> dict:
        """Get a note."""
        if title not in notes:
            raise ValueError(f"no note {title} in /srv/notes/db.sqlite")
        return {"title": title, "body": notes[title]}

    return mcp


@pytest.mark.anyio
async def test_wrapped_tools_are_unchanged_and_measured():
    server = OunceServer.wrap(plain_inventory())
    async with Client(server.fastmcp) as client:
        names = sorted(t.name for t in await client.list_tools())
    assert names == ["get_resource", "search_resources"]
    report = await server.cost_report()
    assert {t.name for t in report.tools} == {"get_resource", "search_resources"}
    assert len(await server.catalog()) == 2


@pytest.mark.anyio
async def test_wrapped_errors_are_categorized_and_redacted():
    server = OunceServer.wrap(notes_server(), effects={"add_note": Effect.WRITE})
    async with Client(server.fastmcp) as client:
        result = await client.call_tool("get_note", {"title": "x"}, raise_on_error=False)
    assert result.is_error
    assert "/srv/notes" not in result.content[0].text
    assert result.meta["ouncemcp"]["category"] == "internal"
    assert server._effect_of("add_note") is Effect.WRITE


@pytest.mark.anyio
async def test_wrapped_servers_get_traces_and_discovery():
    sink = MemorySink()
    auto = OunceServer.wrap(plain_inventory(), budget=Budget(startup_tokens=50))
    assert (await auto.discovery()).mode == "direct"  # search (279 tokens) would cost more
    server = OunceServer.wrap(plain_inventory(), trace=sink, discovery="search")
    async with Client(server.fastmcp) as client:
        listed = [t.name for t in await client.list_tools()]
        await client.call_tool(
            "call_tool", {"name": "get_resource", "arguments": {"id": "vm-00000"}}
        )
    assert listed[:3] == ["search_tools", "describe_tool", "call_tool"]
    assert [t.tool for t in sink.traces] == ["get_resource", "call_tool"]
    assert sink.traces[0].result_tokens > 0


def test_wrap_rejects_unknown_effect_names():
    with pytest.raises(ValueError, match="does not have"):
        OunceServer.wrap(notes_server(), effects={"nope": Effect.READ})
    with pytest.raises(TypeError):
        OunceServer.wrap(object())  # type: ignore[arg-type]
