# Evaluating with task suites

`ouncemcp eval` runs tasks against a server the way an agent would and judges the outcome on the server ([proposal 0019](../proposals/0019-eval-kit.md)).

```bash
uv pip install -e ".[eval]"
ouncemcp eval examples/inventory/eval.yaml
ouncemcp eval examples/inventory/eval.yaml --output baseline.json
ouncemcp eval examples/inventory/eval.yaml --compare baseline.json --fail-on-regression
```

A task:

```yaml
- id: close-approved
  caller: {subject: ana, tenant: acme}   # in-process servers: run as this verified caller
  approve: true                          # answer to approval requests
  prompt: Close ticket T-1.
  expect_tools: [tickets.close]
  checks:
    - result: tickets.close
      path: status
      equals: closed
    - state: eval_checks.py:t1_closed_once   # function(server, run) -> bool
  script:                                    # what the replay model does
    - call: tickets.close
      args: {ticket_id: T-1}
    - say: Ticket T-1 is closed.
```

- Put some tasks in `split: heldout` and keep them out of description tuning; the report shows the train minus held-out gap.
- Use `{{last.<tool>.<path>}}` in script arguments to reuse a value from an earlier result, and `page: next_cursor` to follow cursors.
- `--server` runs the same suite against another server, for example a plain FastMCP version, to compare tokens per task.
- The replay model checks the server, not a model's judgment. To measure a real model, run the same suite through OpenRouter ([proposal 0026](../proposals/0026-real-model-evals.md)):

    ```bash
    export OPENROUTER_API_KEY=...
    ouncemcp eval examples/inventory/eval.yaml --model openrouter:anthropic/claude-haiku-5.5 \
        --repeats 3 --max-cost 2.00
    ```

    `--max-cost` (default $1.00) stops the suite before it spends more. The report shows the provider's token counts, the cost per task and which model answered. Real models vary between runs, so use several `--repeats` and compare rates.
- Write checks about outcomes, not about a script: a real model may reach the right result by another path, or rightly refuse. `approve: false` makes the simulated user decline confirmations, for tasks where the user would not want the write.

`--html report.html` writes the report as one page that opens offline: splits, tasks with their failed checks, regressions and the environment ([proposal 0020](../proposals/0020-report-ui.md)).

Read traces from a run with `ouncemcp trace calls.jsonl` ([traces](../concepts/traces.md)).
