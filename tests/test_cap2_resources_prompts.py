"""CAP-2: typed, budgeted, policy-checked resources and prompts (proposal 0014)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.server.context import reset_transport, set_transport
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel

from ouncemcp import Budget, OunceServer, ToolContext
from ouncemcp.security import require_scopes
from ouncemcp.testing import as_caller


class Note(BaseModel):
    id: str
    title: str
    tags: list[str] | None = None


def build(**kwargs: Any) -> OunceServer:
    server = OunceServer("notes", budget=Budget(result_tokens=200, result_ceiling=300), **kwargs)

    @server.resource("notes://{note_id}")
    async def note(note_id: str) -> Note:
        """One note as JSON."""
        return Note(id=note_id, title=f"Note {note_id}")

    @server.resource("notes://readme")
    def readme() -> str:
        """The notes readme."""
        return "word " * 2000

    @server.resource("notes://huge", max_tokens=50)
    def huge() -> dict:
        """Too big to send as JSON."""
        return {"rows": list(range(500))}

    @server.resource("notes://mine")
    async def mine(ctx: ToolContext) -> Note:
        """The caller's own note."""
        return Note(id=ctx.principal, title=ctx.identity.tenant or "none")

    @server.resource("notes://admin", policy=require_scopes("admin"))
    def admin() -> str:
        """Admin notes."""
        return "secret plans"

    @server.prompt()
    def summarize(topic: str, ctx: ToolContext) -> str:
        """Ask for a summary of notes on a topic."""
        return f"Summarize my notes about {topic} for {ctx.identity.tenant}."

    @server.prompt(policy=require_scopes("admin"))
    def audit() -> str:
        """Ask for an audit of every note."""
        return "Audit all notes."

    return server


async def read(server: OunceServer, uri: str) -> Any:
    async with Client(server.fastmcp) as client:
        return await client.read_resource(uri)


@pytest.mark.anyio
async def test_typed_resources_and_templates():
    server = build()
    (content,) = await read(server, "notes://n-7")
    assert content.text == '{"id":"n-7","title":"Note n-7"}'
    assert content.mime_type == "application/json"
    async with Client(server.fastmcp) as client:
        templates = [t.uri_template for t in await client.list_resource_templates()]
    assert "notes://{note_id}" in templates


@pytest.mark.anyio
async def test_long_text_is_cut_to_the_budget_with_a_note():
    server = build()
    (content,) = await read(server, "notes://readme")
    assert server.counter.count(content.text) <= 300
    total = server.counter.count("word " * 2000)
    assert "[ouncemcp] Truncated:" in content.text and f"of {total:,} tokens shown" in content.text


@pytest.mark.anyio
async def test_json_over_budget_is_refused_not_cut():
    server = build()
    async with Client(server.fastmcp) as client:
        with pytest.raises(Exception, match="over its 50-token limit"):
            await client.read_resource("notes://huge")


@pytest.mark.anyio
async def test_handlers_get_the_verified_caller():
    server = build()
    with as_caller(subject="ana", tenant="acme"):
        (content,) = await read(server, "notes://mine")
    assert content.text == '{"id":"ana","title":"acme"}'


@pytest.mark.anyio
async def test_policies_hide_resources_and_prompts():
    server = build()

    async def visible() -> tuple[set[str], set[str]]:
        async with Client(server.fastmcp) as client:
            resources = {str(r.uri) for r in await client.list_resources()}
            prompts = {p.name for p in await client.list_prompts()}
        return resources, prompts

    resources, prompts = await visible()
    assert "notes://admin" not in resources and "audit" not in prompts
    async with Client(server.fastmcp) as client:
        with pytest.raises(MCPError, match="Resource not found"):
            await client.read_resource("notes://admin")
    with as_caller(subject="root", scopes=["admin"]):
        resources, prompts = await visible()
    assert "notes://admin" in resources and "audit" in prompts


@pytest.mark.anyio
async def test_enforce_on_stdio_covers_resources_and_prompts():
    server = build(stdio_policy="enforce")
    token = set_transport("stdio")
    try:
        async with Client(server.fastmcp) as client:
            resources = {str(r.uri) for r in await client.list_resources()}
            prompts = {p.name for p in await client.list_prompts()}
    finally:
        reset_transport(token)
    assert "notes://admin" not in resources and "audit" not in prompts


@pytest.mark.anyio
async def test_prompts_take_typed_arguments_and_context():
    server = build()
    async with Client(server.fastmcp) as client:
        listed = {p.name: p for p in await client.list_prompts()}
        with as_caller(subject="ana", tenant="acme"):
            result = await client.get_prompt("summarize", {"topic": "billing"})
    assert [a.name for a in listed["summarize"].arguments] == ["topic"]
    assert result.messages[0].content.text == "Summarize my notes about billing for acme."


def test_descriptions_are_required():
    server = OunceServer("s")
    with pytest.raises(ValueError, match="description"):

        @server.resource("x://y")
        def nodoc() -> str:
            return ""
