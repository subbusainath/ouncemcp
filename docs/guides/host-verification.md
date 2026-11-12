# Host verification

The M0 and M1 exit criteria need the example servers to work in real hosts, with versions recorded in the [compatibility matrix](../compatibility.md). This needs a person with the host apps; it cannot run in CI.

## Set up once

```bash
git clone https://github.com/subbusainath/ouncemcp.git && cd ouncemcp
uv venv && uv pip install -e ".[dev]"
uv run ouncemcp check examples/inventory/server.py   # also downloads the tokenizer once
pwd; which uv                                         # full paths for the configs below
```

Desktop apps often do not find `uv` on `PATH`, so use its full path. After editing a JSON config, check it with `python3 -m json.tool <file>`: hosts ignore an invalid file without saying so.

## Configure the hosts

Replace `/FULL/PATH/TO/uv` and `/FULL/PATH/TO/ouncemcp` with the paths printed above.

**Claude Desktop**: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). Add `mcpServers` inside the file's existing top-level `{ }`, next to any other settings, then quit the app fully and reopen it. Use the **Chat** tab: cloud sessions in the Code tab do not see local servers.

```json
"mcpServers": {
  "ounce-inventory": {
    "command": "/FULL/PATH/TO/uv",
    "args": ["run", "--directory", "/FULL/PATH/TO/ouncemcp", "python", "examples/inventory/server.py"]
  },
  "ounce-tickets": {
    "command": "/FULL/PATH/TO/uv",
    "args": ["run", "--directory", "/FULL/PATH/TO/ouncemcp", "python", "examples/ticketing/server.py"]
  }
}
```

**Cursor**: the same `mcpServers` object in `.cursor/mcp.json` in the opened folder, wrapped in `{ }`. Turn the servers on in Cursor Settings → MCP and wait for a green dot (2 and 4 tools). Use the chat in **Agent** mode.

**VS Code**: `.vscode/mcp.json` with `"servers"` instead of `"mcpServers"` and `"type": "stdio"` on each server; start them from that file and use Copilot Chat in Agent mode.

**Claude mobile app**: it only talks to remote servers over HTTPS. Serve the example over HTTP at a public URL (see [deploying over HTTP](deploying-http.md)), add it as a custom connector on claude.ai, and run the prompts from the app.

## Run the checklist

In a new chat, one prompt at a time:

1. "List running VMs in ap-south-1." Expect `inventory.search` with `region`, `kind: vm`, `status: running`, and 12 VMs starting with `vm-00040`.
2. "Show full details for the first one." Expect `inventory.get`, and `response_format=detailed` if the model wants config and metrics.
3. "Page through all databases in us-east-1." Expect repeated `inventory.search` calls following `next_cursor` to the end.
4. "Close ticket T-1." Expect an approval request naming "Printer on floor 3 is jammed". Decline it: the reply says nothing changed.
5. "Close ticket T-1 again." Accept: the ticket is closed. A host that cannot ask gets `approval_unavailable`, which is also a result to record.

## Record

For each host, add a row to the [compatibility matrix](../compatibility.md): host and version (About dialog), OS, the OunceMCP commit, the FastMCP version (`uv pip show fastmcp`), and the result. Also note:

- how the host shows the approval request in steps 4 and 5;
- whether read and write tools are presented differently;
- which definition fields the host shows the model, if it exposes them (BUD-7 host study: `outputSchema`? `title`? annotations?);
- whether the host lists tools lazily, which changes the discovery numbers (DISC-6 follow-up).
