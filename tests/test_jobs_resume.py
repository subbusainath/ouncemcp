"""Jobs that resume after a restart: named jobs with JSON arguments are run again by
the next jobs.get after their process stopped (spec section 15.4, Jobs)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from ouncemcp import CursorSigner, Effect, MemoryStore, OunceServer, ToolContext
from ouncemcp.execution import handles as handles_mod
from ouncemcp.testing import as_caller

ALICE = {"subject": "alice", "tenant": "acme"}
RUNS: list[tuple[str, int]] = []


class Total(BaseModel):
    period: str
    total: int


class Started(BaseModel):
    job: str


def build(
    store: MemoryStore, signer: CursorSigner, *, hang: bool = False, max_attempts: int = 3
) -> OunceServer:
    server = OunceServer("reports", handles=True, store=store, cursors=signer)

    @server.job("report.total", max_attempts=max_attempts)
    async def total(args: dict[str, Any], attempt: int) -> Total:
        RUNS.append((args["period"], attempt))
        if hang:
            await asyncio.sleep(3600)  # this process "stops" before finishing
        return Total(period=args["period"], total=42)

    @server.tool(name="report.start", effect=Effect.READ, description="Start a report job.")
    async def start(period: str, ctx: ToolContext) -> Started:
        return Started(job=await ctx.start_job("report.total", {"period": period}))

    @server.tool(
        name="report.closure", effect=Effect.READ, description="Start a job from a closure."
    )
    async def closure(ctx: ToolContext) -> Started:
        async def work() -> Total:
            await asyncio.sleep(3600)
            return Total(period="x", total=0)

        return Started(job=await ctx.start_job(work))

    return server


async def get(server: OunceServer, job: str) -> dict[str, Any]:
    return (await server.call_tool("jobs.get", {"job": job})).structured_content  # type: ignore[return-value]


def crash(server: OunceServer) -> None:
    for task in list(server.jobs._tasks):
        task.cancel()


@pytest.fixture
def shared(monkeypatch):
    RUNS.clear()
    monkeypatch.setattr(handles_mod, "JOB_LEASE_S", 0.05)
    monkeypatch.setattr(handles_mod, "HEARTBEAT_S", 3600)
    return MemoryStore(), CursorSigner([b"k" * 32])


@pytest.mark.anyio
async def test_a_named_job_resumes_on_another_replica_after_a_crash(shared):
    store, signer = shared
    first = build(store, signer, hang=True)
    with as_caller(**ALICE):
        job = (await first.call_tool("report.start", {"period": "2026-10"})).structured_content[
            "job"
        ]
        await asyncio.sleep(0.01)
        crash(first)
        await asyncio.sleep(0.1)  # the lease runs out

        second = build(store, signer)
        status = await get(second, job)
        assert status["state"] == "running" and status["attempt"] == 2
        await second.jobs.drain()
        done = await get(second, job)
    assert done["state"] == "done" and done["result"] == {"period": "2026-10", "total": 42}
    assert RUNS == [("2026-10", 1), ("2026-10", 2)]


@pytest.mark.anyio
async def test_only_one_replica_resumes_a_job(shared):
    store, signer = shared
    first = build(store, signer, hang=True)
    with as_caller(**ALICE):
        job = (await first.call_tool("report.start", {"period": "p"})).structured_content["job"]
        await asyncio.sleep(0.01)
        crash(first)
        await asyncio.sleep(0.1)
        a, b = build(store, signer, hang=True), build(store, signer, hang=True)
        states = await asyncio.gather(get(a, job), get(b, job))
        crash(a)
        crash(b)
    assert sorted(s["state"] for s in states) == ["running", "running"]
    assert [r for r in RUNS if r[1] == 2] == [("p", 2)]  # one restart, not two


@pytest.mark.anyio
async def test_attempts_are_limited(shared):
    store, signer = shared
    server = build(store, signer, hang=True, max_attempts=2)
    with as_caller(**ALICE):
        job = (await server.call_tool("report.start", {"period": "p"})).structured_content["job"]
        for _ in range(2):
            await asyncio.sleep(0.01)
            crash(server)
            await asyncio.sleep(0.1)
            status = await get(server, job)
    assert status["state"] == "interrupted"
    assert status["retry"] and "2 attempts" in status["retry"]
    assert len(RUNS) == 2


@pytest.mark.anyio
async def test_closure_jobs_still_report_interrupted(shared):
    store, signer = shared
    server = build(store, signer)
    with as_caller(**ALICE):
        job = (await server.call_tool("report.closure", {})).structured_content["job"]
        await asyncio.sleep(0.01)
        crash(server)
        await asyncio.sleep(0.1)
        assert (await get(build(store, signer), job))["state"] == "interrupted"


def test_job_names_and_arguments_are_checked(shared):
    store, signer = shared
    server = build(store, signer)
    with pytest.raises(ValueError, match="already"):

        @server.job("report.total")
        async def again(args: dict[str, Any], attempt: int) -> Total:
            return Total(period="", total=0)

    with pytest.raises(ValueError, match="max_attempts"):
        server.job("x", max_attempts=0)


@pytest.mark.anyio
async def test_unknown_job_names_and_unserializable_arguments_are_refused(shared):
    store, signer = shared
    server = build(store, signer)

    @server.tool(name="bad.start", effect=Effect.READ, description="Start a job wrongly.")
    async def bad(kind: str, ctx: ToolContext) -> Started:
        args: Any = {"when": object()} if kind == "report.total" else {}
        return Started(job=await ctx.start_job(kind, args))

    with as_caller(**ALICE):
        for kind in ("nope", "report.total"):
            result = await server.call_tool("bad.start", {"kind": kind})
            assert result.is_error, kind
    assert RUNS == []
