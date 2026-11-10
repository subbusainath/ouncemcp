# Traces

`OunceServer(trace=MemorySink())`, `JsonlSink("calls.jsonl")`, `LoggingSink()` or `CallbackSink(fn)` records one trace per tool call: result tokens with budget and truncation, latency, attempts, outcome, error category and code, and the parent meta-tool for proxied calls (BUD-5).

Traces are redacted by default (SEC-11): principals are keyed digests, error messages and results are never recorded, and arguments only with `Tracer(sink, arguments=True)`, with secret keys dropped and strings scrubbed of credentials, hosts, paths, email addresses and phone numbers.

`ouncemcp dev` attaches a sink that prints one line per call. Details: [proposal 0009](../proposals/0009-traces.md).
