# `budget-override-without-reason`

Severity: Warning. Reported by `ouncemcp check`, and as a `BudgetOverrideWarning` when the tool is registered.

## What it finds

A tool sets `result_tokens` above the server's `Budget.result_tokens`, or sets `definition_tokens`, without `budget_reason` (spec BUD-4).

## Why it matters

Budgets keep definitions and results small for every caller. A tool that needs more is often fine, but reviewers should see why, and the reason should stay with the code so a later change can check it still holds.

## How to fix it

Say why the tool needs the larger budget:

```python
@server.tool(
    effect=Effect.READ,
    result_tokens=6_000,
    budget_reason="Audit entries are only useful whole; the tool pages by day instead.",
)
```

`ouncemcp check` lists every override with its reason. Lowering a budget needs no reason.
