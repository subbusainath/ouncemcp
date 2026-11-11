# M2 report: Framework 0.2

M2 was planned in [proposal 0010](proposals/0010-m2-plan.md) and built in seven steps, each with its own proposal (0011 to 0017). This report checks the M2 exit criteria from spec section 15 and release gates 1 to 5 (section 14.2). Numbers are from the test suite and the pilots, counted with `o200k_base`.

## What was built

| Area | Requirements | Proposal |
| --- | --- | --- |
| Shared store (memory, Redis, any py-key-value backend), idempotent writes, `ctx.deps` | EXE-4, SEC-6 (shared single use), spec 9.2 | [0011](proposals/0011-store-and-idempotency.md) |
| Safe writes: preview, approve, commit | Section 10, gate 4 | [0012](proposals/0012-safe-writes.md) |
| Results by reference and background jobs | RES-5 | [0013](proposals/0013-handles-and-jobs.md) |
| Resources and prompts | CAP-2 | [0014](proposals/0014-resources-and-prompts.md) |
| Migration adapter and guide, `ouncemcp audit`, URL checks | DX-7, AUD-1 to AUD-13 (HTML excepted), part of SEC-9 | [0015](proposals/0015-migration-and-audit.md) |
| Client broker | CLI-1 to CLI-8 | [0016](proposals/0016-client-broker.md) |
| `ouncemcp.toml`, health and readiness, metrics, description lints | Spec 9.2, 9.4, CAP-5 | [0017](proposals/0017-operations.md) |
| Policies over stdio (`stdio_policy`) | Decision 3 of proposal 0010 | [0010](proposals/0010-m2-plan.md) |

## Exit criteria

| Criterion | Result | Evidence |
| --- | --- | --- |
| Pilots pass release gates 1 to 5 | Gates 2 to 5 pass; gate 1 is pending (below) | Gate table |
| The migration guide works on one existing FastMCP server | Pass: markitdown-mcp, wrapped unchanged (step 1), then migrated | `examples/markitdown`, `tests/test_pilot_markitdown.py` |
| `audit` totals match `check` within 1% for the same server | Pass: 488 and 488 on the inventory example, in-process and over stdio | `tests/test_audit.py` |

## Release gates 1 to 5

| Gate | Result | Evidence |
| --- | --- | --- |
| 1. Conformance | **Pending.** The fixtures behave as the suite requires in-process, including the resource and prompt scenarios added in M2, but the official suite has not been run: the CI job is not required until its first run confirms `conformance/expected-failures.yml` | `tests/test_eval1_conformance_fixtures.py`, `.github/workflows/conformance.yml` |
| 2. Isolation | Pass | Security catalog on the ticketing pilot (cross-tenant, guessed names); handles and job ids refused for another caller (report pilot); idempotency keyed per tenant; `ctx.cache` partitions |
| 3. Writes | Pass | A create that times out after the upstream write produces one ticket, and the retry gets the stored result (the plain FastMCP version writes twice); concurrent duplicates run once; two replicas sharing a store write once |
| 4. Approvals | Pass | Approved writes run once after the person sees the preview; declines change nothing; receipts cannot be replayed or moved to other arguments; clients that cannot ask get `approval_unavailable`; enforced on stdio |
| 5. Budgets | Pass | Every example, the migration steps and a fresh `ouncemcp init` project pass `ouncemcp check`; the RES-8 fuzz and RES-9 property tests pass |

## Pilots

| Pilot | Key result |
| --- | --- |
| Ticketing (`examples/ticketing`) | No duplicate write on retry after a timeout; approval with the exact ticket shown; admin-only delete; 555 definition tokens against 283 for the plain version |
| Long-running report (`examples/report`) | `jobs.get` 168 tokens and a 50-row page 1,351, against 52,002 for all 2,000 rows; works across two replicas and a restart; a job lost in a restart reports `interrupted` |
| Migration of markitdown-mcp (`examples/markitdown`) | Result for a 60-section document from 14,760 tokens to 388 plus pages; metadata, loopback and private URLs refused; `file:` access opt-in and confined |

## Measured overhead

In-process calls through the full stack take about 0.22 ms p50 on the session machine; tracing adds 0.01 to 0.15 ms. This is not a benchmark (M3 has those).

## Known limits

- The official conformance suite has not been run (gate 1).
- Single-use receipts and idempotency hold across replicas only with a shared atomic store (`RedisStore`); `KeyValueStore` claims are atomic per process.
- `check_url` resolves names once and does not follow redirects for you; full SEC-9 stays in P1.
- Interrupted jobs must be started again; jobs are not mapped onto the MCP tasks extension.
- Five tests need network access to tiktoken's download host (the `gpt2` encoding and stdio subprocess tests) and fail in a sandbox without it.
- Host verification (Claude Desktop, VS Code, Claude mobile) is still to be done by the owner.
