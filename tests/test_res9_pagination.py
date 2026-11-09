"""RES-9 and RES-10: budget-aware pagination and minimum items."""

from typing import Annotated

import anyio
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, Field

from ouncemcp import Budget, Cursor, Effect, OunceServer, Paginated, ToolContext


class Row(BaseModel):
    id: str
    text: str


class Page(BaseModel):
    items: Annotated[list[Row], Paginated]
    total: int
    next_cursor: Cursor = None


class In(BaseModel):
    limit: int = Field(20, ge=1, le=100)
    cursor: str | None = None


def build(rows: list[Row], budget: int, min_items: int = 1) -> OunceServer:
    server = OunceServer("p", budget=Budget(result_tokens=budget))

    @server.tool(name="rows.list", effect=Effect.READ, min_items=min_items)
    def list_rows(args: In, ctx: ToolContext) -> Page:
        """List rows."""
        window = ctx.page(rows, cursor=args.cursor, limit=args.limit)
        return Page(items=window.items, total=window.total, next_cursor=window.next_cursor)

    return server


async def collect(server: OunceServer, limit: int) -> tuple[list[str], int]:
    seen: list[str] = []
    cursor = None
    calls = 0
    while True:
        calls += 1
        args = {"limit": limit} | ({"cursor": cursor} if cursor is not None else {})
        result = await server.call_tool("rows.list", args)
        assert not result.is_error, result.content[0].text
        data = result.structured_content
        seen.extend(item["id"] for item in data["items"])
        cursor = data.get("next_cursor")
        if cursor is None:
            return seen, calls
        assert calls < 1000


@settings(max_examples=80, deadline=None)
@given(
    n=st.integers(0, 150),
    limit=st.integers(1, 100),
    budget=st.integers(150, 2500),
    text_len=st.integers(0, 200),
)
def test_every_item_exactly_once(n, limit, budget, text_len):
    rows = [Row(id=f"r-{i}", text="x" * ((i * 7) % (text_len + 1))) for i in range(n)]
    seen, _ = anyio.run(collect, build(rows, budget), limit)
    assert seen == [r.id for r in rows]


@pytest.mark.anyio
async def test_trimmed_page_rewrites_cursor_and_says_so():
    rows = [Row(id=f"r-{i}", text="lorem ipsum " * 10) for i in range(100)]
    server = build(rows, 300)
    result = await server.call_tool("rows.list", {"limit": 50})
    meta = result.meta["ouncemcp"]
    kept = len(result.structured_content["items"])
    assert 1 <= kept < 50
    assert meta["truncation"]["cursor_rewritten"] is True
    assert "next_cursor" in result.content[0].text
    assert "skips" not in result.content[0].text


@pytest.mark.anyio
async def test_invalid_cursor_is_actionable_error():
    server = build([Row(id="r-0", text="")], 500)
    result = await server.call_tool("rows.list", {"cursor": "garbage"})
    assert result.is_error
    assert "cursor" in result.content[0].text.lower()


@pytest.mark.anyio
async def test_min_items_respected():
    rows = [Row(id=f"r-{i}", text="lorem ipsum " * 30) for i in range(20)]
    server = build(rows, 400, min_items=3)
    result = await server.call_tool("rows.list", {"limit": 20})
    if not result.is_error:
        assert len(result.structured_content["items"]) >= 3


def test_min_items_must_be_positive():
    server = OunceServer("p")
    with pytest.raises(ValueError, match="min_items"):

        @server.tool(name="x", effect=Effect.READ, min_items=0)
        def x() -> Page:
            """X."""
            return Page(items=[], total=0)


def test_paginated_requires_cursor_field():
    class Bad(BaseModel):
        items: Annotated[list[Row], Paginated]

    server = OunceServer("p")
    with pytest.raises(TypeError, match="Cursor"):

        @server.tool(name="x", effect=Effect.READ)
        def x() -> Bad:
            """X."""
            return Bad(items=[])


@pytest.mark.anyio
async def test_inventory_example_pages_without_gaps_at_max_limit():
    from data import RESOURCES
    from server import server

    expected = [r["id"] for r in RESOURCES if r["region"] == "ap-south-1"]
    seen, cursor, calls = [], None, 0
    while True:
        calls += 1
        args = {"region": "ap-south-1", "limit": 100}
        if cursor is not None:
            args["cursor"] = cursor
        result = await server.call_tool("inventory.search", args)
        seen += [item["id"] for item in result.structured_content["items"]]
        cursor = result.structured_content.get("next_cursor")
        if cursor is None:
            break
    assert seen == expected
    assert calls > 1  # the 2,000-token budget forces trimming at limit=100
