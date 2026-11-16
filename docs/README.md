---
hide:
  - navigation
  - toc
---

<div class="hero" markdown>

<p class="hero-logo"><img src="assets/logo.png#only-light" alt="OunceMCP"><img src="assets/logo-dark.png#only-dark" alt="OunceMCP"></p>

# OunceMCP { .visually-hidden }

<p class="tagline">Build MCP servers that agents use well: small tool definitions, results that fit the context window, writes that never happen twice, and security that holds when the model is wrong.</p>

[Read the Book](book/index.md){ .md-button .md-button--primary }
[Quickstart](quickstart.md){ .md-button }

</div>

OunceMCP is a Python framework on [FastMCP](https://gofastmcp.com). Every tool definition and every result is measured in tokens and kept within a budget you set, and writes, approvals and tenant isolation are built in rather than left to each tool.

```python title="A tool: typed, bounded, and with an error the model can act on"
--8<-- "examples/book/shelf/step1_catalog.py:get"
```

```console
$ ouncemcp check examples/book/shelf/step1_catalog.py
Startup context  297 tokens across 2 tools  [OK, budget 4,000]

Tool           Total   Desc  Input  Output  Other  Status
books.search     162     23     54      72     13  OK
books.get        135     21     38      64     12  OK
```

## Where to start

<div class="grid cards" markdown>

-   :material-book-open-variant:{ .lg .middle } __The OunceMCP Book__

    ---

    Learn the framework by building one library server, chapter by chapter. Every stage is a tested file.

    [:octicons-arrow-right-24: Start reading](book/index.md)

-   :material-rocket-launch:{ .lg .middle } __Quickstart__

    ---

    `ouncemcp init`, tests and CI, and a server running in your MCP host in five minutes.

    [:octicons-arrow-right-24: Get started](quickstart.md)

-   :material-api:{ .lg .middle } __Reference__

    ---

    Every public name, every error category and every `ouncemcp check` lint rule.

    [:octicons-arrow-right-24: Browse the reference](reference/index.md)

</div>

## What you get

<div class="grid cards" markdown>

-   __Measured context__

    `ouncemcp check` counts every definition the way a client receives it, and every result reports the tokens it used.

-   __Results that fit__

    Results over budget are trimmed and stay valid against their schema. Paged lists resume exactly where the trim stopped.

-   __Safe writes__

    Approvals ask a person before anything changes; idempotent writes are replayed, never repeated.

-   __Security by default__

    Identity comes from the verified token, policies hide and refuse tools, and cursors and handles are bound to the caller.

-   __Large catalogs__

    Above the startup budget, discovery switches to three small search tools automatically.

-   __Proof, not claims__

    Task suites, a benchmark harness and a trace viewer measure what agents actually do with your server.

</div>

## Project

The [specification](spec.md) holds the requirements, roadmap and milestone status. Design decisions are recorded as [proposals](proposals/README.md), and results are in the [M0](m0-report.md), [M2](m2-report.md) and [M4](m4-report.md) reports. To build this site locally, run `uv pip install -e ".[docs]"` and then `uv run mkdocs serve`.
