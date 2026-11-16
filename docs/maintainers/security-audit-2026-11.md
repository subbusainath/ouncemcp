# Security audit, November 2026

An internal audit of the whole repository on 16 November 2026, against release `1.0.0rc2`. It is not a substitute for the external review that 1.0 requires ([brief](security-review.md)).

## How it was done

- **Code reading** of every security-relevant module: identity, policies, signed state and cursors, approvals, idempotency, handles and jobs, the stores, outbound fetches, HTTP defaults, error and trace redaction, limits, the broker, OAuth, the HTML reports and the command line.
- **Attacks against a running server** over HTTP:
    - forged `Host` and `Origin` headers;
    - hidden-tool probing;
    - oversized and deeply nested input;
    - error leakage;
    - metric inflation;
    - hostile input to the discovery tools.
- **Hostile input** to every redaction pattern and to the URL checker (36 bypass forms).
- **Repository checks:** secrets in the full git history, workflow permissions and triggers, and known vulnerabilities in every locked dependency (`pip-audit`, `npm audit`).

## Findings and fixes

| # | Severity | Finding | Fix |
| --- | --- | --- | --- |
| 1 | **High** | **Denial of service through error redaction.** Three redaction patterns took quadratic time. A tool that echoes an argument in an error message, which is a common pattern (`No book with id '...'`), let one unauthenticated request stall the whole server: 7 s for a 16 KB argument and about 2 minutes for 64 KB, the default limit. | The patterns are now linear (200 KB in under 0.03 s). `redact` scans at most 8,192 characters, cut at whitespace, so no secret is left half visible. |
| 2 | Medium | **SSRF through IPv4 inside IPv6.** `check_url` and `safe_client` accepted NAT64 addresses (`64:ff9b::/96`, `64:ff9b:1::/48`) and IPv4-compatible addresses (`::a.b.c.d`) that carry a private IPv4 address, such as `[64:ff9b::a9fe:a9fe]` for the metadata service. On a network with a NAT64 gateway these reach private hosts. | The embedded IPv4 address must be public too, for NAT64, IPv4-compatible, 6to4 and Teredo forms. |
| 3 | Medium | **Unbounded metrics.** Calls to made-up tool names each created a metric series, with no limit and no authentication needed: memory growth, and a cardinality explosion in Prometheus. | Calls to unknown names are recorded as one `(unknown tool)` series in traces and metrics. The metrics sink keeps at most 2,000 series. |
| 4 | Medium | **In-memory store never shrank.** `MemoryStore` removed an expired record only when the same key was read again, and had no size limit. Idempotency and single-use records therefore accumulated for ever. | Expired records are swept on writes. The store keeps at most 100,000 records (`max_entries`) and drops the soonest-expiring first. |
| 5 | Low | **Approved continuation tokens were reusable.** After an approval, a tool that asked a follow-up question issued a state token that carried the approval and could be presented again until it expired (15 minutes). Only the client could do this, not the model. | The token is consumed on use, like the approval receipt. |
| 6 | Low | **Credentials in audit reports.** `ouncemcp audit https://user:pass@host/mcp?token=...` wrote the full URL into the JSON, Markdown and HTML reports. | Reports show the URL without credentials or query string. |
| 7 | Low | **Tag name pasted into a script in the release workflow.** Only maintainers can push tags, and tags are protected, so this was hardening. | The tag is passed as an argument. |

## Checked and found sound

- **Signed cursors and state:**
    - HMAC-SHA256, compared in constant time;
    - bound to tool, caller, arguments and expiry;
    - canonical encoding;
    - key rotation supported.
- **Tenant isolation:** handles and jobs are bound to caller and tenant, and caches are partitioned by principal, tenant and scopes.
- **Policies:** hidden tools are indistinguishable from missing ones (`unknown_tool`), including through `call_tool`, and unauthenticated writes are refused.
- **Approvals:**
    - asked before the handler runs;
    - a failing rule asks, so it fails closed;
    - receipts are single-use and bound to the arguments;
    - there is no argument a model can set to approve.
- **HTTP:**
    - loopback by default;
    - a public bind is refused without `allowed_hosts`;
    - forged `Host` gets 421 and forged `Origin` gets 403, on `/mcp` and on the health routes.
- **Input limits:**
    - 64 KB per call and 16 levels of nesting;
    - undeclared arguments are refused;
    - 8 calls in flight per caller.
- **Error handling:** unexpected exceptions return a reference id only. A database password, a connection string and a file path in an exception did not reach the caller.
- **URL checker:** refuses decimal, octal, hexadecimal and short IPv4 forms, mapped IPv6, userinfo and backslash tricks, metadata host names, and non-HTTP schemes.
- **`safe_client`:** pins the connection to the checked address, re-checks redirects and ignores proxy variables.
- **HTML reports:** data is escaped for a script context, and a hash-based Content-Security-Policy with `default-src 'none'` is set. The UI has no `innerHTML` and no dynamic links.
- **Repository:**
    - no secrets in history (only the deliberate fake token in the redaction tests);
    - workflows use `contents: read` and SHA-pinned actions;
    - publishing goes through trusted publishing with no stored token.
- **Dependencies:** no known vulnerabilities in any locked Python dependency.

## Accepted risks and advice for operators

- **`/metrics`, `/healthz` and `/readyz` need no token.** `/metrics` shows tool names and call counts. Expose these paths to your own network only ([deploying](../guides/deploying-http.md)).
- **A server without authentication has one caller.** All anonymous clients share the `anonymous` principal: its concurrency limit, cursors and handles. Configure `auth=` for anything multi-user.
- **stdio trusts the local user** unless `stdio_policy="enforce"`.
- **The passthrough guard checks headers for the exact inbound token.** It does not catch a token that is re-encoded or placed in a URL or body. It is a safety net, not a boundary.
- **Approvals trust the client.** A client that answers approval requests without showing them to a person defeats them. That is inherent to MCP.
- **The report UI's dev-only `shadcn` CLI** pulls in `deepmerge`, which has a low-severity advisory and no patched release. It is not in the shipped bundle.
- **PyPI trusted publisher:** restrict it to the `pypi` environment, as PyPI recommends, and consider turning off "Allow administrators to bypass" on that environment.

## Release

The fixes change no public signature except one addition: `MemoryStore(max_entries=...)`. They ship in `1.0.0rc3`.
