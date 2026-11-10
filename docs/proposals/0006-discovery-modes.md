# 0006: Discovery modes, `direct` and `search`

Status: proposed for M1. Covers spec DISC-1 to DISC-5 and DISC-7 in full and DISC-6 in part (see [spec section 8.1.3](../spec.md)). DISC-8 (`code` mode) is not part of this proposal.

Numbered 0006 because 0003 (signed cursors), 0004 (tokenizer) and 0005 (counting scope and output schemas) are in open pull requests.

## Problem

Every tool definition is sent to the model before the first message. At about 110 tokens per tool in a typical server, 40 tools already cost more than the 4,000-token startup budget in [spec section 8.1.2](../spec.md). M0 could only warn about this. The spec asks for the server to choose between sending every tool (`direct`) and sending a small search surface (`search`) by itself, without letting a hidden tool become a way around authorization.

## Proposal

### API

```python
server = OunceServer(
    "inventory",
    budget=Budget(startup_tokens=4_000),
    discovery="auto",            # "auto" (default) | "direct" | "search"
    pinned=["inventory.search"], # stay in tools/list in search mode
    search_results=5,            # most tools one search returns
)

@server.tool(name="payroll.export", effect=Effect.READ, auth=require_scopes("payroll"))
```

- `await server.discovery()` returns a `DiscoveryDecision`: `setting`, `mode`, `direct_tokens`, `search_tokens`, `threshold`, `pinned` and a one-sentence `reason`.
- `await server.catalog()` returns every registered definition as a client receives it, for `check`.
- `await server.measure_recall(cases, k=None)` returns a `RecallReport` (see DISC-6 below).
- `@server.tool(auth=...)` takes a FastMCP authorization check or a list of them. It is the only security-related option added here: search needs something to filter on.

### DISC-1: automatic choice

`auto` measures two numbers with the server's tokenizer, on definitions as a client receives them (BUD-1):

- `direct_tokens`: every registered tool.
- `search_tokens`: the three meta-tools plus pinned tools.

It selects `search` only when `direct_tokens` is above `Budget.startup_tokens` and `search_tokens` is smaller. At or below the threshold, or when search would not save anything, it selects `direct`. `direct` and `search` override the choice. The decision is made on the whole registered catalog, not per caller, so one server has one mode and one meta-tool list. It is recomputed after a tool is registered, and logged by `run()` and reported by `check`.

### Search surface

Three meta-tools, always in this order and before any pinned tool:

| Meta-tool | Arguments | Returns |
| --- | --- | --- |
| `search_tools` | `query`, `detail` (`name`, `summary`, `full`; default `summary`) | Ranked matches, at most `search_results` |
| `describe_tool` | `name`, `detail` (default `full`) | One definition |
| `call_tool` | `name`, `arguments` | The tool's own result, shaped as usual |

Results are compact JSON text only. The meta-tools declare no output schema, so they stay small (279 tokens in total) and results are not sent twice.

### DISC-4: detail levels

| Level | Search returns | `describe_tool` returns |
| --- | --- | --- |
| `name` | `{"tools": ["a", "b"]}` | `{"name": "a"}` |
| `summary` | name, first sentence of the description (up to 160 characters), and `params`, where `?` marks an optional parameter | the same |
| `full` | the definition as `tools/list` would send it | the same |

### DISC-2 and DISC-3: authorization

- Search and `describe_tool` read the catalog through FastMCP's `get_tool_catalog`, which applies each tool's `auth` check. An unauthorized tool is never matched, described, or ranked.
- `describe_tool` and `call_tool` answer an unauthorized name and an unknown name identically, so the answer does not reveal that a tool exists.
- Hiding is not the control. Tools missing from `tools/list` are still called by name, and the server's own call path applies the same `auth` check to the guessed name. Both the direct call and `call_tool` are tested.
- FastMCP skips component `auth` checks on stdio, where the local user is trusted. OunceMCP inherits that and does not change it.

### DISC-5: stable definitions

The three Tool objects are built once, with fixed names, descriptions and schemas that say nothing about the catalog. The tests compare the serialized definitions across servers with 1, 30 and 60 tools, with and without pinned tools, and before and after registering more tools. They are byte-identical. The names `search_tools`, `describe_tool` and `call_tool` are reserved and cannot be used for a tool.

### DISC-7: built on FastMCP

`OunceSearchTransform` subclasses FastMCP's `BM25SearchTransform` and keeps its ranking, its catalog access and its transform plumbing. It adds `describe_tool`, detail levels, stable definitions, and clearer errors. A custom `call_tool` replaces FastMCP's only to improve the error message.

### DISC-6: recall

The spec asks for recall to be measured in the eval kit, which does not exist yet. This proposal adds the measurement it will call:

```python
report = await server.measure_recall([
    RecallCase("find unpaid invoices", {"billing.invoices"}),
])
report.mean_recall; report.missed_tools; report.render_text()
```

It ranks the whole catalog with the same index search uses and reports recall at `k` per case and overall. It works in `direct` mode too, so it can inform the choice of mode.

### `check`

`ouncemcp check` measures the whole catalog, so per-tool budgets and the fail exit code apply to tools that search mode sends only on request. It also prints the mode and the numbers behind it, and `check --json` has a `discovery` object.

## Alternatives

- **Choose the mode per caller.** Authorization changes the catalog per principal, so the cost would too, and the meta-tool list could change between callers. One decision per server keeps prompt caches valid.
- **Use FastMCP's transform unchanged.** It has no `describe_tool` and no detail levels, and its `call_tool` error does not say what to do next.
- **Return structured content from the meta-tools.** That adds an output schema to each definition and doubles every result.
- **Put the choice in the framework's client.** Not every host runs OunceMCP's client.

## Impact

With the synthetic 10-tool-family servers used in the tests (about 112 tokens per tool, `o200k_base`):

| Tools | `direct` | `search` | `auto` picks |
| ---: | ---: | ---: | --- |
| 10 | 1,117 | 279 | `direct` |
| 20 | 2,234 | 279 | `direct` |
| 40 | 4,468 | 279 | `search` |
| 100 | 11,170 | 279 | `search` |

Search mode costs the agent extra calls (a search and then a call). Whether the saved tokens are worth those calls depends on the task and is the benchmark's question, not this proposal's.

Existing servers with fewer tools than the threshold are unchanged. Servers over it switch to `search` on upgrade, so `discovery="direct"` restores the old behavior.

## Migration

- `OunceServer.cost_report()` and `list_tools()` describe what clients receive, so in search mode they show the meta-tools. Use `catalog()` for the full set.
- `check` now measures `catalog()` for `OunceServer`. For direct-mode servers the numbers are the same as before.
- Tool names `search_tools`, `describe_tool` and `call_tool` are now reserved.

## Not covered

- `code` mode (DISC-8) and cross-server discovery in the client (CLI-2).
- Recall inside the eval kit, and recall thresholds in CI.
- Other ranking strategies. BM25 is the only one, as in FastMCP's default.
- Authorization policy beyond passing `auth=` through (the SEC requirements).
