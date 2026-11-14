# 0023: Pinned connections and redirect checks for outbound fetches

Status: proposed for M4. Completes spec SEC-9 (the first part, `check_url`, is in [proposal 0015](0015-migration-and-audit.md)).

## Problem

`check_url` checks a URL before a fetch, but the fetch resolves the name again. A DNS answer that changes in between (DNS rebinding) sends the connection to a private address, and a public page can redirect the client to `http://169.254.169.254/`. Both are standard ways around a pre-check.

## Proposal

`ouncemcp.security.safe_client(...)` returns an `httpx2.AsyncClient` whose transport (`PinnedTransport`) checks and pins every request:

- **Pinned connections.** It resolves the host once per request, checks every address, and connects to the checked address. The certificate is still verified for the host name: the name goes in SNI (`sni_hostname`) and the `Host` header is unchanged.
- **Redirects.** The client follows redirects (`max_redirects`, default 5), and each hop passes through the same check, including the host allowlist.
- **Separate pools per host name.** Two names that share an address never share a connection, so a TLS session verified for one name is never reused for another.
- **No environment proxies.** `trust_env=False`, because a proxy from `HTTP(S)_PROXY` would make its own connection and bypass the pin.
- **Token passthrough guard.** Installed on every request (SEC-4).

```python
server = OunceServer("web", deps=safe_client(allow_hosts=[".example.com"], timeout=10))

@server.tool(effect=Effect.READ)
async def fetch(url: str, ctx: ToolContext) -> Page:
    response = await ctx.deps.get(url)  # URLNotAllowed for unsafe targets and redirects
```

`checked_addresses(url, ...)` is the shared check: it returns the host and the addresses that may be used, and `check_url` is now built on it.

## Alternatives considered

- **Re-checking after connecting** (reading the peer address). Too late for HTTP: the request has been sent by then.
- **Disabling redirects.** Breaks ordinary sites; checking each hop is just as safe.

## Impact on tokens and task success

None.

## Migration notes

None. Tools that fetch model-chosen URLs should move from `check_url` plus their own client to `safe_client`.
