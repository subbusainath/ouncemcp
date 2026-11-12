# Compatibility matrix

Hosts and versions on which the example servers have been verified, following the [host verification guide](guides/host-verification.md).

| Host | Host version | OS | OunceMCP | FastMCP | Transport | Servers | Result | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MCP Python SDK stdio client | 2.3.0 | Linux | M0 | 4.1.6 | stdio | inventory | Pass (automated, `tests/test_stdio_e2e.py`) | 2026-11 |
| Official MCP conformance suite 0.2.0-alpha.12 | revision 2026-07-28 | Linux (CI) | `fadf7eb` (PR #14) | 4.1.6 | Streamable HTTP | conformance target | Pass: 36 checks passed; the 5 failures are the expected ones in `conformance/expected-failures.yml` (completions, image, audio, embedded-resource and mixed tool content) | 2026-11-11 |
| Claude Desktop (Chat) | not recorded | macOS | `a36e51c` (`main` after PR #14) | 4.1.6 | stdio | inventory, ticketing | Pass, reported by the owner | 2026-11-12 |
| Cursor (Agent) | not recorded | macOS | `a36e51c` | 4.1.6 | stdio | inventory, ticketing | Pass, reported by the owner | 2026-11-12 |
| VS Code (Copilot agent mode) | | | | | stdio | | Not yet verified | |
| Claude mobile app | | | | | Streamable HTTP (remote connector) | | Not yet verified; needs the server at a public HTTPS URL | |

## What the host runs covered

The owner ran the checklist in the [host verification guide](guides/host-verification.md) in each host: listing running VMs (`inventory.search`), details for one (`inventory.get`), paging through a region with `next_cursor`, and closing a ticket through the approval flow (`tickets.close`), declining once and accepting once.

## Not yet recorded

- **Host versions** for Claude Desktop and Cursor. Add them from Claude → About Claude and Cursor → About Cursor.
- **Which definition fields each host shows the model** (BUD-7 host study): whether `outputSchema`, `title` or annotations reach the model. This decides the output-schema default and fills `verified_hosts`.
- **Per-prompt observations** (approval form shown or not, how read and write tools are presented), so later runs can be compared.

## Notes from the runs

- In Claude Desktop, MCP servers from `claude_desktop_config.json` are available in the **Chat** tab. Cloud sessions in the **Code** tab run on Anthropic's servers and do not see them.
- A `claude_desktop_config.json` that is not valid JSON is ignored without an error in the app. Check it with `python3 -m json.tool <file>` after editing.
- Desktop apps may not find `uv` on `PATH`; use its full path (`which uv`).
