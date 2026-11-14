"""EXE-5: read-only, independent tools are marked parallel-safe for clients."""

from __future__ import annotations

import pytest
from fastmcp import Client
from pydantic import BaseModel

from ouncemcp import Effect, OunceServer
from ouncemcp.core.effects import PARALLEL_SAFE_META, is_parallel_safe


class Out(BaseModel):
    ok: bool


def _server() -> OunceServer:
    server = OunceServer("s")

    @server.tool(name="a.read", effect=Effect.READ, description="Read a thing by id.")
    def read() -> Out:
        return Out(ok=True)

    @server.tool(
        name="a.next",
        effect=Effect.READ,
        description="Read the next item from a shared queue.",
        parallel_safe=False,
    )
    def next_item() -> Out:
        return Out(ok=True)

    @server.tool(
        name="a.write", effect=Effect.WRITE, description="Creates a thing. Side effect: stores it."
    )
    def write() -> Out:
        return Out(ok=True)

    return server


@pytest.mark.anyio
async def test_reads_are_marked_parallel_safe_on_the_wire():
    server = _server()
    async with Client(server.fastmcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert (tools["a.read"].meta or {}).get(PARALLEL_SAFE_META) is True
    assert (tools["a.next"].meta or {}).get(PARALLEL_SAFE_META) is False
    assert (tools["a.write"].meta or {}).get(PARALLEL_SAFE_META) is False
    assert [n for n, t in tools.items() if is_parallel_safe(t)] == ["a.read"]
    assert server.tools["a.read"].parallel_safe and not server.tools["a.write"].parallel_safe


def test_writes_cannot_be_marked_parallel_safe():
    server = OunceServer("s")
    with pytest.raises(ValueError, match="parallel_safe"):

        @server.tool(
            name="w", effect=Effect.WRITE, description="Creates a thing.", parallel_safe=True
        )
        def w() -> Out:
            return Out(ok=True)


@pytest.mark.anyio
async def test_the_mark_costs_no_counted_tokens():
    server = _server()
    report = await server.cost_report()
    data = report.to_dict()
    assert "_meta" in data["counting_scope"]["not_counted"]


def test_is_parallel_safe_reads_plain_dicts():
    assert is_parallel_safe({"_meta": {PARALLEL_SAFE_META: True}})
    assert not is_parallel_safe({"name": "x"})
    assert not is_parallel_safe({"_meta": {PARALLEL_SAFE_META: "yes"}})
