# Errors and execution

- **Eight error categories** with a `retryable` flag: validation, authentication, permission, not found, rate limit, transient, ambiguous write, internal. See the [error reference](../errors/validation.md).
- **No leaks.** Unexpected exceptions return a reference id only; author messages are redacted (ERR-3).
- **Deadlines.** Every call has one (default 30 s, `deadline_ms=` per tool) and is cancelled when it passes; `ctx.remaining()` gives the time left for upstream timeouts.
- **Retries.** Transient read failures are retried with bounded backoff inside the deadline. Writes never are; a write that times out is reported as `ambiguous_write`.
- **Limits.** Request size, nesting depth and calls in flight per caller (`Limits`).
- **Idempotent writes.** `@server.tool(effect=Effect.WRITE, idempotency=True)` runs a write at most once per caller, tenant and arguments (or client key in `_meta`), and replays the result to duplicates. A write that passes its deadline finishes in the background, so calling again returns its result instead of writing twice. Share `OunceServer(store=RedisStore(client))` across replicas ([proposal 0011](../proposals/0011-store-and-idempotency.md)).
- **Dependencies.** `OunceServer(deps=...)`, read as `ctx.deps`; `guarded_client(...)` builds an upstream HTTP client that can never forward the caller's token.
- **Composite tools.** `await ctx.gather({"a": step_a, "b": step_b})` runs several upstream calls concurrently inside the deadline and returns one result, so an agent makes one call instead of many (CAP-7). Example: `examples/inventory/workflow.py`.
- **Parallel-safe reads.** Read tools carry `_meta["ouncemcp/parallelSafe"]: true`, so clients can run independent reads together; set `parallel_safe=False` on a read that depends on call order. Writes are never marked ([proposal 0022](../proposals/0022-parallel-safe-tools.md)).
- **Asking for input.** `ctx.require_input(requests, state=...)` returns an MCP `input_required` result; `ctx.input_responses` and `ctx.resume_state()` read the answer.

Details: [proposal 0007](../proposals/0007-errors-and-execution-limits.md).
