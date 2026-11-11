"""Long-running report built with OunceMCP (pilot 3, milestone M2).

``report.start`` starts a sales report in the background and returns a job id at once.
``jobs.get`` reports progress and, when the report is done, a short summary plus a
handle; ``handles.read`` pages through the full rows. Job ids and handles only work for
the caller who started the report, expire, and live in the server's store, so with a
shared store any replica can answer and they survive a restart.

Run over stdio:      python examples/report/server.py
Check token budget:  ouncemcp check examples/report/server.py
"""

from __future__ import annotations

import asyncio
import random
from typing import Literal

from pydantic import BaseModel, Field

from ouncemcp import CursorSigner, Effect, OunceServer, OunceStore, ToolContext
from ouncemcp.execution.handles import JobResult

REGIONS = ["north", "south", "east", "west"]


class StartInput(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Month, e.g. 2026-10")
    group_by: Literal["region", "product"] = "region"


class Started(BaseModel):
    job: str = Field(description="Pass to jobs.get to check progress")
    message: str


class Summary(BaseModel):
    period: str
    rows: int
    revenue: float
    top: str


async def build_rows(period: str, delay_s: float) -> list[dict]:
    """Stand-in for a slow warehouse query."""
    await asyncio.sleep(delay_s)
    rng = random.Random(period)
    return [
        {
            "order": f"{period}-{i:05d}",
            "region": rng.choice(REGIONS),
            "product": f"P-{rng.randint(1, 40):02d}",
            "amount": round(rng.uniform(5, 900), 2),
        }
        for i in range(2_000)
    ]


def build(
    *,
    store: OunceStore | None = None,
    cursors: CursorSigner | None = None,
    delay_s: float = 2.0,
) -> OunceServer:
    """The pilot server. Replicas share ``store`` and ``cursors`` (the signing keys)."""
    server = OunceServer(
        "reports",
        instructions="Start a report, then poll jobs.get; page rows with handles.read.",
        handles=True,
        store=store,
        cursors=cursors,
    )

    @server.tool(name="report.start", effect=Effect.READ)
    async def start(args: StartInput, ctx: ToolContext) -> Started:
        """Start a monthly sales report in the background, then use jobs.get with the job id."""

        async def work() -> JobResult:
            rows = await build_rows(args.period, delay_s)
            totals: dict[str, float] = {}
            for row in rows:
                totals[row[args.group_by]] = totals.get(row[args.group_by], 0) + row["amount"]
            summary = Summary(
                period=args.period,
                rows=len(rows),
                revenue=round(sum(totals.values()), 2),
                top=max(totals, key=lambda k: totals[k]),
            )
            return JobResult(summary=summary, items=rows)

        job = await ctx.start_job(work)
        return Started(job=job, message="Report started. Check it with jobs.get.")

    return server


server = build()

if __name__ == "__main__":
    server.run()
