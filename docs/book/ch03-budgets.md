# 3. Budgets: what a tool costs

## Measuring the catalog

Run `check` on step 1:

```bash
ouncemcp check examples/book/shelf/step1_catalog.py
```

```text
Tokenizer        o200k_base
Startup context  297 tokens across 2 tools  [OK, budget 4,000]
Output schemas   140 of 297 tokens (47%)
Counted          name, description, inputSchema, outputSchema
Not counted      title, annotations, _meta

Tool           Total   Desc  Input  Output  Other  Status
books.search     162     23     54      72     13  OK
books.get        135     21     38      64     12  OK

Per-tool budget: target 250, warn 500, fail 1000
Discovery        direct (direct costs 297 tokens, within the 4,000-token threshold)
```

Read it from the top:

- **Startup context** is what every session pays before the user says anything: the sum of all definitions, counted on the JSON a client receives from `tools/list`.
- **Output schemas** shows how much of that is the typed result. Typed outputs help the model and the host, but they are not free. Here they are almost half.
- The **table** breaks each tool into description, input schema, output schema and the rest (the name and JSON punctuation).
- **Per-tool budget** is the default `Budget`: aim for 250 tokens a tool, warn at 500, fail at 1,000.
- **Discovery** tells you whether the host gets every definition or a search tool instead (chapter 12).

297 tokens for two tools is a good start. The finished Shelf in chapter 9 has nine tools in 1,322 tokens.

## Lints: the mistakes `check` catches

`check` also reads your definitions the way a model would. Here is a poor version of `books.search`:

```python
class Found(BaseModel):
    books: list[dict]

@server.tool(name="books.search", effect=Effect.READ)
def search(query: str, filters: dict) -> Found:
    """Search."""
```

```text
Lints:
  WARNING [undocumented-parameter] books.search: query has no description; say what the value is and where it comes from
  WARNING [free-form-input] books.search: filters accepts any structure; declare a model or typed fields
  WARNING [description-too-short] books.search: description has 1 words; say what the tool does and returns
```

Each lint links to a page in the [rule reference](../rules/input-after-effect.md) with the reason and the fix. Every stage of Shelf passes with no lints, and the book's tests keep it that way.

!!! note
    Run `ouncemcp check` in CI. It exits non-zero when a tool is over its fail budget, so a definition cannot quietly grow.

## Results have budgets too

Definitions are paid once per session; results are paid on every call. Each result is shaped to fit a result budget, 2,000 tokens by default. Every call reports what it used in `_meta.ouncemcp`:

```json
{"ouncemcp": {"tokenizer": "o200k_base", "result_tokens": 88, "budget": 2000, "format": "concise", "truncated": false}}
```

Chapter 4 shows what happens when a result is larger than its budget.

## Setting budgets

The defaults suit most servers. Change them for the whole server with `Budget`, or for one tool:

```python
from ouncemcp import Budget, OunceServer

server = OunceServer("shelf", budget=Budget(result_tokens=1_500, startup_tokens=3_000))

@server.tool(
    name="books.export",
    effect=Effect.READ,
    result_tokens=6_000,
    budget_reason="Exports a whole shelf list for printing; the user asks for it explicitly.",
)
```

A tool that raises its own budget must say why. Without `budget_reason`, `check` warns (`budget-override-without-reason`), and the reason is printed next to the tool in every report, so reviewers see the trade-off.

## Counting tokens

Counts use the `o200k_base` tokenizer by default. Different models tokenize differently, but the relative sizes, which are what you are optimising, barely change. Pass `tokenizer="cl100k_base"` or your own counting function to `OunceServer` to match your model; every report names the tokenizer it used. For Claude models, `ouncemcp check server.py --tokenizer anthropic:claude-opus-5-5` asks Anthropic's own token-counting API (install `ouncemcp[anthropic]` and set `ANTHROPIC_API_KEY`).

## Summary

- Startup context is paid every session; result tokens are paid every call.
- `ouncemcp check` measures both definitions and lints, and belongs in CI.
- Override a budget only with a reason.

Next, we make search results that stay small however large the library grows.

!!! question "Questions about this chapter?"
    Ask in `#help` on the [OunceMCP Discord](https://discord.gg/mQz4ZuZe3u), one post per question.
