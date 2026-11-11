# 0013: Results by reference and background jobs

Status: proposed for M2 (step 3 of [proposal 0010](0010-m2-plan.md)). Covers spec RES-5 and the long-running report pilot. Builds on the store from [proposal 0011](0011-store-and-idempotency.md).

## Problem

A large result could only be trimmed to the budget or refused. A slow tool held the call open until its deadline, and nothing let an agent come back for the answer later.

## Proposal

`OunceServer(handles=True)` registers two read tools: `handles.read(handle, cursor, limit)` (115 tokens) and `jobs.get(job)` (151 tokens). They are opt-in, so existing catalogs do not change.

- `await ctx.store_result(items, ttl_s=3600)` stores a list and returns a handle. `handles.read` pages it with the budget-aware pager, so a page never exceeds the result budget and cursors resume without gaps (RES-9).
- `await ctx.start_job(work, ttl_s=3600)` runs `work` in the background and returns a job id at once. `work` returns a summary (model or dict) or `JobResult(summary=..., items=[...])`; items are stored behind a handle. `jobs.get` reports `running`, `done` (summary, handle, item count), `failed` (category and message, never internal details) or `interrupted`.
- Handles and job ids are signed state tokens (SEC-6) bound to the caller and tenant, with an expiry. Another caller gets `invalid_state`.
- Data and job records live in the store, so with a shared store any replica serves them and they survive a restart. A job runs in the process that started it and renews a 30-second lease; if the process stops, the lease runs out and `jobs.get` reports `interrupted` with "start it again".

Measured on the report pilot: all 2,000 rows would cost 52,002 tokens; `jobs.get` costs 168 and one 50-row page 1,351.

## Alternatives considered

- **Spilling over-budget results to a handle automatically.** The result would no longer match the tool's output schema (RES-8). Authors return a handle field explicitly instead.
- **The MCP tasks extension for jobs.** It is an extension, not core 2026-07-28, and client support varies. Plain tools work with every client; mapping jobs onto tasks can come later.
- **Resuming interrupted jobs.** Needs durable work queues; out of scope for M2.

## Impact on tokens and task success

Two extra definitions (266 tokens) when enabled. Large results cost a summary plus the pages the agent actually reads.

## Migration notes

None; handles are off by default.
