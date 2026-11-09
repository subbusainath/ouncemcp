# OunceMCP

**Every token counts.** OunceMCP is a framework for building Model Context Protocol (MCP) servers and clients that are fast, low-noise, and safe for agents by default. It builds on [FastMCP](https://gofastmcp.com) and the official MCP Python SDK, and targets the MCP `2026-07-28` specification.

> **Status: pre-alpha.** This repository contains the project layout and specification only. No framework code has been implemented or benchmarked yet. See the [roadmap](#roadmap).

## Why

Most MCP slowdowns don't come from the protocol. They come from loading every tool definition up front, returning large raw results, and making the model chain many small calls. OunceMCP makes the efficient design the default one:

- **Context budgets:** token limits for tool definitions, the startup catalog, and each result, checked in CI.
- **Automatic discovery:** switches from exposing every tool to search-then-call once the catalog is too large.
- **Response shaping:** concise results by default, field selection, pagination, truncation notes, and handles for large data.
- **Safe execution:** declared effects, deadlines, bounded retries, idempotent writes, and approvals.
- **Visibility:** see exactly what the model received and what it cost.
- **Optional audit:** measure any MCP server, including ones not built with OunceMCP.

## Planned developer experience

The API below is a proposal from the specification and will change.

```python
from ouncemcp import OunceServer, Effect, Budget

server = OunceServer(
    name="inventory",
    budget=Budget(startup_tokens=4_000, result_tokens=2_000),
    discovery="auto",
)

@server.tool(name="inventory.search", effect=Effect.READ, deadline_ms=5_000)
async def search(args, ctx):
    ...
```

```bash
ouncemcp init my-server     # scaffold a server with tests and CI
ouncemcp dev                # run with hot reload and a live budget summary
ouncemcp check              # lint schemas, descriptions, budgets, and policy
ouncemcp context <tool>     # show exactly what the model receives
ouncemcp audit <server>     # optional: measure any MCP server
```

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
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
