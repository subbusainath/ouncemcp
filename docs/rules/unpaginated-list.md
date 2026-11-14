# `unpaginated-list`

Severity: Warning. Reported by `ouncemcp check` (spec DX-3, RES-3).

## What it finds

The tool's output has a list of records (a list of models) that is not marked `Paginated`, has no declared maximum length, and the output has no cursor.

## Why it matters

When the list is larger than the result budget, OunceMCP trims it to fit and says so, but the caller has no way to fetch the records that were left out. Agents then either stop early or call the tool again and get the same first page.

## How to fix it

Page the list:

```python
class SearchResult(BaseModel):
    items: Annotated[list[Ticket], Paginated]
    next_cursor: Cursor = None
```

and use `ctx.page(rows, cursor=..., limit=...)` in the handler. If the list is a bounded summary (the top 5, one entry per region), declare the bound instead: `costliest: list[Costly] = Field(max_length=10)`.
