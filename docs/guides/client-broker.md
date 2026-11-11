# Connecting to many servers

`ouncemcp.client.Broker` gives an agent or host one catalog over many MCP servers ([proposal 0016](../proposals/0016-client-broker.md)).

```python
from ouncemcp.client import Broker

async with Broker({
    "crm": "https://crm.example.com/mcp",
    "tickets": "examples/ticketing/server.py",
    "git": ["uvx", "mcp-server-git"],
}, elicitation_handler=ask_user) as broker:
    mode, definitions = await broker.host_tools()
    # mode "direct": give the model every definition.
    # mode "search": give it search_tools, describe_tool and call_tool, and answer
    # them with await broker.handle(name, arguments).
    result = await broker.call("crm.search", {"query": "acme"})
```

- Tool names are `<server>.<tool>`, so servers never collide.
- Each result has `tokens`, and `for_model()` labels where it came from. Treat it as data.
- When the model wants to call a write tool because of another server's output, pass that result as `caused_by=`. The broker then asks your `approver`, or refuses.
- Servers that fail to connect are in `broker.unavailable`; a server that keeps failing is skipped for a while.
