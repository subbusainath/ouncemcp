# 0017: Configuration file, health and readiness, metrics, description lints

Status: proposed for M2 (step 7 of [proposal 0010](0010-m2-plan.md), additions A2 to A5).

## Proposal

**Configuration file (A3).** `OunceServer.from_config(name, "ouncemcp.toml", **overrides)` reads `[budget]`, `[limits]`, `[discovery]`, `[tokenizer]`, `[output]` and `[security]` (`http_bind`, `stdio_policy`, `audience`, `tenant_claims`). `startup_warn` and `result_default` from the earlier sketch are accepted as aliases. Unknown sections and keys are errors that list the allowed ones. Keyword arguments override the file. `http_bind` becomes the default host for HTTP transports (`OunceServer(http_host=...)`).

**Health and readiness (A2, spec 9.2).** Every server serves `GET /healthz` and `GET /readyz` on HTTP transports. Readiness writes and reads a probe key in the store and runs each `readiness=[...]` check; any failure or exception gives `503` with the failing names. The deployment guide now covers replicas (shared `RedisStore` and `OUNCEMCP_CURSOR_KEYS`), probes and a container image.

**Metrics (A4, spec 9.4).** `OunceServer(metrics=True)` attaches a `MetricsSink` (also usable on its own as a trace sink) and serves `GET /metrics` in the Prometheus text format: calls, errors by category, truncations and result tokens per tool, and latency p50 and p95 over the last 1,000 calls. `snapshot()` returns the same as a dict. OpenTelemetry export stays in M3 (DX-5).

**Description lints (A5, CAP-5).** `ouncemcp check` warns with three new rules: `description-too-short` (under five words), `write-without-side-effects` (a write or destructive description that names no change), and `missing-usage-guidance` (in a server with several tools, no "use", "instead", "only", "before", "when" or similar). They found four tools in the examples, now fixed, which moved some measured definition sizes by 1 to 6 tokens; the documents quoting them are updated.

## Alternatives considered

- **Reading `ouncemcp.toml` automatically.** A file that silently changes a server's behavior depending on the working directory is surprising; `from_config` is explicit.
- **An OpenTelemetry dependency for metrics.** Prometheus text needs nothing installed; an exporter can be added as another trace sink.

## Impact on tokens and task success

None on what the model receives, apart from the improved example descriptions.

## Migration notes

`check` reports more warnings; none fails it.
