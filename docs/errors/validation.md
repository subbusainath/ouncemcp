# `validation`: Invalid input

The request cannot succeed as sent. Retrying the same call will fail the same way.

Results in this category have `isError: true`, start with `[ouncemcp] Invalid input:`, and carry `_meta.ouncemcp.category = "validation"` and `retryable = false`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `invalid_input` | Arguments do not match the tool's input schema. The message lists up to three fields and how to fix each, without repeating the values sent. | Fix the named fields and call again. |
| `request_too_large` | The arguments are larger than `Limits.max_request_bytes`. | Send less data, or raise the limit on the server. |
| `request_too_deep` | The arguments are nested deeper than `Limits.max_depth`. | Flatten the input. |
| `invalid_cursor` | A pagination cursor was malformed, expired, modified, or issued for another tool, caller or set of filters. `reason` says which. | Pass `next_cursor` back unchanged, or omit `cursor` to start over. |
| `invalid_state` | A handle, approval or continuation token was malformed, expired, replayed, or issued to another caller or for another request (spec SEC-6). `reason` is not exposed to keep tokens opaque; the message says what to do. | Start the operation again to get a new token. |
| `result_too_large` | Even the smallest valid result is over the tool's result budget. `reason` is `item_over_budget`, `min_items_over_budget` or `fixed_over_budget`. | Narrow the request, lower `limit` when the message suggests it, or use `response_format=concise`. Server authors: raise `result_tokens` or return smaller items. |
| `upstream_rejected_input` | The upstream service answered 400 or 422. | Check the arguments against the tool description. |
| `validation` | A handler raised `InvalidInput` without a code. | Follow the message. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
