# 0003: Signed cursors

Status: implemented for M1 (SEC-12). Replaces the unsigned `o1.` cursors from [proposal 0002](0002-schema-safe-shaping-and-pagination.md). See [spec section 8.2](../spec.md).

## Problem

Cursors issued by `ctx.page` and `ctx.window` were `o1.` plus a base64url offset. Anyone could write one:

- **Tampering.** A client can invent an offset and read any position in a result set, including positions the server never handed out.
- **Cross-user reuse.** A cursor issued to one caller works for another. In a server with per-caller visibility, a cursor from a privileged caller opens the same position for a less privileged one.
- **Filter mismatch.** A cursor from `region=ap-south-1` works with `region=eu-west-1`. The agent gets a page that looks valid and belongs to no query.
- **No expiry.** A cursor never goes stale, so it keeps pointing at data that has long since changed.

MCP treats cursors as opaque and says servers should validate them and return `-32602` for invalid ones ([pagination](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination.md)). Opaque is not the same as tamper-evident.

## Proposal

### Format

`s1.` followed by base64url (no padding) of 42 bytes:

| Bytes | Field |
| ---: | --- |
| 4 | Expiry, Unix seconds, big-endian |
| 4 | Offset of the first item of the next page, big-endian |
| 6 | Digest of the tool name |
| 6 | Digest of the caller (principal) |
| 6 | Digest of the normalized query arguments |
| 16 | MAC |

The MAC is HMAC-SHA256 over the first 26 bytes with a fixed domain prefix, truncated to 16 bytes. The result is 59 characters. The `s1` prefix is the version. A change to the layout uses a new prefix, and old prefixes can be kept readable for as long as needed.

The three digests are in the signed body, so after the MAC is verified the server can tell the agent which binding failed without trusting any unsigned data. The raw principal and arguments never appear in the cursor.

### What a cursor is bound to

- **Tool.** The tool that issued it.
- **Principal.** The verified caller. The default is the FastMCP access token's `subject` (or its `sub` claim, or its client id) when the server has authentication configured, and `"anonymous"` otherwise, which is correct for a single local caller over stdio. `OunceServer(principal=fn)` takes any `fn(ctx) -> str | None`. This is a stand-in until SEC-1 defines the verified identity object. SEC-1 will replace the default and not the binding.
- **Query.** A SHA-256 digest of the call's arguments as canonical JSON: validated values with defaults filled in, keys sorted, `None` values dropped. This means an omitted optional argument and an explicit `null` or default are the same query. `cursor`, `limit` and `response_format` are left out, so an agent can change the page size (as truncation notes tell it to) or format between pages. A tool that names its paging arguments differently passes `ignore=("page_size", "page_token")` to `ctx.page`, `ctx.offset` or `ctx.window`.
- **Expiry.** One hour by default (`CursorSigner(ttl_s=...)`).

### Keys and rotation

```python
from ouncemcp import CursorSigner, OunceServer

server = OunceServer("inventory", cursors=CursorSigner([new_key, old_key], ttl_s=900))
```

- Keys are at least 16 bytes. The first signs and every key verifies. To rotate, put the new key first and drop the old one after `ttl_s` has passed, when the cursors it signed have expired.
- With no `cursors=` argument, the server reads `OUNCEMCP_CURSOR_KEYS`, a comma-separated list, newest first.
- With neither, the server uses a random per-process key and logs a warning the first time it issues a cursor: cursors stop working after a restart and across replicas, and the variable to set is named. This is the development path. It fails closed, since cursors that cannot be verified are rejected, never accepted. `ouncemcp dev` is not built yet, so "no key configured" currently stands for dev mode.

### Errors

A cursor that cannot be used returns `isError: true` with `_meta.ouncemcp = {error: "invalid_cursor", reason}` and a message that says how to recover. `InvalidCursor.reason` is one of:

| `reason` | Cause | Advice in the message |
| --- | --- | --- |
| `malformed` | Wrong prefix, length, encoding or padding | Pass `next_cursor` unchanged, or omit `cursor` |
| `legacy` | An `o1.` cursor from before this change | Omit `cursor` and start over |
| `signature` | Modified, forged, or signed by a key that is no longer configured | Pass `next_cursor` unchanged, or omit `cursor` |
| `expired` | Past its expiry | Omit `cursor` and start again |
| `tool` | Issued by another tool | Use it only with the tool that returned it |
| `principal` | Issued to another caller | Use only cursors you received |
| `query` | Arguments other than paging arguments changed | Keep the same arguments (only `limit` may change), or omit `cursor` to start over with the new filters |

Messages never include the other caller's identity. The reason distinguishes `signature` from the rest only after the MAC has checked out, so an attacker learns nothing about which part of a forged cursor was wrong.

### API changes

- New: `CursorSigner` (exported from `ouncemcp`), `OunceServer(cursors=, principal=)`, `ToolContext.principal`, `ToolContext.encode_cursor`, `ignore=` on `ctx.page`, `ctx.offset` and `ctx.window`, `InvalidCursor.reason`.
- `ctx.page`, `ctx.offset` and `ctx.window` keep their call shape. They now return and accept signed cursors.
- `ouncemcp.core.cursor.encode_offset` and `decode_offset` are removed. They were never exported from the package. `PageSpec.encode` still takes `Callable[[int], str]`, and the server passes the signing function for the call.
- A `ToolContext` built by hand, as a handler unit test or a benchmark might, signs with a per-process random key.

## Alternatives considered

- **Encrypt the cursor so offsets are hidden.** Rejected. An offset is not a secret, and a MAC gives the integrity we need at half the size of an AEAD envelope.
- **JWT or PASETO.** Rejected: a JSON envelope is several times longer than 59 characters, and every cursor token is paid for in the model's context on every page.
- **Server-side cursor table, with a random id as the cursor.** Rejected for M1: it needs shared state, which breaks the "no process-local state for correctness" rule in section 9.2, and it needs eviction. It may return as the backing for result handles (RES-5).
- **Bind `limit` too.** Rejected. Truncation notes tell the agent to retry with a lower `limit`, so a changed `limit` has to keep working.
- **Longer digests and MAC.** A 6-byte digest is a 2^-48 chance that a cursor for one tool, caller or query is accepted for another one with a colliding digest, and finding such a pair does not help an attacker, who cannot mint cursors. The MAC is 128 bits. Longer values would add about a token per 2 bytes for no gain.
- **Fail at startup when no key is configured.** Deferred until `ouncemcp dev` exists to carry the "dev" mode. A strict flag for production can be added then.
- **Keyset cursors (resume after the last key, not at an offset).** Out of scope. They would make paging stable when the data changes. Cursors here keep the offset semantics from proposal 0002, and a page boundary can still shift if rows are inserted between calls.

## Impact

Measured on the inventory example (Python 3.12.3, x86_64, 2 vCPU).

| Measure | Before | After |
| --- | ---: | ---: |
| Cursor length | 8 characters | 59 characters |
| Cursor cost in tokens (`o200k_base`) | 4 | 38 |
| Four-task session, definitions plus results | 1,510 | 1,543 |
| Added time, `bind` + `encode` + `decode` | | 5.0 + 2.3 + 3.8 microseconds |
| Added server time per call, p50 | 0.14 to 0.44 ms (M0) | 0.13 to 0.34 ms |
| Added server time, truncated 50-item page, p50 / p95 | 0.70 / 0.80 ms | 0.62 / 0.82 ms |

- A signed cursor costs about 34 more tokens than the old one on every page except the last, about 1.7% of the default 2,000-token result budget. The truncation note reserve already measures the cursor's real size, so pages still fit their budgets. Existing property tests (every item exactly once, result plus note within budget) pass unchanged.
- The overhead figures show the extra cost of signing does not change the picture: PERF-1 and PERF-7 stay met. The plain FastMCP baseline in `m0_overhead.py` builds a bare `ToolContext`, so it signs cursors too, and the "added" column leaves out signing. The microsecond figures above are the cost of signing itself.
- The M0 and M1 result files in `benchmarks/results/` are left unchanged, as the repository convention asks. The numbers above come from re-running the same scripts.

Tests (`tests/test_sec12_cursors.py`): round trip over all offsets, rejection of every single-character change, truncated, extended, padded and junk cursors, rejection of forged and legacy cursors, expiry at the boundary, binding to tool, principal and query (including key order, unset values and type differences), rotation in both directions, key validation, environment keys, the one-time ephemeral-key warning, and the same checks through a server: changed filters, another tool, another principal, expiry, tampering, `limit` changes, `ctx.offset` plus `ctx.window`, `ignore=`, and the access-token principal. The existing every-item-exactly-once property test now runs on signed cursors.

## Migration

- Set `OUNCEMCP_CURSOR_KEYS` (or pass `cursors=CursorSigner([...])`) in every deployment, with the same keys on every replica. Generate a key with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Clients holding an `o1.` cursor get a `legacy` error once and start over. No data is lost: paging restarts from the first page.
- If a tool's paging arguments are not called `cursor` and `limit`, pass `ignore=` so the agent can change them between pages.
- Code that called `ouncemcp.core.cursor.encode_offset` or `decode_offset` directly should use `ctx.encode_cursor` and `ctx.offset`, or `CursorSigner.bind(...)` outside a call.
- Tools that run behind authentication should check that the default principal is the identity they mean, or pass `principal=`.
