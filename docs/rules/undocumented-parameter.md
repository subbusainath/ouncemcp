# `undocumented-parameter`

Severity: Warning. Reported by `ouncemcp check` (spec DX-3).

## What it finds

An input parameter has no description, and its type does not explain it. Enums, booleans and numbers with both a minimum and a maximum count as self-explaining. `response_format`, which OunceMCP adds, is skipped.

## Why it matters

A model fills each argument from the parameter's name, type and description, and nothing else. `id: str` does not say which kind of id, or which tool returns one; a wrong guess costs a failed call and a retry.

## How to fix it

Say what the value is and where it comes from:

```python
async def close(ctx: ToolContext, ticket_id: str = Field(description="Ticket id from tickets.search, e.g. T-1")) -> TicketOut:
```

In an input model, use `Field(description=...)` on the field.
