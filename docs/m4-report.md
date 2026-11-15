# M4 report: release gates

Status of the release gates (spec section 14.2) that can be checked without people or outside systems. Raw data is in `benchmarks/results/`.

## Gate 7: performance (PERF-1 to PERF-5)

Measured with `benchmarks/scripts/m4_release_perf.py` at commit `d80e03d`, on Python 3.13, Linux x86_64, 4 vCPUs, on 15 November 2026 (results in `benchmarks/results/m4-perf.json`).

| Target | Result | Status |
| --- | --- | --- |
| PERF-1: middleware overhead per call, p50 < 2 ms, p95 < 10 ms | Adds 0.24 to 0.43 ms p50 and 0.25 to 1.04 ms p95 across the four M0 cases, including truncation | Met |
| PERF-2: cold start for 50 tools < 1 s | 1.60 s median (2.0 s max). A plain FastMCP server with 50 tools takes 1.01 s on the same machine, of which 0.89 s is imports. OunceMCP adds 0.59 s | **Not met; explained below** |
| PERF-3: token counting at startup for 500 tools < 2 s | 0.30 s to list and count 500 definitions (107,900 tokens); counting alone 0.08 s | Met |
| PERF-4: audit of a 100-tool server, discovery only, < 10 s | 0.28 s over loopback HTTP. A remote server adds its own round trips | Met |
| PERF-5: startup context in `search` mode < 400 tokens | 279 tokens for a 100-tool server (21,500 in `direct` mode) | Met |

### PERF-2

The cold start includes Python importing FastMCP and the MCP SDK, about 0.9 s on this machine before any OunceMCP code runs, so the 1 s target cannot be met on top of FastMCP 4.1.6 here.

OunceMCP's own 0.59 s, from profiling:

- **About 0.25 s** loads the `o200k_base` tokenizer. BUD-1 requires counting definition tokens at startup. Most of the load is Python parsing the encoding file, which holds the interpreter lock, so loading it in a background thread would not shorten the start.
- **About 0.08 s** is OunceMCP's own imports, mostly `jsonschema` and `tiktoken`.
- **The rest** is per-tool registration (schema compaction, a validator per tool) and policy checks on the first `tools/list`.

**Proposed revision, for the owner to decide:** measure PERF-2 as the time OunceMCP adds over plain FastMCP for 50 tools, with a target of under 0.75 s, and report the plain FastMCP baseline alongside it. A long-lived server pays this once. Hosts that start a stdio server per session see it on the first request.

## Gate 6: accuracy

See the [accuracy check](#gate-6-accuracy-against-the-benchmark-baseline) below.
