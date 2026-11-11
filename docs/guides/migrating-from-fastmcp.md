# Migrating from FastMCP

Move an existing FastMCP server (or a server written with the MCP Python SDK's FastMCP-style `MCPServer`) to OunceMCP in small steps. Every step is a working server, and `ouncemcp audit` measures each one. The worked example is [markitdown-mcp](https://github.com/subbusainath/ouncemcp/tree/main/examples/markitdown).

## Step 0: measure what you have

```bash
ouncemcp audit path/to/server.py:mcp --output before.json      # in-process
ouncemcp audit -- python path/to/server.py                      # or over stdio
ouncemcp audit https://mcp.example.com/mcp --bearer "$TOKEN"     # or a deployed server
```

`audit` reports startup tokens per tool and field, resources and prompts, schema bloat, latency and a discovery recommendation. It only lists tools; add `--sample tool='{"arg": 1}'` to measure real result sizes.

## Step 1: wrap it, unchanged

```python
from original import mcp
from ouncemcp import Effect, OunceServer

server = OunceServer.wrap(mcp, effects={"add_note": Effect.WRITE, "get_note": Effect.READ})
server.run()
```

No tool code changes. You get `ouncemcp check` and `ouncemcp dev`, categorized and redacted errors (exception text no longer reaches the model), deadlines, request limits, traces and automatic discovery. Name every write in `effects`, so a write that times out is reported as `ambiguous_write`.

## Step 2: move one tool to `@server.tool`

```python
@server.tool(name="convert_to_markdown", effect=Effect.READ)
async def convert_to_markdown(uri: str, ctx: ToolContext) -> Converted:
    ...
```

Keep the tool name, so hosts and prompts keep working. Give it a Pydantic output model: results are now shaped to the budget and match their output schema. Mark rarely needed fields `Detail`. Repeat for each tool; wrapped and moved tools can be served side by side while you go.

## Step 3: bound what comes back

- Lists: mark the list `Paginated` and the cursor `Cursor`, and page with `ctx.page(...)`.
- Large documents or exports: `OunceServer(handles=True)` and return `await ctx.store_result(chunks)`; the agent reads on with `handles.read`.
- Slow work: `await ctx.start_job(work)` and `jobs.get`.

## Step 4: make it safe

- URLs from the model: `check_url(uri)` refuses loopback, private, link-local and metadata addresses.
- Upstream HTTP: `deps=...` with `guarded_client(...)`, so the caller's token is never forwarded.
- Writes: `idempotency=True`, and `approval="required"` with a `preview` for anything a person should confirm ([safe writes](safe-writes.md)).
- Who may call what: `policy=` ([securing tools](securing-tools.md)).

## Step 5: compare

```bash
ouncemcp audit path/to/server.py --compare before.json --fail-on-regression
```

## markitdown-mcp, measured

| | Original | Step 1 (wrapped) | Migrated |
| --- | ---: | ---: | ---: |
| Tool definition tokens | 85 | 85 | 394 (134 for the tool, 260 for `handles.read` and `jobs.get`) |
| Result for a 60-section report | 14,760 tokens, all at once | 14,760 | 388 (preview and handle), then 547 per 2-chunk page |
| Fetches `http://169.254.169.254/...`, `127.0.0.1`, `[::1]` | Yes | Yes | Refused |
| `file:` URIs | Any path | Any path | Off unless enabled, then only under one folder |
| Lines changed | | 0 (8-line wrapper) | 79 added, 22 removed |
