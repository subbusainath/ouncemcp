"""BUD-7 (declared counting scope) and BUD-9 (output schemas on, off or auto)."""

import json

import pytest
from conftest import EXAMPLE
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from ouncemcp import Budget, Effect, OunceServer, measure_tools
from ouncemcp.cli import main
from ouncemcp.core.costs import COUNTED_FIELDS, UNCOUNTED_FIELDS, counting_scope


class Small(BaseModel):
    ok: bool


class Big(BaseModel):
    alpha: str = Field(description="The first of many documented fields on this model")
    beta: str = Field(description="The second of many documented fields on this model")
    gamma: int = Field(description="A number that is documented at some length too")
    delta: list[str] = Field(description="A list of strings with a long description")
    epsilon: dict[str, str] = Field(description="A mapping with a long description as well")


def add_tools(server: OunceServer, **small_big) -> OunceServer:
    @server.tool(name="t.small", effect=Effect.READ, **small_big.get("small", {}))
    def small() -> Small:
        """Small tool."""
        return Small(ok=True)

    @server.tool(name="t.big", effect=Effect.READ, **small_big.get("big", {}))
    def big() -> Big:
        """Big tool."""
        return Big(alpha="a", beta="b", gamma=1, delta=["x"], epsilon={"k": "v"})

    return server


async def advertised(server: OunceServer) -> dict[str, bool]:
    return {t.name: t.output_schema is not None for t in await server.list_tools()}


# ---------------------------------------------------------------- BUD-9: modes


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("setting", "expected"),
    [(True, True), ("on", True), (False, False), ("off", False)],
)
async def test_server_level_on_and_off(setting, expected):
    server = add_tools(OunceServer("t", output_schemas=setting))
    assert await advertised(server) == {"t.small": expected, "t.big": expected}


@pytest.mark.anyio
async def test_default_is_on():
    assert await advertised(add_tools(OunceServer("t"))) == {"t.small": True, "t.big": True}


@pytest.mark.parametrize("bad", ["yes", "ON", "", 1, None])
def test_server_level_rejects_other_values(bad):
    with pytest.raises(ValueError, match="'on', 'off' or 'auto'"):
        OunceServer("t", output_schemas=bad)


@pytest.mark.parametrize("bad", ["yes", "", 0, True])
def test_tool_level_rejects_other_values(bad):
    server = OunceServer("t")
    with pytest.raises(ValueError, match="'on', 'off' or 'auto'"):

        @server.tool(name="t.x", effect=Effect.READ, output_schema=bad)
        def x() -> Small:
            """X."""
            return Small(ok=True)


@pytest.mark.anyio
async def test_a_tool_can_override_the_server():
    off_for_one = add_tools(OunceServer("t"), big={"output_schema": "off"})
    assert await advertised(off_for_one) == {"t.small": True, "t.big": False}
    on_for_one = add_tools(OunceServer("t", output_schemas="off"), small={"output_schema": "on"})
    assert await advertised(on_for_one) == {"t.small": True, "t.big": False}


@pytest.mark.anyio
async def test_auto_keeps_schemas_for_tools_that_stay_within_the_target():
    server = add_tools(OunceServer("t", output_schemas="auto"))
    assert await advertised(server) == {"t.small": True, "t.big": True}


@pytest.mark.anyio
async def test_auto_drops_the_schema_of_tools_that_would_go_over_the_target():
    on = await add_tools(OunceServer("t")).cost_report()
    sizes = {t.name: t.total for t in on.tools}
    assert sizes["t.small"] < sizes["t.big"]
    target = (sizes["t.small"] + sizes["t.big"]) // 2
    budget = Budget(tool_definition_target=target, tool_definition_warn=target)
    server = add_tools(OunceServer("t", output_schemas="auto", budget=budget))
    assert await advertised(server) == {"t.small": True, "t.big": False}


@pytest.mark.anyio
async def test_auto_can_be_set_for_one_tool():
    budget = Budget(tool_definition_target=40, tool_definition_warn=40)
    server = add_tools(OunceServer("t", budget=budget), big={"output_schema": "auto"})
    assert await advertised(server) == {"t.small": True, "t.big": False}


@pytest.mark.anyio
async def test_the_requested_mode_and_the_decision_are_recorded():
    budget = Budget(tool_definition_target=40, tool_definition_warn=40)
    server = add_tools(
        OunceServer("t", output_schemas="auto", budget=budget),
        small={"output_schema": "off"},
    )
    small, big = server.tools["t.small"], server.tools["t.big"]
    assert (small.output_schema, small.output_schema_advertised) == ("off", False)
    assert (big.output_schema, big.output_schema_advertised) == ("auto", False)
    roomy = add_tools(OunceServer("t", output_schemas="auto"))
    assert roomy.tools["t.big"].output_schema_advertised is True


@pytest.mark.anyio
async def test_a_tool_without_an_advertised_schema_still_returns_structured_results():
    server = add_tools(OunceServer("t", output_schemas="off"))
    result = await server.call_tool("t.big", {})
    assert not result.is_error
    assert result.structured_content["alpha"] == "a"
    tool = next(t for t in await server.list_tools() if t.name == "t.big")
    assert tool.output_schema is None


@pytest.mark.anyio
async def test_off_makes_definitions_smaller_by_the_schema_tokens():
    on = await add_tools(OunceServer("t", output_schemas="on")).cost_report()
    off = await add_tools(OunceServer("t", output_schemas="off")).cost_report()
    assert off.total < on.total
    assert off.output_schema_tokens == 0
    assert on.total - off.total == on.output_schema_tokens


# ---------------------------------------------------------------- BUD-9: the share


@pytest.mark.anyio
async def test_inventory_example_reports_its_output_schema_share():
    from server import server

    report = await server.cost_report()
    assert report.total == 488
    assert report.output_schema_tokens == 241
    assert report.output_schema_share == pytest.approx(241 / 488, abs=1e-4)


@pytest.mark.anyio
async def test_share_is_zero_when_schemas_are_off_and_for_an_empty_server():
    off = await add_tools(OunceServer("t", output_schemas="off")).cost_report()
    assert (off.output_schema_tokens, off.output_schema_share) == (0, 0.0)
    empty = measure_tools([], Budget())
    assert (empty.output_schema_tokens, empty.output_schema_share) == (0, 0.0)


def test_check_json_reports_the_share(capsys):
    assert main(["check", str(EXAMPLE / "server.py"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["startup_tokens"] == 488
    assert data["output_schema_tokens"] == 241
    assert data["output_schema_share"] == pytest.approx(0.4939, abs=1e-4)
    assert data["output_schema_share"] == round(241 / 488, 4)
    modes = {
        t["name"]: (t["output_schema_mode"], t["output_schema_advertised"]) for t in data["tools"]
    }
    assert set(modes.values()) == {("on", True)}


def test_check_text_reports_the_share(capsys):
    assert main(["check", str(EXAMPLE / "server.py")]) == 0
    out = capsys.readouterr().out
    assert "Output schemas   241 of 488 tokens (49%)" in out


def test_check_text_shows_zero_when_off(tmp_path, capsys):
    path = tmp_path / "srv.py"
    path.write_text(
        "from pydantic import BaseModel\n"
        "from ouncemcp import Effect, OunceServer\n"
        "server = OunceServer('t', output_schemas='off')\n"
        "class Out(BaseModel):\n    v: str\n"
        "@server.tool(name='t.get', effect=Effect.READ)\n"
        "def get(key: str) -> Out:\n    '''Get a value.'''\n    return Out(v=key)\n"
    )
    assert main(["check", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["output_schema_tokens"] == 0 and data["output_schema_share"] == 0.0
    assert data["tools"][0]["output_schema_mode"] == "off"
    assert data["tools"][0]["output_schema_advertised"] is False


def test_check_on_a_plain_fastmcp_server_reports_the_share_without_modes(capsys):
    assert main(["check", f"{EXAMPLE / 'plain_server.py'}:mcp", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["output_schema_tokens"] == sum(t["output_schema_cost"] for t in data["tools"])
    assert data["output_schema_tokens"] >= sum(t["output_schema"] for t in data["tools"])
    assert {t["output_schema_mode"] for t in data["tools"]} == {None}
    assert all(t["output_schema_advertised"] == (t["output_schema"] > 0) for t in data["tools"])


# ---------------------------------------------------------------- BUD-7: scope


def test_the_scope_names_counted_and_uncounted_fields():
    assert COUNTED_FIELDS == ("name", "description", "inputSchema", "outputSchema")
    assert UNCOUNTED_FIELDS == ("title", "annotations", "_meta")
    scope = counting_scope()
    assert scope["counted"] == list(COUNTED_FIELDS)
    assert scope["not_counted"] == list(UNCOUNTED_FIELDS)
    assert "tools/list" in scope["measured_on"]
    assert scope["verified_hosts"] == []


def test_check_json_includes_the_counting_scope(capsys):
    assert main(["check", str(EXAMPLE / "server.py"), "--json"]) == 0
    scope = json.loads(capsys.readouterr().out)["counting_scope"]
    assert scope == counting_scope()


def test_check_json_includes_the_scope_for_plain_fastmcp_servers(capsys):
    assert main(["check", f"{EXAMPLE / 'plain_server.py'}:mcp", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counting_scope"]["counted"] == [
        "name",
        "description",
        "inputSchema",
        "outputSchema",
    ]


def test_check_text_and_context_state_the_scope(capsys):
    assert main(["check", str(EXAMPLE / "server.py")]) == 0
    out = capsys.readouterr().out
    assert "Counted          name, description, inputSchema, outputSchema" in out
    assert "Not counted      title, annotations, _meta" in out
    assert main(["context", str(EXAMPLE / "server.py"), "inventory.get"]) == 0
    err = capsys.readouterr().err
    assert "Counted: name, description, inputSchema, outputSchema" in err
    assert "not counted: title, annotations, _meta" in err


async def definition_total(mcp: FastMCP) -> int:
    from ouncemcp.core.wire import wire_tools

    return measure_tools(await wire_tools(mcp), Budget()).total


@pytest.mark.anyio
async def test_the_declared_uncounted_fields_really_are_not_counted():
    plain, decorated = FastMCP("a"), FastMCP("b")

    @plain.tool(name="t.get", description="Get one thing.")
    def get_plain(key: str) -> str:
        return key

    @decorated.tool(
        name="t.get",
        description="Get one thing.",
        title="A long human readable title that hosts may show but models need not read",
        annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
        meta={"vendor": {"note": "x" * 200}},
    )
    def get_decorated(key: str) -> str:
        return key

    from ouncemcp.core.wire import wire_tools

    wire = (await wire_tools(decorated))[0]
    assert wire.title and wire.annotations is not None and wire.meta
    assert await definition_total(plain) == await definition_total(decorated)


@pytest.mark.anyio
async def test_the_declared_counted_fields_really_are_counted():
    base, longer = FastMCP("a"), FastMCP("b")

    @base.tool(name="t.get", description="Get one thing.")
    def get_base(key: str) -> str:
        return key

    @longer.tool(name="t.get", description="Get one thing. " * 20)
    def get_longer(key: str) -> str:
        return key

    assert await definition_total(longer) > await definition_total(base)
