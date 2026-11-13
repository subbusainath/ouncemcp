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
- The replay model checks the server, not a model's judgment. Real-model runs use the same suites once a model adapter and API keys are set up.

`--html report.html` writes the report as a page built from [A2UI](https://a2ui.org) components, and `--a2ui report.jsonl` writes the A2UI messages ([proposal 0020](../proposals/0020-a2ui-reports.md)).

Read traces from a run with `ouncemcp trace calls.jsonl` ([traces](../concepts/traces.md)).
