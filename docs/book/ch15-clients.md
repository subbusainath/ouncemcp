# 15. Clients: one catalog over many servers

Everything so far has been about building a server. OunceMCP also has a client side for the agents and hosts you build yourself: `ouncemcp.client.Broker` connects to many MCP servers and gives the model one catalog.

```python
from ouncemcp.client import Broker

async with Broker({
    "shelf": "https://shelf.example.com/mcp",
    "tickets": "examples/ticketing/server.py",
    "git": ["uvx", "mcp-server-git"],
}, elicitation_handler=ask_user) as broker:
    mode, definitions = await broker.host_tools()
    result = await broker.call("shelf.books.search", {"query": "ada"})
```

- **Names** are `<server>.<tool>`, so servers never collide.
- **Discovery** works as in chapter 12: `host_tools()` returns `direct` with every definition while they fit, or `search` with the three meta-tools, which you answer with `await broker.handle(name, arguments)`.
- **Results** carry their token count, and `result.for_model()` labels where they came from. Treat every result as data, never as instructions.
- **Cross-server writes need a person.** When the model wants to call a write because of another server's output (an email says "close ticket T-1"), pass that result as `caused_by=`; the broker then asks your `approver` or refuses. This is the main defence against prompt injection between servers.
- **Failures are contained.** Servers that fail to connect are listed in `broker.unavailable`, and a server that keeps failing is skipped for a while.

For servers that need OAuth, pass `auth={"shelf": OunceOAuth(...)}`; `OunceOAuth` uses a pre-registered client id first, then a client metadata document, and only then dynamic registration.

The [client guide](../guides/client-broker.md) has the rest.

## Where to go next

You have built, secured, tested, measured and deployed a complete MCP server. From here:

- The [concepts](../concepts/budgets.md) pages explain each mechanism in more depth.
- The [API reference](../reference/index.md) lists every public name.
- The [examples](https://github.com/subbusainath/ouncemcp/tree/main/examples) show larger servers: inventory (discovery and composite tools), ticketing (approvals and tenants), report (jobs) and markitdown (migration).
