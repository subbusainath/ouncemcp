# `not_found`: Not found

The thing asked for does not exist, or the caller is not allowed to see it. The two cases look the same on purpose (spec DISC-3).

Results in this category have `isError: true`, start with `[ouncemcp] Not found:`, and carry `_meta.ouncemcp.category = "not_found"` and `retryable = false`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `unknown_tool` | No tool by that name is visible to this caller. | List or search the tools again. |
| `upstream_not_found` | The upstream service answered 404 or 410. | Check the id, or search for the item first. |
| `not_found` | A handler raised `NotFound`. | Follow the message. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
