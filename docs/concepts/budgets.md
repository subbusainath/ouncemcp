# Context budgets

Every token a host sends the model costs time, money and attention. OunceMCP measures tokens where the model sees them and enforces limits in two places.

**Definitions.** `ouncemcp check` and every server start count each tool definition as a client receives it from `tools/list`: the wire JSON with `name`, `description`, `inputSchema` and `outputSchema` (BUD-1, BUD-7). `title`, annotations and `_meta` are not counted. Defaults (`Budget`): target 250, warn 500, fail 1,000 tokens per tool; warn at 4,000 startup tokens.

**Results.** Every result is shaped to fit its budget (default 2,000 tokens; `detailed` responses up to the 8,000 ceiling). Shaping trims lists first, then long strings, then optional fields, and always keeps the result valid against the advertised output schema. If even the smallest valid result is too large, the agent gets an actionable `result_too_large` error instead (RES-8).

```python
server = OunceServer("inventory", budget=Budget(result_tokens=1_500))

@server.tool(name="inventory.search", effect=Effect.READ, result_tokens=800, min_items=2)
```

**Tokenizer.** Counts use `o200k_base` by default. Choose another tiktoken encoding or your own function with `OunceServer(tokenizer=...)`; every report names the tokenizer ([proposal 0004](../proposals/0004-pluggable-tokenizer.md)).

**Output schemas.** Typed output schemas make definitions larger. Set `output_schemas="off"` or `"auto"` on the server, or `output_schema=` on a tool ([proposal 0005](../proposals/0005-counting-scope-and-output-schema-modes.md)).

**In traces.** With `OunceServer(trace=...)`, each call records the result tokens it used against its budget ([traces](traces.md)).
