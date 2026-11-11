# Examples

Pilot servers from the specification (docs/spec.md section 15.1). Each pilot is also built as a plain FastMCP server for comparison of lines of code, setup steps, tokens, turns, and task success.

| Directory | Pilot | Milestone |
| --- | --- | --- |
| `inventory/` | Read-only cloud inventory: pagination, credentials, bounded output | M1 |
| `ticketing/` | Protected ticketing workflow: preview and commit, approvals, idempotency (built) | M2 |
| `report/` | Long-running report: handles, ownership, expiry, multi-replica (built) | M2 |
| `markitdown/` | Migration of markitdown-mcp: wrap, then bound results and fetches (built) | M2 |

`ouncemcp.example.toml` shows the proposed configuration file.
