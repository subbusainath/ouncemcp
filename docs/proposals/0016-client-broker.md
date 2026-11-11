# 0016: The client broker

Status: proposed for M2 (step 6 of [proposal 0010](0010-m2-plan.md)). Covers spec CLI-1 to CLI-8.

## Problem

Agents and hosts that connect to many servers load every definition from every server, cannot tell two servers' `search` tools apart, and treat whatever a server returns as something the model may act on.

## Proposal

```python
from ouncemcp.client import Broker

async with Broker(
    {"crm": "https://crm.example.com/mcp", "wiki": "examples/inventory/server.py", "git": ["uvx", "mcp-server-git"]},
    elicitation_handler=ask_user,      # offered to servers only if given
    approver=confirm_cross_server,     # needed for writes suggested by another server
) as broker:
    mode, definitions = await broker.host_tools()      # "direct" or "search"
    result = await broker.call("crm.search", {"query": "acme"})
    print(result.tokens, result.for_model())
```

| Requirement | Behavior |
| --- | --- |
| CLI-1 | Server ids are 1 to 32 lowercase characters; tools are `<id>.<tool>`. Targets: URL, server file, command (string or list), or a FastMCP or OunceServer object |
| CLI-2 | `host_tools()` returns every definition while the total fits `Budget.startup_tokens`, else `search_tools`, `describe_tool` and `call_tool`, which `handle()` answers with BM25 over names and descriptions across servers |
| CLI-3 | Definitions are cached for the server's `ttlMs` (FastMCP's own cache is bypassed so there is one cache); `cacheScope` is recorded; `notifications/tools/list_changed` drops a server's cache |
| CLI-4 | Structured output is validated against `outputSchema` (the SDK checks too); a mismatch gives an error result with `broker_error: invalid_output` and the data withheld |
| CLI-5 | Elicitation, sampling and roots are offered to servers only when the host passes a handler. OunceMCP servers then refuse approvals (`approval_unavailable`) instead of asking a client that cannot show them: the approval gate now checks that the client declared elicitation |
| CLI-6 | A deadline per call (default 60 s); connections opened once and reused; a server that fails to connect is listed in `unavailable` and skipped; after `failure_threshold` consecutive failures a server is cut off for `cooldown_s` |
| CLI-7 | Every `BrokerResult` has `tokens` and `tokenizer` |
| CLI-8 | Results are `untrusted`, and `for_model()` labels their source. A call with `caused_by=` a result from another server, to a tool that is not marked read-only, needs the host's `approver`; without one it is refused (`cross_server_call_refused`). Server instructions are returned per server by `instructions()`, never merged |

## Alternatives considered

- **Trusting `readOnlyHint` for CLI-8.** Annotations are hints (SEC-3), but a broker has nothing else to go on for third-party servers. A tool without the hint is treated as a write, so a missing or wrong hint fails safe.
- **Reusing FastMCP's client cache.** It does not expose `cacheScope` or tie invalidation to the broker's view of each server.

## Impact on tokens and task success

Hosts get one catalog. Once the combined definitions exceed the startup budget, the startup cost drops to the three meta-tools.

## Migration notes

The approval gate now also requires the client to declare elicitation before asking. Clients that never declared it already could not answer, and now get `approval_unavailable` instead of an `input_required` they cannot handle.
