"""M0: server-side overhead of an OunceMCP tool vs an equivalent plain FastMCP tool.

Both tools run the same handler and return the same data. The difference in
latency is what OunceMCP adds: input and output validation, shaping, token
counting, and metadata. PERF-1 target: p50 < 2 ms, p95 < 10 ms of added time.

Run: python benchmarks/scripts/m0_overhead.py [iterations]
"""

from __future__ import annotations

import asyncio
import json
import platform
import statistics
import sys
import time

from _common import RESULTS
from fastmcp import FastMCP
from server import SearchInput, SearchResult, search
from server import server as lean

from ouncemcp import Budget, Effect, OunceServer


def build_plain() -> FastMCP:
    mcp = FastMCP("plain-equivalent")

    @mcp.tool(name="inventory.search")
    async def plain_search(
        region: str, kind: str | None = None, status: str | None = None, limit: int = 20
    ) -> SearchResult:
        """Same handler, no OunceMCP layer."""
        # OunceServer.tool returns the original handler, so this is the same code path.
        return await search(SearchInput(region=region, kind=kind, status=status, limit=limit))

    return mcp


def build_tight() -> OunceServer:
    """Same tool with a 300-token result budget, so every full page is truncated."""
    tight = OunceServer("tight", budget=Budget(result_tokens=300))
    tight.tool(name="inventory.search", effect=Effect.READ)(search)
    return tight


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


async def timed(call, n: int) -> list[float]:
    for _ in range(50):
        await call()
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        await call()
        out.append((time.perf_counter() - t0) * 1000)
    return out


def _row(lean_ms: list[float], plain_ms: list[float]) -> dict:
    return {
        "ouncemcp_p50_ms": round(statistics.median(lean_ms), 3),
        "ouncemcp_p95_ms": round(pct(lean_ms, 0.95), 3),
        "plain_p50_ms": round(statistics.median(plain_ms), 3),
        "plain_p95_ms": round(pct(plain_ms, 0.95), 3),
        "added_p50_ms": round(statistics.median(lean_ms) - statistics.median(plain_ms), 3),
        "added_p95_ms": round(pct(lean_ms, 0.95) - pct(plain_ms, 0.95), 3),
    }


async def main(n: int) -> dict:
    plain = build_plain()
    cases = {
        "small (limit=5)": {"region": "ap-south-1", "limit": 5},
        "page (limit=20)": {"region": "ap-south-1", "limit": 20},
        "full page (limit=50)": {"region": "ap-south-1", "limit": 50},
    }
    results = {}
    tight = build_tight()
    full = {"region": "ap-south-1", "limit": 50}
    tight_ms = await timed(lambda: tight.fastmcp.call_tool("inventory.search", full), n)
    plain_ms = await timed(lambda: plain.call_tool("inventory.search", full), n)
    results["truncated to 300 tokens"] = _row(tight_ms, plain_ms)
    for label, args in cases.items():
        lean_ms = await timed(lambda a=args: lean.fastmcp.call_tool("inventory.search", a), n)
        plain_ms = await timed(lambda a=args: plain.call_tool("inventory.search", a), n)
        results[label] = _row(lean_ms, plain_ms)
    return {
        "iterations": n,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "target": {"added_p50_ms": 2, "added_p95_ms": 10},
        "cases": results,
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    data = asyncio.run(main(n))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "m0-overhead.json").write_text(json.dumps(data, indent=2) + "\n")
    print(f"{n} calls per case, Python {data['python']}")
    for label, r in data["cases"].items():
        print(
            f"{label:<24} ouncemcp p50 {r['ouncemcp_p50_ms']:.3f} p95 {r['ouncemcp_p95_ms']:.3f} | "
            f"plain p50 {r['plain_p50_ms']:.3f} p95 {r['plain_p95_ms']:.3f} | "
            f"added p50 {r['added_p50_ms']:+.3f} p95 {r['added_p95_ms']:+.3f} ms"
        )
