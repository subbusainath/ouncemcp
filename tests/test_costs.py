import pytest

from ouncemcp import measure_tools
from ouncemcp.core.costs import definition_payload
from ouncemcp.core.tokens import compact_json


@pytest.mark.anyio
async def test_breakdown_sums_to_total_and_matches_recount():
    from server import server

    from ouncemcp import get_counter

    tools = await server.list_tools()
    report = measure_tools(tools, server.budget)
    counter = get_counter()
    for tool, cost in zip(tools, report.tools, strict=True):
        parts = (
            cost.name_tokens + cost.description + cost.input_schema + cost.output_schema
        ) + cost.structure
        assert parts == cost.total
        assert cost.total == counter.count(compact_json(definition_payload(tool)))
    assert report.total == sum(t.total for t in report.tools)
    assert not report.failed


def test_status_thresholds():
    from ouncemcp import Budget

    long_desc = "word " * 1200
    report = measure_tools(
        [{"name": "big", "description": long_desc, "inputSchema": {"type": "object"}}], Budget()
    )
    assert report.tools[0].status == "fail"
    assert report.failed
