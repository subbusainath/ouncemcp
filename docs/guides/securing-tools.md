# Securing tools

A checklist for a server that others will call.

1. **Authenticate on HTTP.** Configure a FastMCP auth provider (`OunceServer(..., auth=...)` passes through to FastMCP) and set `audience=` to the server's own resource URL, so tokens for other services are refused.
2. **Put a policy on every write.** `policy=writes_require(authenticated)` on the server, and `require_scopes(...)` or `require_tenant(...)` on sensitive tools. `ouncemcp check` warns about destructive tools without one.
3. **Use your own upstream credentials.** Install `passthrough_guard()` on your HTTP client so the caller's MCP token can never be forwarded:
   ```python
   upstream = httpx.AsyncClient(event_hooks={"request": [passthrough_guard()]})
   ```
4. **Cache through `ctx.cache`**, never a module-level dict keyed by arguments alone.
5. **Hand out handles and approvals with `ctx.issue_state`**, and accept them with `ctx.verify_state(..., consume=True)` for approvals.
6. **Ask before you act.** Call `ctx.require_input` before any irreversible effect; mark the effect with `ctx.effect_applied()`.
7. **Set `OUNCEMCP_CURSOR_KEYS`** on every replica; cursors and state tokens are signed with it.
8. **Run the security catalog** in your tests ([testing](testing.md)).
