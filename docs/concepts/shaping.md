# Response shaping

- **Concise by default.** Fields marked `Detail` are returned only with `response_format="detailed"` (RES-1).
- **Only declared fields.** Results contain exactly the output model's fields; upstream extras are dropped (RES-6).
- **Structured and text.** Results carry `structuredContent` and a compact text rendering (RES-7).
- **Pagination without gaps.** Mark the list `Paginated` and the cursor field `Cursor`, and page with `ctx.page(rows, cursor=..., limit=...)`. If shaping trims the page, `next_cursor` moves to resume after the last item returned (RES-9).
- **Signed cursors.** Cursors expire and only work for the same tool, caller and filters. Set `OUNCEMCP_CURSOR_KEYS` (comma-separated, newest first) on every replica in production ([proposal 0003](../proposals/0003-signed-cursors.md)).
- **Truncation notes.** The agent is told what was removed and how to get more (RES-4).

Details: [proposal 0002](../proposals/0002-schema-safe-shaping-and-pagination.md).
