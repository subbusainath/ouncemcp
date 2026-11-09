# Benchmarks

Reproducible benchmark harness (docs/spec.md section 13). Milestone: M3.

| Directory | Contents |
| --- | --- |
| `servers/` | Server definitions: pilots, plain FastMCP equivalents, and open-source servers |
| `tasks/` | Task suites with verifiable outcomes, including held-out tasks |
| `scripts/` | Scripts to run every condition and collect results |
| `results/` | Published raw results with environment details |

Publication rules: publish scripts, configurations, raw results, and environment details; report regressions as well as improvements; never publish a figure that cannot be reproduced from these scripts.
