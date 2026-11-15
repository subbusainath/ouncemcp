# 8. Background jobs and handles

Some work is too slow for one call, and some results are too large for one context. Step 5 adds a circulation report for librarians that has both problems: it reads every book at the branch, and it returns a row per book.

## Turning handles on

```python
server = OunceServer("shelf", ..., handles=True)
```

`handles=True` adds two small tools:

| Tool | Does |
| --- | --- |
| `jobs.get` | Reports a job's state and, when it is done, its summary and a handle to its rows |
| `handles.read` | Reads a stored result a page at a time |

## A job

```python title="examples/book/shelf/step5_complete.py"
--8<-- "examples/book/shelf/step5_complete.py:job"
```

The job is a plain async function registered by name with `@server.job`. It takes a Pydantic model of arguments and returns a `JobResult`: a small **summary** the model reads directly, and the **items**, stored behind a handle.

The tool that starts it returns at once with a job id:

1. The model calls `reports.start` and gets `{"job": "...", "message": "Report started. Check it with jobs.get."}`.
2. It calls `jobs.get` with the id. While the job runs, the state is `running`; when it finishes, `done`, with the summary in `result`, the number of rows in `items` and a `handle`.
3. If it needs the rows, it calls `handles.read` with the handle, a page at a time.

The summary for the north branch fits in a few dozen tokens; the 30 rows never enter the context unless the model asks for them.

## Surviving a restart

Because the job is registered **by name** and started as `ctx.start_job("reports.circulation", args)`, its name and arguments are saved in the store. If the process running it stops, the next `jobs.get`, on any replica that shares the store, starts it again, up to `max_attempts` times. A job should therefore be safe to run twice: read, compute, and write through idempotent operations.

For a one-off background task that does not need to survive a restart, pass a coroutine function instead: `await ctx.start_job(work)`.

## Handles without jobs

A tool that already has a large result can store it and return a handle straight away:

```python
handle = await ctx.store_result(rows)
return ExportStarted(handle=handle, rows=len(rows))
```

Handles, like cursors, are signed and bound to the tenant that created them, and they expire. Another branch cannot read your report by guessing or reusing its handle.

## Summary

- `handles=True` adds `jobs.get` and `handles.read`.
- Register slow work with `@server.job(name)` and start it with `ctx.start_job(name, args)`; it restarts after a crash.
- Return a small summary for the model and keep the rows behind a handle.
