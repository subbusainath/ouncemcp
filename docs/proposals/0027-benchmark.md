# 0027: The benchmark harness, first results and PERF-6

Status: accepted (the PERF-6 revision, 15 November 2026). Covers spec section 13 and PERF-6.

## Proposal

**`ouncemcp bench benchmarks/bench.yaml`.** A benchmark file names task suites, the servers to compare for each (conditions, with a tool-name map when names differ), the models, the number of runs, and one spending limit for the whole run, shared by all models.

- Every (suite, model, condition) combination runs its tasks; several combinations run at once.
- The report compares candidate with baseline for each suite and model: startup tokens, tokens per task, success, calls, turns, latency and cost. It checks PERF-6 for each pair.
- Output is text, JSON and a page in the report app. `--from results.json` rebuilds the page from published results without rerunning.

**Supporting changes.**

- Outcome checks that work on any server: `answer` checks (facts in the final reply, with typographic quotes normalised) and state checks reading `server.deps`.
- `run_suite(tool_map=...)`.
- A `CostPool` shared by `OpenRouterModel` instances.

## First results

Pilots against their plain FastMCP equivalents: four models, 5 runs each, 840 runs, $2.19. The full results are in [`benchmarks/results/m3-bench.md`](https://github.com/subbusainath/ouncemcp/blob/main/benchmarks/results/m3-bench.md).

- **Inventory.** OunceMCP used 58–85% fewer tokens per task, and was as accurate or more accurate (GPT-5.4 mini: 100% against 75%).
- **Ticketing.** OunceMCP used 17–40% more tokens per task, but was 100% against 68–70% successful: the plain server let every model skip approvals, delete without permission and read another team's ticket.
- **Definitions.** OunceMCP's definitions are 77–139% larger on these 2–4 tool servers.

## PERF-6: proposed revision

PERF-6 ("at least 70% fewer startup tokens and fewer total task tokens with no drop in task success") is met for 0 of 8 pairs. The startup part assumed large catalogs, where `search` discovery replaces most definitions. Small servers stay in `direct` mode and pay for output schemas.

Accepted revision:

1. **Split PERF-6 by server size.**
    - For servers over the startup budget, which use `search` mode: keep the 70% startup reduction.
    - For small servers: "no more than 2× the plain definition tokens, and no drop in task success".
2. **Task tokens.** Keep "fewer task tokens" as the target for servers whose results are larger than a few hundred tokens. Report it without a pass or fail for servers with small results, where definitions dominate.
3. **A follow-up to measure next:** `output_schemas="auto"`, or `"off"` for small servers. Output schemas are 48% of inventory's definition tokens. The open BUD-7 decision about the output-schema default should use a benchmark run with schemas off.

## Alternatives considered

- **Publishing only the inventory suite.** It meets most of PERF-6, but leaving out ticketing would hide a real cost and the main safety result.
- **Counting the safety tasks separately.** They are reported per task, and the success column includes them because they are real user outcomes.

## Impact on tokens and task success

None on servers. This is the evidence for the launch claims; no claim beyond these numbers should be published.

## Migration notes

None.
