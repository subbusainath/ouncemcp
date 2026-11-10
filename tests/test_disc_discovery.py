"""DISC-1 to DISC-7: automatic direct/search discovery (spec section 8.1.3)."""

import contextvars
import json
import sys
from typing import Annotated, Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthContext
from fastmcp.server.transforms.search import BM25SearchTransform
from pydantic import BaseModel, Field

from ouncemcp import (
    Budget,
    Cursor,
    CursorSigner,
    Effect,
    OunceServer,
    Paginated,
    ToolContext,
)
from ouncemcp.core.costs import definition_payload
from ouncemcp.core.tokens import compact_json, get_counter
from ouncemcp.discovery import (
    CALL_TOOL,
    DESCRIBE_TOOL,
    DETAIL_LEVELS,
    SEARCH_TOOL,
    OunceSearchTransform,
    RecallCase,
)

META = [SEARCH_TOOL, DESCRIBE_TOOL, CALL_TOOL]

TOPICS = [
    ("invoice", "Find customer invoices by status and billing period."),
    ("shipment", "Track parcel shipments by carrier and destination."),
    ("ticket", "Search support tickets by priority and assignee."),
    ("payment", "List card payments and refunds for an account."),
    ("employee", "Look up employees by department and manager."),
    ("warehouse", "Check warehouse stock levels by aisle and product."),
    ("campaign", "Report email campaign opens and clicks by segment."),
    ("deployment", "Show service deployments by environment and version."),
    ("contract", "Find signed contracts by counterparty and renewal date."),
    ("sensor", "Read sensor temperature and humidity by site."),
]


class Out(BaseModel):
    ok: bool = True
    name: str = ""


def add_tool(
    server: OunceServer,
    name: str,
    description: str,
    *,
    auth: Any = None,
    effect: Effect = Effect.READ,
) -> None:
    class Args(BaseModel):
        query: str = Field(description="What to look for.")
        limit: int = Field(10, ge=1, le=50, description="Maximum rows.")

    def handler(args: Args) -> Out:
        return Out(name=name)

    handler.__doc__ = description
    server.tool(name=name, effect=effect, description=description, auth=auth)(handler)


def big_server(count: int = 40, **kwargs: Any) -> OunceServer:
    server = OunceServer("big", **kwargs)
    for i in range(count):
        topic, text = TOPICS[i % len(TOPICS)]
        add_tool(server, f"{topic}_{i:02d}", f"{text} Variant {i} of the {topic} service.")
    return server


def small_server(**kwargs: Any) -> OunceServer:
    server = OunceServer("small", **kwargs)
    add_tool(server, "invoice_find", "Find customer invoices by status.")
    add_tool(server, "ticket_find", "Search support tickets by priority.")
    return server


async def names(server: OunceServer) -> list[str]:
    async with Client(server.fastmcp) as client:
        return [t.name for t in await client.list_tools()]


async def call(server: OunceServer, tool: str, args: dict[str, Any]) -> Any:
    async with Client(server.fastmcp) as client:
        return await client.call_tool(tool, args)


def payload(result: Any) -> Any:
    return json.loads(result.content[0].text)


# ------------------------------------------------------------------ DISC-1


@pytest.mark.anyio
async def test_small_catalog_stays_direct():
    server = small_server()
    decision = await server.discovery()
    assert decision.setting == "auto"
    assert decision.mode == "direct"
    assert await names(server) == ["invoice_find", "ticket_find"]


@pytest.mark.anyio
async def test_large_catalog_switches_to_search():
    server = big_server()
    decision = await server.discovery()
    assert decision.direct_tokens > server.budget.startup_tokens
    assert decision.mode == "search"
    assert await names(server) == META


@pytest.mark.anyio
async def test_mode_switches_exactly_at_the_threshold():
    probe = big_server(12)
    total = (await probe.discovery()).direct_tokens
    at = big_server(12, budget=Budget(startup_tokens=total))
    below = big_server(12, budget=Budget(startup_tokens=total - 1))
    assert (await at.discovery()).mode == "direct"
    assert (await below.discovery()).mode == "search"
    assert len(await names(at)) == 12
    assert await names(below) == META


@pytest.mark.anyio
async def test_explicit_override_wins_in_both_directions():
    forced_direct = big_server(discovery="direct")
    assert (await forced_direct.discovery()).mode == "direct"
    assert (await forced_direct.discovery()).setting == "direct"
    assert len(await names(forced_direct)) == 40

    forced_search = small_server(discovery="search")
    assert (await forced_search.discovery()).mode == "search"
    assert await names(forced_search) == META


def test_invalid_discovery_setting_is_rejected():
    with pytest.raises(ValueError, match="discovery must be"):
        OunceServer("x", discovery="code")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_auto_stays_direct_when_search_would_not_be_smaller():
    server = OunceServer("tiny", budget=Budget(startup_tokens=1))
    add_tool(server, "one", "Do one thing.")
    decision = await server.discovery()
    assert decision.direct_tokens > server.budget.startup_tokens
    assert decision.search_tokens >= decision.direct_tokens
    assert decision.mode == "direct"
    assert "not smaller" in decision.reason


@pytest.mark.anyio
async def test_decision_explains_itself_and_serializes():
    decision = await big_server().discovery()
    data = decision.to_dict()
    assert data["mode"] == "search"
    assert data["setting"] == "auto"
    assert data["threshold"] == 4_000
    assert data["direct_tokens"] == decision.direct_tokens
    assert data["search_tokens"] == decision.search_tokens < decision.direct_tokens
    assert f"{decision.direct_tokens:,}" in decision.reason
    assert "threshold" in decision.reason
    json.dumps(data)


@pytest.mark.anyio
async def test_decision_is_recomputed_when_tools_are_added():
    server = big_server(5)
    assert (await server.discovery()).mode == "direct"
    for i in range(5, 45):
        add_tool(server, f"extra_{i}", f"Extra tool number {i} for reporting on widgets.")
    assert (await server.discovery()).mode == "search"
    assert await names(server) == META


@pytest.mark.anyio
async def test_search_surface_cost_is_measured_on_what_clients_receive():
    server = big_server()
    decision = await server.discovery()
    report = await server.cost_report()
    assert report.total == decision.search_tokens
    assert [t.name for t in report.tools] == META


@pytest.mark.anyio
async def test_catalog_measures_every_registered_definition():
    server = big_server()
    counter = get_counter()
    catalog = await server.catalog()
    assert len(catalog) == 40
    direct_tokens = sum(counter.count(compact_json(definition_payload(t))) for t in catalog)
    assert direct_tokens == (await server.discovery()).direct_tokens

    direct = small_server()
    wire = await direct.cost_report()
    assert wire.total == (await direct.discovery()).direct_tokens


@pytest.mark.anyio
async def test_pinned_tools_stay_visible_in_search_mode():
    server = big_server(pinned=["invoice_00"])
    assert await names(server) == [*META, "invoice_00"]
    decision = await server.discovery()
    assert decision.pinned == ("invoice_00",)
    unpinned = big_server()
    assert decision.search_tokens > (await unpinned.discovery()).search_tokens


@pytest.mark.anyio
async def test_pinned_tools_are_not_repeated_in_search_results():
    server = big_server(pinned=["invoice_00"])
    found = payload(await call(server, SEARCH_TOOL, {"query": "invoice", "detail": "name"}))
    assert "invoice_00" not in found["tools"]


@pytest.mark.anyio
async def test_unknown_pinned_tool_names_the_problem():
    server = big_server(pinned=["no_such_tool"])
    with pytest.raises(ValueError, match="no_such_tool"):
        await server.discovery()


@pytest.mark.parametrize("meta_name", META)
def test_user_tools_cannot_take_meta_tool_names(meta_name):
    server = OunceServer("x")
    with pytest.raises(ValueError, match=meta_name):
        add_tool(server, meta_name, "Collides with a discovery meta-tool.")


# ------------------------------------------------------------------ DISC-2 and DISC-3


PRINCIPAL: contextvars.ContextVar[str | None] = contextvars.ContextVar("principal", default=None)


def admin_only(ctx: AuthContext) -> bool:
    return PRINCIPAL.get() == "admin"


def protected_server(**kwargs: Any) -> OunceServer:
    server = OunceServer("guarded", discovery="search", **kwargs)
    add_tool(server, "invoice_find", "Find customer invoices by status.")
    add_tool(server, "payroll_export", "Export payroll runs for all employees.", auth=admin_only)
    add_tool(server, "salary_lookup", "Look up an employee salary.", auth=admin_only)
    return server


@pytest.fixture
def as_user():
    token = PRINCIPAL.set("user")
    yield
    PRINCIPAL.reset(token)


@pytest.fixture
def as_admin():
    token = PRINCIPAL.set("admin")
    yield
    PRINCIPAL.reset(token)


@pytest.mark.anyio
@pytest.mark.parametrize("detail", DETAIL_LEVELS)
async def test_unauthorized_tools_never_appear_in_search(as_user, detail):
    server = protected_server()
    for query in ("payroll export", "payroll_export", "salary employee", "everything"):
        result = await call(server, SEARCH_TOOL, {"query": query, "detail": detail})
        assert "payroll" not in result.content[0].text
        assert "salary" not in result.content[0].text


@pytest.mark.anyio
async def test_authorized_principal_finds_protected_tools(as_admin):
    server = protected_server()
    found = payload(await call(server, SEARCH_TOOL, {"query": "payroll export", "detail": "name"}))
    assert "payroll_export" in found["tools"]


@pytest.mark.anyio
async def test_unauthorized_describe_is_indistinguishable_from_unknown(as_user):
    server = protected_server()
    async with Client(server.fastmcp) as client:
        with pytest.raises(ToolError) as hidden:
            await client.call_tool(DESCRIBE_TOOL, {"name": "payroll_export"})
        with pytest.raises(ToolError) as missing:
            await client.call_tool(DESCRIBE_TOOL, {"name": "payroll_exportx"})
    assert str(hidden.value).replace("payroll_export", "X") == str(missing.value).replace(
        "payroll_exportx", "X"
    )
    assert "search_tools" in str(hidden.value)


@pytest.mark.anyio
async def test_guessed_name_call_by_unauthorized_principal_fails(as_user):
    server = protected_server()
    async with Client(server.fastmcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("payroll_export", {"query": "all"})
        with pytest.raises(ToolError):
            await client.call_tool(
                CALL_TOOL, {"name": "payroll_export", "arguments": {"query": "x"}}
            )
    with pytest.raises(Exception):  # noqa: B017 - any refusal is acceptable in-process
        await server.call_tool("payroll_export", {"query": "all"})


@pytest.mark.anyio
async def test_hidden_tools_are_callable_by_name_when_authorized(as_admin):
    server = protected_server()
    assert await names(server) == META
    direct = await call(server, "payroll_export", {"query": "all"})
    assert direct.structured_content["name"] == "payroll_export"
    proxied = await call(
        server, CALL_TOOL, {"name": "payroll_export", "arguments": {"query": "all"}}
    )
    assert proxied.structured_content["name"] == "payroll_export"


@pytest.mark.anyio
async def test_hidden_tools_without_policy_are_still_callable_by_name(as_user):
    server = protected_server()
    assert "invoice_find" not in await names(server)
    result = await call(server, "invoice_find", {"query": "open"})
    assert result.structured_content["name"] == "invoice_find"


@pytest.mark.anyio
async def test_call_tool_proxy_error_tells_the_agent_what_to_do(as_user):
    server = protected_server()
    async with Client(server.fastmcp) as client:
        with pytest.raises(ToolError, match="search_tools"):
            await client.call_tool(CALL_TOOL, {"name": "nope"})
        with pytest.raises(ToolError, match="search"):
            await client.call_tool(CALL_TOOL, {"name": SEARCH_TOOL})


# ------------------------------------------------------------------ DISC-4


@pytest.mark.anyio
async def test_search_detail_levels():
    server = big_server()
    query = "track parcel shipments by carrier"

    names_only = payload(await call(server, SEARCH_TOOL, {"query": query, "detail": "name"}))
    assert names_only["detail"] == "name"
    assert all(isinstance(n, str) for n in names_only["tools"])
    assert names_only["tools"][0].startswith("shipment_")

    summary = payload(await call(server, SEARCH_TOOL, {"query": query, "detail": "summary"}))
    assert summary["detail"] == "summary"
    first = summary["tools"][0]
    assert set(first) == {"name", "description", "params"}
    assert first["params"] == ["query", "limit?"]
    assert "inputSchema" not in first

    full = payload(await call(server, SEARCH_TOOL, {"query": query, "detail": "full"}))
    assert full["detail"] == "full"
    expected = {
        t.name: t.model_dump(mode="json", by_alias=True, exclude_none=True)
        for t in await server.catalog()
    }
    for entry in full["tools"]:
        assert entry == expected[entry["name"]]
        assert "inputSchema" in entry


@pytest.mark.anyio
async def test_each_detail_level_costs_more_than_the_last():
    server = big_server()
    counter = get_counter()
    costs = []
    for level in DETAIL_LEVELS:
        result = await call(server, SEARCH_TOOL, {"query": "shipment carrier", "detail": level})
        costs.append(counter.count(result.content[0].text))
    assert costs == sorted(costs)
    assert len(set(costs)) == 3


@pytest.mark.anyio
async def test_default_detail_is_summary_and_results_are_capped():
    server = big_server(search_results=3)
    found = payload(await call(server, SEARCH_TOOL, {"query": "service"}))
    assert found["detail"] == "summary"
    assert len(found["tools"]) == 3


@pytest.mark.anyio
async def test_unknown_detail_level_is_rejected():
    server = big_server()
    async with Client(server.fastmcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool(SEARCH_TOOL, {"query": "x", "detail": "everything"})


@pytest.mark.anyio
async def test_describe_tool_levels():
    server = big_server()
    full = payload(await call(server, DESCRIBE_TOOL, {"name": "invoice_00"}))
    assert full["name"] == "invoice_00"
    assert full["inputSchema"]["required"] == ["query"]
    summary = payload(
        await call(server, DESCRIBE_TOOL, {"name": "invoice_00", "detail": "summary"})
    )
    assert set(summary) == {"name", "description", "params"}
    only_name = payload(await call(server, DESCRIBE_TOOL, {"name": "invoice_00", "detail": "name"}))
    assert only_name == {"name": "invoice_00"}


@pytest.mark.anyio
async def test_empty_search_gives_a_hint():
    server = big_server()
    found = payload(await call(server, SEARCH_TOOL, {"query": "zzzzqqqq"}))
    assert found["tools"] == []
    assert "hint" in found


# ------------------------------------------------------------------ DISC-5


async def meta_definitions(server: OunceServer) -> str:
    async with Client(server.fastmcp) as client:
        tools = [t for t in await client.list_tools() if t.name in META]
    return json.dumps(
        [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools],
        sort_keys=False,
    )


@pytest.mark.anyio
async def test_meta_tool_definitions_are_byte_identical_across_catalogs():
    small = big_server(30, discovery="search")
    large = big_server(60, discovery="search")
    different = OunceServer("other", discovery="search")
    add_tool(different, "lonely", "The only tool on a very different server.")
    reference = await meta_definitions(small)
    assert reference == await meta_definitions(large)
    assert reference == await meta_definitions(different)
    assert reference == await meta_definitions(
        big_server(30, discovery="search", pinned=["invoice_00"])
    )


@pytest.mark.anyio
async def test_meta_tool_definitions_do_not_change_when_the_catalog_does():
    server = big_server(30, discovery="search")
    before = await meta_definitions(server)
    for i in range(100, 120):
        add_tool(server, f"late_{i}", f"Added later number {i} for gadgets.")
    assert await meta_definitions(server) == before


@pytest.mark.anyio
async def test_meta_tools_come_first_in_a_fixed_order():
    server = big_server(pinned=["ticket_02", "invoice_00"])
    assert await names(server) == [*META, "ticket_02", "invoice_00"]


@pytest.mark.anyio
async def test_meta_tools_are_cheap_and_have_no_output_schema():
    server = big_server()
    async with Client(server.fastmcp) as client:
        tools = await client.list_tools()
    assert all(t.output_schema is None for t in tools)
    assert (await server.discovery()).search_tokens < 400


# ------------------------------------------------------------------ DISC-7


def test_search_builds_on_the_fastmcp_bm25_transform():
    assert issubclass(OunceSearchTransform, BM25SearchTransform)
    server = big_server()
    assert any(isinstance(t, OunceSearchTransform) for t in server.fastmcp.transforms)


# ------------------------------------------------------------------ DISC-6 (recall hook)


@pytest.mark.anyio
async def test_recall_reports_per_case_and_overall():
    server = big_server()
    report = await server.measure_recall(
        [
            RecallCase("track parcel shipments by carrier", {"shipment_01"}),
            RecallCase("warehouse stock levels by aisle", {"warehouse_05"}),
            RecallCase("cryptocurrency mining", {"invoice_00"}),
        ],
        k=5,
    )
    assert report.k == 5
    assert report.cases[0].recall == 1.0
    assert report.cases[2].recall == 0.0
    assert report.cases[2].missed == ("invoice_00",)
    assert report.mean_recall == pytest.approx((1.0 + report.cases[1].recall + 0.0) / 3)
    assert report.missed_tools == ["invoice_00"]
    data = report.to_dict()
    assert data["k"] == 5 and len(data["cases"]) == 3
    json.dumps(data)
    assert "mean recall" in report.render_text()


@pytest.mark.anyio
async def test_recall_defaults_to_the_servers_result_cap_and_checks_names():
    server = big_server(search_results=2)
    report = await server.measure_recall([RecallCase("invoice billing", {"invoice_00"})])
    assert report.k == 2
    with pytest.raises(ValueError, match="nope"):
        await server.measure_recall([RecallCase("x", {"nope"})])
    with pytest.raises(ValueError, match="at least one"):
        await server.measure_recall([])
    with pytest.raises(ValueError, match="at least one"):
        await server.measure_recall([RecallCase("x", set())])


@pytest.mark.anyio
async def test_recall_works_even_when_the_server_runs_in_direct_mode():
    server = small_server()
    report = await server.measure_recall([RecallCase("support tickets priority", {"ticket_find"})])
    assert report.mean_recall == 1.0


# ------------------------------------------------------------------ check and stdio

SERVER_SOURCE = '''
from pydantic import BaseModel

from ouncemcp import Effect, OunceServer

server = OunceServer("many", {kwargs})


class Out(BaseModel):
    name: str = ""


for i in range({count}):
    def handler(q: str, i: int = i) -> Out:
        """Find customer invoices by status and billing period for the finance team."""
        return Out(name=f"invoice_{{i}}")

    server.tool(name=f"invoice_{{i:02d}}", effect=Effect.READ)(handler)

if __name__ == "__main__":
    server.run()
'''


def write_server(tmp_path, count: int = 60, kwargs: str = "") -> str:
    path = tmp_path / "many_server.py"
    path.write_text(SERVER_SOURCE.format(count=count, kwargs=kwargs))
    return str(path)


def test_check_json_reports_the_discovery_decision(tmp_path, capsys):
    from ouncemcp.cli import main

    assert main(["check", write_server(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["tool_count"] == 60
    assert data["discovery"]["mode"] == "search"
    assert data["discovery"]["direct_tokens"] == data["startup_tokens"]
    assert data["discovery"]["search_tokens"] < data["startup_tokens"]


def test_check_text_names_the_mode(tmp_path, capsys):
    from ouncemcp.cli import main

    assert main(["check", write_server(tmp_path, kwargs='discovery="direct"')]) == 0
    out = capsys.readouterr().out
    assert "Discovery        direct (set explicitly" in out


def test_check_still_applies_per_tool_budgets_in_search_mode(tmp_path, capsys):
    from ouncemcp.cli import main

    path = tmp_path / "heavy.py"
    path.write_text(
        "from pydantic import BaseModel\n"
        "from ouncemcp import Budget, Effect, OunceServer\n"
        "class Out(BaseModel):\n    ok: bool = True\n"
        "server = OunceServer('heavy', discovery='search', budget=Budget("
        "tool_definition_target=5, tool_definition_warn=10, tool_definition_fail=20))\n"
        "@server.tool(name='big', effect=Effect.READ)\n"
        "def big(q: str) -> Out:\n"
        '    """A description that is certainly longer than twenty tokens in total, "\n'
        '    "so this definition fails the per-tool budget even in search mode."""\n'
        "    return Out()\n"
    )
    assert main(["check", str(path)]) == 1
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.anyio
async def test_search_mode_over_a_real_stdio_transport(tmp_path):
    path = write_server(tmp_path, kwargs='pinned=["invoice_03"]')
    transport = PythonStdioTransport(path, python_cmd=sys.executable)
    async with Client(transport) as client:
        listed = [t.name for t in await client.list_tools()]
        assert listed == [*META, "invoice_03"]
        found = await client.call_tool(SEARCH_TOOL, {"query": "invoice billing", "detail": "name"})
        assert json.loads(found.content[0].text)["tools"]
        proxied = await client.call_tool(
            CALL_TOOL, {"name": "invoice_07", "arguments": {"q": "open"}}
        )
        assert proxied.structured_content["name"] == "invoice_7"


# ------------------------------------------------------------------ signed cursors in search mode


class Row(BaseModel):
    id: str


class RowPage(BaseModel):
    items: Annotated[list[Row], Paginated]
    next_cursor: Cursor = None


class RowQuery(BaseModel):
    region: str = "ap-south-1"
    limit: int = Field(2, ge=1, le=10)
    cursor: str | None = None


def paged_server(principal: Any = None) -> OunceServer:
    rows = [Row(id=f"r-{i}") for i in range(6)]
    server = big_server(40, cursors=CursorSigner([b"k" * 32]), principal=principal)

    @server.tool(name="rows_list", effect=Effect.READ, description="List stored rows by region.")
    def rows_list(args: RowQuery, ctx: ToolContext) -> RowPage:
        page = ctx.page(rows, cursor=args.cursor, limit=args.limit)
        return RowPage(items=page.items, next_cursor=page.next_cursor)

    return server


@pytest.mark.anyio
async def test_signed_cursors_resume_through_the_call_tool_meta_tool():
    server = paged_server()
    assert (await server.discovery()).mode == "search"
    async with Client(server.fastmcp) as client:
        first = await client.call_tool(CALL_TOOL, {"name": "rows_list", "arguments": {}})
        cursor = first.structured_content["next_cursor"]
        assert cursor.startswith("s1.")
        second = await client.call_tool(
            CALL_TOOL, {"name": "rows_list", "arguments": {"cursor": cursor}}
        )
    assert [r["id"] for r in second.structured_content["items"]] == ["r-2", "r-3"]


@pytest.mark.anyio
async def test_cursor_from_search_mode_is_bound_to_the_tool_and_its_filters():
    server = paged_server()
    async with Client(server.fastmcp) as client:
        first = await client.call_tool(CALL_TOOL, {"name": "rows_list", "arguments": {}})
        cursor = first.structured_content["next_cursor"]
        changed = await client.call_tool(
            CALL_TOOL,
            {"name": "rows_list", "arguments": {"cursor": cursor, "region": "eu-west-1"}},
            raise_on_error=False,
        )
    assert changed.is_error
    assert changed.meta["ouncemcp"]["reason"] == "query"


@pytest.mark.anyio
async def test_cursor_from_search_mode_is_bound_to_the_principal():
    who = {"name": "alice"}
    server = paged_server(principal=lambda ctx: who["name"])
    async with Client(server.fastmcp) as client:
        first = await client.call_tool(CALL_TOOL, {"name": "rows_list", "arguments": {}})
        cursor = first.structured_content["next_cursor"]
        who["name"] = "bob"
        other = await client.call_tool(
            CALL_TOOL,
            {"name": "rows_list", "arguments": {"cursor": cursor}},
            raise_on_error=False,
        )
    assert other.is_error
    assert other.meta["ouncemcp"]["reason"] == "principal"
