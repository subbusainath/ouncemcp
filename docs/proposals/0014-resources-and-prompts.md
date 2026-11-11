# 0014: Resources and prompts

Status: proposed for M2 (step 4 of [proposal 0010](0010-m2-plan.md), addition A1). Covers spec CAP-2, which was planned for M4 and moved into M2 by the owner.

## Problem

OunceMCP only wrapped tools. Servers that also expose documents or prompt templates had to fall back to raw FastMCP, without budgets, policies or the verified caller.

## Proposal

```python
@server.resource("notes://{note_id}", policy=require_scopes("notes:read"))
async def note(note_id: str, ctx: ToolContext) -> Note:
    """One note as JSON."""

@server.prompt()
def summarize(topic: str) -> str:
    """Ask for a summary of notes on a topic."""
```

- **Content.** `str` is `text/plain`, `bytes` is binary, models, dicts and lists are compact JSON (`application/json`) without unset optional fields. `mime_type=` overrides it. `{placeholders}` make a resource template.
- **Budgets.** Counted with the server's tokenizer against `max_tokens` (default `Budget.result_ceiling`). Text over budget is cut with a note giving shown and total tokens; JSON over budget is refused, because cut JSON would not parse; prompts over budget are refused. Binary content is not counted.
- **Policy.** `policy=` plus the server's policy and `audience` hide resources, templates and prompts from lists and refuse reads, exactly as for tools. `stdio_policy="enforce"` covers them too. Policies see `effect=READ`.
- **Context.** A `ToolContext` parameter gives the verified identity, `deps`, `cache` and `store`.
- **Descriptions** are required, as for tools.

The conformance target now has the suite's resource and prompt fixtures, so those scenarios are no longer expected to fail. Completions are still unsupported.

## Alternatives considered

- **Leaving resources and prompts to FastMCP.** Simpler, but every security and budget guarantee would stop at the tool boundary.

## Impact on tokens and task success

None for tools. Resource reads are bounded by their budget.

## Migration notes

None.
