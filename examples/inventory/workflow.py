"""A composite workflow tool (spec CAP-7) over the inventory data.

``inventory.cost_review`` answers "where is the money going, and what is broken?" for
several regions in one call. Without it an agent would page through
``inventory.search`` for every region and add the numbers up itself: many calls, many
result tokens and arithmetic done by the model. The handler makes the per-region
service calls concurrently with ``ctx.gather``, inside one deadline, and returns only
the summary.

Run over stdio:      python examples/inventory/workflow.py
Check token budget:  ouncemcp check examples/inventory/workflow.py
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated

import anyio
from data import REGIONS, RESOURCES
from pydantic import BaseModel, Field

from ouncemcp import Detail, Effect, NotFound, OunceServer, ToolContext

server = OunceServer(
    "inventory-workflows",
    instructions="Cost and health reviews across regions. One call per review.",
)


async def fetch_region(region: str) -> list[dict]:
    """Stand-in for one upstream inventory API call per region."""
    await anyio.sleep(0.01)
    if region not in REGIONS:
        raise NotFound(f"Unknown region '{region}'.", hint=f"Regions: {', '.join(REGIONS)}.")
    return [r for r in RESOURCES if r["region"] == region]


class ReviewInput(BaseModel):
    regions: list[str] = Field(
        default_factory=lambda: list(REGIONS),
        description="Regions to review; default all",
        max_length=10,
    )
    top: int = Field(3, ge=1, le=10, description="How many of the costliest resources to list")


class Costly(BaseModel):
    id: str
    name: str
    cost_per_month: float


class RegionReview(BaseModel):
    region: str
    resources: int
    cost_per_month: float
    degraded: list[str]
    by_team: Annotated[dict[str, float] | None, Detail] = None


class CostReview(BaseModel):
    total_cost_per_month: float
    regions: list[RegionReview] = Field(max_length=10)  # bounded by ReviewInput.regions
    costliest: list[Costly] = Field(max_length=10)  # bounded by ReviewInput.top


@server.tool(name="inventory.cost_review", effect=Effect.READ, deadline_ms=10_000)
async def cost_review(args: ReviewInput, ctx: ToolContext) -> CostReview:
    """Review monthly cost and degraded resources across regions in one call.
    Use inventory.search instead to list individual resources."""
    fetched = await ctx.gather(
        {region: (lambda r=region: fetch_region(r)) for region in dict.fromkeys(args.regions)}
    )
    reviews, everything = [], []
    for region, rows in fetched.items():
        teams: dict[str, float] = defaultdict(float)
        for row in rows:
            teams[row["owner"].split("-oncall@")[0]] += row["cost_per_month"]
        reviews.append(
            RegionReview(
                region=region,
                resources=len(rows),
                cost_per_month=round(sum(r["cost_per_month"] for r in rows), 2),
                degraded=sorted(r["id"] for r in rows if r["status"] == "degraded"),
                by_team={team: round(cost, 2) for team, cost in sorted(teams.items())},
            )
        )
        everything.extend(rows)
    costliest = sorted(everything, key=lambda r: r["cost_per_month"], reverse=True)[: args.top]
    return CostReview(
        total_cost_per_month=round(sum(r.cost_per_month for r in reviews), 2),
        regions=reviews,
        costliest=[
            Costly(**{k: r[k] for k in ("id", "name", "cost_per_month")}) for r in costliest
        ],
    )


if __name__ == "__main__":
    server.run()
