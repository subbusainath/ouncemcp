# Security and policy

All of this is in `ouncemcp.security`. Design: [proposal 0008](../proposals/0008-security-and-policy.md).

| Requirement | What OunceMCP does |
| --- | --- |
| SEC-1 Verified identity | `ctx.identity` comes only from the verified access token: `principal` (issuer and subject), `tenant`, `scopes`, `claims`. `clientInfo` is never used |
| SEC-2 Policy on lists and calls | `policy=` on the server and on tools; one decision hides the tool from lists and search and refuses calls |
| SEC-3 Annotations are hints | Policies see the effect declared at registration, never MCP annotations |
| SEC-4 Audience, no passthrough | `OunceServer(audience=...)` checks `aud`/`resource`; `check_outbound` and `passthrough_guard()` block forwarding the caller's token |
| SEC-5 Partitioned caches | `ctx.cache` is keyed by principal, tenant and scopes |
| SEC-6 Bound state | `ctx.issue_state` / `ctx.verify_state`: tokens bound to caller, request and expiry; `consume=True` for single use |
| SEC-7 Replay-safe effects | `ctx.require_input` refuses to ask after `ctx.effect_applied()` unless the tool is `replay_safe` |
| SEC-8 HTTP defaults | Bind `127.0.0.1`, validate `Host` and `Origin`; public binds need `allowed_hosts` |
| SEC-11 Trace redaction | See [traces](traces.md) |
| SEC-12 Signed cursors | See [shaping](shaping.md) |

```python
from ouncemcp.security import authenticated, require_scopes, writes_require

server = OunceServer(
    "tickets",
    audience="https://mcp.example.com",
    policy=writes_require(authenticated),
)

@server.tool(name="tickets.purge", effect=Effect.DESTRUCTIVE, policy=require_scopes("admin"))
async def purge(...) -> Purged: ...
```

Policies apply over HTTP and to in-process calls. Over stdio FastMCP skips component authorization, because the server runs as the local user who started it, so every tool is visible and callable there.

Test policies with `ouncemcp.testing.as_caller(subject=..., tenant=..., scopes=[...])`, and probe a whole server with the [security catalog](../guides/testing.md).
