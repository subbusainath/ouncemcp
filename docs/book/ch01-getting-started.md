# 1. Getting started

## What MCP is, in one page

The Model Context Protocol (MCP) connects an AI application (the **host**, such as Claude Desktop, VS Code or your own agent) to **servers** that offer it capabilities. A server offers three kinds of things:

- **Tools**: functions the model can call, such as `books.search`.
- **Resources**: data the host can show or attach, such as a file or a record.
- **Prompts**: reusable messages a user can pick.

When a session starts, the host asks the server for its tools and sends every definition (name, description, input schema and output schema) to the model as part of its context. When the model calls a tool, the result goes into the context too.

That is the whole problem this book is about. **Everything a server sends is read by the model, on every turn, and paid for in tokens, time and attention.** A server with 40 verbose tools can use 20,000 tokens before the user has typed a word. A search that returns 500 rows can push the conversation out of the context window. A write that times out and is retried can charge a card twice.

OunceMCP is a framework on top of [FastMCP](https://gofastmcp.com) that makes the right thing the default: it measures every definition and result, shapes results to a budget, and gives writes and security first-class support.

## Installing

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install ouncemcp      # the ouncemcp command
ouncemcp --version
```

To follow the book with its code, clone the repository:

```bash
git clone https://github.com/subbusainath/ouncemcp
cd ouncemcp
uv sync
```

`pipx install ouncemcp` works too. To use the clone instead, run `uv tool install -e .`.

## The commands you will use

| Command | What it does |
| --- | --- |
| `ouncemcp init NAME` | Scaffold a new server with tests and CI |
| `ouncemcp dev server.py` | Run with hot reload and a token count per call |
| `ouncemcp check server.py` | Measure every definition and run the lints |
| `ouncemcp context server.py TOOL` | Show exactly what the model receives |
| `ouncemcp eval suite.yaml` | Run a task suite with a model |
| `ouncemcp trace` | Open the trace and report viewer |
| `ouncemcp audit TARGET` | Measure any MCP server, even one not built with OunceMCP |

Appendix A lists every option.

## A project of your own

If you want a fresh project rather than the book's code, `init` writes a working server, tests and a CI workflow:

```bash
ouncemcp init notes-server
cd notes-server
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

The [quickstart](../quickstart.md) walks through that project and connecting it to a host.

## Summary

- The host sends every tool definition and every result to the model. Size is cost.
- OunceMCP measures that cost and keeps it within budgets you set.
- `check`, `dev` and `context` are the tools you will reach for most.

Next, we write the first version of Shelf.
