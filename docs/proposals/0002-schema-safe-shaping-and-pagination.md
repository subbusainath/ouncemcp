# 0002: Schema-safe shaping and budget-aware pagination

Status: accepted for M1 (PR #3); follow-ups resolved in PR #4. Covers spec RES-8, RES-9 and RES-10 (see [spec section 15.2](../spec.md)).

## Problem

M0 shaping had three gaps:

- Trimming could produce a result that no longer matched the advertised output schema. Examples: a string shortened under a `pattern`, a list cut below `minItems`, or plain text sent instead of `structuredContent`. The MCP spec says servers "MUST provide structured results that conform to this schema" ([MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md)).
- When a handler issued a cursor and OunceMCP then trimmed the page, the cursor still pointed past the trimmed items. Those items were silently skipped. M0 only warned about it.
- A list could be trimmed to a single item with no way for the tool author to set a floor.

## Proposal

### API

```python
from typing import Annotated
from ouncemcp import Cursor, Paginated, ToolContext

class SearchResult(BaseModel):
    items: Annotated[list[Resource], Paginated]
    next_cursor: Cursor = None          # Annotated[str | None, Cursor marker]

@server.tool(name="inventory.search", effect=Effect.READ, min_items=1)
async def search(args: SearchInput, ctx: ToolContext) -> SearchResult:
    page = ctx.page(rows, cursor=args.cursor, limit=args.limit)
    return SearchResult(items=page.items, next_cursor=page.next_cursor)
```

- `ctx.page(rows, cursor=, limit=)` slices one page from a sequence and records the window.
- `ctx.offset(cursor)` decodes a cursor for handlers that query a database themselves. `ctx.window(items, start=, has_more=, total=)` records that window so OunceMCP can still rewrite the cursor.
- `min_items` (default 1, must be at least 1) is the smallest number of items any non-empty list is trimmed to (RES-10).
- Registration fails with `TypeError` if an output model has a `Paginated` field without exactly one `Cursor` field, or the other way round, or if the cursor field doesn't default to `None`.

### Shaping order

The server builds a `jsonschema` Draft 2020-12 validator from the compacted output schema it advertises. When the result is over budget, shaping runs in this order:

1. **Lists, largest first.** A binary search finds how many items fit, never below `min_items`. If the trimmed result fails the schema (for example `minItems`), items are added back one at a time. When the trimmed list is the `Paginated` field, `next_cursor` is moved to the first item not returned, during the search, so the size estimate includes the new cursor.
2. **Strings.** Only strings of at least 64 characters, and never cursor fields. A cut that fails the schema (`pattern`, `enum`, `maxLength`) is reverted.
3. **Optional fields.** The same field is dropped across all items (for example `items[*].tags`), largest first, and reverted if the schema rejects it. The cursor and the page list are never dropped.

If the result is still over budget:

- **With an advertised output schema:** the call returns `isError: true` with an actionable message. The message gives the smallest valid size, asks for a lower `limit` or narrower filters, and suggests `response_format=concise` for detailed calls. `_meta.ouncemcp` has `error: "result_too_large"`, `min_tokens`, `budget` and `format`.
- **Only with `output_schemas=False`:** the serialized text is cut and `structuredContent` is omitted, as in M0. This is the only case where text-only output can happen.

The truncation note tells the agent what was removed and what to do next. The advice depends on what happened: call again with `next_cursor` (cursor rewritten), the handler's cursor skips items so use a lower `limit` (handler-managed cursor), or ask for fewer items to get every field (fields dropped).

### Cursor format

Framework cursors are `o1.` followed by the base64url offset without padding. They are opaque to the agent, as the [MCP pagination spec](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination.md) requires. A cursor that can't be decoded raises `InvalidCursor`, which the tool returns as an actionable error with `_meta.ouncemcp.error = "invalid_cursor"`. M1 cursors are not signed. SEC-12 replaces them with signed, versioned cursors. The `o1.` prefix leaves room for that change.

### Other behavior changes

- Unset optional (`None`) fields are now omitted in detailed results too, not only concise ones. Compacted schemas don't allow `null`, so emitting it would break conformance.
- The server passes `assume_valid=True` because Pydantic has already validated the handler's model. That skips one validation of the full untrimmed result, which was the slowest step. Every change made during shaping is still validated.

## Alternatives considered

- **Send text-only results when the schema can't be met.** Rejected: it breaks the MCP MUST whenever a schema is advertised.
- **Keep the advertised schema loose (no `pattern`, `minItems`).** Rejected: it hides useful constraints from the model and only moves the problem.
- **Let handlers re-run their query with a smaller limit.** Rejected for M1: it needs a second handler call and a retry protocol. It may come back as an opt-in after PERF-7.
- **Signed cursors now.** Deferred to SEC-12 so this change stays focused on shaping.

## Impact

Measured on the inventory example (Python 3.12.15, x86_64, 2 vCPU, 5,000 calls per case):

| Case | M0 added p50 / p95 | M1 added p50 / p95 |
| --- | ---: | ---: |
| Truncated to 300 tokens | 1.44 / 1.32 ms | 1.85 / 1.94 ms (0.70 / 0.80 ms after the follow-ups) |
| limit=5 | 0.14 ms p50 | 0.15 / 0.04 ms |
| limit=20 | | 0.25 / 0.12 ms |
| limit=50 | 0.44 ms p50 | 0.45 / 0.54 ms |

- Truncated calls cost about 0.4 ms more because each change is validated. Before `assume_valid`, the cost was 3.4 ms. That met PERF-1 (p50 under 2 ms) but not PERF-7 (truncation under 1 ms p50). The follow-ups below bring it to 0.70 ms.
- Session tokens on the four-task benchmark: 1,512 vs 1,510 in M0, because of the new cursor format. Definition tokens are unchanged (488).

Tests:

- `tests/test_res8_conformance.py` covers patterns, `minItems`, dropping optional fields across items, `ResultTooLarge`, detailed conformance, and the text-only fallback only without schemas. A Hypothesis fuzz test runs through the server and checks that every result either conforms or is `isError`.
- `tests/test_res9_pagination.py` has a Hypothesis property test that every item is returned exactly once across pages under random budgets and limits. It also covers cursor rewriting, invalid cursors and `min_items`.

## Migration

- Mark the page list with `Annotated[list[X], Paginated]` and the cursor with `Cursor = None`. Then replace custom offset cursors with `ctx.page(...)` or `ctx.offset(...)` plus `ctx.window(...)`.
- Clients that read `null` optional fields from detailed results should treat a missing key the same way.
- Tools that relied on text-only fallback with schemas enabled now get `isError` results. Lower `limit` defaults or raise `result_tokens` for those tools.

## Resolved questions

These were open when PR #3 merged. All three are resolved in the follow-up change.

### Note reserve

The fixed 80-token `NOTE_RESERVE` is gone. Shaping now reserves the note's real size:

- Before fitting, it builds the note this result will most likely get: the largest top-level list trimmed, with kept counts written with as many digits as the full length, plus the right cursor advice. It reserves that note's token count.
- After fitting, it counts the actual note. If the result plus note is over budget, it refits from the original data with the reserve raised by the overflow. This happens at most three times. In a random sweep of 2,160 truncated pages, 20 (about 1%) needed a second pass and none needed a third.
- The full note is used while it costs at most 10% of the budget (`NOTE_SHARE`). Otherwise a short note is used. For example: `[ouncemcp] Truncated: kept 7 of 50 in 'items'. Use next_cursor for more.` The short note keeps the first change and counts the rest, so a dropped field is still named.

Effect at a 300-token budget: the page keeps 7 items instead of 5, and the note takes about 22 tokens instead of 80 reserved. A property test checks that a trimmed page always holds as many items as fit: one more item, with its note, goes over the budget.

### Advice when the floor can't shrink

`ResultTooLarge` now carries `items`, the number of items left in the largest list, and a `reason`. The error text only suggests a lower `limit` when that could help:

| Reason | When | Advice to the agent |
| --- | --- | --- |
| `item_over_budget` | One item alone is over budget | "Even one item is about N tokens, over the B-token budget, so asking for fewer items will not help. Narrow the filters or ask for a different item." |
| `min_items_over_budget` | The list is at `min_items` (more than 1) | "Even K items, the fewest this tool trims to, are about N tokens... Ask for fewer than K items with a lower limit, or narrow the filters." |
| `fixed_over_budget` | No list is left to trim | "The smallest valid result is about N tokens... Narrow the request." |

The detailed format adds "or use response_format=concise" in each case. `_meta.ouncemcp` adds `reason`, `items` (when there is a list), and a `hint` for the developer: raise the tool's `result_tokens`, lower its `min_items`, or return smaller items. The same hint is logged as a warning on the `ouncemcp` logger. The agent cannot change these settings, so the hint is kept out of the text the model reads. `min_tokens` now includes the actual note, not the old fixed reserve.

### PERF-7: truncation speed

Five changes, each measured:

1. **Estimate, then check.** Items are counted one at a time from the start, stopping once they pass the target, so only the items that could fit are encoded. The exact count is checked at the estimate, then the search moves upward in steps of 1, 2, 4 and so on. A close estimate needs two or three exact counts instead of a binary search over the whole list.
2. **Prefix count for the budget check.** A result longer than six characters per budget token is first counted up to a comma near that length. If the prefix alone is over budget (with a 4-token margin for the cut), the full result is never counted. If shaping then makes no change, the result is returned with an exact count.
3. **Skip validation for safe list cuts.** `list_trim_floor(schema, path, cursor_key)` walks the advertised schema. If every keyword on the path is known to be safe for cutting an array to its first k items, the validator is skipped and `minItems` is applied directly. Safe keywords are `type`, `items`, `properties`, `required`, `maxItems`, `uniqueItems`, `minItems`, local `$ref` and annotations, and the cursor must be a plain string. Anything else, such as `contains`, `anyOf`, `if`/`then` or a cursor `pattern`, is still validated.
4. **Scan only trimmed lists.** The scan for list candidates no longer looks inside a list that hasn't been trimmed. Its nested lists are smaller, so they can't be picked first.
5. **Cached field plans.** `to_jsonable` caches each model's field list (name, detail, required) instead of reading field metadata on every call.

Results with `shape_result` alone (`benchmarks/scripts/m1_truncation.py`, 500 runs, Python 3.12.15, x86_64, 2 vCPU):

| Case | PR #3 p50 | Now p50 | Items kept (PR #3 / now) |
| --- | ---: | ---: | ---: |
| 50 items, 300-token budget | 2.18 ms | 0.70 ms | 5 / 7 |
| 100 items, 2,000-token budget | 9.87 ms | 3.12 ms | 53 / 54 |
| 1,000 items, 36,014 tokens, 2,000-token budget | 37.63 ms | 7.32 ms | 53 / 54 |

Through the server (`m0_overhead.py 5000`), a truncated 50-item call now adds 0.70 / 0.80 ms (p50 / p95), so PERF-7 is met. Untruncated calls add 0.14 to 0.31 ms, down from 0.15 to 0.45 ms because of the cached field plans.

What is left: cost now grows with the budget, not with the size of the result. A 2,000-token budget needs a few exact counts of about 2,000 tokens each. The remaining cost that grows with result size is `to_jsonable` and `json.dumps` over the full result: about 4 ms of the 7.3 ms for 1,000 items. Converting only the items that could fit is the next step if large results become common.
