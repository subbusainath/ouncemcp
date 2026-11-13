# 0019: Agent task suites and the replay model

Status: proposed for M3 (step 2). Covers spec EVAL-3 to EVAL-7 and the eval-output part of DISC-6. Real model adapters and the benchmark follow once API keys and a budget are agreed.

## Problem

Unit tests prove the framework's pieces; nothing yet ran a task the way an agent does, judged the outcome on the server, and reported the numbers the spec asks for (success, tool choice, argument validity, calls, turns, tokens, latency, errors).

## Proposal

**Suites in YAML (EVAL-3).** A suite names a server (a file, `-- command`, or URL) and tasks: a prompt, a split (`train` or `heldout`), the tools a correct answer uses, checks, and a script for the replay model. Optional per task: the caller (`make_token` fields), the answer to approval requests, a turn limit. Format and checks are documented in `ouncemcp.eval.agent.suite` and the [guide](../guides/evaluating.md).

**Checks are judged on the server, never on what the model says.**

| Check | Passes when |
| --- | --- |
| `called` | a tool the server lists was called with at least these arguments |
| `not_called` | the tool was never called |
| `result` + `path` + `equals`/`contains`/`exists` | the tool's last successful structured result matches |
| `error` + `category` | the tool's last call failed in that category; the tool must be listed for the caller, or with `hidden: true` must be hidden from them |
| `state: file.py:function` | `function(server, run)` returns true, e.g. reading the ticket store |

Calls to tools the server does not list never satisfy `called` or (without `hidden`) `error`: an early version passed a "not found" check against a server that lacked the tool entirely, and the tests now cover that.

**Models.** A `Model` answers with text and tool calls. `ReplayModel` plays each task's script: tool calls, values from earlier results (`"{{last.inventory.search.items.0.id}}"`), paging until `next_cursor` is empty, and a final answer. It needs no key, costs nothing and is deterministic, so suites run in CI and catch server regressions: renamed tools, changed schemas, wrong results, broken approvals. Measuring how well a real model chooses tools needs a real model; adapters implement the same protocol.

**Runs.** Each task runs `repeats` times against a fresh server (a server file is imported again for every run), so state never leaks between runs. Tokens per task are what the model would read and write: definitions plus the conversation so far on every turn, counted with the server's tokenizer, or the model's own usage when it reports it.

**Reports (EVAL-4 to EVAL-7, DISC-6).** Per split and per task: success rate, tool-selection accuracy (calls to an expected tool), argument validity (calls without a validation error), calls, turns, tokens, definition tokens, latency p50 and p95, errors by category, and failed checks. The header records model, host, tokenizer, OunceMCP and FastMCP versions, server target and git commit, Python, platform, suite digest and time (EVAL-5). The train minus held-out success gap flags descriptions tuned to the training tasks (EVAL-6). `--compare old.json` lists regressions in success, tokens or calls, and `--fail-on-regression` fails CI (EVAL-7). Suites with a `recall` section report search recall (DISC-6).

**CLI.** `ouncemcp eval suite.yaml [--repeats N] [--split train|heldout|all] [--server TARGET] [--json] [--output PATH] [--html PATH] [--compare OLD] [--fail-on-regression] [--allow-failures]`. It exits 1 when any run fails.

**Suites for the pilots.** `examples/inventory/eval.yaml` (6 tasks, 2 held out, with paging, an error case and recall) and `examples/ticketing/eval.yaml` (6 tasks, 2 held out: approved and declined closes, a retried create, another tenant, an admin-only delete), both passing.

Writing the inventory suite found that `inventory.get` raised a plain `ValueError` for an unknown id, which callers saw as an `internal` error; it now raises `NotFound`.

## Alternatives considered

- **Judging success by the model's final answer.** The spec requires verified outcomes; a model can claim success it did not achieve.
- **Recording real runs and replaying them.** Useful later for cheap regression runs of real-model transcripts; the protocol allows it.

## Impact on tokens and task success

None on servers. The same suite run against two servers compares their token cost per task.

## Migration notes

None. PyYAML is in the `eval` extra.
