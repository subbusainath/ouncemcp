# Security review brief

The 1.0 exit criteria include a security review by someone outside the project. This page gives a reviewer what they need to start: what OunceMCP protects, where each control lives, how it is tested, and what has already been checked automatically. Findings go to the maintainer privately, as `SECURITY.md` describes.

## What OunceMCP is

OunceMCP is a Python framework on FastMCP for building MCP servers, and it includes a client (`Broker`) that connects to many servers. Its security job is to keep an MCP server safe when:

- the model calling it is wrong or manipulated (prompt injection), or
- the network caller is hostile.

It does not authenticate users itself: FastMCP's auth providers verify tokens, and OunceMCP enforces what follows from the verified identity.

## Assets and trust boundaries

| Asset | Threat |
| --- | --- |
| One tenant's data (catalog, caches, handles, jobs, cursors) | Read or changed by another tenant |
| Writes to upstream systems | Made without a person's approval, made twice, or made on the model's say-so alone |
| The caller's access token | Forwarded to an upstream service (token passthrough) |
| Internal network | Reached through a URL the model chose (SSRF, DNS rebinding, redirects) |
| Server internals | Leaked through error messages, traces or results |
| A host using the `Broker` | One server's output steering a write on another server |

Boundaries: model → server (arguments are untrusted), network client → server (HTTP, `Host`/`Origin`), server → upstream (outbound requests), server → server (through the broker).

## Controls and where to look

| Control | Requirement | Code | Tests |
| --- | --- | --- | --- |
| Identity only from the verified token; `audience` check | SEC-1, SEC-4 | `security/identity.py`, `core/server.py` (`audience=`) | `tests/test_sec_security.py` |
| One policy decides listing, search and calls; guessed names get the same answer | SEC-2, SEC-3, DISC-3 | `security/policy.py`, `discovery/` | `tests/test_sec_security.py`, `tests/test_eval2_security_catalog.py` |
| Policies over stdio (`stdio_policy`) | Proposal 0010 | `security/stdio.py` | `tests/test_sec_security.py` |
| No token passthrough | SEC-4 | `security/passthrough.py` | `tests/test_sec_security.py`, security catalog `TokenPassthrough` |
| Caches partitioned by caller | SEC-5 | `security/cache.py` | `tests/test_sec_security.py` |
| Signed, caller-bound, expiring state (cursors, handles, approval receipts) | SEC-6, SEC-12 | `security/state.py`, `core/` cursors | `tests/test_sec12_cursors.py`, `tests/test_res5_handles_jobs.py` |
| No input request after an effect | SEC-7 | `security/replay.py`, `ToolContext.require_input` | `tests/test_sec_security.py` |
| HTTP: loopback by default, `Host` and `Origin` checks | SEC-8 | `security/http.py` | `tests/test_sec_security.py` |
| Safe outbound fetch: private, metadata and link-local addresses refused, connection pinned to the checked address, redirects re-checked | SEC-9 | `security/urls.py`, `security/fetch.py` | `tests/test_sec9_urls.py`, `tests/test_sec9_fetch.py` |
| OAuth client registration order | SEC-10 | `client/oauth.py` | `tests/test_sec10_oauth.py` |
| Trace redaction | SEC-11 | `trace/` | `tests/test_sec_security.py` |
| Approvals before writes; the model cannot approve | Gate 4 | `execution/approval.py`, `core/server.py` (`_approval_gate`) | `tests/test_sec_security.py`, ticketing pilot tests |
| Idempotent writes; ambiguous writes are never retried | Gate 3, EXE-4 | `execution/idempotency.py`, `execution/retry.py` | `tests/test_exe4_idempotency.py` |
| Error redaction | ERR-3 | `core/errors.py` | error tests |
| Request limits (size, depth, concurrency) | EXE-6 | `execution/limits.py` | limits tests |
| Cross-server writes need a person | CLI-6 | `client/broker.py` | `tests/test_client_broker.py` |

Design records: proposals [0008](../proposals/0008-security-and-policy.md) (security and policy), [0011](../proposals/0011-store-and-idempotency.md), [0012](../proposals/0012-safe-writes.md), [0013](../proposals/0013-handles-and-jobs.md), [0016](../proposals/0016-client-broker.md), [0023](../proposals/0023-safe-fetch.md) and [0025](../proposals/0025-oauth.md).

## Already checked automatically

The pre-review results below, from 15 November 2026, are a starting point for the reviewer and not a substitute for the review.

- **CodeQL** (Python, JavaScript/TypeScript, Actions) runs on every pull request and push to `main`, and its checks pass. Open alerts, if any, are listed under *Security → Code scanning*.
- **The security catalog** (`ouncemcp.eval.run_security_suite`) runs in the test suite against the ticketing and markitdown pilots and the Book's Shelf server. It probes guessed tool names, cross-tenant reads, cursor and handle reuse, SSRF targets and token passthrough.
- **Bandit** (`bandit -r src -l`) reports 1 medium and 14 low findings, and none is a vulnerability:
    - **B104** (`security/urls.py`): `0.0.0.0` is a placeholder address for a lookup that is skipped, not a server binding.
    - **B101**: each `assert` narrows a type. The one in the approval gate is behind an `if spec.approval not in (None, "never")` check at its only call site, so removing asserts with `-O` does not skip an approval.
    - **B603, B607 and B404**: the `subprocess` calls run `git` with fixed arguments and the server command the developer typed for `ouncemcp dev`, never model input.
    - **B105**: the canary token that the security catalog plants on purpose.
    - **B311**: retry jitter, not a security use.
- **pip-audit** over every locked dependency, all extras included, finds no known vulnerabilities.
- **Dependabot** alerts and security updates are on. The report UI's dev-only `shadcn` CLI carries a low-severity `deepmerge` advisory that has no patched release; it is not in the shipped bundle.

## Known limits, stated in the docs

- Over stdio, policies are trusted by default (`stdio_policy="trust"`), because the local user started the server. `ouncemcp check` warns about this, and `stdio_policy="enforce"` turns policies on.
- Hiding a tool from a caller is not access control by itself; the same policy runs on every call.
- `safe_client` defends against DNS rebinding by pinning the connection. Plain `check_url` alone cannot, as the docs say.
- The in-memory store is for one process. Several replicas must share Redis and the same `OUNCEMCP_CURSOR_KEYS`.

## What we ask the reviewer to look at first

1. **Tenant isolation end to end:** catalog, search, caches, cursors, handles, jobs and approval receipts, for one tenant attacking another.
2. **The approval and idempotency paths** under concurrency and retries, including a client that answers an approval for different arguments.
3. **SSRF defences** in `security/urls.py` and `security/fetch.py`: IPv6 and encoded forms, redirects, and proxies set in the environment.
4. **Signed state:** key handling and rotation, expiry, and binding in `security/state.py`.
5. **Broker cross-server rules** against prompt injection carried in tool results.

## Deliverable

A short report with each finding's severity, the affected code and a suggested fix. Findings go to GitHub private vulnerability reporting. Fixes ship in a patch release, and the reviewer and report are named in `docs/m4-report.md` when the review is complete.
