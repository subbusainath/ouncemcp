# API reference

Generated from the docstrings in the code. The [concepts](../concepts/budgets.md) and [guides](../guides/testing.md) explain how the pieces fit together; these pages list every public class and function.

- [Server and tools](server.md): `OunceServer`, `ToolContext`, effects, results and pagination.
- [Budgets and token counting](budgets.md): `Budget`, `BudgetOverride`, tokenizers and cost reports.
- [Errors](errors.md): the error classes a tool can raise.
- [Execution](execution.md): stores, idempotency, approvals, retries, limits and jobs.
- [Security](security.md): identity, policies, cached data, state tokens and safe fetches.
- [Clients](client.md): the broker and OAuth.
- [Evaluation](eval.md): task suites, models, reports and the benchmark.
- [Testing helpers](testing.md): `as_caller` and `make_token`.

Names exported from `ouncemcp`, `ouncemcp.security`, `ouncemcp.client` and `ouncemcp.eval.agent` are the public API. Anything else may change before 1.0.
