# 0008: Verified identity, policy, bound state and safe HTTP defaults

Status: proposed for M1. Covers spec SEC-1 to SEC-8 (see [spec section 8.2](../spec.md)). SEC-11 (trace redaction) is in [proposal 0009](0009-traces.md). SEC-9 (SSRF) and SEC-10 (OAuth integrations) are P1 and not part of this proposal.

## Problem

Until now, `auth=` was passed through to FastMCP unchanged. That gave visibility and call checks (DISC-2, DISC-3), but nothing else in section 8.2:

- The cursor principal came from the token subject alone. Subjects are only unique per issuer, so two identity providers could produce the same caller.
- Checks received the FastMCP component, so a check could read `annotations`, which are hints that a proxy or client can change.
- Nothing checked that a token was issued for this server, and nothing stopped a handler from forwarding the caller's token upstream.
- Handlers had no cache that is safe to share between tenants, and no way to hand out handles or approvals that only work for the caller and request they were issued for.
- Nothing stopped a write tool from asking for input (MCP `input_required`) after it had already changed something, although the client answers by sending the whole request again.
- FastMCP leaves `Host` and `Origin` validation off by default.

## Proposal

All of it is in the new `ouncemcp.security` package and wired into `OunceServer` and `ToolContext`. Servers that set none of the new options behave as before, apart from the HTTP defaults and the issuer in the principal.

### Identity (SEC-1)

`ctx.identity` is an `Identity` built only from the verified access token: `principal`, `subject`, `issuer`, `client_id`, `tenant`, `scopes`, `audiences`, `claims`, `authenticated`. The raw token is not kept. `principal` is `issuer|subject` when the token names an issuer, else the subject, else the client id. The tenant comes from the first of `tenant`, `tenant_id`, `tid`, `org_id` that the token carries (`OunceServer(tenant_claims=...)` changes the list). `clientInfo` and arguments are never read. Without a verified token the identity is anonymous.

The default cursor and concurrency principal now uses the same rule, so it gains the issuer prefix. Cursors issued before the upgrade fail with `reason: principal` and the agent starts over, which cursors already allow.

### Policy (SEC-2, SEC-3)

A policy is a function from `PolicyRequest(tool, effect, tags, identity)` to `True` or `False`, sync or async. `OunceServer(policy=...)` applies to every tool and `@server.tool(policy=...)` adds more; all must allow. Anything other than `True`, including an exception, denies.

The policies become one FastMCP auth check on the tool, so the same decision hides it from `tools/list` and search and refuses direct calls and `call_tool` by a guessed name. The effect and tags come from what was declared at registration, looked up by name, never from the component's annotations. A test relabels a destructive tool as read-only and access does not change.

Helpers: `allow_all`, `authenticated`, `require_scopes(...)`, `require_tenant(...)`, `writes_require(policy)`, `all_of(...)`, `any_of(...)`. `auth=` still works and runs after the policies.

### Token audience and passthrough (SEC-4)

`OunceServer(audience="https://mcp.example.com")` (or a list) refuses any token whose `aud` claim or RFC 8707 `resource` does not name the server, and refuses anonymous callers. A trailing `/` is ignored. A token without an audience is refused because it could be meant for any server. Refused callers see no tools, and calls fail as `not_found`, the same as for any hidden tool.

`check_outbound(headers)` raises `TokenPassthrough` (category `permission`, code `token_passthrough`) if any header value contains the inbound token, and logs which header. `passthrough_guard()` is the same check as an httpx request hook, so a server can install it once on its upstream client. The error tells the caller this is a server problem and does not repeat the token.

### Partitioned caches (SEC-5)

`ctx.cache` is a view of the server's `PartitionedCache` (`OunceServer(cache=...)`), namespaced by tool. Entries are keyed by a digest of the principal, tenant, scopes and whether the caller is authenticated, plus the handler's key. A tenant never gets another tenant's entry, and a caller whose scopes change gets fresh results. It is an in-memory LRU with expiry (default 60 s, 1,024 entries). Replicas do not share it, which is safe: the worst case is a miss.

### Bound state (SEC-6)

`ctx.issue_state(kind, data, request=..., ttl_s=...)` returns a token `b1.<payload>.<mac>`, and `ctx.verify_state(token, kind, request=..., consume=...)` returns the data or raises `InvalidState` (category `validation`, code `invalid_state`). The payload holds the kind, expiry (default 15 minutes), a nonce, digests of the principal and of `request` (any JSON value; default the tool name), and the data, which is signed but not encrypted and limited to 2 KB.

The MAC uses the cursor keys (`OUNCEMCP_CURSOR_KEYS`) under a separate domain, so key rotation works the same way and a cursor can never pass as state or the reverse. `consume=True` accepts a token once per process, which blocks replay of an approval. Replicas need a shared `used` store for the same guarantee; that store arrives with idempotency (EXE-4) in M2.

### Replay-safe effects (SEC-7)

`ctx.require_input(input_requests, state=...)` returns an MCP `input_required` result. The `state` is issued as bound state for this caller, tool and arguments, and `ctx.resume_state()` verifies it on the retry. `ctx.input_responses` holds the client's answers.

`ctx.effect_applied()` records that the call changed something. After it, `require_input` on a write or destructive tool raises `UnsafeInputRequest`, which the caller sees as an internal error with a reference id, unless the tool was registered with `replay_safe=True`. `ouncemcp check` reports the same pattern from the handler source as the `input-after-effect` error, and direct `elicit` calls in write tools as the `elicit-in-write` warning.

### HTTP defaults (SEC-8)

`server.run("http")` and the other HTTP transports bind to `127.0.0.1` and turn on FastMCP's `Host` and `Origin` guard (`host_origin_protection=True`). Binding any other address without `allowed_hosts` raises an error that says what to set. `host_origin_protection=False` is still accepted for deployments behind a proxy that does the check. `server.http_app()` returns the ASGI app with the guard on. A test sends a foreign `Origin` (403) and a foreign `Host` (421).

### Lints and documentation links (DX-3, DX-6)

`ouncemcp check` now runs three rules: `input-after-effect` (error, fails `check`), `elicit-in-write` (warning) and `destructive-without-policy` (warning). `check --json` has a `lints` list. Every finding and every error result (`_meta.ouncemcp.docs`) links to a page under `docs/errors/` or `docs/rules/`, and a test fails if a category, rule or emitted error code has no page or if a relative link in the docs is broken.

## Alternatives considered

- **Keep using FastMCP `auth=` alone.** It cannot see the declared effect, so "anyone may read, only admins may write" needs per-tool wiring, and checks are free to read annotations.
- **A separate policy middleware.** It would have to repeat FastMCP's visibility filtering for lists, search and `describe_tool`. Converting policies to an auth check reuses the path DISC-2 and DISC-3 already test.
- **Encrypted state tokens.** Not needed for binding and would hide nothing a server should send anyway. Signed is smaller and simpler.

## Impact on tokens and task success

Tool definitions do not change. Error results gain a `docs` URL in `_meta`, which hosts do not show the model (BUD-7). State tokens are about 150 to 250 characters, depending on the data.

## Migration notes

- Principals gain an `issuer|` prefix when the token names an issuer. Outstanding cursors fail once with `reason: principal`.
- HTTP servers that bind to a public address must pass `allowed_hosts` (and `allowed_origins` for browser clients), or `host_origin_protection=False` behind a checking proxy.
- `ouncemcp check` can now fail on `input-after-effect`.
