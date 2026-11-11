# Resources and prompts

`@server.resource(uri)` and `@server.prompt()` work like tools: typed content, token budgets, policies and the verified caller ([proposal 0014](../proposals/0014-resources-and-prompts.md)).

```python
@server.resource("notes://{note_id}")
async def note(note_id: str, ctx: ToolContext) -> Note:
    """One note as JSON."""
    return await ctx.deps.notes.get(ctx.identity.tenant, note_id)

@server.resource("notes://readme", max_tokens=1_000)
def readme() -> str:
    """The team readme; cut with a note if it is longer than 1,000 tokens."""

@server.prompt(policy=require_scopes("admin"))
def audit(period: str) -> str:
    """Ask for an audit of a period's notes."""
    return f"Audit all notes from {period}."
```

- Text over budget is cut with a note; JSON and prompts over budget are refused.
- `policy=` hides resources and prompts from lists and refuses reads, including over stdio with `stdio_policy="enforce"`.
