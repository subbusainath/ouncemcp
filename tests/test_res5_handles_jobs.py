"""RES-5 handles and background jobs, and pilot 3 (long-running report): works across
two replicas and a restart (spec 15.1)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from ouncemcp import CursorSigner, Effect, MemoryStore, OunceServer, ToolContext
from ouncemcp.execution import handles as handles_mod
from ouncemcp.testing import as_caller

PILOT = Path(__file__).resolve().parents[1] / "examples" / "report"
ALICE = {"subject": "alice", "tenant": "acme"}
MALLORY = {"subject": "mallory", "tenant": "globex"}


@pytest.fixture
def report() -> Any:
    from ouncemcp.cli.loader import load_server

    load_server(str(PILOT / "server.py"))
    return sys.modules["_ouncemcp_target_server"]


async def poll(server: OunceServer, job: str) -> dict[str, Any]:
    await server.jobs.drain()
    return (await server.call_tool("jobs.get", {"job": job})).structured_content


@pytest.mark.anyio
async def test_a_report_runs_in_the_background_and_pages_by_handle(report):
    server = report.build(delay_s=0.05)
    with as_caller(**ALICE):
        started = await server.call_tool("report.start", {"period": "2026-10"})
        job = started.structured_content["job"]
        running = (await server.call_tool("jobs.get", {"job": job})).structured_content
        done = await poll(server, job)
        page1 = await server.call_tool("handles.read", {"handle": done["handle"], "limit": 100})
        page2 = await server.call_tool(
            "handles.read",
            {
                "handle": done["handle"],
                "limit": 100,
                "cursor": page1.structured_content["next_cursor"],
            },
        )
    assert running["state"] == "running"
    assert done["state"] == "done" and done["items"] == 2000
    assert done["result"]["rows"] == 2000 and done["result"]["period"] == "2026-10"
    first, second = page1.structured_content, page2.structured_content
    assert first["total"] == 2000 and len(first["items"]) <= 100
    assert first["items"][-1]["order"] < second["items"][0]["order"]
    assert page1.meta["ouncemcp"]["result_tokens"] <= page1.meta["ouncemcp"]["budget"]


@pytest.mark.anyio
async def test_job_ids_and_handles_only_work_for_their_caller(report):
    server = report.build(delay_s=0.0)
    with as_caller(**ALICE):
        job = (await server.call_tool("report.start", {"period": "2026-09"})).structured_content[
            "job"
        ]
        handle = (await poll(server, job))["handle"]
    with as_caller(**MALLORY):
        stolen_job = await server.call_tool("jobs.get", {"job": job})
        stolen_rows = await server.call_tool("handles.read", {"handle": handle})
    assert stolen_job.is_error and stolen_rows.is_error
    assert stolen_rows.meta["ouncemcp"]["error"] == "invalid_state"


@pytest.mark.anyio
async def test_two_replicas_and_a_restart_share_jobs_and_handles(report):
    store, keys = MemoryStore(), CursorSigner(["shared-signing-key-0123456789"])
    a = report.build(store=store, cursors=keys, delay_s=0.0)
    b = report.build(store=store, cursors=keys, delay_s=0.0)
    with as_caller(**ALICE):
        job = (await a.call_tool("report.start", {"period": "2026-08"})).structured_content["job"]
        await a.jobs.drain()
        on_b = (await b.call_tool("jobs.get", {"job": job})).structured_content
        restarted = report.build(store=store, cursors=keys)
        on_new = (await restarted.call_tool("jobs.get", {"job": job})).structured_content
        rows = await restarted.call_tool("handles.read", {"handle": on_new["handle"], "limit": 5})
    assert on_b["state"] == on_new["state"] == "done"
    assert on_b["result"] == on_new["result"]
    assert len(rows.structured_content["items"]) == 5


@pytest.mark.anyio
async def test_a_job_lost_in_a_restart_reports_interrupted(report, monkeypatch):
    store, keys = MemoryStore(), CursorSigner(["shared-signing-key-0123456789"])
    a = report.build(store=store, cursors=keys, delay_s=30)
    with as_caller(**ALICE):
        job = (await a.call_tool("report.start", {"period": "2026-07"})).structured_content["job"]
        for task in list(a.jobs._tasks):
            task.cancel()  # the process "stops" before the job finishes
        import time

        later = time.time() + handles_mod.JOB_LEASE_S + 1
        monkeypatch.setattr(handles_mod.time, "time", lambda: later)
        b = report.build(store=store, cursors=keys)
        status = (await b.call_tool("jobs.get", {"job": job})).structured_content
    assert status["state"] == "interrupted"
    assert "Start it again" in status["retry"]


class Out(BaseModel):
    handle: str


@pytest.mark.anyio
async def test_store_result_from_any_tool_and_failed_jobs():
    server = OunceServer("h", handles=True)

    @server.tool(name="logs.export", effect=Effect.READ)
    async def export(ctx: ToolContext) -> Out:
        """Export logs by handle."""
        return Out(handle=await ctx.store_result([f"line {i}" for i in range(500)]))

    @server.tool(name="logs.broken", effect=Effect.READ)
    async def broken(ctx: ToolContext) -> Out:
        """Start a job that fails."""

        async def work() -> None:
            raise RuntimeError("disk at /var/secret failed")

        return Out(handle=await ctx.start_job(work))

    handle = (await server.call_tool("logs.export")).structured_content["handle"]
    page = (
        await server.call_tool("handles.read", {"handle": handle, "limit": 10})
    ).structured_content
    assert page["items"][:2] == ["line 0", "line 1"] and page["total"] == 500
    job = (await server.call_tool("logs.broken")).structured_content["handle"]
    status = await poll(server, job)
    assert status["state"] == "failed"
    assert "/var/secret" not in str(status)


@pytest.mark.anyio
async def test_jobs_need_handles_enabled():
    server = OunceServer("h")

    @server.tool(name="t.start", effect=Effect.READ)
    async def start(ctx: ToolContext) -> Out:
        """Start."""

        async def work() -> None:
            return None

        return Out(handle=await ctx.start_job(work))

    result = await server.call_tool("t.start")
    assert result.is_error and "handles.read" not in [t.name for t in await server.list_tools()]
