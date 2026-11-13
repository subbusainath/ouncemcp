# 0018: Trace viewer, OpenTelemetry export and the results view

Status: proposed for M3 (step 1). Covers spec DX-5 and the remaining terminal part of DX-4.

## Problem

Traces (proposal 0009) went to a sink, but there was no way to look at them, no export to the observability tools teams already run, and `ouncemcp context` showed definitions but not what a call returns.

## Proposal

- **Truncation detail.** `CallTrace.truncation` records what shaping removed: list paths with kept and total counts, shortened string paths, dropped optional fields. Field names and counts only, never values (SEC-11). `CallTrace.replayed` marks idempotent replays.
- **OpenTelemetry (DX-5).** `trace.otel.OTelSink` records each call as a span `tools/call <tool>` with its real start time and duration, `ouncemcp.*` attributes (outcome, result tokens, tokenizer, budget, truncation, attempts, effect, error category and code, principal digest, parent) and an error status on failures. It uses only the OpenTelemetry API (the `otel` extra); the application configures the SDK and exporter, and without one spans cost nothing.
- **`ouncemcp trace calls.jsonl`.** Prints a summary per tool and writes one self-contained HTML file (`calls.jsonl.html`, or `--html PATH`): totals, a per-tool table (calls, errors by category, truncations, tokens, latency p50 and p95) and every call with filters by tool and outcome and the truncation details. It loads nothing from the network, works in light and dark mode and fits a phone screen. `--json` prints the summary.
- **`ouncemcp context server.py tool --call '{...}'` (DX-4).** Calls the tool in-process and prints the content the model reads, the structured content, the token count and `_meta.ouncemcp`.

## Alternatives considered

- **A live web server for traces.** A static file is enough for one file of traces, can be attached to an issue, and needs no port. A live view can come later for long-running servers; OpenTelemetry backends already cover that.

## Impact on tokens and task success

None.

## Migration notes

None. `opentelemetry-api` is already installed with FastMCP; the `otel` extra names it.
