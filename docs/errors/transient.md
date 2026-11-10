# `transient`: Temporary failure

A temporary problem. Reads were already retried inside the deadline (spec EXE-2); a later call may succeed.

Results in this category have `isError: true`, start with `[ouncemcp] Temporary failure:`, and carry `_meta.ouncemcp.category = "transient"` and `retryable = true`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `deadline_exceeded` | A read did not finish within its deadline and was cancelled. | Try again, or narrow the request. |
| `upstream_unreachable` | The upstream service could not be reached and the request was not sent, so even a write did not happen. | Try again later. |
| `transient` | A handler raised `TransientError`. | Try again later. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
