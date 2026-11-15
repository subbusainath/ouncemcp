# Quickstart

From nothing to a tested server running in your MCP host.

## 1. Create a project

```bash
pipx install ouncemcp        # or: uv tool install ouncemcp
ouncemcp init notes-server
cd notes-server
uv venv && uv pip install -e ".[dev]"
```

Until OunceMCP is published on PyPI, install it from a checkout in the same step: `uv pip install -e /path/to/ouncemcp -e ".[dev]"`.

`init` writes:

| File | What it is |
| --- | --- |
| `server.py` | `notes.search` (read) and `notes.add` (write) |
| `tests/test_server.py` | Calls both tools, checks errors and the token budget |
| `.github/workflows/ci.yml` | Runs the tests and `ouncemcp check` |
| `pyproject.toml`, `README.md`, `.gitignore` | Project files |

## 2. Test and check

```bash
uv run pytest                    # 6 tests pass as generated
uv run ouncemcp check server.py  # token budget per tool, discovery mode, lints
```

## 3. Develop with hot reload

```bash
uv run ouncemcp dev server.py
```

`dev` prints the budget summary, serves on `http://127.0.0.1:8000/mcp`, restarts when you save a file, and prints one line per tool call with the tokens the result used:

```text
[ouncemcp] notes-server: 2 tools
  notes.search     182 tokens
  notes.add        118 tokens
  startup context: 300 tokens (warn at 4,000); discovery: direct (direct costs 300 tokens, within the 4,000-token threshold); counted with o200k_base
[ouncemcp] call notes.search ok 41/2,000 tokens 0.6 ms
```

## 4. Connect a host

Over stdio, hosts start the server themselves. For Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "notes": {"command": "uv", "args": ["run", "--directory", "/path/to/notes-server", "python", "server.py"]}
  }
}
```

For VS Code, add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "notes": {"type": "stdio", "command": "uv", "args": ["run", "python", "server.py"]}
  }
}
```

## 5. Write your own tools

Every tool declares an effect and uses Pydantic models for input and output:

```python
@server.tool(name="tickets.close", effect=Effect.WRITE, policy=require_scopes("tickets:write"))
async def close(args: CloseInput, ctx: ToolContext) -> Closed:
    """Close a ticket. Side effect: notifies the requester."""
    ...
```

Next: [budgets](concepts/budgets.md), [securing tools](guides/securing-tools.md).
