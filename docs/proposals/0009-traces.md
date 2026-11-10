# 0009: Per-call traces with token usage, redacted by default

Status: proposed for M1. Covers spec BUD-5 (token usage in traces) and SEC-11 (trace redaction). The trace viewer, OpenTelemetry export (DX-5) and `ouncemcp trace` are M3.

## Problem

`check` measures definitions, and each result carries its token count in `_meta`, but nothing recorded what each call actually cost, how long it took, whether it was retried or truncated, or how it failed. The `trace` package was an empty placeholder. Anything that does record calls also risks keeping secrets and personal data.

## Proposal

`OunceServer(trace=...)` takes a sink, a list of sinks, or a `Tracer`. Without it nothing is recorded and the call path is unchanged.

The middleware records one `CallTrace` per tool call, including discovery meta-tools and the calls `call_tool` proxies (with `parent` set to the meta-tool):

| Field | Meaning |
| --- | --- |
| `tool`, `effect`, `parent` | What was called; `effect` as declared |
| `outcome` | `ok`, `error` or `input_required` |
| `result_tokens`, `tokenizer`, `budget`, `truncated` | Tokens of the result as the model receives it (the shaped count for successful results, the text for errors), counted with the server's tokenizer (BUD-5) |
| `duration_ms`, `started_at`, `attempts` | Server time, and how many tries a read took (EXE-2) |
| `error_category`, `error_code` | From the error result; never the message |
| `resolved_by` | `write_resolver` when an ambiguous write was resolved |
| `principal` | A keyed digest (`p:` and 12 hex characters) by default |
| `arguments` | Only with `Tracer(arguments=True)`, redacted |

Sinks: `MemorySink` (recent traces, for tests and `ouncemcp dev`), `JsonlSink(path)`, `LoggingSink` (the `ouncemcp.trace` logger) and `CallbackSink(fn)`. A sink that raises is logged and skipped; it never fails the call.

### Redaction (SEC-11)

- No raw principal: `principals="hash"` (default) uses an HMAC with a per-process key, so traces from one process can be grouped by caller but not reversed. `"omit"` drops it, and `"plain"` keeps it for local debugging.
- No error messages, only category and code. Messages can quote data.
- No arguments unless asked. With `arguments=True`, keys that look secret (`password`, `token`, `api_key`, `authorization`, `cookie`, `session`, `card`, `ssn` and similar) become `<redacted>`, strings go through the ERR-3 `redact()` plus email and phone patterns and are cut at 200 characters, and containers are cut at 20 items and 4 levels.
- Results are never recorded, only their size.

## Alternatives considered

- **OpenTelemetry now.** It adds a dependency and a collector to set up for M1. The `CallTrace` fields map directly to span attributes, so an exporter can be added as a sink in M3.
- **Recording in the per-tool wrapper.** It would miss meta-tools, unknown tools and limit rejections, which only the middleware sees.

## Impact on tokens and task success

None on what the model receives. Measured on a small tool with a `MemorySink`, tracing added 0.03 to 0.15 ms per call at p50 across runs; with no tracer the middleware returns before any trace work.

## Migration notes

None. Tracing is off unless `trace=` is set.
