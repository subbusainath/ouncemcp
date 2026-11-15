# 0028: Jobs that resume after a restart

Status: proposed for M4. Completes the jobs work from [proposal 0013](0013-handles-and-jobs.md).

## Problem

A background job runs in the process that started it. If that process stops, the job's lease runs out and `jobs.get` reports `interrupted`, and the caller has to start the work again by hand. The job was a Python closure, so no other replica could run it.

## Proposal

**Named jobs.** A job registered by name, with its arguments stored as JSON, can be run by any replica:

```python
@server.job("report.build", max_attempts=3)
async def build_report(args: StartInput) -> JobResult:   # or (args, attempt)
    ...

job = await ctx.start_job("report.build", args.model_dump(mode="json"))
```

- **Resuming.** The next `jobs.get` after the lease runs out, on any replica sharing the store, starts the job again from the beginning and reports `running` with `attempt: 2`.
- **One restart per attempt.** The store has no way to list keys, so jobs resume when polled rather than at startup. A claim per attempt (`put_if_absent` on `job-claim/<id>#<attempt>`) means two replicas polling at the same moment start it once.
- **Attempt limit.** After `max_attempts` runs (default 3), `jobs.get` reports `interrupted` with how many attempts were used.
- **Repeatable work only.** A named job must be safe to run more than once: reports, exports, reads. Writes need their own idempotency.
- **Checks at start.**
    - Arguments must be JSON (they are stored); anything else is refused when the job starts.
    - Unknown job names are refused.
    - A job's first parameter, if annotated with a Pydantic model, receives a validated model.
- **Closure jobs** (`ctx.start_job(work)`) behave as before and are reported `interrupted`.
- **The report pilot** now uses a named job, and its test checks that a report lost in a restart is built again on another replica.

## Not included: MCP tasks

MCP's tasks extension (SEP-2663, `io.modelcontextprotocol/tasks`) lets a client run any tool call as a task and poll it. FastMCP supports it through the separate `fastmcp-tasks` package, which OunceMCP does not depend on. Mapping jobs onto MCP tasks would make `jobs.get` unnecessary for clients that support tasks. It needs a decision on adding that dependency, so it is left for the owner.

## Alternatives considered

- **Resuming at startup.** It needs a store that can list keys, which `KeyValueStore` backends do not promise. Resuming on poll needs no new store capability, and the caller is waiting for exactly that job anyway.
- **Checkpointing inside jobs.** It would let long jobs continue rather than restart, but it needs an API for jobs to save progress. It can come later if real jobs are long enough to need it.

## Impact on tokens and task success

`jobs.get` gains an optional `attempt` field (a few output-schema tokens). Callers no longer have to start interrupted reports again themselves.

## Migration notes

None. Existing closure jobs work unchanged. Move a job to `@server.job` to make it resumable.
