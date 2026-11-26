# Appendix B: errors and lints

## Error categories

| Category | Raise | Retryable | Reference |
| --- | --- | --- | --- |
| `validation` | `InvalidInput` | no | [validation](../errors/validation.md) |
| `authentication` | `AuthenticationRequired` | no | [authentication](../errors/authentication.md) |
| `permission` | `PermissionDenied` | no | [permission](../errors/permission.md) |
| `not_found` | `NotFound` | no | [not found](../errors/not_found.md) |
| `rate_limit` | `RateLimited` | yes | [rate limit](../errors/rate_limit.md) |
| `transient` | `TransientError` | yes | [transient](../errors/transient.md) |
| `ambiguous_write` | `AmbiguousWrite` | no | [ambiguous write](../errors/ambiguous_write.md) |
| `internal` | any other exception | no | [internal](../errors/internal.md) |

The category pages list the error codes, such as `approval_declined`, `unknown_tool` and `result_too_large`.

## Lints

| Rule | Catches | Chapter |
| --- | --- | --- |
| [undocumented-parameter](../rules/undocumented-parameter.md) | A parameter with no description | 2 |
| [description-too-short](../rules/description-too-short.md) | A description that does not say what the tool does and returns | 2 |
| [missing-usage-guidance](../rules/missing-usage-guidance.md) | A description that never says when to use the tool | 2 |
| [inconsistent-tool-names](../rules/inconsistent-tool-names.md) | Names that mix styles | 2 |
| [free-form-input](../rules/free-form-input.md) | An argument that accepts any structure | 3 |
| [budget-override-without-reason](../rules/budget-override-without-reason.md) | A raised budget without `budget_reason` | 3 |
| [unpaginated-list](../rules/unpaginated-list.md) | An unbounded list without a cursor | 4 |
| [write-without-side-effects](../rules/write-without-side-effects.md) | A write that does not say what it changes | 6 |
| [elicit-in-write](../rules/elicit-in-write.md) | A write that asks the user mid-way | 6 |
| [input-after-effect](../rules/input-after-effect.md) | Asking for input after a write started | 6 |
| [destructive-without-policy](../rules/destructive-without-policy.md) | A destructive tool anyone can call | 7 |
| [stdio-skips-policy](../rules/stdio-skips-policy.md) | Policies that do not run over stdio | 7 |

Did not find your error? Ask in `#help` on the [OunceMCP Discord](https://discord.gg/mQz4ZuZe3u) with the full message.
