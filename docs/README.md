# OunceMCP documentation

OunceMCP is a Python framework on FastMCP for building MCP servers that are fast, low-noise and safe for agents by default.

| Start here | |
| --- | --- |
| [The OunceMCP Book](book/index.md) | Learn the framework by building one server, chapter by chapter |
| [Quickstart](quickstart.md) | A working server, tests and CI in five minutes |
| [Concepts](concepts/budgets.md) | Budgets, discovery, response shaping, security, execution, traces |
| [Guides](guides/securing-tools.md) | Securing tools, HTTP deployment, testing, host verification |
| [Error reference](errors/validation.md) | Every error category and code, with causes and fixes |
| [Rule reference](rules/input-after-effect.md) | Every `ouncemcp check` lint rule |
| [Compatibility](compatibility.md) | Hosts and versions verified to work |

Project documents:

| Document | Contents |
| --- | --- |
| [spec.md](spec.md) | Project specification: requirements, API sketch, CLI, benchmark plan, roadmap and milestone status |
| [m0-report.md](m0-report.md) | M0 prototype results |
| [performance-research.md](performance-research.md) | Research on MCP latency, context bloat, and the open-source launch strategy |
| [proposals/](proposals/README.md) | Design proposals for API or behavior changes |

Build this site locally with `uv pip install -e ".[docs]" && uv run mkdocs serve`.
