"""M0: tokens the model receives from the OunceMCP inventory vs the plain FastMCP version.

Counts are o200k_base tokens of exactly what each server sends: tool definitions
(name, description, inputSchema, outputSchema) and the text content of results.
Run: python benchmarks/scripts/m0_compare.py
"""

from __future__ import annotations

import asyncio
import json
import platform

from _common import RESULTS
from fastmcp import Client
from plain_server import mcp as plain
from server import server as lean

from ouncemcp import get_counter, measure_tools

# (task, lean call, plain call). Each pair answers the same question.
TASKS = [
    (
        "List resources in ap-south-1",
        ("inventory.search", {"region": "ap-south-1"}),
        ("search_resources", {"region": "ap-south-1"}),
    ),
    (
        "Find degraded databases in ap-south-1",
        ("inventory.search", {"region": "ap-south-1", "kind": "database", "status": "degraded"}),
        ("search_resources", {"region": "ap-south-1", "kind": "database", "status": "degraded"}),
    ),
    (
        "Who owns resource da-00002?",
        ("inventory.get", {"id": "da-00002"}),
        ("get_resource", {"id": "da-00002"}),
    ),
    (
        "Show config and metrics for da-00002",
        ("inventory.get", {"id": "da-00002", "response_format": "detailed"}),
        ("get_resource", {"id": "da-00002"}),
    ),
]


def _text_tokens(result) -> int:
    counter = get_counter()
    return sum(counter.count(c.text) for c in result.content if getattr(c, "text", None))


async def main() -> dict:
    counter = get_counter()
    report: dict = {"tokenizer": counter.encoding_name, "python": platform.python_version()}
    async with Client(lean.fastmcp) as lc, Client(plain) as pc:
        lean_defs = measure_tools(await lc.list_tools(), lean.budget)
        plain_defs = measure_tools(await pc.list_tools(), lean.budget)
        report["startup_tokens"] = {"ouncemcp": lean_defs.total, "plain": plain_defs.total}
        rows = []
        for task, (ln, la), (pn, pa) in TASKS:
            lr = await lc.call_tool(ln, la)
            pr = await pc.call_tool(pn, pa)
            meta = (lr.meta or {}).get("ouncemcp", {})
            rows.append(
                {
                    "task": task,
                    "ouncemcp_tokens": _text_tokens(lr),
                    "plain_tokens": _text_tokens(pr),
                    "ouncemcp_truncated": meta.get("truncated"),
                }
            )
        report["tasks"] = rows
    lean_total = report["startup_tokens"]["ouncemcp"] + sum(r["ouncemcp_tokens"] for r in rows)
    plain_total = report["startup_tokens"]["plain"] + sum(r["plain_tokens"] for r in rows)
    report["session_total"] = {"ouncemcp": lean_total, "plain": plain_total}
    return report


if __name__ == "__main__":
    data = asyncio.run(main())
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "m0-inventory-tokens.json").write_text(json.dumps(data, indent=2) + "\n")
    s = data["startup_tokens"]
    print(f"Startup definitions   ouncemcp {s['ouncemcp']:>7,}   plain {s['plain']:>7,}")
    for r in data["tasks"]:
        print(
            f"{r['task'][:38]:<38}  ouncemcp {r['ouncemcp_tokens']:>7,}   "
            f"plain {r['plain_tokens']:>7,}"
        )
    t = data["session_total"]
    print(f"{'Session total':<38}  ouncemcp {t['ouncemcp']:>7,}   plain {t['plain']:>7,}")
