# Evaluation

## Security suite

!!! success "Stable"
    Covered by the 1.0 API freeze ([proposal 0029](../proposals/0029-api-freeze.md)): no breaking change before 2.0.

::: ouncemcp.eval.run_security_suite

::: ouncemcp.eval.SecurityReport

::: ouncemcp.eval.CrossTenant

::: ouncemcp.eval.CursorReuse

::: ouncemcp.eval.HandleReplay

::: ouncemcp.eval.GuessedName

::: ouncemcp.eval.SSRFProbe

::: ouncemcp.eval.TokenPassthrough

## Task suites

!!! warning "Provisional"
    Task suites, models and the benchmark (`ouncemcp.eval.agent`, `ouncemcp.eval.bench`) depend on model APIs and benchmark methods that move fast. They may change in a minor release; every change is named in the [changelog](../changelog.md).

::: ouncemcp.eval.agent.load_suite

::: ouncemcp.eval.agent.run_suite

::: ouncemcp.eval.agent.EvalReport

::: ouncemcp.eval.agent.compare

## Models

::: ouncemcp.eval.agent.Model

::: ouncemcp.eval.agent.ReplayModel

::: ouncemcp.eval.agent.OpenRouterModel

## Benchmark

::: ouncemcp.eval.bench.load_bench

::: ouncemcp.eval.bench.run_bench

::: ouncemcp.eval.bench.compare_releases
