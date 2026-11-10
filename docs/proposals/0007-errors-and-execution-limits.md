# 0007: Error categories, deadlines, bounded retries and request limits

Status: proposed for M1. Covers spec ERR-1 to ERR-3, EXE-1, EXE-2, EXE-3 and EXE-6 (see [spec section 8.1.5](../spec.md)). EXE-4 (idempotency) and EXE-5 (parallel-safe metadata) are not part of this proposal.

Numbered 0007 because 0003 to 0006 are in open pull requests.

## Problem

M0 let handler exceptions through as FastMCP formats them. That has four problems:

- The text of any exception goes to the model. In a quick check, a handler that raised `RuntimeError("db at 10.0.0.5 password=hunter2")` sent exactly that string to the client, and argument validation errors included the values sent and a pydantic documentation link.
- There is no way to tell a retryable failure from a permanent one, so an agent either gives up on blips or repeats calls that cannot work.
- `deadline_ms` was optional and only handed to FastMCP, so most calls had no deadline, and a timeout looked the same for a read and a write.
- Nothing bounded the size or nesting of a request, or how many calls one caller could have running.

## Proposal

### Categories (ERR-1)

Every failed call returns `isError: true` with text `[ouncemcp] <Label>: <message> [<hint>]` and `_meta.ouncemcp`:

```json
{"error": "upstream_rate_limited", "category": "rate_limit", "retryable": true, "retry_after_s": 7}
```

| Category | Label | `retryable` | Typical source |
| --- | --- | --- | --- |
| `validation` | Invalid input | no | bad arguments, oversized or over-nested request, upstream 400/422 |
| `authentication` | Authentication required | no | `AuthenticationRequired`, upstream 401 |
| `permission` | Not permitted | no | `PermissionDenied`, `PermissionError`, FastMCP authorization errors, upstream 403 |
| `not_found` | Not found | no | `NotFound`, unknown tool, upstream 404/410 |
| `rate_limit` | Rate limited | yes | `RateLimited(retry_after=...)`, upstream 429 (reads `Retry-After`), too many concurrent calls |
| `transient` | Temporary failure | yes | `TransientError`, deadline on a read, connection refused, upstream 502/503/504 |
| `ambiguous_write` | Write status unknown | no | deadline or post-send transport failure on a write or destructive tool, `AmbiguousWrite` |
| `internal` | Internal error | no | anything else |

`error` is a finer code inside the category, such as `deadline_exceeded`, `write_status_unknown`, `request_too_large` or `too_many_concurrent_calls`. The existing `invalid_cursor` and `result_too_large` results now carry `category: "validation"` as well.

Tool authors raise `InvalidInput`, `AuthenticationRequired`, `PermissionDenied`, `NotFound`, `RateLimited`, `TransientError` or `AmbiguousWrite` (all subclasses of `OunceError`) from handlers. Errors from HTTP client libraries are recognized by shape (a `response.status_code`, or class names such as `ConnectError` and `ReadTimeout`), so OunceMCP needs no HTTP client dependency.

A failure before a request is sent (connection refused) is `transient` even for a write, because the write cannot have happened. A timeout or reset after sending is `ambiguous_write` for a write and `transient` for a read.

### Messages (ERR-2 and ERR-3)

- **Validation** messages say how to fix the input and never repeat the values sent: ``Fix the arguments and call again: `limit` must be at most 100; `mode` must be one of: fast or slow.`` At most three problems are listed, then a count. `Limits.max_error_chars` (default 300) caps every message.
- **Unexpected exceptions** are `internal`. Their text is not returned at all. The caller gets a generic message and a reference id, and the full exception goes to the `ouncemcp` logger at ERROR under the same reference.
- **Messages and hints written by tool authors** pass through `redact()`: stack traces, URLs, IPv4 and IPv6 addresses, `host:port`, internal and common public hostnames, file paths, `Bearer` tokens, `key=value` credentials, well-known token shapes and long opaque strings. This is best effort, so it is a second line of defense and not the guarantee. The guarantee is that unexpected exceptions are never shown.
- Upstream HTTP errors are classified by status and never forwarded.

### Deadlines and cancellation (EXE-1)

- Every call has a deadline: the tool's `deadline_ms`, else `Limits.default_deadline_ms` (30 s). `deadline_ms` must be a positive integer.
- OunceMCP enforces the deadline itself and no longer passes it to FastMCP, so the error is categorized like any other. Past the deadline the handler is cancelled.
- Cancelling the call, which is what a client does when it cancels a request, reaches the handler as an ordinary cancellation, so awaits on upstream calls stop with it. `ctx.remaining()` returns the seconds left, for passing to upstream timeouts, and `ctx.attempt` the try number.
- Synchronous handlers run in a worker thread that is abandoned when the deadline passes, because a thread cannot be cancelled. The call returns on time and the thread finishes on its own. Prefer `async def` for handlers that call remote services.
- Cancellation is not rollback. A handler must not assume that `finally` blocks undo the work it started.

### Retries (EXE-2)

`RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=2.0, jitter=0.2)` on the server, `retry=RetryPolicy(max_attempts=1)` to turn it off.

- Only tools declared `Effect.READ` are retried, and only after `transient` or `rate_limit` failures. Writes are never retried automatically.
- Backoff doubles from `base_delay` up to `max_delay`, with jitter. An upstream `Retry-After` replaces the backoff.
- All tries share the one deadline. If the wait for the next try would not end before the deadline, the failure is returned at once instead of sleeping. A `retry_after_s` longer than the deadline is reported to the agent rather than waited for.

### Ambiguous writes (EXE-3)

A deadline or post-send transport failure on a `write` or `destructive` tool returns `ambiguous_write` with `write_status_unknown`: the write may or may not have been applied, so check state before trying again.

There is no idempotency store yet (EXE-4). The seam for one is `OunceServer(write_resolver=...)`, an object with `async resolve(WriteAttempt) -> BaseModel | dict | None`. `WriteAttempt` has `tool`, `principal`, `arguments` and `reason`. If the resolver returns the result, the call succeeds with `_meta.ouncemcp.resolved_by = "write_resolver"`. If it returns `None`, raises, returns something that does not fit the output model, or takes longer than 2 seconds, the write stays ambiguous. The resolver is consulted only for ambiguous writes.

### Limits (EXE-6)

`OunceServer(limits=Limits(...))`:

| Limit | Default | When exceeded |
| --- | --- | --- |
| `max_request_bytes` | 65,536 (JSON-encoded arguments) | `validation` / `request_too_large` |
| `max_depth` | 16 levels of objects and lists | `validation` / `request_too_deep` |
| `max_concurrency` | 8 calls in flight per principal | `rate_limit` / `too_many_concurrent_calls`, `retry_after_s: 1` |
| `default_deadline_ms` | 30,000 | see above |
| `max_error_chars` | 300 | message is cut |

Requests are checked in middleware, before FastMCP validates them and before any handler runs. Depth is counted without recursion, so hostile input cannot exhaust the stack. Calls over the concurrency limit are refused immediately rather than queued, so one noisy caller cannot tie up workers. The caller is the one SEC-12 already resolves for cursors: the access token's subject (else client id), or `anonymous` without authentication, as on stdio. A server that authenticates in another way passes `principal=` to `OunceServer`, and that one function names the caller for cursors, the concurrency limit and the write resolver.

Output size is bounded by the existing result budget and hard ceiling (RES-3, RES-8), which apply to what a handler returns. Memory a handler uses to build a large result is not bounded.

## Alternatives

- **Let FastMCP's `mask_error_details` hide errors.** It hides all details, including messages meant for the agent, and does not categorize anything.
- **Classify by message text.** Fragile. Types, status codes and explicit error classes are used instead.
- **Queue calls over the concurrency limit.** Hides overload and holds connections. Refusing with a retry hint is simpler for agents to act on.
- **Retry writes when an idempotency key exists.** That is EXE-4 and belongs with its store.

## Impact

- Calls that raise now return an error result instead of raising out of `server.call_tool`. Through a client, `call_tool` still raises `ToolError` unless `raise_on_error=False`.
- Existing tools gain a 30 s deadline, which is a behavior change for tools that ran longer. Set `deadline_ms` on them.
- Per-call overhead on the M0 benchmark, from 2,000 calls: +0.11 to +0.28 ms p50 for results that fit and +0.57 ms when truncating (M1 before this change: +0.14 to +0.31 and +0.70, on a different run), so no measurable change.

## Migration

- Raise `OunceError` subclasses instead of letting raw exceptions out when the agent should see a message.
- Handlers that call remote services should be `async def`, use `ctx.remaining()` for upstream timeouts, and not catch `CancelledError` without re-raising it.

## Not covered

- EXE-4 idempotency keys and storage (M2). EXE-5 parallel-safe metadata: no client convention exists yet, so it needs its own design.
- Tracing and metrics by error category (spec 9.4).
- Limits on memory or time spent building a result inside a handler.
- Per-principal request rates, as opposed to concurrency.
- Redaction of tool results, as opposed to errors.
