# Response shaping

- **Concise by default.** Fields marked `Detail` are returned only with `response_format="detailed"` (RES-1).
- **Skip work that will not be returned (RES-11).** `ctx.includes("metrics")` says whether a field is returned in this call, `ctx.detailed` whether the caller asked for detail, and `ctx.result_budget` how many tokens the result may use. `inventory.get` only fetches metrics for detailed calls:

    ```python
    if ctx.includes("metrics"):
        detail.metrics = metrics_for(id)
    ```
- **Only declared fields.** Results contain exactly the output model's fields; upstream extras are dropped (RES-6).
- **Structured and text.** Results carry `structuredContent` and a compact text rendering (RES-7).
- **Pagination without gaps.** Mark the list `Paginated` and the cursor field `Cursor`, and page with `ctx.page(rows, cursor=..., limit=...)`. If shaping trims the page, `next_cursor` moves to resume after the last item returned (RES-9).
- **Signed cursors.** Cursors expire and only work for the same tool, caller and filters. Set `OUNCEMCP_CURSOR_KEYS` (comma-separated, newest first) on every replica in production ([proposal 0003](../proposals/0003-signed-cursors.md)).
- **Results by reference.** With `OunceServer(handles=True)`, `await ctx.store_result(items)` returns a handle the agent pages with `handles.read`, and `await ctx.start_job(work)` runs slow work in the background for `jobs.get` ([proposal 0013](../proposals/0013-handles-and-jobs.md)). A job registered with `@server.job("name")` and started as `ctx.start_job("name", args)` is started again by the next `jobs.get` if its process stops, on any replica sharing the store ([proposal 0028](../proposals/0028-resumable-jobs.md)).
- **Truncation notes.** The agent is told what was removed and how to get more (RES-4).

Details: [proposal 0002](../proposals/0002-schema-safe-shaping-and-pagination.md).
