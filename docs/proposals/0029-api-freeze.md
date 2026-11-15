# 0029: The 1.0 API freeze

Status: draft for the owner's review (M4 exit criterion "API frozen").

## Problem

After 1.0, people will build on OunceMCP and expect upgrades not to break them. That needs three things: a clear line between public and internal names, promises about what may change and how, and a mechanical check that catches accidental changes. The check exists (`tests/test_api_surface.py`, added in M4). This proposal draws the line and states the promises.

## What is public

`tests/api_snapshot.json` records 114 names in 8 modules, with every public signature.

### Stable at 1.0

Semantic versioning applies to these: no breaking change before 2.0.

| Module | Names |
| --- | --- |
| `ouncemcp` | `OunceServer` (constructor, `tool`, `job`, `resource`, `prompt`, `run`, `http_app`, `list_tools`, `call_tool`, `cost_report`, `discovery`, `measure_recall`, `budget_overrides`, `wrap`, `from_config`, `check_ready`, `add_trace_sink`, `catalog`, `tools`), `ToolContext` (all public members), `Effect`, `Budget`, `BudgetOverride`, `BudgetOverrideWarning`, `Detail`, `Paginated`, `Cursor`, `CursorSigner`, `Limits`, `RetryPolicy`, `Idempotency`, `JobResult`, stores (`OunceStore`, `MemoryStore`, `KeyValueStore`, `RedisStore`), errors (`OunceError` and its subclasses), tokenizers (`TokenCounter`, `TiktokenCounter`, `CallableCounter`, `get_counter`), cost reports (`measure_tool`, `measure_tools`, `ServerCost`, `ToolCost`), `DiscoveryDecision`, `RecallCase`, `RecallReport` |
| `ouncemcp.security` | Identity (`Identity`, `current_identity`, `identity_from_token`, `ANONYMOUS_IDENTITY`), policies (`Policy`, `PolicyRequest`, `allow_all`, `authenticated`, `require_scopes`, `require_tenant`, `writes_require`, `all_of`, `any_of`, `audience_matches`), state (`StateSigner`, `InvalidState`), caches (`PartitionedCache`, `CacheView`), outbound safety (`check_url`, `checked_addresses`, `safe_client`, `PinnedTransport`, `URLNotAllowed`, `guarded_client`, `passthrough_guard`, `check_outbound`, `TokenPassthrough`), `UnsafeInputRequest` |
| `ouncemcp.client` | `Broker`, `BrokerResult`, `BrokerTool`, `BrokerError`, `CrossServerCall`, `META_DEFINITIONS`, `OunceOAuth`, `registration_method` |
| `ouncemcp.trace` | `CallTrace`, `Tracer`, `TraceSink`, `MemorySink`, `JsonlSink`, `LoggingSink`, `CallbackSink`, `redact_text`, `redact_value` |
| `ouncemcp.testing` | `as_caller`, `make_token` |
| `ouncemcp.eval` | the security suite: `run_security_suite`, `SecurityReport` and the probes |

### Provisional at 1.0

These are public and documented, but may change in a minor release, with the change named in the changelog. They depend on fast-moving parts: model APIs, benchmark methods and report formats.

- `ouncemcp.eval.agent`: task suites, models and reports.
- `ouncemcp.eval.bench`.
- `ouncemcp.ui`.

### To make internal before 1.0 (proposed)

These are plumbing that is public only by accident. Each keeps working for one release with a `DeprecationWarning`.

- `ouncemcp.security.resolve_http_options`: used by `OunceServer.run`; nobody needs to call it.
- `ouncemcp.security.partition_key`: used by `PartitionedCache` only.
- `ouncemcp.shape_result`, `ouncemcp.PageWindow`, `ouncemcp.WriteAttempt`, `ouncemcp.WriteResolver`: kept public but documented as advanced. The proposal is to keep them stable, because `write_resolver=` and custom shaping are real extension points.

## What else is part of the contract

These are visible to users and agents outside Python, so they get the same promise as the stable API.

- **Wire contract:**
    - the error categories and codes;
    - the `_meta["ouncemcp"]` keys on results;
    - `_meta["ouncemcp/parallelSafe"]` on tools;
    - the `handles.read` and `jobs.get` tools and their inputs.
- **Command line:** command names, flags and exit codes of `ouncemcp check`, `context`, `audit`, `eval`, `bench` and `trace`.
- **Report files:** JSON reports carry `report_version`. A change that removes or renames a field bumps it, and readers of older versions keep working for one minor release.
- **Lint rule names**, since CI configurations refer to them.

## The rules after 1.0

1. **Semantic versioning.**
    - Patch releases fix bugs.
    - Minor releases add names and options; provisional names may change here.
    - Only a major release removes or changes stable names.
2. **Deprecation before removal.** A stable name is deprecated (warning, changelog, docs) in a minor release and removed no earlier than the next major.
3. **Snapshot updates in the PR.** Every change to the public API updates `tests/api_snapshot.json` in the same PR. The PR says whether the change is an addition, a provisional change or a deprecation.
4. **Defaults are API.** Changing a default (budgets, `output_schemas`, discovery thresholds, `stdio_policy`) counts as a breaking change unless it only makes results smaller or safer, and it needs a proposal.
5. **Supported versions.**
    - Python: 3.11 and later, following CPython's end-of-life dates.
    - FastMCP: today OunceMCP pins it exactly (`fastmcp==4.1.6`). At 1.0 this should become a tested range within one FastMCP major (`>=4.1.6,<5`), with CI testing the lowest and highest versions in that range.

## Before declaring the freeze

- [x] Snapshot test of every public name and signature, stable on Python 3.11 to 3.13.
- [x] API reference generated from the code.
- [x] Missing exports added: `JobResult`, `BudgetOverride`, `BudgetOverrideWarning`; `ouncemcp.trace` and `ouncemcp.eval` tracked.
- [ ] Owner review of the stable and provisional lists above.
- [ ] Deprecate the two internal security helpers.
- [ ] Change the FastMCP pin to a tested range.
- [ ] Add a CHANGELOG with the 1.0 entry, and mark the stable modules in the API reference.

## Alternatives considered

- **Freezing everything exported today.** That would lock in plumbing such as `resolve_http_options` and the fast-moving eval APIs.
- **No provisional tier.** Then the evaluation tools could not keep up with model APIs without a major release.

## Impact on tokens and task success

None.
