# 7. Callers and policies

Step 3 lends every book to `demo-member`. A real library has members who must see only their own loans, two branches that keep separate shelves, and librarians who can see everything at their branch. Step 4 adds all three.

## Who is calling

Over HTTP, a host sends an OAuth access token with each request. You configure verification with FastMCP's maintained auth providers (GitHub, Google, Azure, Auth0, WorkOS, Keycloak, any JWT issuer):

```python
from fastmcp.server.auth.providers.jwt import JWTVerifier

server = OunceServer(
    "shelf",
    auth=JWTVerifier(jwks_uri="https://auth.example.com/.well-known/jwks.json", issuer="https://auth.example.com"),
    audience="https://shelf.example.com/mcp",
)
```

`audience` is the server's own URL: tokens issued for any other service are refused, so a token stolen from one server cannot be used on another.

Inside a tool, `ctx.identity` is the verified caller: `subject`, `tenant`, `scopes`, and `authenticated`. It comes only from the verified token. **Never take identity from tool arguments**: the model writes those.

```python title="examples/book/shelf/step4_secure.py"
--8<-- "examples/book/shelf/step4_secure.py:member"
```

A local stdio run has no token. Shelf treats that as the demo member, so `ouncemcp dev` keeps working; a signed-in caller with no branch is refused.

## Isolating tenants

Every tool now filters by the caller's branch: search only finds books at your branch, `books.get` answers `not_found` for another branch's book, and `loans.return` answers `not_found` for someone else's loan. The test for this chapter signs in as a south member and checks that a north book and a north loan are invisible.

!!! warning
    Filter in the query, not after it. A tool that loads every row and then drops the ones the caller may not see will one day return them in an error message, a count or a truncation note.

Cursors (chapter 4) and approval receipts (chapter 6) are bound to the caller, so they cannot be passed from one member to another either.

## Policies

Some tools are for some callers only. A policy decides before the handler runs:

```python title="examples/book/shelf/step4_secure.py"
--8<-- "examples/book/shelf/step4_secure.py:open"
```

`require_scopes("librarian")` refuses callers without that scope. Policies do two things:

1. **Calls are refused** before the handler runs.
2. **The tool is hidden** from callers who may not use it, so it costs them no context. A member never sees `loans.open` in the tool list, and calling it by guessing the name fails as `unknown_tool`, exactly as if it did not exist.

Hiding is not access control on its own: the same policy runs on every call, whatever name the caller guessed. Other policies in `ouncemcp.security`: `authenticated` (any verified caller), `require_tenant(...)`, `writes_require(policy)` (reads for everyone, writes only when `policy` allows), and `all_of`/`any_of` to combine them. A policy is any function of a `PolicyRequest` that returns `True` or `False`.

### stdio

A local stdio server has no token, so by default policies are not enforced there and `check` warns `stdio-skips-policy`. Shelf sets `stdio_policy="enforce"` so the librarian tool stays locked even in a local run.

## Fetching URLs safely

Tools that fetch a URL the model gave them can be tricked into reading internal addresses (`http://169.254.169.254/` and friends). Fetch them with `ouncemcp.security.safe_client()`, an HTTP client that resolves the host once, refuses private and link-local addresses, pins the connection to the checked address and re-checks every redirect. For your own upstream APIs, build the client in `deps` with `guarded_client()`, which refuses to forward the caller's token upstream.

## Summary

- Verify tokens with a FastMCP auth provider and set `audience`.
- Identity comes from `ctx.identity`, never from arguments.
- Filter every query by tenant; answer `not_found` for other tenants' data.
- Policies refuse calls and hide tools; enforce them on stdio too when it matters.

!!! question "Questions about this chapter?"
    Ask in `#help` on the [OunceMCP Discord](https://discord.gg/mQz4ZuZe3u), one post per question.
