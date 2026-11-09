# M0 report: core prototype

Milestone M0 checks whether the core idea holds up: a thin layer over FastMCP that makes tools typed, effect-declared, and context-budgeted, without slowing the server down.

## What was built

| Area | Module | Notes |
| --- | --- | --- |
| Server and tool decorator | `core/server.py` | `OunceServer`, `@server.tool(name=..., effect=...)`, sync and async handlers, optional `ToolContext` |
| Effects | `core/effects.py` | `READ`, `WRITE`, `DESTRUCTIVE`, mapped to MCP tool annotations |
| Budgets | `core/budget.py` | Per-tool definition target 250, warn 500, fail 1,000; startup 4,000; result 2,000; detailed ceiling 8,000 |
| Token accounting | `core/tokens.py`, `core/costs.py` | `o200k_base` via tiktoken; per-tool breakdown that sums to the total |
| Schema compaction | `core/schema.py` | Optional fields advertised without `anyOf`/`null`; titles removed |
| Response shaping | `core/shaping.py` | `Detail` fields, concise/detailed formats, list then string truncation, truncation notes |
| Wire listing | `core/wire.py` | Definitions are measured through an in-memory MCP client |
| CLI | `cli/` | `ouncemcp check` and `ouncemcp context` |
| Example | `examples/inventory/` | OunceMCP version and a plain FastMCP version over the same data |

## Exit criteria

| Criterion | Result |
| --- | --- |
| The example works in a host | Partly met. The server runs as a stdio subprocess and is driven by a real MCP client in `tests/test_stdio_e2e.py`. It has not yet been tried in Claude Desktop, VS Code, or another host app. |
| Token counts match a manual recount | Met. The stdio test re-tokenizes the definitions received over the wire and asserts they equal `cost_report()` exactly (488 tokens). |
| Overhead within PERF-1 (added p50 < 2 ms, p95 < 10 ms) | Met. See below. |

## Token results

Tokenizer `o200k_base`. Results come from `benchmarks/scripts/m0_compare.py` and are saved in `benchmarks/results/m0-inventory-tokens.json`.

| Task | OunceMCP | Plain FastMCP |
| --- | ---: | ---: |
| Tool definitions (startup) | 488 | 195 |
| List resources in ap-south-1 | 498 | 29,716 |
| Find degraded databases in ap-south-1 | 153 | 1,777 |
| Who owns resource da-00002? | 101 | 296 |
| Show config and metrics for da-00002 | 270 | 296 |
| Session total | 1,510 | 32,280 |

How to read this:

- The plain server is not a strawman. It has the same descriptions and returns the upstream records as they are, which is a common pattern.
- The responses are not identical. For "list resources", OunceMCP returns a page of 20 summaries with `total` and `next_cursor`, while the plain server returns all 100 full records. That is the design difference being measured: the agent gets what it needs to choose its next step, plus a way to get more.
- When the agent really does need everything (the last row), the two are about the same. The savings come from not sending detail by default, not from compression.
- Definitions are larger with OunceMCP: 488 tokens versus 195. About half of that (241 tokens) is output schemas. The rest is enums, constraints, field descriptions, and the `response_format` parameter. Definitions are paid once per session; results are paid on every call.

## Overhead results

Results come from `benchmarks/scripts/m0_overhead.py` (5,000 calls per case after 50 warm-up calls) and are saved in `benchmarks/results/m0-overhead.json`. The benchmark ran in-process through `FastMCP.call_tool`, on Python 3.12.15, x86_64, 2 vCPUs. The baseline is a plain FastMCP tool that runs the same handler and returns the same model.

| Case | OunceMCP p50 / p95 | Plain p50 / p95 | Added p50 / p95 |
| --- | --- | --- | --- |
| 5 items | 0.348 / 0.411 ms | 0.208 / 0.337 ms | +0.140 / +0.074 ms |
| 20 items | 0.541 / 0.577 ms | 0.301 / 0.437 ms | +0.240 / +0.140 ms |
| 50 items | 0.924 / 0.996 ms | 0.487 / 0.525 ms | +0.437 / +0.472 ms |
| 50 items truncated to 300 tokens | 1.930 / 2.176 ms | 0.490 / 0.857 ms | +1.440 / +1.319 ms |

Most of the added time is token counting. The truncation path is the most expensive because it binary-searches how many items to keep. It still stays under the 2 ms p50 target, but with little headroom, so M1 should cache the tokens per item instead of re-encoding the whole result on every step.

## Findings that changed the design

1. **Measure the wire, not the object.** The in-process tool object and the wire format list keys in a different order (`type` comes first on the wire), and that changed the count by one token per tool. The budget report now lists tools through an in-memory MCP client, so `check` matches what hosts receive.
2. **Pydantic's optional-field encoding is wasteful.** Compacting `anyOf: [X, null], default: null` to `X` cut definitions from 590 to 490 tokens with no change to validation. Explicit `null` arguments are still accepted.
3. **Output schemas are the biggest definition cost.** They are on by default, because typed results help clients and the MCP spec expects structured results to match them. `OunceServer(..., output_schemas=False)` turns them off (247 tokens for this example). M1 needs host data on whether models actually see output schemas.
4. **Truncation and pagination interact.** If the handler returns `next_cursor` for 50 items and the framework keeps 20, the cursor skips 30. The note now says so and tells the agent which `limit` to use. The example also caps `limit` at 50 so a full concise page fits. M1 should let handlers paginate based on the budget (for example a `ctx.fit()` helper).
5. **Never trim a list to zero.** A single huge item used to be dropped entirely. Lists now keep at least one item, and long strings are shortened instead.

## Known limitations

- **Text-only fallback.** When a result cannot fit even after trimming lists and strings, OunceMCP returns truncated text with no `structuredContent`. If an output schema is advertised, that breaks the MCP expectation that structured results match it. This must be fixed in M1, either by returning a tool error or by guaranteeing a minimal valid structure.
- **Tokenizer.** Counts use `o200k_base`. Other model tokenizers differ, so treat the numbers as a consistent relative measure, not an exact bill. Pluggable tokenizers are planned.
- **What the model sees is host-dependent.** Counts include the name, description, input schema, and output schema. They exclude `title`, annotations, and `_meta`, which hosts typically do not put in the prompt. This has not been verified per host.
- **Cursors are unsigned.** The example cursor is a base64 offset. M1 cursors must be signed and bound to the caller (spec, security section).
- **No discovery, approvals, client, or eval yet.** These are M1 to M3.

## Reproduce

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
python benchmarks/scripts/m0_compare.py
python benchmarks/scripts/m0_overhead.py 5000
```
