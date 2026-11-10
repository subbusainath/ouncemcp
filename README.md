# OunceMCP

**Every token counts.** OunceMCP is a framework for building Model Context Protocol (MCP) servers and clients that are fast, low-noise, and safe for agents by default. It builds on [FastMCP](https://gofastmcp.com) and the official MCP Python SDK, and targets the MCP `2026-07-28` specification.

> **Status: pre-alpha (M0 core prototype).** The core server API, token accounting, result budgets, and the `check` and `context` commands work and are tested. Discovery, safe writes, the client, eval, and audit are not built yet. APIs will change. See the [roadmap](#roadmap) and the [M0 report](docs/m0-report.md).

## Why

Most MCP slowdowns don't come from the protocol. They come from loading every tool definition up front, returning large raw results, and making the model chain many small calls. OunceMCP makes the efficient design the default one:

- **Context budgets:** token limits for tool definitions, the startup catalog, and each result, checked in CI.
- **Automatic discovery:** switches from exposing every tool to search-then-call once the catalog is too large.
- **Response shaping:** concise results by default, field selection, pagination, truncation notes, and handles for large data.
- **Safe execution:** declared effects, deadlines, bounded retries, idempotent writes, and approvals.
- **Visibility:** see exactly what the model received and what it cost.
- **Optional audit:** measure any MCP server, including ones not built with OunceMCP.

## Quickstart (M0)

```python
from typing import Annotated

from pydantic import BaseModel, Field

from ouncemcp import Cursor, Detail, Effect, OunceServer, Paginated, ToolContext

server = OunceServer("inventory")


class SearchInput(BaseModel):
    region: str = Field(description="Region, e.g. ap-south-1")
    limit: int = Field(20, ge=1, le=50)
    cursor: str | None = None


class Resource(BaseModel):
    id: str
    name: str
    status: str
    tags: Annotated[dict[str, str] | None, Detail] = None  # only with response_format=detailed


class SearchResult(BaseModel):
    items: Annotated[list[Resource], Paginated]
    next_cursor: Cursor = None


@server.tool(name="inventory.search", effect=Effect.READ, deadline_ms=5_000)
async def search(args: SearchInput, ctx: ToolContext) -> SearchResult:
    """Find cloud resources in a region."""
    rows = await load_resources(args.region)
    page = ctx.page(rows, cursor=args.cursor, limit=args.limit)
    return SearchResult(items=page.items, next_cursor=page.next_cursor)


if __name__ == "__main__":
    server.run()  # stdio; prints a token budget summary to stderr
```

What you get:

- **Declared effects:** `effect` is required and maps to MCP `readOnlyHint` and `destructiveHint`.
- **Typed inputs and outputs:** Pydantic models in, Pydantic models out, validated both ways.
- **Compact schemas:** optional fields are advertised without the `anyOf`/`null` noise.
- **Concise by default:** fields marked `Detail` are only returned when the agent asks for `response_format="detailed"`.
- **Result budgets:** every result fits its token budget and still matches its output schema. Lists are trimmed first, then long strings, then optional fields, and the agent gets a note saying what was removed and how to get more. If the smallest valid result is still too large, the agent gets an `isError` result asking for a narrower call instead of broken JSON.
- **Safe pagination:** mark the page list `Paginated` and the cursor `Cursor`, and use `ctx.page(...)`. If OunceMCP trims the page to fit the budget, it moves `next_cursor` so the next call resumes after the last item returned. No item is skipped or repeated. Lists are never trimmed below `min_items` (default 1). Cursors are signed: they expire, and only work for the same tool, caller and filters. Set `OUNCEMCP_CURSOR_KEYS` in production ([proposal 0003](docs/proposals/0003-signed-cursors.md)).
- **Budget checks:** `ouncemcp check server.py` counts the tokens of every tool definition as the host receives it, and fails CI when a tool goes over budget.

```bash
ouncemcp check examples/inventory/server.py
ouncemcp context examples/inventory/server.py inventory.search
```

Counts use `o200k_base` by default. Choose another tiktoken encoding with `OunceServer(tokenizer="cl100k_base")` or `ouncemcp check --tokenizer cl100k_base`, or pass your own counting function with `tokenizer=fn, tokenizer_name="my-tokenizer-v1"`. Every report names the tokenizer it used ([proposal 0004](docs/proposals/0004-pluggable-tokenizer.md)).

Planned commands (`init`, `dev`, `eval`, `bench`, `trace`, and the optional `audit`) report the milestone they ship in.

## M0 results

Measured on the inventory example with the `o200k_base` tokenizer. Full method and caveats are in [docs/m0-report.md](docs/m0-report.md).

| | OunceMCP | Plain FastMCP |
| --- | ---: | ---: |
| Tool definitions (2 tools) | 488 tokens | 195 tokens |
| Four-task session, definitions plus results | 1,510 tokens | 32,280 tokens |
| Added server time per call, p50 / p95 | 0.14–0.44 ms / 0.07–0.47 ms (1.44 / 1.32 ms when truncating) | baseline |

In M1, results that are trimmed must still match the output schema. Truncated calls now add 0.70 / 0.80 ms (p50 / p95) and other calls 0.14 to 0.31 ms. Shaping a 36,000-token result down to a 2,000-token budget takes 7.3 ms. See [proposal 0002](docs/proposals/0002-schema-safe-shaping-and-pagination.md) and `benchmarks/scripts/m1_truncation.py`.

Typed output schemas make definitions larger. Results are where the savings come from. Servers that don't need output schemas can pass `output_schemas=False` (247 tokens for this example).

## Repository layout

```text
src/ouncemcp/
  core/        capabilities, budgets, policy, response shaping, token accounting
  discovery/   direct, search, and code discovery modes
  execution/   deadlines, retries, idempotency, approvals
  client/      reference client broker
  eval/        conformance runner, security and agent task suites
  trace/       telemetry and trace viewer
  cli/         init, dev, check, context, eval, bench, trace, audit
  audit/       optional audit command; reuses core token accounting
examples/      pilot servers: inventory, ticketing, report
benchmarks/    servers, tasks, scripts, published results
docs/          specification, research, concepts, guides, proposals
tests/
```

## Roadmap

| Milestone | Scope | Estimate |
| --- | --- | --- |
| M0: core spike | `OunceServer`, typed tools with effects, token counting, result budgets, one example server | 2 weeks |
| M1: v0.1 | Framework core, automatic discovery, errors, security, `init`/`dev`/`check`/`context`, docs | 6 weeks |
| M2: v0.2 | Safe writes, client broker, migration guide, pilots, optional `audit` | 6 weeks |
| M3: v0.5 | Eval kit, trace viewer, public benchmark, launch | 6 weeks |
| M4: v1.0 | Security review, host compatibility matrix, stable API | 6 weeks |

The full plan is in [docs/spec.md](docs/spec.md), and the supporting research is in [docs/performance-research.md](docs/performance-research.md).

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
python benchmarks/scripts/m0_compare.py     # token comparison
python benchmarks/scripts/m0_overhead.py    # per-call overhead
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
