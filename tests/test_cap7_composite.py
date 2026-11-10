"""CAP-7: a composite workflow tool that combines several service calls."""

from __future__ import annotations

import anyio
import pytest
from data import REGIONS, RESOURCES
from pydantic import BaseModel
from server import server as inventory
from workflow import server as workflows

from ouncemcp import Effect, OunceServer, ToolContext


@pytest.mark.anyio
async def test_cost_review_matches_a_manual_aggregation():
    result = await workflows.call_tool("inventory.cost_review", {"regions": REGIONS[:2], "top": 2})
    data = result.structured_content
    rows = [r for r in RESOURCES if r["region"] in REGIONS[:2]]
    assert data["total_cost_per_month"] == pytest.approx(
        round(sum(r["cost_per_month"] for r in rows), 2), abs=0.02
    )
    assert [r["region"] for r in data["regions"]] == REGIONS[:2]
    expected = sorted(rows, key=lambda r: r["cost_per_month"], reverse=True)[:2]
    assert [c["id"] for c in data["costliest"]] == [r["id"] for r in expected]
    degraded = sorted(
        r["id"] for r in rows if r["region"] == REGIONS[0] and r["status"] == "degraded"
    )
    assert data["regions"][0]["degraded"] == degraded
    assert "by_team" not in data["regions"][0]


@pytest.mark.anyio
async def test_one_composite_call_replaces_many_search_calls_and_costs_fewer_tokens():
    composite = await workflows.call_tool("inventory.cost_review", {})
    composite_tokens = composite.meta["ouncemcp"]["result_tokens"]
    calls, tokens = 0, 0
    for region in REGIONS:
        cursor = None
        while True:
            args = {"region": region, "limit": 100, **({"cursor": cursor} if cursor else {})}
            page = await inventory.call_tool("inventory.search", args)
            calls += 1
            tokens += page.meta["ouncemcp"]["result_tokens"]
            cursor = page.structured_content.get("next_cursor")
            if not cursor:
                break
    assert calls >= len(REGIONS)
    assert composite_tokens * 3 < tokens


@pytest.mark.anyio
async def test_a_failing_step_fails_the_whole_call_with_a_categorized_error():
    result = await workflows.call_tool(
        "inventory.cost_review", {"regions": ["us-east-1", "mars-1"]}
    )
    assert result.is_error
    assert result.meta["ouncemcp"]["category"] == "not_found"
    assert "mars-1" in result.content[0].text


class Out(BaseModel):
    order: list[str]
    peak: int


@pytest.mark.anyio
async def test_gather_runs_steps_concurrently_up_to_the_limit_and_keeps_order():
    server = OunceServer("g")

    @server.tool(name="g.run", effect=Effect.READ)
    async def run(ctx: ToolContext) -> Out:
        """Run steps."""
        active, peak = 0, 0

        def step(name: str, delay: float):
            async def go() -> str:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await anyio.sleep(delay)
                active -= 1
                return name

            return go

        done = await ctx.gather(
            {n: step(n, d) for n, d in [("a", 0.05), ("b", 0.01), ("c", 0.03), ("d", 0.01)]},
            concurrency=2,
        )
        return Out(order=list(done.values()), peak=peak)

    result = (await server.call_tool("g.run")).structured_content
    assert result == {"order": ["a", "b", "c", "d"], "peak": 2}


@pytest.mark.anyio
async def test_gather_stays_inside_the_call_deadline():
    server = OunceServer("g")
    cancelled: list[str] = []

    @server.tool(name="g.slow", effect=Effect.READ, deadline_ms=100)
    async def slow(ctx: ToolContext) -> Out:
        """Run a slow step."""

        async def forever() -> str:
            try:
                await anyio.sleep(10)
            finally:
                cancelled.append("slow")
            return "never"

        await ctx.gather({"slow": forever})
        return Out(order=[], peak=0)

    result = await server.call_tool("g.slow")
    assert result.is_error
    assert result.meta["ouncemcp"]["error"] == "deadline_exceeded"
    assert cancelled == ["slow"]
