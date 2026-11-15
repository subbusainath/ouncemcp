# 6. Safe writes

Reading a catalog cannot hurt anyone. Borrowing a book changes the world: a copy leaves the shelf and someone else cannot have it. Step 3 adds borrowing and returning, and this chapter is about the two ways writes go wrong with agents:

1. **The model acts without the person agreeing.** It misreads "can I borrow this?" as "borrow this".
2. **A write happens twice.** A call times out, the host retries, and the book is borrowed twice.

## A server you can build fresh

Writes change state, so tests need a fresh library each time. Step 3 builds the server in a function:

```python title="examples/book/shelf/step3_writes.py"
--8<-- "examples/book/shelf/step3_writes.py:build"
```

`deps=` is whatever your tools need (a database pool, an API client, here the `Library`), read in tools as `ctx.deps`. The module still ends with `server = build()`, so `ouncemcp dev` and hosts find it as before.

## Asking first

```python title="examples/book/shelf/step3_writes.py"
--8<-- "examples/book/shelf/step3_writes.py:borrow"
```

Look at the decorator:

- `effect=Effect.WRITE` says the tool changes state.
- `approval="required"` asks the person **before the handler runs**. If they decline, the handler never runs and the model gets `approval_declined` (category `permission`).
- `preview=borrow_preview` writes the question. It takes the same arguments as the tool, must not change anything, and should say exactly what will happen: "Borrow 'The Forest Forest' (b-002) for 3 weeks?" is a question a person can answer; "Allow loans.borrow?" is not.

How the question reaches the person depends on the client. Current clients get an MCP `input_required` result with a signed, single-use receipt; older clients that support elicitation are asked directly. A client that can do neither is refused, unless the server has an **approver** hook:

```python
server = build(approver=lambda request: request.arguments["book_id"] in staff_picks)
```

An approver receives an `ApprovalRequest` (tool, arguments, caller, preview text) and returns `True` or `False`. Use it to route approvals to a ticket queue or a chat bot, and in tests.

!!! warning
    The model cannot approve its own write. There is no argument for it (`approval` and `approved` are reserved parameter names), and only the client's answer to the server's own question counts.

The description says "The server asks the user to confirm, so call it directly." Without that sentence, models tend to ask the user in chat first and then call the tool, so the person is asked twice.

`approval` can also be a function, for writes that need a yes only sometimes:

```python
@server.tool(name="fines.waive", effect=Effect.WRITE, approval=lambda r: r.arguments["amount"] > 20)
```

## Happening once

`idempotency=True` keys each write by the caller, the tenant and the arguments. A second identical call does not run the handler: it replays the first result. The test for this chapter borrows `b-002` twice and checks there is one loan.

```python title="examples/book/shelf/step3_writes.py"
--8<-- "examples/book/shelf/step3_writes.py:return"
```

Two more things keep writes honest:

- **Writes are never retried automatically.** If one fails with a transient error, the model is told; it is not silently repeated.
- **A write that passes its deadline keeps running** in the background and reports `ambiguous_write`. Because it is idempotent, calling again returns its result once it finishes instead of writing twice.

Clients that want their own key, such as a host retrying after a network error, pass one in `_meta`, and it is used instead of the arguments.

## Lints for writes

| Lint | Catches |
| --- | --- |
| `write-without-side-effects` | A write whose description does not say what it changes |
| `elicit-in-write` | A write that asks the user mid-way, after it may have changed something; use `approval=` |
| `input-after-effect` | A tool that asks for input after a write has started |
| `destructive-without-policy` | An `Effect.DESTRUCTIVE` tool anyone can call |

## Summary

- Mark writes `Effect.WRITE`; add `approval="required"` and a `preview` that says exactly what will happen.
- `idempotency=True` makes retries safe: the second call replays the first result.
- Writes are never retried for you; a write past its deadline is reported as `ambiguous_write`.
- Use `deps=` and a `build()` function so tests get fresh state.
