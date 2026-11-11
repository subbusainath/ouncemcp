"""Proposal 0010, decision 3: ``stdio_policy`` chooses whether policies apply on stdio."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.server.context import reset_transport, set_transport
from pydantic import BaseModel

from ouncemcp import Effect, OunceServer
from ouncemcp.core.lints import lint_server
from ouncemcp.discovery import CALL_TOOL, DESCRIBE_TOOL, SEARCH_TOOL
from ouncemcp.security import authenticated, require_scopes


class Out(BaseModel):
    ok: bool = True


@contextmanager
def stdio() -> Iterator[None]:
    token = set_transport("stdio")
    try:
        yield
    finally:
        reset_transport(token)


def build(**kwargs: Any) -> OunceServer:
    server = OunceServer("s", **kwargs)

    @server.tool(name="notes.read", effect=Effect.READ)
    async def read() -> Out:
        """Read notes."""
        return Out()

    @server.tool(name="notes.purge", effect=Effect.DESTRUCTIVE, policy=require_scopes("admin"))
    async def purge() -> Out:
        """Delete every note."""
        return Out()

    return server


async def session(server: OunceServer) -> tuple[list[str], Any]:
    async with Client(server.fastmcp) as client:
        names = sorted(t.name for t in await client.list_tools())
        result = await client.call_tool("notes.purge", {}, raise_on_error=False)
    return names, result


@pytest.mark.anyio
async def test_trust_matches_fastmcp_and_skips_policies_on_stdio():
    server = build()
    with stdio():
        names, result = await session(server)
    assert names == ["notes.purge", "notes.read"]
    assert not result.is_error


@pytest.mark.anyio
async def test_enforce_applies_policies_on_stdio_as_the_anonymous_caller():
    server = build(stdio_policy="enforce")
    with stdio():
        names, result = await session(server)
    assert names == ["notes.read"]
    assert result.is_error
    assert result.meta["ouncemcp"]["category"] == "not_found"


@pytest.mark.anyio
async def test_enforce_also_covers_search_describe_and_proxied_calls():
    server = build(stdio_policy="enforce", discovery="search")
    with stdio():
        async with Client(server.fastmcp) as client:
            found = await client.call_tool(SEARCH_TOOL, {"query": "delete notes purge"})
            described = await client.call_tool(
                DESCRIBE_TOOL, {"name": "notes.purge"}, raise_on_error=False
            )
            proxied = await client.call_tool(
                CALL_TOOL, {"name": "notes.purge", "arguments": {}}, raise_on_error=False
            )
    assert "notes.purge" not in found.content[0].text
    assert described.is_error and proxied.is_error


@pytest.mark.anyio
async def test_enforce_does_not_change_http_or_in_process_behavior():
    server = build(stdio_policy="enforce", policy=None)
    names, result = await session(server)
    assert names == ["notes.read"]
    assert result.is_error


@pytest.mark.anyio
async def test_a_server_policy_needing_a_verified_caller_hides_everything_on_stdio():
    server = OunceServer("s", stdio_policy="enforce", policy=authenticated)

    @server.tool(name="open.read", effect=Effect.READ, policy=lambda r: True)
    async def open_read() -> Out:
        """Read."""
        return Out()

    with stdio():
        async with Client(server.fastmcp) as client:
            assert [t.name for t in await client.list_tools()] == []


def test_check_warns_only_when_policies_exist_and_stdio_is_trusted():
    rules = [f.rule for f in lint_server(build())]
    assert "stdio-skips-policy" in rules
    assert "stdio-skips-policy" not in [f.rule for f in lint_server(build(stdio_policy="enforce"))]
    plain = OunceServer("p")
    assert lint_server(plain) == []


def test_bad_mode_is_rejected():
    with pytest.raises(ValueError, match="stdio_policy"):
        OunceServer("s", stdio_policy="maybe")  # type: ignore[arg-type]
