# Changelog

All notable changes to OunceMCP. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [semantic versioning](https://semver.org/) with the promises in [proposal 0029](https://github.com/subbusainath/ouncemcp/blob/main/docs/proposals/0029-api-freeze.md).

## Unreleased

### Security

Found by an internal audit ([report](https://subbusainath.github.io/ouncemcp/maintainers/security-audit-2026-11/)); upgrading is recommended.

- **High:** error redaction could take minutes on a long argument echoed in an error message, letting one request stall the server. The patterns are now linear and the scanned text is capped.
- `check_url` and `safe_client` refuse IPv6 addresses that carry a private IPv4 address (NAT64, IPv4-compatible, 6to4, Teredo).
- Calls to unknown tool names share one `(unknown tool)` metric series; the metrics sink is bounded.
- `MemoryStore` removes expired records on writes and is bounded (`max_entries`, default 100,000).
- A continuation token that carries an approval works once.
- Audit reports leave out URL credentials and query strings.

## 1.0.0rc2 (15 November 2026)

### Fixed

- The project description on PyPI (the README) used relative links, which PyPI resolves against pypi.org, so every link returned a 404. All links are now absolute.

## 1.0.0rc1 (15 November 2026)

Release candidate for 1.0.0. Install it with `pip install --pre ouncemcp` or `pipx install "ouncemcp==1.0.0rc1"`. 1.0.0 follows the external security review; the API below will not change before it unless the review requires it.

The first stable release. The public API is frozen: names listed as stable in proposal 0029 do not change in a breaking way before 2.0.

### Stable

- **Server and tools** (`ouncemcp`): `OunceServer` with typed tools, declared effects, resources, prompts and background jobs; `ToolContext`; budgets and per-tool overrides; response shaping with `Detail`, `Paginated` and signed `Cursor`s; automatic discovery; errors in eight categories; deadlines, retries and limits; stores, idempotent writes and approvals; handles and resumable jobs; tokenizers and cost reports.
- **Security** (`ouncemcp.security`): verified identity, policies, partitioned caches, signed state, safe outbound fetches and token-passthrough guards.
- **Clients** (`ouncemcp.client`): the `Broker` over many servers and `OunceOAuth`.
- **Traces** (`ouncemcp.trace`): the tracer, its sinks and redaction.
- **Testing** (`ouncemcp.testing`) and the **security suite** (`ouncemcp.eval`).
- **Contracts outside Python:** error categories and codes, `_meta["ouncemcp"]` keys, the `handles.read` and `jobs.get` tools, command names, flags and exit codes, report `report_version`, and lint rule names.

### Provisional

May change in a minor release, with the change named here: `ouncemcp.eval.agent` (task suites and models), `ouncemcp.eval.bench`, `ouncemcp.ui` and `AnthropicCounter`.

### Added

- `AnthropicCounter` and `--tokenizer anthropic:MODEL`: token counts from Anthropic's counting API, through the official SDK (`pip install "ouncemcp[anthropic]"`). Provisional (spec BUD-6).

### Deprecated

- `ouncemcp.security.resolve_http_options` and `ouncemcp.security.partition_key` are internal. They still work but raise a `DeprecationWarning`, and will be removed in 1.1. `OunceServer.run` and `PartitionedCache` use them for you.

### Changed

- FastMCP is supported as a range, `>=4.1.6,<5`, instead of an exact pin. CI tests the lowest version (the lockfile) and the newest release in the range.

### Supported versions

- Python 3.11, 3.12 and 3.13.
- FastMCP 4.1.6 and later 4.x releases.
