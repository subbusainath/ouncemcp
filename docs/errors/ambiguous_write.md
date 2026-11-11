# `ambiguous_write`: Write status unknown

A write or destructive call timed out or lost its connection after the request was sent. It may or may not have been applied (spec EXE-3).

Results in this category have `isError: true`, start with `[ouncemcp] Write status unknown:`, and carry `_meta.ouncemcp.category = "ambiguous_write"` and `retryable = false`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `write_status_unknown` | Deadline or transport failure after sending a write. | Check the current state before trying again. Do not repeat the write blindly. |
| `write_still_running` | An idempotent write passed its deadline and is still finishing in the background (proposal 0011). | Call again with the same arguments: you get its result once it finishes, and it will not run twice. |
| `ambiguous_write` | A handler raised `AmbiguousWrite`. | Check the current state first. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
