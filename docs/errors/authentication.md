# `authentication`: Authentication required

The caller has no valid credentials for this action.

Results in this category have `isError: true`, start with `[ouncemcp] Authentication required:`, and carry `_meta.ouncemcp.category = "authentication"` and `retryable = false`.

| Code (`_meta.ouncemcp.error`) | Cause | Fix |
| --- | --- | --- |
| `upstream_unauthenticated` | The upstream service answered 401 to the server's own credentials. | Server operators: refresh the upstream credentials. |
| `authentication` | A handler raised `AuthenticationRequired`. | Reauthorize through the client's normal sign-in flow. |

See [proposal 0007](../proposals/0007-errors-and-execution-limits.md) for how errors are classified.
