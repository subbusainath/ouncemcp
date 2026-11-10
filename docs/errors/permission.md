# `permission`: Not permitted

The caller is known and not allowed to do this. Never retry with broader authority.

Results in this category have `isError: true`, start with `[ouncemcp] Not permitted:`, and carry `_meta.ouncemcp.category = "permission"` and `retryable = false`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `permission_denied` | A FastMCP authorization check or a Python `PermissionError` refused the call. | Ask for access, or use a tool you are allowed to call. |
| `upstream_forbidden` | The upstream service answered 403. | The server's upstream account lacks permission; tell the server owner. |
| `token_passthrough` | The server tried to send the caller's MCP access token to an upstream service, and OunceMCP blocked it (spec SEC-4). | Server authors: use the server's own upstream credentials, never the inbound token. |
| `permission` | A handler raised `PermissionDenied`. | Follow the message. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
