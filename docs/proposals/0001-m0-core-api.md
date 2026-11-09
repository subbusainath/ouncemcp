# 0001: M0 core API

Status: accepted for M0, open for revision in M1.

## Problem

Developers need a way to write MCP tools that are lean by default without learning a new framework from scratch, and the framework needs to measure what it costs.

## Proposal

```python
server = OunceServer("inventory", budget=Budget(result_tokens=2_000), output_schemas=True)

@server.tool(name="inventory.search", effect=Effect.READ, deadline_ms=5_000, result_tokens=None)
async def search(args: SearchInput, ctx: ToolContext) -> SearchResult:
    """One-line description the model reads."""
```

Rules enforced at registration:

- `effect` is required (`Effect.READ`, `Effect.WRITE`, `Effect.DESTRUCTIVE`).
- Tool names match `^[A-Za-z0-9_.-]{1,128}$` and must be unique. A description (docstring or argument) is required.
- The return type must be a Pydantic model.
- Inputs are either one Pydantic model, whose fields are flattened into the tool schema, or plain typed parameters.
- Credential and identity parameters (`api_key`, `token`, `password`, `tenant`, and similar) are rejected. Identity comes from the transport, not the model.
- `response_format` (`concise` or `detailed`) is added to the schema only when the output model has `Detail` fields.

Result shaping:

- Concise results drop `Detail` fields and unset optional fields.
- Results that exceed the budget are trimmed: the largest lists first (never to zero items), then the longest strings, then, as a last resort, the serialized text.
- A note is appended to the text content: `[ouncemcp] Result truncated to fit N tokens: kept X of Y items in 'path'. ...`. If the result also carries a cursor, the note warns that the cursor skips removed items.
- Shaping metadata goes in `_meta.ouncemcp` (`result_tokens`, `budget`, `format`, `truncated`, `truncation`).

## Alternatives considered

- **A `fields` parameter instead of `Detail`.** This is more flexible, but it costs schema tokens on every tool and the model has to guess field names. It may come later as an opt-in.
- **Raising an error on oversize results.** This is safer for structured clients but worse for agents, which usually do better with a partial result plus a note. It stays the planned fix only for the text-only fallback case.
- **Counting the in-process tool object.** This was rejected because field order differs from the wire and the counts drifted.

## Impact

On the inventory example: definitions are 488 tokens (versus 195 for plain FastMCP), and a four-task session uses 1,510 tokens versus 32,280. Added server time is 0.14 to 0.44 ms at p50, and 1.44 ms when truncating. See `docs/m0-report.md`.

## Open questions for M1

- Should output schemas default to off for hosts that do not use them?
- Budget-aware pagination (`ctx.fit()`), so handlers can set cursors after shaping.
- Behavior of the text-only fallback when an output schema is advertised.
