# `rate_limit`: Rate limited

A limit was hit. The same call can succeed later. `retry_after_s` says when, if known.

Results in this category have `isError: true`, start with `[ouncemcp] Rate limited:`, and carry `_meta.ouncemcp.category = "rate_limit"` and `retryable = true`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `too_many_concurrent_calls` | This caller already has `Limits.max_concurrency` calls in progress. | Wait for one to finish, then retry. |
| `upstream_rate_limited` | The upstream service answered 429. `Retry-After` is passed on as `retry_after_s`. | Wait `retry_after_s` seconds, then retry. |
| `rate_limit` | A handler raised `RateLimited`. | Wait, then retry. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
