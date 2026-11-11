# Ticketing pilot

Protected ticketing workflow (pilot 2 in spec section 15.1, milestone M2): preview and commit, approvals, idempotency and tenant isolation.

| File | What it is |
| --- | --- |
| `server.py` | The OunceMCP version: `tickets.search`, `tickets.create`, `tickets.close`, `tickets.delete` |
| `plain_server.py` | The same workflow as a plain FastMCP server, with matching descriptions |
| `upstream.py` | A fake ticket API with configurable write latency, shared by both |

```bash
ouncemcp check examples/ticketing/server.py
python examples/ticketing/server.py          # stdio; acts for the demo tenant "acme"
```

## What the OunceMCP version guarantees

| Behavior | OunceMCP | Plain FastMCP |
| --- | --- | --- |
| A create that times out after the upstream wrote it, then is retried | One ticket; the retry gets the stored result | Two tickets |
| Closing a ticket | The user sees "Close ticket T-1: 'Printer on floor 3 is jammed'?" and must say yes | Runs immediately |
| Deleting a ticket | Needs the `tickets:admin` scope and the user's yes; hidden from everyone else | Anyone can call it |
| Another tenant's tickets | Never listed; closing one by id gives `not_found` | Hard-coded single tenant |
| A client that cannot ask the user | Close and delete are refused (`approval_unavailable`) | Runs |
| Over stdio | Policies enforced (`stdio_policy="enforce"`); approvals always | No checks |

All of it is tested in `tests/test_pilot_ticketing.py`, including the duplicate-write baseline for the plain version.

## Cost

| Measure | OunceMCP | Plain FastMCP |
| --- | ---: | ---: |
| Lines of code (no blank lines, comments or docstrings) | 102 | 26 |
| Tool definition tokens (`o200k_base`) | 554 | 283 |
| Setup steps beyond `pip install` | none | none |

The extra definition tokens are mostly output schemas (223 of 554); `output_schemas="off"` removes them. The extra lines are typed input and output models, the tenant check, and the two preview functions. Turns and task success with a real model are measured in M3.

## Not covered yet

- Approvals for clients older than MCP 2026-07-28 use direct elicitation, which is only checked against FastMCP's behavior, not a real older host.
- Replicas need `OunceServer(store=RedisStore(client))` for idempotency and single-use receipts to hold across processes.
