"""RES-11: handlers can skip work for fields that will not be returned."""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel

from ouncemcp import Detail, Effect, OunceServer, ToolContext


class Owner(BaseModel):
    name: str
    history: Annotated[list[str] | None, Detail] = None


class Item(BaseModel):
    id: str
    owner: Owner
    metrics: Annotated[dict | None, Detail] = None


@pytest.mark.anyio
async def test_includes_follows_the_response_format():
    seen: dict[str, object] = {}
    server = OunceServer("s")

    @server.tool(name="item.get", effect=Effect.READ, description="Get one item by id.")
    def get(ctx: ToolContext) -> Item:
        seen.update(
            detailed=ctx.detailed,
            metrics=ctx.includes("metrics"),
            owner=ctx.includes("owner"),
            history=ctx.includes("owner.history"),
            budget=ctx.result_budget,
        )
        return Item(id="a", owner=Owner(name="n"))

    await server.call_tool("item.get", {})
    assert seen == {
        "detailed": False,
        "metrics": False,
        "owner": True,
        "history": False,
        "budget": 2000,
    }
    await server.call_tool("item.get", {"response_format": "detailed"})
    assert seen["detailed"] and seen["metrics"] and seen["history"]
    assert seen["budget"] == server.budget.result_ceiling


@pytest.mark.anyio
async def test_includes_rejects_unknown_fields():
    server = OunceServer("s")
    errors: list[str] = []

    @server.tool(name="item.get", effect=Effect.READ, description="Get one item by id.")
    def get(ctx: ToolContext) -> Item:
        for path in ("metric", "owner.nope", "id.x"):
            try:
                ctx.includes(path)
            except ValueError as exc:
                errors.append(str(exc))
        return Item(id="a", owner=Owner(name="n"))

    await server.call_tool("item.get", {})
    assert len(errors) == 3 and "Item has no field 'metric'" in errors[0]


@pytest.mark.anyio
async def test_inventory_get_skips_the_metrics_lookup_in_concise_mode():
    import data
    from server import server as inventory

    before = data.METRICS_LOOKUPS
    concise = await inventory.call_tool("inventory.get", {"id": "vm-00000"})
    assert data.METRICS_LOOKUPS == before
    assert "metrics" not in (concise.structured_content or {})
    detailed = await inventory.call_tool(
        "inventory.get", {"id": "vm-00000", "response_format": "detailed"}
    )
    assert data.METRICS_LOOKUPS == before + 1
    assert detailed.structured_content["metrics"] == data.BY_ID["vm-00000"]["metrics"]
