# OunceMCP

**Every token counts.** OunceMCP is a framework for building Model Context Protocol (MCP) servers and clients that are fast, low-noise, and safe for agents by default. It builds on [FastMCP](https://gofastmcp.com) and the official MCP Python SDK, and targets the MCP `2026-07-28` specification.

> **Status: pre-1.0 (M0 to M4 built).** The framework, safe writes, jobs, the client broker, the eval kit, the trace viewer and the benchmark work and are tested; the official MCP conformance suite passes in CI and the M4 performance gates are met ([M4 report](https://subbusainath.github.io/ouncemcp/m4-report/)). The 1.0 API is frozen ([proposal 0029](https://subbusainath.github.io/ouncemcp/proposals/0029-api-freeze/), [changelog](https://github.com/subbusainath/ouncemcp/blob/main/CHANGELOG.md)); 1.0 ships after the external security review. Release gates that need people or outside systems are listed in the [spec](https://subbusainath.github.io/ouncemcp/spec/) (section 14.2). New here? Read [The OunceMCP Book](https://subbusainath.github.io/ouncemcp/book/).

## Why

Most MCP slowdowns don't come from the protocol. They come from loading every tool definition up front, returning large raw results, and making the model chain many small calls. OunceMCP makes the efficient design the default one:

- **Context budgets:** token limits for tool definitions, the startup catalog, and each result, checked in CI.
- **Automatic discovery:** switches from exposing every tool to search-then-call once the catalog is too large.
- **Response shaping:** concise results by default, field selection, pagination, truncation notes, and handles for large data.
- **Safe execution:** declared effects, deadlines, bounded retries, idempotent writes, and approvals.
- **Visibility:** see exactly what the model received and what it cost.
- **Optional audit:** measure any MCP server, including ones not built with OunceMCP.

## Quickstart

```bash
pipx install "ouncemcp==1.0.0rc2"   # or: uv tool install --prerelease allow ouncemcp
ouncemcp init notes-server && cd notes-server
uv venv && uv pip install --prerelease allow -e ".[dev]"
uv run pytest && uv run ouncemcp dev server.py
```

The full walkthrough is in [docs/quickstart.md](https://subbusainath.github.io/ouncemcp/quickstart/). To learn the framework step by step, read [The OunceMCP Book](https://subbusainath.github.io/ouncemcp/book/): one library server built across 15 chapters, every stage tested. A tool looks like this:

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
- **Safe pagination:** mark the page list `Paginated` and the cursor `Cursor`, and use `ctx.page(...)`. If OunceMCP trims the page to fit the budget, it moves `next_cursor` so the next call resumes after the last item returned. No item is skipped or repeated. Lists are never trimmed below `min_items` (default 1). Cursors are signed: they expire, and only work for the same tool, caller and filters. Set `OUNCEMCP_CURSOR_KEYS` in production ([proposal 0003](https://subbusainath.github.io/ouncemcp/proposals/0003-signed-cursors/)).
- **Automatic discovery:** `OunceServer(discovery="auto")` (the default) sends every tool while the definitions fit `Budget.startup_tokens`. Above it, the server sends `search_tools`, `describe_tool` and `call_tool` instead (279 tokens whatever the catalog), plus any `pinned` tools. Search results come in three detail levels (`name`, `summary`, `full`) and only include tools the caller is authorized to use (`@server.tool(auth=...)`). Hiding a tool is not access control: the same check runs on a guessed name. `discovery="direct"` or `"search"` overrides the choice, and `await server.measure_recall(cases)` reports how often search finds the tools agents need. See [proposal 0006](https://subbusainath.github.io/ouncemcp/proposals/0006-discovery-modes/).
- **Categorized errors:** every failure is one of validation, authentication, permission, not found, rate limit, transient, ambiguous write or internal, with a `retryable` flag. Validation messages say how to fix the input without repeating it. Unexpected exceptions are never shown to the model: it gets a reference id and the details go to the log. Messages you write are redacted for URLs, addresses, hostnames, paths and credentials. Raise `InvalidInput`, `NotFound`, `RateLimited` and the other `OunceError` classes from handlers.
- **Execution limits:** every call has a deadline (default 30 s) and is cancelled when it passes or the client cancels. Reads are retried a few times with bounded backoff inside the deadline, and writes never are. A write that times out is reported as ambiguous, not as failed. Request size, nesting depth and calls in flight per principal are bounded (`Limits`). See [proposal 0007](https://subbusainath.github.io/ouncemcp/proposals/0007-errors-and-execution-limits/).
- **Security and policy:** `ctx.identity` comes from the verified token only. `policy=` on the server or a tool decides who sees and calls it, on the declared effect, never on annotations. `audience=` refuses tokens issued for other servers, `passthrough_guard()` stops the caller's token from reaching upstream APIs, `ctx.cache` is partitioned by tenant, `ctx.issue_state` hands out handles and approvals bound to caller, request and expiry, and HTTP binds to loopback with `Host` and `Origin` checks ([proposal 0008](https://subbusainath.github.io/ouncemcp/proposals/0008-security-and-policy/)).
- **Safe writes:** `idempotency=True` runs a write once per caller and arguments, and a write that times out finishes in the background so a retry gets its result instead of writing twice. `approval="required"` with a `preview` asks a person first, through the client, and refuses when nobody can be asked ([safe writes](https://subbusainath.github.io/ouncemcp/guides/safe-writes/)).
- **Large results and slow work:** `OunceServer(handles=True)` adds `handles.read` and `jobs.get`. `await ctx.store_result(rows)` returns a handle to page, and `await ctx.start_job(work)` runs work in the background. Both work across replicas with a shared `RedisStore`.
- **Resources and prompts:** `@server.resource(uri)` and `@server.prompt()` get the same budgets, policies and verified caller as tools.
- **Migration and audit:** `OunceServer.wrap(existing_fastmcp_server)` adds all of the above without touching its tools, and `ouncemcp audit <url | file | -- command>` measures any MCP server ([migration guide](https://subbusainath.github.io/ouncemcp/guides/migrating-from-fastmcp/)).
- **Many servers:** `ouncemcp.client.Broker` connects a host to many servers as one catalog, with namespaced tools, cross-server search, schema checks, deadlines, failure isolation, and no cross-server authority from server output ([broker](https://subbusainath.github.io/ouncemcp/guides/client-broker/)).
- **Operations:** `OunceServer.from_config(name, "ouncemcp.toml")`, `/healthz`, `/readyz` and, with `metrics=True`, Prometheus `/metrics` ([deploying](https://subbusainath.github.io/ouncemcp/guides/deploying-http/)).
- **Traces:** `OunceServer(trace=MemorySink())` records each call's result tokens, latency, retries and outcome, redacted by default ([proposal 0009](https://subbusainath.github.io/ouncemcp/proposals/0009-traces/)). `ouncemcp trace calls.jsonl` writes an HTML report you can search and filter ([proposal 0020](https://subbusainath.github.io/ouncemcp/proposals/0020-report-ui/)), and `OTelSink()` exports spans to OpenTelemetry ([proposal 0018](https://subbusainath.github.io/ouncemcp/proposals/0018-trace-viewer/)).
- **Task suites:** `ouncemcp eval suite.yaml` runs YAML tasks against a server, judges outcomes on the server, and reports success, tool choice, tokens, latency and regressions against a baseline ([proposal 0019](https://subbusainath.github.io/ouncemcp/proposals/0019-eval-kit/)).
- **Composite tools:** `ctx.gather(...)` runs several upstream calls in one tool call. The example `inventory.cost_review` returns 651 tokens in 1 call where paging `inventory.search` takes 9,896 tokens over 8 calls.
- **Testing:** `ouncemcp.testing.as_caller(...)` runs code as a verified caller, and `ouncemcp.eval.run_security_suite(server, cases)` probes for cross-tenant leaks, guessed names, cursor and handle reuse, replay, token passthrough and SSRF.
- **Budget checks:** `ouncemcp check server.py` counts the tokens of every tool definition as the host receives it, and fails CI when a tool goes over budget or a lint error is found. It also reports which discovery mode the server will use and why. Every lint and error links to a page explaining it.

```bash
ouncemcp check examples/inventory/server.py
ouncemcp context examples/inventory/server.py inventory.search
ouncemcp context examples/inventory/server.py --html context.html   # every tool, as a page
ouncemcp dev examples/inventory/server.py      # hot reload, live per-call token lines
```

Counts use `o200k_base` by default. Choose another tiktoken encoding with `OunceServer(tokenizer="cl100k_base")` or `ouncemcp check --tokenizer cl100k_base`, or pass your own counting function with `tokenizer=fn, tokenizer_name="my-tokenizer-v1"`. Every report names the tokenizer it used ([proposal 0004](https://subbusainath.github.io/ouncemcp/proposals/0004-pluggable-tokenizer/)).

`ouncemcp audit` is the optional command for measuring any server. `ouncemcp eval` runs task suites, with the replay model or real models through OpenRouter, `ouncemcp bench` compares servers across models, and `ouncemcp trace` reads traces.

## Benchmark (M3)

Two pilot servers against their plain FastMCP equivalents: four models (Claude Haiku 5.5, Claude Sonnet 5.5, GPT-5.4 mini, DeepSeek V4.1 Flash), 21 tasks, 5 runs each, success judged on outcomes. See the [full results and caveats](https://github.com/subbusainath/ouncemcp/blob/main/benchmarks/results/m3-bench.md).

| | Inventory (large results) | Ticketing (writes, small results) |
| --- | --- | --- |
| Tokens per task, OunceMCP vs plain | 58–85% fewer | 17–40% more |
| Task success, OunceMCP vs plain | 100% vs 75–100% | 100% vs 68–70% |
| Tool definitions, OunceMCP vs plain | 584 vs 244 tokens | 649 vs 366 tokens |

On these small servers the definitions are larger, mostly from output schemas. PERF-6, revised for small servers (definitions at most 2× plain, no drop in success), is met for ticketing and missed for inventory at 2.4× ([proposal 0027](https://subbusainath.github.io/ouncemcp/proposals/0027-benchmark/)). On the plain ticketing server every model skipped the approval, deleted without permission and read another team's ticket.

## M0 results

Measured on the inventory example with the `o200k_base` tokenizer. Full method and caveats are in [docs/m0-report.md](https://subbusainath.github.io/ouncemcp/m0-report/).

| | OunceMCP | Plain FastMCP |
| --- | ---: | ---: |
| Tool definitions (2 tools) | 488 tokens | 195 tokens |
| Four-task session, definitions plus results | 1,510 tokens | 32,280 tokens |
| Added server time per call, p50 / p95 | 0.14–0.44 ms / 0.07–0.47 ms (1.44 / 1.32 ms when truncating) | baseline |

In M1, results that are trimmed must still match the output schema. Truncated calls now add 0.70 / 0.80 ms (p50 / p95) and other calls 0.14 to 0.31 ms. Shaping a 36,000-token result down to a 2,000-token budget takes 7.3 ms. See [proposal 0002](https://subbusainath.github.io/ouncemcp/proposals/0002-schema-safe-shaping-and-pagination/) and `benchmarks/scripts/m1_truncation.py`.

Typed output schemas make definitions larger. Results are where the savings come from. Output schemas can be set to `"on"` (default), `"off"` or `"auto"` for a server (`output_schemas=`) or one tool (`output_schema=`). In M1, `"off"` gave 247 tokens for this example, and `"auto"`, which drops a schema only where it would put a definition over the per-tool target, gave 378. `ouncemcp check` reports the output-schema share of definition tokens (241 of 504 in the current example) and states which fields it counts: `name`, `description`, `inputSchema` and `outputSchema`, not `title`, `annotations` or `_meta` ([proposal 0005](https://subbusainath.github.io/ouncemcp/proposals/0005-counting-scope-and-output-schema-modes/)).

## Repository layout

```text
src/ouncemcp/
  core/        capabilities, budgets, response shaping, token accounting, errors, lints,
               resources and prompts, config file
  security/    identity, policy, audience and passthrough, caches, bound state, HTTP
               defaults, stdio policy, URL checks
  discovery/   direct and search discovery modes
  execution/   deadlines, retries, limits, middleware, store, idempotency, approvals,
               handles and jobs
  trace/       per-call traces, redaction and metrics
  eval/        conformance target, security test catalog and agent task suites
  cli/         init, dev, check, context, audit, eval, trace (bench planned)
  client/      reference client broker
  audit/       optional audit command
  testing.py   as_caller and make_token for tests
examples/      pilot servers: inventory, ticketing, report, markitdown migration
benchmarks/    servers, tasks, scripts, published results
docs/          specification, research, concepts, guides, proposals
tests/
```

## Roadmap

| Milestone | Scope | Status |
| --- | --- | --- |
| M0: core spike | `OunceServer`, typed tools with effects, token counting, result budgets, one example server | Built ([M0 report](https://subbusainath.github.io/ouncemcp/m0-report/)) |
| M1: v0.1 | Framework core, automatic discovery, errors, security, `init`/`dev`/`check`/`context`, docs | Built |
| M2: v0.2 | Safe writes, client broker, migration guide, pilots, optional `audit` | Built ([M2 report](https://subbusainath.github.io/ouncemcp/m2-report/)) |
| M3: v0.5 | Eval kit, trace viewer, public benchmark, launch | Built ([benchmark](https://github.com/subbusainath/ouncemcp/blob/main/benchmarks/results/m3-bench.md)); launch waits on the repository going public |
| M4: v1.0 | Security review, host compatibility matrix, stable API, the book | API frozen ([0029](https://subbusainath.github.io/ouncemcp/proposals/0029-api-freeze/)), all release gates checked, release workflow ready ([M4 report](https://subbusainath.github.io/ouncemcp/m4-report/)); waiting on the external security review |

The full plan is in [docs/spec.md](https://subbusainath.github.io/ouncemcp/spec/), and the supporting research is in [docs/performance-research.md](https://subbusainath.github.io/ouncemcp/performance-research/).

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
uv pip install -e ".[docs]" && uv run mkdocs serve   # documentation site
python benchmarks/scripts/m0_compare.py     # token comparison
python benchmarks/scripts/m0_overhead.py    # per-call overhead
```

## Contributing

See [CONTRIBUTING.md](https://github.com/subbusainath/ouncemcp/blob/main/CONTRIBUTING.md). Security issues: see [SECURITY.md](https://github.com/subbusainath/ouncemcp/blob/main/SECURITY.md).

## License

[Apache-2.0](https://github.com/subbusainath/ouncemcp/blob/main/LICENSE)
