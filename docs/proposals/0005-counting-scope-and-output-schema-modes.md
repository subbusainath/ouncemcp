# 0005: Counting scope and output-schema modes

Status: implemented for M1 (BUD-7 declared scope, BUD-9 modes and share). Per-host data for BUD-7 is P1 and not part of this change. See [spec section 8.1.2](../spec.md). The number 0005 leaves 0003 (signed cursors) and 0004 (pluggable tokenizer) free for the open PRs that use them.

## Problem

- **BUD-7.** `check` reports one token figure per tool, but nothing says what the figure includes. Hosts show the model different parts of a definition, so a number with no scope can be read as more exact than it is.
- **BUD-9.** Output schemas are the largest single part of a definition: 241 of the inventory example's 488 definition tokens. A server could only turn them all off (`output_schemas=False`), tool by tool was not possible, and `check` did not say how much they cost. M0 left open whether models see them at all, which is the BUD-7 host study. Until that is answered, the framework should make the trade easy to measure and to change, and keep the safe default.

## Proposal

### Declared counting scope (BUD-7)

A tool definition's cost is the token count of the compact JSON of four fields, taken from the definition as a client receives it in `tools/list`: `name`, `description`, `inputSchema`, `outputSchema`. Everything else is not counted. The named uncounted fields are `title`, `annotations` and `_meta`.

- `ouncemcp.core.costs` has `COUNTED_FIELDS`, `UNCOUNTED_FIELDS` and `counting_scope()`.
- `ouncemcp check --json` has a top-level `counting_scope` object:

```json
{
  "counted": ["name", "description", "inputSchema", "outputSchema"],
  "not_counted": ["title", "annotations", "_meta"],
  "other_fields": "not counted",
  "measured_on": "compact JSON of each tool definition as an MCP client receives it in tools/list, fields in wire order",
  "verified_hosts": [],
  "note": "Hosts may show the model more or fewer fields than these. ..."
}
```

- `verified_hosts` is an empty list on purpose. It will list hosts once the per-host study (P1) has recorded which fields reach the model. The `note` says so in the meantime.
- The text report prints `Counted` and `Not counted` lines, and `ouncemcp context` prints the same scope after its token summary. `ServerCost.to_dict()` includes the scope too.
- A test shows the declaration is true: adding a long `title`, `annotations` and `_meta` to a tool does not change its count, and lengthening the description does.

### Output-schema modes (BUD-9)

```python
OunceServer("inventory", output_schemas="auto")              # server default: "on" | "off" | "auto"

@server.tool(name="inventory.search", effect=Effect.READ, output_schema="off")   # per tool
```

| Mode | The tool's definition |
| --- | --- |
| `on` (default) | advertises its output schema |
| `off` | does not advertise it |
| `auto` | advertises it, unless that would put the definition over `Budget.tool_definition_target` (250 tokens by default), in which case it does not |

- `output_schemas` on the server also still accepts `True` and `False`, which mean `on` and `off`. Any other value raises `ValueError`. The tool-level `output_schema` takes only the three strings, or `None` to follow the server.
- `auto` is decided once, when the tool is registered, by counting the definition with its compacted schema with the server's counter. It uses the in-process serialization, which can differ from the wire by a token or two (the M0 wire-order finding), so a tool that sits within two tokens of the target can land on either side. `check` measures the wire, so a tool that `auto` kept can still be reported at a size a token or two over the target.
- The decision depends on the budget, not on the schema: the schema is dropped whenever the definition with it is over the target, even if the definition would still be over the target without it. `auto` never changes a tool that fits.
- An unadvertised schema changes only the definition. Results are still validated by Pydantic and shaped against the model's schema, and they still return structured content when it fits. Shaping may fall back to text-only truncation when no schema is advertised, as before (RES-8).
- `OunceServer.tools[name]` records both the requested mode (`output_schema`) and the outcome (`output_schema_advertised`).

### The share in `check` (BUD-9)

The share is the definition tokens that advertised output schemas cost, which is what `off` would save. It is measured by recounting each tool's definition without `outputSchema`, so it includes the key and its separators, and the figure equals the difference between a server built with `on` and the same server built with `off` (a test asserts this).

- `ServerCost.output_schema_tokens` and `output_schema_share` (0.0 to 1.0, four places; 0.0 for an empty server).
- `check --json` has `output_schema_tokens` and `output_schema_share` at the top level, and each tool has `output_schema_cost` (its share of the tokens), `output_schema_advertised` (read from the wire, so it is also set for plain FastMCP servers) and `output_schema_mode` (`null` for servers not built with OunceMCP). The existing per-tool `output_schema` field is unchanged: the tokens of the schema alone, which is 2 tokens fewer than its cost for `inventory.search` (108 and 110).
- The text report prints `Output schemas   241 of 488 tokens (49%)`.

## Alternatives considered

- **`auto` by share:** drop the schema when it is more than some share of the definition. Rejected. A small tool with a small schema would lose a useful contract while a large tool under budget would keep a large schema. The budget is what the developer has already chosen.
- **`auto` against the startup budget.** Tools register one by one, so it would need a second pass after the last tool, and it would change which tools lose schemas when one is added. It can be added later as a server-level step without changing this API.
- **`auto` that follows the host.** Needs the per-host data from BUD-7, which is P1.
- **Default `auto` or `off`.** Rejected until the host study says whether models see output schemas. The spec's open decision keeps `on` until then. Output schemas also give clients something to validate against, as the MCP specification expects.
- **Report the share as the schema's own tokens (237 here).** Rejected: it leaves out the key and does not equal what `off` saves, and the spec's example figure is 241.

## Impact

Measured on the inventory example (`o200k_base`), by building the same server with each mode:

| Mode | Definition tokens | Output-schema share | Notes |
| --- | ---: | ---: | --- |
| `on` (default) | 488 | 241 (49%) | unchanged from before |
| `off` | 247 | 0 | same as `output_schemas=False` before |
| `auto` | 378 | 131 (35%) | `inventory.search` (279 tokens with its schema, over the 250 target) loses its schema and drops to 169. `inventory.get` (209) keeps it |

Defaults are unchanged, so servers behave as before unless they set a mode. Reports gain fields, and no existing field changed meaning. Registration with `auto` counts one definition per tool, which happens once.

Tests (`tests/test_bud7_9_output_schemas.py`): every mode at server and tool level, validation of values, `auto` on both sides of the target and per tool, recorded decisions, structured results without an advertised schema, `off` saving exactly the reported share, the inventory figures (241 of 488), text and JSON reports for OunceMCP and plain FastMCP servers, the scope in `check`, `context` and `ServerCost`, and tests that the uncounted fields really do not change a count while the counted ones do.

## Migration

- `output_schemas=True` and `False` keep working. New code can write `"on"` and `"off"`.
- `server.output_schemas` is now the string mode (`"on"`, `"off"` or `"auto"`), where it was a `bool`. Code that read it as a truth value should compare with `"off"`.
- Consumers of `check --json` get new keys. `ToolCost` has three new fields with defaults, so existing constructions still work.
- To reduce definition size, try `output_schemas="auto"` and compare `check` before and after. Keep `on` for tools whose clients validate results.
