# Safe writes

Three settings make a write tool safe to hand to an agent:

```python
@server.tool(
    name="tickets.close",
    effect=Effect.WRITE,
    approval="required",          # a person says yes first; or a function: lambda r: r.arguments["amount"] > 1000
    preview=close_preview,        # what the person sees; same signature as the handler, read-only
    idempotency=True,             # a retried write is replayed, never repeated
)
```

- **Approvals** are asked before the handler runs, through the client (MCP 2026-07-28 `input_required`, or elicitation on older clients). Clients that cannot ask are refused unless you set `OunceServer(approver=...)`. The model cannot approve by itself. See [proposal 0012](../proposals/0012-safe-writes.md).
- **Idempotency** keys each write by caller, tenant and arguments (or a client key). A write that passes its deadline finishes in the background, and calling again returns its result. See [proposal 0011](../proposals/0011-store-and-idempotency.md).
- **Replicas** share state through `OunceServer(store=RedisStore(redis.asyncio.Redis(...)))`.

A complete example is the [ticketing pilot](https://github.com/subbusainath/ouncemcp/tree/main/examples/ticketing).
