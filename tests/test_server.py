from typing import Annotated

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from ouncemcp import Budget, Detail, Effect, OunceServer, ToolContext


class Out(BaseModel):
    value: str


class RichOut(BaseModel):
    value: str
    debug: Annotated[str | None, Detail] = None


class In(BaseModel):
    q: str = Field(description="query")
    limit: int = Field(10, ge=1, le=20)


def make() -> OunceServer:
    return OunceServer("t")


def test_effect_is_required():
    s = make()
    with pytest.raises(TypeError, match="effect"):

        @s.tool(name="a")  # type: ignore[call-arg]
        def a() -> Out:
            """Doc."""
            return Out(value="x")


@pytest.mark.parametrize("bad", ["has space", "", "x" * 129, "slash/name"])
def test_invalid_names_rejected(bad):
    s = make()
    with pytest.raises(ValueError):
        s.tool(name=bad, effect=Effect.READ)(lambda: Out(value="x"))


def test_duplicate_and_missing_description_rejected():
    s = make()

    def f() -> Out:
        """Doc."""
        return Out(value="x")

    s.tool(name="f", effect=Effect.READ)(f)
    with pytest.raises(ValueError, match="already"):
        s.tool(name="f", effect=Effect.READ)(f)

    def g() -> Out:
        return Out(value="x")

    with pytest.raises(ValueError, match="description"):
        s.tool(name="g", effect=Effect.READ)(g)


def test_return_type_must_be_model():
    s = make()

    def f() -> dict:
        """Doc."""
        return {}

    with pytest.raises(TypeError):
        s.tool(name="f", effect=Effect.READ)(f)


def _takes_api_key(api_key: str) -> Out:
    """Doc."""
    return Out(value=api_key)


def _takes_tenant(tenant: str) -> Out:
    """Doc."""
    return Out(value=tenant)


@pytest.mark.parametrize(("fn", "param"), [(_takes_api_key, "api_key"), (_takes_tenant, "tenant")])
def test_reserved_params_rejected(fn, param):
    with pytest.raises(ValueError, match=param):
        make().tool(name="f", effect=Effect.READ)(fn)


@pytest.mark.anyio
async def test_effect_annotations_and_schema():
    s = make()

    @s.tool(name="t.read", effect=Effect.READ)
    async def read(args: In) -> Out:
        """Read."""
        return Out(value=args.q)

    @s.tool(name="t.drop", effect=Effect.DESTRUCTIVE)
    async def drop(args: In) -> RichOut:
        """Drop."""
        return RichOut(value=args.q)

    tools = {t.name: t for t in await s.list_tools()}
    assert tools["t.read"].annotations.read_only_hint is True
    assert tools["t.drop"].annotations.destructive_hint is True
    read_props = tools["t.read"].input_schema["properties"]
    assert set(read_props) == {"q", "limit"}  # flattened, no response_format, no ctx
    assert "response_format" in tools["t.drop"].input_schema["properties"]
    assert tools["t.read"].input_schema["required"] == ["q"]


@pytest.mark.anyio
async def test_call_validates_and_shapes():
    s = OunceServer("t", budget=Budget(result_tokens=500))
    seen = {}

    @s.tool(name="t.echo", effect=Effect.READ)
    def echo(args: In, ctx: ToolContext) -> RichOut:
        """Echo."""
        seen["ctx"] = ctx
        return RichOut(value=args.q * args.limit, debug="trace")

    async with Client(s.fastmcp) as c:
        r = await c.call_tool("t.echo", {"q": "ab", "limit": 2})
        assert r.structured_content == {"value": "abab"}
        assert r.meta["ouncemcp"]["format"] == "concise"
        assert seen["ctx"].result_budget == 500
        r = await c.call_tool("t.echo", {"q": "ab", "response_format": "detailed"})
        assert r.structured_content["debug"] == "trace"
        with pytest.raises(ToolError):
            await c.call_tool("t.echo", {"q": "ab", "limit": 99})


@pytest.mark.anyio
async def test_output_schemas_can_be_disabled():
    s = OunceServer("t", output_schemas=False)

    @s.tool(name="t.x", effect=Effect.READ)
    def x() -> Out:
        """X."""
        return Out(value="v")

    (tool,) = await s.list_tools()
    assert tool.output_schema is None
    async with Client(s.fastmcp) as c:
        r = await c.call_tool("t.x", {})
        assert r.content[0].text == '{"value":"v"}'
