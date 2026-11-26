# 11. Measuring with agents

Unit tests prove the server does what you wrote. They do not prove a model can use it. For that you need tasks, a model, and numbers.

## A task suite

A suite is a YAML file of tasks. Each task has a prompt, the caller, the tools it should use, and checks on the outcome. Here is part of Shelf's suite:

```yaml title="examples/book/shelf/eval.yaml"
--8<-- "examples/book/shelf/eval.yaml:borrow"
```

Checks judge **what happened**, not what the model said:

| Check | Passes when |
| --- | --- |
| `called: tool` (+ `args`) | The tool was called, with at least these arguments |
| `not_called: tool` | It was not |
| `result: tool` + `path` + `equals`/`contains`/`exists` | Its last successful result matches |
| `error: tool` + `category` | Its last call failed with this category |
| `state: file.py:function` | `function(server, run)` returns true; it can inspect the server's own state |
| `answer: [...]` | The final answer contains these texts (for facts the server holds) |

The `state` checks for Shelf are two lines each, in `eval_checks.py`: a borrow that was approved wrote exactly one loan; one that was declined wrote nothing.

## Replay first

Every task can carry a `script`: the calls a good agent would make. The `replay` model plays the scripts, with no key and no cost, so the suite runs in CI and proves the tasks and checks are right:

```bash
ouncemcp eval examples/book/shelf/eval.yaml
```

```text
Split      Runs  Success  Select  Args ok  Calls  Turns   Tokens   p50 ms
all           8     100%    100%     100%    1.0    2.0  2941.25     49.5
train         6     100%    100%     100%    1.0    2.0  2959.67     53.7
heldout       2     100%    100%     100%    1.0    2.0   2886.0     46.0
Train minus held-out success: +0%
```

The book's tests run this command, so a change that breaks a task breaks the build.

## Then real models

```bash
export OPENROUTER_API_KEY=...
ouncemcp eval examples/book/shelf/eval.yaml --model openrouter:anthropic/claude-haiku-5.5 --max-cost 1.00
```

`--max-cost` stops starting runs once the spend reaches it. The report gives, per split:

- **Success**: tasks whose checks all passed.
- **Select**: how often the model chose the expected tools.
- **Args ok**: calls whose arguments validated first time.
- **Tokens** and **turns** per task: what a task costs.

Mark some tasks `split: heldout` and do not tune descriptions on them. If train success climbs while held-out does not, you are fitting your descriptions to your tests.

Save a run with `--output before.json`, change a description, and compare with `--compare before.json --fail-on-regression`: the comparison only reports differences that are larger than the noise between repeats.

## Benchmarks

`ouncemcp bench` runs several models over several servers and conditions (for example, plain FastMCP against OunceMCP) and writes a report with confidence intervals. The repository's own benchmark is in [`benchmarks/`](https://github.com/subbusainath/ouncemcp/tree/main/benchmarks) and its results are in the [M4 report](../m4-report.md).

## Traces

In production, record what actually happens:

```python
from ouncemcp.trace import JsonlSink

server = OunceServer("shelf", trace=JsonlSink("calls.jsonl"))
```

Each call records its result tokens against the budget, truncation, latency, attempts, outcome and error category. Open the file in the viewer:

```bash
ouncemcp trace calls.jsonl
```

The viewer shows the slowest and largest calls, every truncation, and the errors by category. Traces can also go to OpenTelemetry; see [traces](../concepts/traces.md).

## Summary

- Write tasks with checks on outcomes, and a script for each.
- Run them with `replay` in CI, and with real models before you ship a description change.
- Keep held-out tasks you never tune on.
- Trace production calls and read the viewer.

!!! question "Questions about this chapter?"
    Ask in `#help` on the [OunceMCP Discord](https://discord.gg/mQz4ZuZe3u), one post per question.
