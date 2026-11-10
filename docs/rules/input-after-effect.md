# `input-after-effect`

Severity: Error. Reported by `ouncemcp check` and fails it.

## What it finds

A write or destructive handler calls `ctx.require_input(...)` after `ctx.effect_applied()`, and the tool is not registered with `replay_safe=True`.

## Why it matters

In MCP 2026-07-28 the client answers an `input_required` result by sending the whole `tools/call` again, so the handler runs from the start. Whatever it did before asking happens twice: two charges, two emails, two deleted rows (spec SEC-7). OunceMCP also refuses this at run time with an internal error.

## How to fix it

Ask for input before the effect:

```python
@server.tool(name="ticket.close", effect=Effect.WRITE)
async def close(ticket_id: str, ctx: ToolContext) -> Closed:
    if not ctx.input_responses:
        ctx.require_input(CONFIRM, state={"ticket": ticket_id})
    await api.close(ticket_id)
    ctx.effect_applied()
    return Closed(ticket_id=ticket_id)
```

If running the effect twice is harmless (it is idempotent, or keyed by an idempotency key), register the tool with `replay_safe=True`.
