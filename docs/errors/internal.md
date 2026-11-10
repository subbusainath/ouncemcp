# `internal`: Internal error

Something unexpected failed inside the server. No details are shown to the caller (spec ERR-3); the full exception is in the server log under the reference id.

Results in this category have `isError: true`, start with `[ouncemcp] Internal error:`, and carry `_meta.ouncemcp.category = "internal"` and `retryable = false`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `internal_error` | An unexpected exception, including a write tool that asked for input after an effect (spec SEC-7). | Callers: report the reference id to the server owner. Server owners: search the log for `ref <id>`. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
