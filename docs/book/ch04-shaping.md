# 4. Shaping results

Step 1 caps search at ten books. That keeps results small, but a model looking for every book by an author has no way to see the eleventh. Step 2 fixes that and makes results smaller at the same time.

## Concise by default, detail on request

Most calls need a few fields. Some need all of them. Mark the rarely needed fields `Detail`:

```python title="examples/book/shelf/step2_shaped.py"
--8<-- "examples/book/shelf/step2_shaped.py:models"
```

A `Detail` field must be optional. OunceMCP adds a `response_format` argument (`concise` or `detailed`) to every tool that has one, and leaves the field out of concise results. The schema still lists it, so the model knows it can ask.

## Paging without gaps

```python title="examples/book/shelf/step2_shaped.py"
--8<-- "examples/book/shelf/step2_shaped.py:search"
```

Three pieces make a paged list:

1. The list is marked `Paginated` and the next-page token is typed `Cursor`.
2. The tool takes a `cursor` argument.
3. `ctx.page(rows, cursor=..., limit=...)` returns `page.items` and `page.next_cursor`.

Cursors are **signed**. They expire, and they only work for the same tool, the same caller and the same query, so a model cannot hand one user's cursor to another or edit it to skip ahead. In production, set `OUNCEMCP_CURSOR_KEYS` so every replica signs with the same key (chapter 13).

!!! note
    `check` warns `unpaginated-list` on a read tool that returns an unbounded list without a cursor. Either page it or bound it with `max_length`.

## When a result is still too big

Every result has a token budget (chapter 3). If a result is over it, OunceMCP **shapes** it: it trims lists first, then long strings, then optional fields, and the result always stays valid against the output schema. The model is told what was removed.

Here is `books.search` with a deliberately tiny 300-token budget, asked for 50 books:

```text
[ouncemcp] Truncated: kept 12 of 50 in 'books'. Use next_cursor for more.
```

```json
{"truncated": true, "truncation": {"lists": {"books": {"kept": 12, "total": 50}}, "cursor_rewritten": true}}
```

Because the list is `Paginated`, `next_cursor` was **rewritten** to resume after book 12, not book 50. Nothing is skipped. Without paging, shaping still keeps the result valid, but the dropped rows are gone.

If even the smallest valid result is over the budget, the model gets a `result_too_large` error that says how to ask for less.

## Skipping work nobody asked for

Reviews come from a slow service, and concise results never include them. So why fetch them?

```python title="examples/book/shelf/step2_shaped.py"
--8<-- "examples/book/shelf/step2_shaped.py:get"
```

`ctx.includes("reviews")` is true only when the field will be returned. The test for this chapter checks that a concise `books.get` never calls the review service.

Also on the context:

| Attribute | Gives |
| --- | --- |
| `ctx.detailed` | Whether the caller asked for `detailed` |
| `ctx.result_budget` | The tokens this result may use |
| `ctx.remaining()` | Seconds left before the deadline |

## Deadlines

`deadline_ms=5_000` gives each call five seconds. When it passes, the call is cancelled and the model gets a `transient` error it may retry. The default is 30 seconds. Pass `ctx.remaining()` as the timeout of upstream calls, so they never outlive the call that made them.

## Summary

- Mark rarely needed fields `Detail`; the model asks for them with `response_format="detailed"`.
- Page lists with `Paginated`, `Cursor` and `ctx.page`; cursors are signed and survive truncation.
- Results over budget are shaped, never broken; the model is told what was cut.
- Use `ctx.includes` to skip work for fields that will not be returned.
