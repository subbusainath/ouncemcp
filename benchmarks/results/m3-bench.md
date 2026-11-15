# M3 benchmark: OunceMCP servers against plain FastMCP equivalents

Raw results: [`m3-bench.json`](m3-bench.json). Rebuild the report page with `ouncemcp bench --from benchmarks/results/m3-bench.json --html bench.html`; rerun with `ouncemcp bench benchmarks/bench.yaml` (needs `OPENROUTER_API_KEY`).

## Setup

- **Servers.** The inventory and ticketing pilots, each as an OunceMCP server and as the plain FastMCP server in the same example folder. Descriptions match; tool names differ and are mapped per condition in [`bench.yaml`](../bench.yaml).
- **Tasks.** [`tasks/inventory.yaml`](../tasks/inventory.yaml) has 11 tasks, 3 of them held out. [`tasks/ticketing.yaml`](../tasks/ticketing.yaml) has 10 tasks, 3 of them held out. Success is judged on outcomes: facts in the final answer for reads, and the ticket store for writes. It never depends on which tool path the model took.
- **Models.** `anthropic/claude-haiku-5.5`, `anthropic/claude-sonnet-5.5`, `openai/gpt-5.4-mini` and `deepseek/deepseek-v4.1-flash`, through OpenRouter, temperature 0.
- **Runs.** 5 runs per task, condition and model: 840 runs in total. The host is the `ouncemcp eval` harness in-process, not a desktop app.
- **Recorded with the run.** Commit `580eb9a`, OunceMCP 0.0.1.dev0, FastMCP 4.1.6, Python 3.13, Linux x86_64, 14 November 2026. Cost was $2.19, with no harness errors.

## Results

| Suite | Model | Startup tokens | Tokens per task | Success | Cost per task |
| --- | --- | --- | --- | --- | --- |
| inventory | Claude Haiku 5.5 | 584 vs 244 (+139%) | 2,855 vs 6,772 (−58%) | 100% vs 100% | $0.0004 vs $0.0008 |
| inventory | Claude Sonnet 5.5 | 584 vs 244 (+139%) | 2,779 vs 6,759 (−59%) | 100% vs 100% | $0.0070 vs $0.0154 |
| inventory | GPT-5.4 mini | 584 vs 244 (+139%) | 1,316 vs 4,177 (−68%) | 100% vs 75% | $0.0009 vs $0.0010 |
| inventory | DeepSeek V4.1 Flash | 584 vs 244 (+139%) | 2,854 vs 18,811 (−85%) | 100% vs 100% | $0.0003 vs $0.0016 |
| ticketing | Claude Haiku 5.5 | 649 vs 366 (+77%) | 2,646 vs 1,964 (+35%) | 100% vs 70% | $0.0004 vs $0.0003 |
| ticketing | Claude Sonnet 5.5 | 649 vs 366 (+77%) | 2,476 vs 2,066 (+20%) | 100% vs 68% | $0.0061 vs $0.0051 |
| ticketing | GPT-5.4 mini | 649 vs 366 (+77%) | 832 vs 593 (+40%) | 100% vs 70% | $0.0008 vs $0.0006 |
| ticketing | DeepSeek V4.1 Flash | 649 vs 366 (+77%) | 2,277 vs 1,941 (+17%) | 100% vs 68% | $0.0002 vs $0.0002 |

The first figure in each cell is OunceMCP, the second is plain FastMCP. Held-out tasks match the training tasks for OunceMCP: 100% in every case, so the gap is 0.

## What the numbers show

- **Results are where OunceMCP saves tokens.** On inventory, the plain server returns every matching record with its config, metrics and tags. OunceMCP returns a budgeted, paged summary, which means 58–85% fewer tokens per task and half to a fifth of the cost.
- **Plain results also hurt accuracy.** GPT-5.4 mini, reading the large plain lists, miscounted resources and missed the costliest function. OunceMCP was 100% against 75% for that model; the other models scored 100% on both servers.
- **Definitions cost more with OunceMCP.** The definitions are 77–139% larger, mostly because of output schemas (48% of inventory's definition tokens) and the `response_format` option. On ticketing, where results are small, this makes tasks cost 17–40% more tokens.
- **The ticketing difference is safety.** Every model on the plain server:
    - closed a ticket the user declined to confirm (there is no approval step);
    - deleted a ticket without admin rights;
    - showed another team's ticket.

    OunceMCP refused all three, for every model and run: 100% against 68–70% success.
- **Remaining plain-side failures:**
    - One plain-server run with Sonnet answered "isn't one of your team's tickets" for the missing ticket, which the answer check did not accept. That makes the plain figure slightly low.
    - One DeepSeek run on plain asked for confirmation in chat instead of deleting.

## PERF-6

Under the original target (at least 70% fewer startup tokens, fewer task tokens, no drop in success), PERF-6 was met for **0 of 8** suite and model pairs: these pilots have 2–4 tools, and the startup target was written for large catalogs where `search` discovery applies.

Under the **revised target** ([proposal 0027](../../docs/proposals/0027-benchmark.md), accepted 15 November 2026), it is met for **4 of 8** (`ouncemcp bench --from benchmarks/results/m3-bench.json` judges these results by the current rule):

- **Ticketing (all four models): met.** Definitions are 1.8× the plain server's (649 vs 366 tokens, within 2×), success did not drop (100% vs 68–70%), and results are small, so task tokens are reported but not judged.
- **Inventory (all four models): not met.** Definitions are 2.4× the plain server's (584 vs 244 tokens), over the 2× limit. Success did not drop and task tokens fell 58–85%. These results were recorded before reports kept result tokens per task, so the task-token rule is not applied to them. Output schemas are about half of these definitions; the next benchmark tries `output_schemas="auto"`.
- **Large servers** are not benchmarked yet.

## Fixes the benchmark produced along the way

- **Answer checks.** A pilot run and the first full run found checks that rejected correct answers: curly apostrophes, "wasn't found", and a resource named instead of given by id. They were fixed before this run, and the first full run is not published.
- **OunceMCP example descriptions.**
    - Ticketing: "after the user confirms" made models ask in chat instead of calling the tool, which then asks the user itself.
    - Inventory: `inventory.search` did not say that `response_format=detailed` returns cost.

    Both descriptions were fixed using training tasks only; the held-out tasks still pass.

## Not yet covered

- Open-source servers. The spec plans 10–20, as published against migrated.
- Desktop hosts.
- Larger catalogs where `search` discovery applies.
