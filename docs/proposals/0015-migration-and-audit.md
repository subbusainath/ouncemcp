# 0015: Migration adapter, audit command and URL checks

Status: proposed for M2 (step 5 of [proposal 0010](0010-m2-plan.md)). Covers DX-7, AUD-1 to AUD-13 (HTML reports, AUD-10 P2, excepted), part of SEC-9, and the migration pilot.

## Problem

Maintainers of existing servers had no way in short of rewriting every tool, and no way to measure a server not built with OunceMCP.

## Proposal

**`OunceServer.wrap(mcp, effects=...)`** puts an existing FastMCP server under OunceMCP without changing its tools: middleware (errors, limits, deadlines, traces), discovery and the stdio filter are added to it, and its tools are recorded so `check`, `dev` and `cost_report` measure them. Unnamed effects count as reads. The given server is changed in place, so it is wrapped once.

**`ouncemcp audit <target>`** connects as a client to a URL (with `--header` or `--bearer`), a server file (in-process), or a command after `--` (stdio; `--env` and `--inherit-env` for the child). It reports:

| Requirement | Output |
| --- | --- |
| AUD-2 | The negotiated protocol version |
| AUD-3, AUD-4, AUD-6 | Startup tokens for tools, resources and prompts; per tool by description, input, output and other; the tokenizer |
| AUD-5 | `long-description` (over 200 tokens), `deep-nesting` (over 4 levels), `large-enum` (over 25 values), `many-optional-fields` (over 12), `duplicated-text` (a sentence in 3 or more descriptions) |
| AUD-7, AUD-12 | Result-token median and p95, only for tools named with `--sample tool=JSON`; nothing else is called |
| AUD-8 | Connect time and `tools/list` p50 and p95 over 5 calls |
| AUD-9 | A discovery recommendation from the same rule as `discovery="auto"`, with the startup cost if followed |
| AUD-10 | Terminal, `--json`, `--markdown`, `--output PATH` |
| AUD-11 | `--fail-startup N`, `--fail-tool N`; exit 1 when exceeded, 2 when the target cannot be audited |
| AUD-13 | `--compare old.json` lists regressions over 5%; `--fail-on-regression` |

Exit criterion check: on the inventory example, audit over stdio and in-process both report 488 tokens, the same as `check`.

**`check_url(url)`** (in `ouncemcp.security`) refuses schemes other than those allowed, metadata host names, and any host resolving to a loopback, private, link-local, shared, reserved or multicast address, with an optional host allowlist. It does not stop DNS rebinding between check and fetch; the docstring says so. Full SEC-9 (pinned connections, redirect handling) stays in P1.

**Migration guide** (`docs/guides/migrating-from-fastmcp.md`) and the **markitdown-mcp pilot** (`examples/markitdown`): result for a 60-section document from 14,760 tokens to 388 plus pages; metadata and private URLs refused; `file:` access opt-in and confined.

## Alternatives considered

- **Vendoring the markitdown-mcp source.** The tool is small and the pilot only needs its interface; re-creating it with the same name, description and behavior avoids depending on the package and its heavy extras in tests.

## Impact on tokens and task success

None for existing servers. Wrapped servers gain discovery, which can lower startup tokens for large catalogs.

## Migration notes

None.
