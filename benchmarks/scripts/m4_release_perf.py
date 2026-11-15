"""Release gate 7: PERF-1 to PERF-5 measured on the current code (spec section 10).

| Target | Measured here |
| --- | --- |
| PERF-1: middleware overhead per call, p50 < 2 ms, p95 < 10 ms | the M0 method: an OunceMCP tool against the same handler in plain FastMCP |
| PERF-2: cold start for 50 tools < 1 s | a fresh Python process: import, register 50 tools, answer tools/list |
| PERF-3: token counting at startup for 500 tools < 2 s | ``cost_report()`` over 500 registered tools |
| PERF-4: audit of a 100-tool server, discovery only, < 10 s | ``ouncemcp.audit.audit`` over HTTP on loopback |
| PERF-5: startup context in search mode < 400 tokens | ``tools/list`` of a 100-tool server in ``search`` mode |

Run: uv run python benchmarks/scripts/m4_release_perf.py
Writes benchmarks/results/m4-perf.json; earlier results files are not changed.
"""

from __future__ import annotations

import asyncio
import json
import platform
import socket
import statistics
import subprocess
import sys
import textwrap
import time

import anyio
from _common import RESULTS, ROOT

TOOLS_MODULE = textwrap.dedent(
    """
    from typing import Annotated, Literal
    from pydantic import BaseModel, Field
    from ouncemcp import Cursor, Detail, Effect, OunceServer, Paginated, ToolContext

    AREAS = ["billing", "users", "orders", "inventory", "search", "reports", "alerts",
             "deploys", "tickets", "docs"]
    VERBS = [("search", Effect.READ), ("get", Effect.READ), ("create", Effect.WRITE),
             ("update", Effect.WRITE), ("archive", Effect.WRITE), ("list_events", Effect.READ),
             ("export", Effect.READ), ("summarize", Effect.READ), ("assign", Effect.WRITE),
             ("count", Effect.READ)]


    class Item(BaseModel):
        id: str
        name: str
        status: Literal["active", "archived"]
        notes: Annotated[str | None, Detail] = None


    class Page(BaseModel):
        items: Annotated[list[Item], Paginated]
        total: int
        next_cursor: Cursor = None


    def build(count: int, **kwargs) -> OunceServer:
        server = OunceServer("generated", **kwargs)
        for i in range(count):
            area, (verb, effect) = AREAS[i // len(VERBS) % len(AREAS)], VERBS[i % len(VERBS)]
            name = f"{area}{i // 100 or ''}.{verb}"

            async def handler(
                ctx: ToolContext,
                query: str = Field("", description="Words to match in names"),
                limit: int = Field(20, ge=1, le=100),
                cursor: str | None = Field(None, description="next_cursor from the last call"),
            ) -> Page:
                rows = [Item(id=f"x-{n}", name=f"item {n}", status="active") for n in range(40)]
                page = ctx.page(rows, cursor=cursor, limit=limit)
                return Page(items=page.items, total=len(rows), next_cursor=page.next_cursor)

            server.tool(
                name=name,
                effect=effect,
                description=f"{verb.replace('_', ' ').capitalize()} {area} records by name. "
                f"Use {area}.get for one record. Side effect: none for reads; writes change "
                f"the {area} store.",
            )(handler)
        return server
    """
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_module(tmp: str) -> str:
    path = f"{tmp}/generated_tools.py"
    with open(path, "w") as f:
        f.write(TOOLS_MODULE)
    return tmp


async def perf1(iterations: int) -> dict:
    sys.path.insert(0, str(ROOT / "benchmarks" / "scripts"))
    import m0_overhead

    return await m0_overhead.main(iterations)


PLAIN_50 = textwrap.dedent(
    """
    import time; t0 = time.perf_counter()
    import anyio
    from fastmcp import Client, FastMCP
    from pydantic import BaseModel, Field
    t_import = time.perf_counter()
    mcp = FastMCP("plain")
    class Item(BaseModel):
        id: str
        name: str
    for i in range(50):
        def handler(query: str = Field("", description="Words to match"), limit: int = 20) -> list[Item]:
            return []
        mcp.tool(name=f"area{i}.search", description=f"Search area {i} records by name.")(handler)
    async def ready():
        async with Client(mcp) as client:
            return len(await client.list_tools())
    n = anyio.run(ready)
    t1 = time.perf_counter()
    print(n, round((t_import - t0) * 1000, 1), round((t1 - t0) * 1000, 1))
    """
)


def _cold(code: str, runs: int) -> tuple[list[float], list[float]]:
    times, imports = [], []
    for _ in range(runs):
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.split()
        assert out[0] == "50", out
        imports.append(float(out[1]))
        times.append(float(out[2]))
    return times, imports


def perf2(tmp: str, runs: int = 5) -> dict:
    """Wall time of a fresh process that imports OunceMCP, registers 50 tools and
    answers tools/list through an in-memory MCP client, against the same for a plain
    FastMCP server, which shows how much of the time is the framework's own."""
    code = textwrap.dedent(
        f"""
        import time; t0 = time.perf_counter()
        import sys; sys.path.insert(0, {tmp!r})
        import anyio
        from fastmcp import Client
        from generated_tools import build
        t_import = time.perf_counter()
        server = build(50, discovery="direct")
        async def ready():
            async with Client(server.fastmcp) as client:
                return len(await client.list_tools())
        n = anyio.run(ready)
        t1 = time.perf_counter()
        print(n, round((t_import - t0) * 1000, 1), round((t1 - t0) * 1000, 1))
        """
    )
    times, imports = _cold(code, runs)
    plain_times, plain_imports = _cold(PLAIN_50, runs)
    added = statistics.median(times) - statistics.median(plain_times)
    return {
        "tools": 50,
        "runs": runs,
        "cold_start_ms_median": statistics.median(times),
        "cold_start_ms_max": max(times),
        "of_which_imports_ms_median": statistics.median(imports),
        "plain_fastmcp_cold_start_ms_median": statistics.median(plain_times),
        "plain_fastmcp_imports_ms_median": statistics.median(plain_imports),
        "added_by_ouncemcp_ms_median": round(added, 1),
        "target_ms": 1000,
        "met": max(times) < 1000,
    }


async def perf3(build) -> dict:  # type: ignore[no-untyped-def]
    server = build(500, discovery="direct")
    tools = await server.list_tools()
    from ouncemcp.core.costs import measure_tools

    timings = []
    for _ in range(5):
        t0 = time.perf_counter()
        measure_tools(tools, server.budget, server.counter)
        timings.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    report = await server.cost_report()
    with_listing = (time.perf_counter() - t0) * 1000
    return {
        "tools": 500,
        "definition_tokens": report.total,
        "counting_ms_median": round(statistics.median(timings), 1),
        "listing_and_counting_ms": round(with_listing, 1),
        "target_ms": 2000,
        "met": with_listing < 2000,
    }


async def perf4(build) -> dict:  # type: ignore[no-untyped-def]
    from ouncemcp.audit.runner import audit

    server = build(100, discovery="direct")
    port = free_port()
    async with anyio.create_task_group() as group:
        group.start_soon(
            lambda: server.fastmcp.run_http_async(
                host="127.0.0.1", port=port, show_banner=False, log_level="error"
            )
        )
        await anyio.sleep(1.0)
        t0 = time.perf_counter()
        report = await audit(f"http://127.0.0.1:{port}/mcp")
        elapsed = (time.perf_counter() - t0) * 1000
        group.cancel_scope.cancel()
    return {
        "tools": len(report.cost.tools),
        "audit_ms": round(elapsed, 1),
        "network": "loopback HTTP (a remote server adds its round-trip time)",
        "target_ms": 10000,
        "met": elapsed < 10000,
    }


async def perf5(build) -> dict:  # type: ignore[no-untyped-def]
    server = build(100, discovery="search")
    decision = await server.discovery()
    report = await server.cost_report()
    return {
        "tools_registered": 100,
        "mode": decision.mode,
        "direct_tokens": decision.direct_tokens,
        "search_mode_startup_tokens": report.total,
        "listed": [t.name for t in report.tools],
        "target_tokens": 400,
        "met": report.total < 400,
    }


async def run(tmp: str, iterations: int) -> dict:
    sys.path.insert(0, tmp)
    from generated_tools import build

    return {
        "PERF-1": await perf1(iterations),
        "PERF-2": perf2(tmp),
        "PERF-3": await perf3(build),
        "PERF-4": await perf4(build),
        "PERF-5": await perf5(build),
    }


if __name__ == "__main__":
    import tempfile

    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    with tempfile.TemporaryDirectory() as tmp:
        results = asyncio.run(run(write_module(tmp), iterations))
    perf1_cases = results["PERF-1"]["cases"]
    results["PERF-1"]["met"] = all(
        c["added_p50_ms"] < 2 and c["added_p95_ms"] < 10 for c in perf1_cases.values()
    )
    commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    data = {
        "gate": "release gate 7: PERF-1 to PERF-5",
        "commit": commit,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpus": __import__("os").cpu_count(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "all_met": all(r["met"] for r in results.values()),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "m4-perf.json").write_text(json.dumps(data, indent=2) + "\n")
    for key, r in results.items():
        print(
            key,
            "met" if r["met"] else "NOT MET",
            {k: v for k, v in r.items() if k not in ("cases", "listed")},
        )
    for label, c in perf1_cases.items():
        print(
            f"  PERF-1 {label}: added p50 {c['added_p50_ms']:+.3f} ms, p95 {c['added_p95_ms']:+.3f} ms"
        )
