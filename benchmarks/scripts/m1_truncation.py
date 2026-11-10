"""M1: time spent shaping a result that is over its budget (spec PERF-7).

Measures ``shape_result`` alone, the step a truncated call adds, on paginated
results of different sizes. PERF-7 target: under 1 ms p50 for a typical page.

Run: python benchmarks/scripts/m1_truncation.py [iterations]
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from typing import Annotated

import jsonschema
from _common import RESULTS
from pydantic import BaseModel

from ouncemcp import Cursor, Paginated, get_counter
from ouncemcp.core.cursor import encode_offset
from ouncemcp.core.schema import compact_schema
from ouncemcp.core.shaping import PageSpec, shape_result
from ouncemcp.core.tokens import compact_json


class Row(BaseModel):
    id: str
    name: str
    status: str
    region: str
    tags: dict[str, str]


class Page(BaseModel):
    items: Annotated[list[Row], Paginated]
    next_cursor: Cursor = None


CASES = {
    "50 items, 300-token budget": (50, 300),
    "100 items, 2,000-token budget": (100, 2_000),
    "1,000 items, 2,000-token budget": (1_000, 2_000),
}


def page(n: int) -> Page:
    rows = [
        Row(
            id=f"r-{i:05d}",
            name=f"resource {i}",
            status="running",
            region="ap-south-1",
            tags={"team": "core", "env": "prod"},
        )
        for i in range(n)
    ]
    return Page(items=rows, next_cursor=encode_offset(n))


def main(iterations: int) -> dict:
    counter = get_counter()
    validator = jsonschema.Draft202012Validator(compact_schema(Page.model_json_schema()))
    results = {}
    for label, (n, budget) in CASES.items():
        value = page(n)
        spec = PageSpec("items", "next_cursor", 0, n, encode_offset)

        def run(value: Page = value, budget: int = budget, spec: PageSpec = spec):
            return shape_result(
                value,
                response_format="concise",
                budget=budget,
                counter=counter,
                validator=validator,
                page=spec,
                assume_valid=True,
            )

        for _ in range(20):
            out = run()
        times = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            out = run()
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        results[label] = {
            "input_tokens": counter.count(compact_json(value.model_dump())),
            "budget": budget,
            "kept_items": len(out.data["items"]),
            "result_tokens": out.tokens,
            "p50_ms": round(statistics.median(times), 3),
            "p95_ms": round(times[min(len(times) - 1, round(0.95 * (len(times) - 1)))], 3),
        }
    return {
        "iterations": iterations,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "target": {"p50_ms": 1},
        "cases": results,
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    data = main(n)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "m1-truncation.json").write_text(json.dumps(data, indent=2) + "\n")
    print(f"{n} runs per case, Python {data['python']}")
    for label, r in data["cases"].items():
        print(
            f"{label:<34} {r['input_tokens']:>6} -> {r['result_tokens']:>5} tokens, "
            f"kept {r['kept_items']:>3} | p50 {r['p50_ms']:.3f} p95 {r['p95_ms']:.3f} ms"
        )
