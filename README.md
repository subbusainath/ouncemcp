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

from ouncemcp import Detail, Effect, OunceServer

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
    items: list[Resource]
    next_cursor: str | None = None


@server.tool(name="inventory.search", effect=Effect.READ, deadline_ms=5_000)
async def search(args: SearchInput) -> SearchResult:
    """Find cloud resources in a region."""
    ...


if __name__ == "__main__":
    server.run()  # stdio; prints a token budget summary to stderr
```

What you get:

- **Declared effects:** `effect` is required and maps to MCP `readOnlyHint` and `destructiveHint`.
- **Typed inputs and outputs:** Pydantic models in, Pydantic models out, validated both ways.
- **Compact schemas:** optional fields are advertised without the `anyOf`/`null` noise.
- **Concise by default:** fields marked `Detail` are only returned when the agent asks for `response_format="detailed"`.
- **Result budgets:** every result fits its token budget. Lists are trimmed first, then long strings, and the agent gets a note saying what was removed and how to get more.
- **Budget checks:** `ouncemcp check server.py` counts the tokens of every tool definition as the host receives it, and fails CI when a tool goes over budget.

```bash
ouncemcp check examples/inventory/server.py
ouncemcp context examples/inventory/server.py inventory.search
```

Planned commands (`init`, `dev`, `eval`, `bench`, `trace`, and the optional `audit`) report the milestone they ship in.

## M0 results

Measured on the inventory example with the `o200k_base` tokenizer. Full method and caveats are in [docs/m0-report.md](docs/m0-report.md).

| | OunceMCP | Plain FastMCP |
| --- | ---: | ---: |
| Tool definitions (2 tools) | 488 tokens | 195 tokens |
| Four-task session, definitions plus results | 1,510 tokens | 32,280 tokens |
| Added server time per call, p50 / p95 | 0.14–0.44 ms / 0.07–0.47 ms (1.44 / 1.32 ms when truncating) | baseline |

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
