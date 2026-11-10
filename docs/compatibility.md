# Compatibility matrix

Hosts and versions on which the inventory example has been verified, following the [host verification guide](guides/host-verification.md).

| Host | Host version | OS | OunceMCP commit | FastMCP | Transport | Result | Definition fields shown to the model | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MCP Python SDK stdio client | 2.3.0 | Linux | M0 | 4.1.6 | stdio | Pass (automated, `tests/test_stdio_e2e.py`) | n/a | 2026-11 |
| Claude Desktop | | | | | stdio | Not yet verified | | |
| VS Code | | | | | stdio | Not yet verified | | |

The official MCP conformance suite runs in CI (`.github/workflows/conformance.yml`); its results are uploaded as a build artifact.
