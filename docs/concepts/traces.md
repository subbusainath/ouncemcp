# Traces

`OunceServer(trace=MemorySink())`, `JsonlSink("calls.jsonl")`, `LoggingSink()` or `CallbackSink(fn)` records one trace per tool call: result tokens with budget and truncation, latency, attempts, outcome, error category and code, and the parent meta-tool for proxied calls (BUD-5).

Traces are redacted by default (SEC-11): principals are keyed digests, error messages and results are never recorded, and arguments only with `Tracer(sink, arguments=True)`, with secret keys dropped and strings scrubbed of credentials, hosts, paths, email addresses and phone numbers.

`ouncemcp dev` attaches a sink that prints one line per call. Details: [proposal 0009](../proposals/0009-traces.md).

**Viewing and exporting** ([proposal 0018](../proposals/0018-trace-viewer.md)):

- `ouncemcp trace calls.jsonl` prints a per-tool summary and writes an HTML report with every call, its tokens against budget and what shaping removed. You can search and filter the calls, and the page opens offline ([proposal 0020](../proposals/0020-report-ui.md)).
- `OTelSink()` sends each call to OpenTelemetry as a span, for Jaeger, Tempo, Honeycomb or any other backend.
- `ouncemcp context server.py tool --call '{"arg": 1}'` shows a result exactly as the model receives it.
