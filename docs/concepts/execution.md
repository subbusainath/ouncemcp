# Errors and execution

- **Eight error categories** with a `retryable` flag: validation, authentication, permission, not found, rate limit, transient, ambiguous write, internal. See the [error reference](../errors/validation.md).
- **No leaks.** Unexpected exceptions return a reference id only; author messages are redacted (ERR-3).
- **Deadlines.** Every call has one (default 30 s, `deadline_ms=` per tool) and is cancelled when it passes; `ctx.remaining()` gives the time left for upstream timeouts.
- **Retries.** Transient read failures are retried with bounded backoff inside the deadline. Writes never are; a write that times out is reported as `ambiguous_write`.
- **Limits.** Request size, nesting depth and calls in flight per caller (`Limits`).
- **Composite tools.** `await ctx.gather({"a": step_a, "b": step_b})` runs several upstream calls concurrently inside the deadline and returns one result, so an agent makes one call instead of many (CAP-7). Example: `examples/inventory/workflow.py`.
- **Asking for input.** `ctx.require_input(requests, state=...)` returns an MCP `input_required` result; `ctx.input_responses` and `ctx.resume_state()` read the answer.

Details: [proposal 0007](../proposals/0007-errors-and-execution-limits.md).
