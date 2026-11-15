# 5. Errors the model can use

A model reads an error and decides what to do next. "Exception: KeyError 'b-999'" tells it nothing. A good error says what went wrong, whether trying again helps, and what to do instead.

## Raising errors

Raise one of OunceMCP's errors, with a message for the model and a hint:

```python
raise NotFound(f"No book with id '{id}'.", hint="Use books.search to find ids.")
raise InvalidInput("The query is empty.", hint="Give at least one word from a title or author.")
raise PermissionDenied(f"All copies of '{book.title}' are on loan.",
                       hint="Tell the user; suggest another book by the same author.")
```

The model receives the text and a category in `_meta`:

```text
[ouncemcp] Not found: No book with id 'b-999'. Use books.search to find ids.
```

```json
{"ouncemcp": {"error": "not_found", "category": "not_found", "retryable": false,
              "docs": "https://github.com/subbusainath/ouncemcp/blob/main/docs/errors/not_found.md"}}
```

## The eight categories

| Raise | Category | Retryable | Use it when |
| --- | --- | --- | --- |
| `InvalidInput` | `validation` | no | The arguments are wrong. Say which and how to fix them. |
| `AuthenticationRequired` | `authentication` | no | The caller must sign in. |
| `PermissionDenied` | `permission` | no | The caller may not do this, or the action is not allowed now. |
| `NotFound` | `not_found` | no | The thing does not exist **for this caller**. |
| `RateLimited` | `rate_limit` | yes | Too many calls. Pass `retry_after=` seconds. |
| `TransientError` | `transient` | yes | An upstream failed in a way that may pass. |
| `AmbiguousWrite` | `ambiguous_write` | no | A write may or may not have happened (chapter 6). |
| any other exception | `internal` | no | A bug. The model sees only a reference id. |

Pydantic validation errors on arguments become `validation` automatically, with the field names.

!!! warning
    Never put secrets or internal details in a message. Unexpected exceptions are already hidden: the model sees `internal` and a reference id, and the full traceback goes to your logs. Messages from OunceMCP errors are shown, after redaction of anything that looks like a token or key.

## Not found, not forbidden

When a member of the south branch asks for a north book (chapter 7), Shelf answers `not_found`, not `permission`. Saying "forbidden" would tell an intruder the book exists. Use `PermissionDenied` when the caller already knows the thing exists, such as their own loan they may not extend.

## Retries are automatic for reads

Transient failures of **read** tools are retried with bounded backoff, inside the deadline, before the model ever sees them. **Writes are never retried**: a write that fails halfway may already have happened. The next chapter is about exactly that.

## Summary

- Raise a category error with a message and a hint that says what to do next.
- Unexpected exceptions never leak; the model sees a reference id.
- Prefer `not_found` to `permission` when existence itself is private.
- Reads are retried for you; writes never are.

The [error reference](../errors/validation.md) has a page for each category.
