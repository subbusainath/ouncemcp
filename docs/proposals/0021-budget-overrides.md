# 0021: Per-tool budget overrides with a reason

Status: proposed for M4. Covers spec BUD-4.

## Problem

`result_tokens=` already set a tool's result budget, but nothing asked why a tool needed more than the server allows, and a tool whose definition was legitimately large could only pass `ouncemcp check` by raising the server-wide thresholds for every tool.

## Proposal

```python
@server.tool(
    effect=Effect.READ,
    result_tokens=6_000,
    definition_tokens=900,
    budget_reason="Audit entries are only useful whole; one tool replaces six lookups.",
)
```

- `result_tokens` (existing) is the tool's concise result budget, up to `Budget.result_ceiling`.
- `definition_tokens` (new) is the size above which this tool's definition fails `check`. It replaces the server's warn and fail thresholds for this tool only.
- `budget_reason` (new) records why. Raising either budget above the server's without a reason emits a `BudgetOverrideWarning` at registration, and `check` reports the `budget-override-without-reason` lint. Lowering a budget needs no reason.
- `server.budget_overrides()` returns every override. `check` lists them with their reasons under "Budget overrides", and `check --json` adds an `override` object to each tool.

## Alternatives considered

- **Failing registration without a reason.** Too strict for exploration and tests; a warning plus a lint gives reviewers the signal without blocking work.
- **Overrides in `ouncemcp.toml`.** Keeping the reason next to the tool keeps it in code review with the code it justifies.

## Impact on tokens and task success

None by default. Overrides make larger tools visible instead of hiding them behind raised server-wide limits.

## Migration notes

None. Existing `result_tokens=` values above the server default now warn until a `budget_reason` is added.
