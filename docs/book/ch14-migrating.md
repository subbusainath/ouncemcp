# 14. Migrating a FastMCP server

You do not have to rewrite a server to use OunceMCP. Each step below leaves a working server, and `ouncemcp audit` measures the difference.

## Step 0: measure

```bash
ouncemcp audit path/to/server.py:mcp --output before.json
```

`audit` works on any MCP server: a Python file, a command run over stdio (`ouncemcp audit -- python server.py`), or a URL. It reports startup tokens per tool and field, schema bloat, latency, and recommendations.

## Step 1: wrap it

```python
from original import mcp
from ouncemcp import Effect, OunceServer

server = OunceServer.wrap(mcp, effects={"add_note": Effect.WRITE, "get_note": Effect.READ})
```

No tool code changes. You get `check`, `dev`, categorized and redacted errors, deadlines, limits, traces and automatic discovery. Name every write in `effects`, so a write that times out is reported as `ambiguous_write` and never retried.

## Step 2: move tools one at a time

Rewrite a tool with `@server.tool`, keeping its name so hosts and prompts keep working. Give it a Pydantic output model and mark rarely needed fields `Detail`. Wrapped and moved tools are served side by side, so move the largest or riskiest first.

## Step 3: bound what comes back

Page lists (chapter 4), put large documents behind handles (chapter 8), and add `approval=` and `idempotency=True` to writes (chapter 6).

## Step 4: compare

```bash
ouncemcp audit server.py --compare before.json
```

The repository's [markitdown example](https://github.com/subbusainath/ouncemcp/tree/main/examples/markitdown) goes through all four steps on a real server. The [migration guide](../guides/migrating-from-fastmcp.md) covers the MCP Python SDK's server too.
