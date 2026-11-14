# 0026: Real models in task suites, through OpenRouter

Status: proposed for M3. Adds real models to spec EVAL-3 to EVAL-7 (the replay model is [proposal 0019](0019-eval-kit.md)).

## Problem

The replay model checks the server side of a task. Whether a model picks the right tool, fills its arguments and stops when it should can only be measured with real models. Every provider has its own API, key and price.

## Proposal

`OpenRouterModel` implements the suite's `Model` protocol against OpenRouter's OpenAI-compatible API, which reaches Anthropic, OpenAI, Google and open models with one key.

```bash
export OPENROUTER_API_KEY=...        # or a placeholder where a proxy injects the key
ouncemcp eval examples/inventory/eval.yaml --model openrouter:anthropic/claude-haiku-5.5 \
    --repeats 3 --max-cost 2.00 --output haiku.json --html haiku.html
```

- **Spending cap.** `--max-cost` (default $1.00) is checked before every request, against the cost OpenRouter reports for each reply. Once it is reached, no new run starts, and a run cut off part-way is left out of the results rather than counted as a failure. A provider's own quota (for example OpenRouter's daily limit on free models) stops the suite the same way. The report notes either one.
- **Provider usage.** Input and output tokens come from the provider's counts. Each run's cost goes into the report (`cost_usd_per_task`), along with total spend and which model actually answered (`served_by`).
- **Tool names.** MCP names with dots (`inventory.search`) are sent as `inventory__search` and mapped back; collisions get a suffix.
- **Reliability.** 429 and 5xx responses are retried with backoff, honoring `Retry-After`. `--fallback-model` is passed to OpenRouter's routing for when the first model is unavailable.
- **Invalid arguments.** Arguments that are not valid JSON reach the server as a validation error, so they count against argument validity instead of crashing the run.

## First results and what they changed

Free model `nvidia/nemotron-3.5-super-vl:free`, 14 November 2026, $0:

| Suite | Runs | Success | Tool selection | Arguments valid |
| --- | --- | --- | --- | --- |
| inventory | 6 | 100% | 100% | 100% |
| ticketing, train | 8 | 100% | 100% | 100% |

The held-out ticketing task `delete-needs-admin` showed that its check was written for the replay script, not for the outcome. The task asks a non-admin to delete a ticket. A real model correctly sees no delete tool, but the check required a call to the hidden tool. It now checks the outcome: the ticket is kept and `tickets.close` is not used instead. The simulated user also declines confirmations in this task (`approve: false`), because a user who asked for a delete would not approve a close.

In one run the model did close the ticket instead of deleting it, which the eval had allowed only because the simulated user approved every confirmation. That task now measures such substitution. The rest of the held-out runs were cut short by the free tier's daily quota, so held-out figures wait for a paid run.

## Alternatives considered

- **One adapter per provider SDK.** More code and keys to maintain. Provider-specific adapters can still implement the same protocol later, for example when the benchmark needs a provider's own token counting (BUD-6).

## Impact on tokens and task success

None on servers. This is the measurement that release gate 6 (accuracy) and the benchmark need.

## Migration notes

None. `--model replay` remains the default.
