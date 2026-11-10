# Inventory pilot (M0)

A read-only cloud inventory over deterministic fake data (400 resources across four regions).

| File | Purpose |
| --- | --- |
| `server.py` | The OunceMCP version: typed tools, declared effects, concise results, pagination, budgets |
| `plain_server.py` | The same data as a plain FastMCP server that returns upstream records as they are |
| `data.py` | Shared fake data |

## Try it

```bash
ouncemcp check examples/inventory/server.py
ouncemcp context examples/inventory/server.py inventory.search
python examples/inventory/server.py          # stdio server
```

## Use it in an MCP host

Add the server to your host's MCP configuration. For example, in Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "inventory": {
      "command": "/absolute/path/to/ouncemcp/.venv/bin/python",
      "args": ["/absolute/path/to/ouncemcp/examples/inventory/server.py"]
    }
  }
}
```

Then ask, for example: "Which databases in ap-south-1 are degraded, and who owns them?"

## Compare with the plain version

```bash
python benchmarks/scripts/m0_compare.py
```

## Composite workflow tool (CAP-7)

`workflow.py` defines `inventory.cost_review`, which answers "where is the money going, and what is broken?" for several regions in one call. It makes one upstream call per region concurrently with `ctx.gather`, inside one deadline, and returns only the summary.

Reviewing all four regions this way costs 651 result tokens in 1 call. Getting the same data by paging `inventory.search` costs 9,896 result tokens over 8 calls, and the model still has to add the numbers up itself (measured with `o200k_base`; see `tests/test_cap7_composite.py`).

```bash
ouncemcp check examples/inventory/workflow.py
```
