"""Follow-ups to proposal 0002: exact note reserve, too-large advice, fast list fitting."""

from typing import Annotated

import anyio
import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, Field

from ouncemcp import (
    Budget,
    Cursor,
    CursorSigner,
    Effect,
    OunceServer,
    Paginated,
    ToolContext,
    get_counter,
)
from ouncemcp.core.schema import compact_schema
from ouncemcp.core.shaping import PageSpec, ResultTooLarge, list_trim_floor, shape_result
from ouncemcp.core.tokens import compact_json

encode_offset = CursorSigner([b"k" * 32]).bind(tool="t", principal="p", query={}).encode


def _output_validator(model: type[BaseModel]) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(compact_schema(model.model_json_schema()))


class Row(BaseModel):
    id: str
    text: str


class Page(BaseModel):
    items: Annotated[list[Row], Paginated]
    next_cursor: Cursor = None


def rows(n: int, text_len: int) -> list[Row]:
    return [Row(id=f"r-{i}", text="lorem ipsum " * (text_len // 12)) for i in range(n)]


def shape_page(n: int, text_len: int, budget: int):
    value = Page(items=rows(n, text_len), next_cursor=encode_offset(n) if n else None)
    spec = PageSpec(
        list_key="items", cursor_key="next_cursor", start=0, count=n, encode=encode_offset
    )
    return shape_result(
        value,
        response_format="concise",
        budget=budget,
        counter=get_counter(),
        validator=_output_validator(Page),
        page=spec,
        assume_valid=True,
    )


@settings(max_examples=80, deadline=None)
@given(n=st.integers(2, 120), text_len=st.integers(0, 400), budget=st.integers(60, 2000))
def test_trimmed_page_keeps_as_many_items_as_fit(n, text_len, budget):
    counter = get_counter()
    try:
        out = shape_page(n, text_len, budget)
    except ResultTooLarge:
        return
    assert out.tokens <= budget
    kept = len(out.data["items"])
    if kept == n or not out.truncation.lists:
        return
    # One more item, with the note that would describe it, must not fit.
    bigger = {"items": [r.model_dump() for r in rows(n, text_len)[: kept + 1]]}
    if kept + 1 < n:
        bigger["next_cursor"] = encode_offset(kept + 1)
    note = out.text.rsplit("\n", 1)[1]
    assert counter.count(f"{compact_json(bigger)}\n{note}") > budget - 2


def test_small_budgets_get_a_short_note():
    counter = get_counter()
    out = shape_page(200, 40, 120)
    note = out.text.rsplit("\n", 1)[1]
    assert note.startswith("[ouncemcp]")
    assert "next_cursor" in note
    assert counter.count(note) <= 24


def test_large_budgets_keep_the_full_note():
    out = shape_page(400, 40, 3000)
    note = out.text.rsplit("\n", 1)[1]
    assert "Result truncated to fit 3000 tokens" in note
    assert "kept" in note and "of 400 items in 'items'" in note


class Big(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$")


class BigPage(BaseModel):
    items: Annotated[list[Big], Paginated]
    next_cursor: Cursor = None


class In(BaseModel):
    limit: int = Field(10, ge=1, le=100)
    cursor: str | None = None


def big_server(min_items: int) -> OunceServer:
    server = OunceServer("big", budget=Budget(result_tokens=120))
    items = [Big(id="x" * 2000 + f"-{i}") for i in range(20)]

    @server.tool(name="big.list", effect=Effect.READ, min_items=min_items)
    def list_big(args: In, ctx: ToolContext) -> BigPage:
        """List big items."""
        page = ctx.page(items, cursor=args.cursor, limit=args.limit)
        return BigPage(items=page.items, next_cursor=page.next_cursor)

    return server


def test_too_large_at_one_item_does_not_suggest_a_lower_limit():
    result = anyio.run(lambda: big_server(1).call_tool("big.list", {"limit": 10}))
    assert result.is_error
    text = result.content[0].text
    assert "lower limit" not in text
    assert "narrow" in text.lower()
    meta = result.meta["ouncemcp"]
    assert meta["error"] == "result_too_large"
    assert meta["reason"] == "item_over_budget"
    assert meta["items"] == 1
    assert "result_tokens" in meta["hint"]


def test_too_large_at_min_items_suggests_fewer_than_the_floor():
    result = anyio.run(lambda: big_server(3).call_tool("big.list", {"limit": 10}))
    assert result.is_error
    text = result.content[0].text
    assert "fewer than 3" in text
    assert result.content[0].text.count("[ouncemcp]") == 1
    meta = result.meta["ouncemcp"]
    assert meta["reason"] == "min_items_over_budget"
    assert meta["items"] == 3


class Fixed(BaseModel):
    code: str = Field(pattern=r"^[A-Z]+$")


def test_too_large_without_lists_says_narrow_the_request():
    server = OunceServer("fixed", budget=Budget(result_tokens=40))

    @server.tool(name="fixed.get", effect=Effect.READ)
    def get() -> Fixed:
        """Get."""
        return Fixed(code="A" * 3000)

    result = anyio.run(lambda: server.call_tool("fixed.get", {}))
    assert result.is_error
    text = result.content[0].text
    assert "limit" not in text
    meta = result.meta["ouncemcp"]
    assert meta["reason"] == "fixed_over_budget"
    assert "items" not in meta


def test_result_too_large_reports_actual_note_size():
    with pytest.raises(ResultTooLarge) as exc:
        shape_result(
            Fixed(code="A" * 3000),
            response_format="concise",
            budget=40,
            counter=get_counter(),
            validator=_output_validator(Fixed),
        )
    counter = get_counter()
    assert exc.value.min_tokens >= counter.count(compact_json({"code": "A" * 3000}))
    assert exc.value.items is None


ITEM = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}


def root(items_schema, cursor=None, required=("items",)):
    props = {"items": items_schema}
    if cursor is not None:
        props["next_cursor"] = cursor
    return {
        "$defs": {"Item": ITEM},
        "type": "object",
        "properties": props,
        "required": list(required),
    }


def test_trim_floor_plain_array_is_zero():
    schema = root({"type": "array", "items": {"$ref": "#/$defs/Item"}})
    assert list_trim_floor(schema, ("items",)) == 0


def test_trim_floor_reports_min_items():
    schema = root({"type": "array", "items": {"$ref": "#/$defs/Item"}, "minItems": 3})
    assert list_trim_floor(schema, ("items",)) == 3


def test_trim_floor_unknown_keywords_need_validation():
    for extra in ({"contains": ITEM}, {"minContains": 1}, {"unevaluatedItems": False}):
        schema = root({"type": "array", "items": ITEM, **extra})
        assert list_trim_floor(schema, ("items",)) is None
    assert (
        list_trim_floor(root({"anyOf": [{"type": "array"}, {"type": "null"}]}), ("items",)) is None
    )


def test_trim_floor_checks_the_cursor_schema():
    arr = {"type": "array", "items": ITEM}
    assert list_trim_floor(root(arr, {"type": "string"}), ("items",), "next_cursor") == 0
    patterned = root(arr, {"type": "string", "pattern": "^p"})
    assert list_trim_floor(patterned, ("items",), "next_cursor") is None
    assert list_trim_floor(root(arr), ("items",), "next_cursor") is None  # not declared


def test_trim_floor_nested_lists():
    inner = {"type": "array", "items": {"type": "string"}, "minItems": 2}
    item = {"type": "object", "properties": {"tags": inner}}
    schema = root({"type": "array", "items": item})
    assert list_trim_floor(schema, ("items", 0, "tags")) == 2
    item_if = {"type": "object", "properties": {"tags": inner}, "if": {}, "then": {}}
    assert list_trim_floor(root({"type": "array", "items": item_if}), ("items", 0, "tags")) is None
