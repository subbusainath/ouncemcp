"""The benchmark harness: ``ouncemcp bench`` (spec section 13, PERF-6; proposal 0027).

A benchmark file names task suites, the servers to compare for each (conditions, such
as an OunceMCP server and its plain FastMCP equivalent, with a tool-name map when they
differ), the models, the repeats and one spending limit for the whole run::

    name: ouncemcp-m3
    repeats: 5
    max_cost_usd: 5.00
    compare: {candidate: ounce, baseline: plain}
    models: [anthropic/claude-haiku-5.5, openai/gpt-5.4-mini]
    suites:
      - suite: tasks/inventory.yaml
        conditions:
          ounce: {server: ../examples/inventory/server.py}
          plain: {server: ../examples/inventory/plain_server.py:mcp,
                  tool_map: {inventory.search: search_resources}}

Every (suite, model, condition) runs the suite's tasks ``repeats`` times; several run at
once. The report compares candidate with baseline for each suite and model: startup
tokens, task tokens, success, calls, turns, latency and cost, and checks PERF-6: at
least 70% fewer startup tokens, fewer task tokens and no drop in task success.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import yaml

from ouncemcp.eval.agent.suite import Suite, SuiteError, load_suite

PERF6_STARTUP_REDUCTION = 0.70


@dataclass(frozen=True)
class Condition:
    name: str
    server: str
    tool_map: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchSuite:
    suite: Suite
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class BenchConfig:
    name: str
    models: tuple[str, ...]
    suites: tuple[BenchSuite, ...]
    repeats: int = 5
    max_cost_usd: float = 5.0
    candidate: str = "ounce"
    baseline: str = "plain"
    path: Path | None = None


def _server_path(ref: str, base: Path) -> str:
    if ref.startswith(("http://", "https://", "-- ")):
        return ref
    file, colon, attr = ref.partition(":")
    return str((base / file).resolve()) + (colon + attr if colon else "")


def load_bench(path: str | Path) -> BenchConfig:
    file = Path(path)
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SuiteError(f"{file}: {exc}") from exc
    if not isinstance(data, dict) or not data.get("suites") or not data.get("models"):
        raise SuiteError(f"{file}: a benchmark needs name, models and suites")
    base = file.resolve().parent
    compare = data.get("compare") or {}
    suites = []
    for i, raw in enumerate(data["suites"]):
        suite = load_suite(base / str(raw["suite"]))
        conditions = tuple(
            Condition(name, _server_path(str(c["server"]), base), dict(c.get("tool_map") or {}))
            for name, c in (raw.get("conditions") or {}).items()
        )
        if not conditions:
            raise SuiteError(f"{file}: suite {i + 1} has no conditions")
        suites.append(BenchSuite(suite, conditions))
    return BenchConfig(
        name=str(data.get("name", file.stem)),
        models=tuple(str(m) for m in data["models"]),
        suites=tuple(suites),
        repeats=int(data.get("repeats", 5)),
        max_cost_usd=float(data.get("max_cost_usd", 5.0)),
        candidate=str(compare.get("candidate", "ounce")),
        baseline=str(compare.get("baseline", "plain")),
        path=file,
    )


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or not old:
        return None
    return round((new - old) / old, 4)


def compare_conditions(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Candidate against baseline for one suite and model (two ``EvalReport.to_dict()``)."""
    c, b = candidate["splits"]["all"], baseline["splits"]["all"]
    startup = _pct_change(c["definition_tokens"], b["definition_tokens"])
    tokens = _pct_change(c["tokens_per_task"], b["tokens_per_task"])
    success_delta = round(c["success_rate"] - b["success_rate"], 3)
    perf6 = {
        "startup_reduction_ok": startup is not None and -startup >= PERF6_STARTUP_REDUCTION,
        "task_tokens_ok": tokens is not None and tokens < 0,
        "success_ok": success_delta >= 0,
    }
    perf6["met"] = all(perf6.values())
    return {
        "startup_tokens": {
            "candidate": c["definition_tokens"],
            "baseline": b["definition_tokens"],
            "change": startup,
        },
        "tokens_per_task": {
            "candidate": c["tokens_per_task"],
            "baseline": b["tokens_per_task"],
            "change": tokens,
        },
        "success_rate": {
            "candidate": c["success_rate"],
            "baseline": b["success_rate"],
            "change": success_delta,
        },
        "calls_per_task": {"candidate": c["calls_per_task"], "baseline": b["calls_per_task"]},
        "turns_per_task": {"candidate": c["turns_per_task"], "baseline": b["turns_per_task"]},
        "latency_ms_p50": {"candidate": c["latency_ms_p50"], "baseline": b["latency_ms_p50"]},
        "cost_usd_per_task": {
            "candidate": c.get("cost_usd_per_task"),
            "baseline": b.get("cost_usd_per_task"),
        },
        "perf6": perf6,
    }


@dataclass
class BenchReport:
    config: BenchConfig
    results: list[dict[str, Any]]  # {suite, model, condition, report}
    usage: dict[str, Any]
    environment: dict[str, Any]

    def _find(self, suite: str, model: str, condition: str) -> dict[str, Any] | None:
        for r in self.results:
            if (r["suite"], r["model"], r["condition"]) == (suite, model, condition):
                return r["report"]  # type: ignore[no-any-return]
        return None

    def comparisons(self) -> list[dict[str, Any]]:
        out = []
        for bs in self.config.suites:
            for model in self.config.models:
                cand = self._find(bs.suite.name, model, self.config.candidate)
                base = self._find(bs.suite.name, model, self.config.baseline)
                if (
                    cand
                    and base
                    and cand["splits"]["all"]["runs"]
                    and base["splits"]["all"]["runs"]
                ):
                    out.append(
                        {"suite": bs.suite.name, "model": model, **compare_conditions(cand, base)}
                    )
        return out

    def to_dict(self) -> dict[str, Any]:
        comparisons = self.comparisons()
        return {
            "report_version": 1,
            "name": self.config.name,
            "header": {
                "models": list(self.config.models),
                "repeats": self.config.repeats,
                "candidate": self.config.candidate,
                "baseline": self.config.baseline,
                "suites": {bs.suite.name: bs.suite.digest for bs in self.config.suites},
                **self.environment,
            },
            "usage": self.usage,
            "perf6": {
                "target": "at least 70% fewer startup tokens, fewer task tokens, no drop in task success",
                "pairs": len(comparisons),
                "met": sum(1 for c in comparisons if c["perf6"]["met"]),
            },
            "comparisons": comparisons,
            "results": self.results,
        }

    def render_text(self) -> str:
        data = self.to_dict()
        title = (
            f"Benchmark {data['name']}: {data['header']['candidate']} vs "
            f"{data['header']['baseline']}, {self.config.repeats} repeats, "
            f"models {', '.join(self.config.models)}"
        )
        lines = [
            title,
            f"Spent ${self.usage['cost_usd']:.4f} of ${self.usage['max_cost_usd']:.2f}",
            "",
            f"{'Suite':<18} {'Model':<30} {'Startup':>16} {'Tokens/task':>18} {'Success':>13}  PERF-6",
        ]
        for c in data["comparisons"]:
            s, t, ok = c["startup_tokens"], c["tokens_per_task"], c["success_rate"]

            def pct(v: float | None) -> str:
                return "-" if v is None else f"{v:+.0%}"

            lines.append(
                f"{c['suite']:<18} {c['model'][:30]:<30} "
                f"{s['candidate']:>6.0f} ({pct(s['change']):>5})  "
                f"{t['candidate']:>8.0f} ({pct(t['change']):>5})  "
                f"{ok['candidate']:>4.0%} vs {ok['baseline']:>4.0%}  "
                f"{'met' if c['perf6']['met'] else 'not met'}"
            )
        lines += [
            "",
            f"PERF-6 met for {data['perf6']['met']} of {data['perf6']['pairs']} suite and model pairs.",
        ]
        lines += [f"Note: {n}" for n in self.usage.get("notes", [])]
        return "\n".join(lines)

    def render_html(self) -> str:
        from ouncemcp.ui import render_report

        return render_report(
            {"kind": "bench", "title": f"Benchmark: {self.config.name}", **self.to_dict()}
        )


async def run_bench(
    config: BenchConfig,
    *,
    models: Sequence[str] | None = None,
    repeats: int | None = None,
    max_cost_usd: float | None = None,
    concurrency: int = 6,
    model_factory: Any = None,
) -> BenchReport:
    """Run every (suite, model, condition). ``model_factory(model_id, pool)`` builds a
    model (default: ``OpenRouterModel``); ``"replay"`` uses each suite's scripts."""
    from ouncemcp.eval.agent.model import ReplayModel
    from ouncemcp.eval.agent.openrouter import CostPool, OpenRouterModel
    from ouncemcp.eval.agent.report import environment
    from ouncemcp.eval.agent.runner import run_suite

    chosen = tuple(models or config.models)
    config = BenchConfig(
        **{
            **config.__dict__,
            "models": chosen,
            "repeats": repeats or config.repeats,
            "max_cost_usd": max_cost_usd if max_cost_usd is not None else config.max_cost_usd,
        }
    )
    pool = CostPool(config.max_cost_usd)
    results: list[dict[str, Any]] = []
    notes: list[str] = []
    limiter = anyio.CapacityLimiter(max(1, concurrency))

    def make(model_id: str, suite: Suite) -> Any:
        if model_id == "replay":
            return ReplayModel(suite.scripts())
        if model_factory is not None:
            return model_factory(model_id, pool)
        return OpenRouterModel(model_id, max_cost_usd=config.max_cost_usd, pool=pool)

    async def one(bs: BenchSuite, model_id: str, condition: Condition) -> None:
        async with limiter:
            if pool.exhausted:
                notes.append(
                    f"{bs.suite.name} / {model_id} / {condition.name}: not started, budget spent"
                )
                return
            model = make(model_id, bs.suite)
            report = await run_suite(
                bs.suite,
                model,
                repeats=config.repeats,
                server=condition.server,
                tool_map=condition.tool_map or None,
            )
            data = report.to_dict()
            notes.extend(
                f"{bs.suite.name} / {model_id} / {condition.name}: {n}" for n in report.notes
            )
            results.append(
                {
                    "suite": bs.suite.name,
                    "model": model_id,
                    "condition": condition.name,
                    "report": data,
                }
            )

    async with anyio.create_task_group() as group:
        for bs in config.suites:
            for model_id in config.models:
                for condition in bs.conditions:
                    group.start_soon(one, bs, model_id, condition)
    order = {
        (bs.suite.name, m, c.name): i
        for i, (bs, m, c) in enumerate(
            (bs, m, c) for bs in config.suites for m in config.models for c in bs.conditions
        )
    }
    results.sort(key=lambda r: order.get((r["suite"], r["model"], r["condition"]), 0))
    env = environment(str(config.path or ""))
    env.pop("server_commit", None)
    env["commit"] = _commit(config.path)
    return BenchReport(
        config=config,
        results=results,
        usage={
            "cost_usd": round(pool.spent_usd, 6),
            "max_cost_usd": pool.limit_usd,
            "notes": notes,
        },
        environment=env,
    )


def _commit(path: Path | None) -> str | None:
    from ouncemcp.eval.agent.report import _git_commit

    return _git_commit(str(path)) if path else None


def load_bench_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())  # type: ignore[no-any-return]


# ---------------------------------------------------------------- release gate 6


def fisher_drop_p(old_ok: int, old_n: int, new_ok: int, new_n: int) -> float:
    """One-sided Fisher exact test: the probability of seeing ``new_ok`` or fewer
    successes in ``new_n`` runs if both releases had the same success rate."""
    from math import comb

    total_ok, total = old_ok + new_ok, old_n + new_n
    denom = comb(total, new_n)
    low = max(0, total_ok - old_n)
    return (
        sum(comb(total_ok, k) * comb(total - total_ok, new_n - k) for k in range(low, new_ok + 1))
        / denom
    )


def _counts(report: Mapping[str, Any]) -> tuple[int, int]:
    split = report["splits"]["all"]
    n = int(split["runs"])
    return round(split["success_rate"] * n), n


def compare_releases(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    *,
    alpha: float = 0.05,
    token_tolerance: float = 0.10,
) -> dict[str, Any]:
    """Release gate 6: does ``new`` (a bench report) drop task success against ``old``?

    For every suite, model and condition in both reports, a one-sided Fisher exact test
    on successes and runs. A drop with p < ``alpha`` fails the gate. Task-level drops
    and token increases over ``token_tolerance`` are warnings: five runs per task are too
    few to separate a real drop from chance."""
    index = {(r["suite"], r["model"], r["condition"]): r["report"] for r in old.get("results", [])}
    checks, warnings = [], []
    for r in new.get("results", []):
        key = (r["suite"], r["model"], r["condition"])
        before = index.get(key)
        if (
            before is None
            or not before["splits"]["all"]["runs"]
            or not r["report"]["splits"]["all"]["runs"]
        ):
            continue
        old_ok, old_n = _counts(before)
        new_ok, new_n = _counts(r["report"])
        p = fisher_drop_p(old_ok, old_n, new_ok, new_n) if new_ok / new_n < old_ok / old_n else 1.0
        checks.append(
            {
                "suite": key[0],
                "model": key[1],
                "condition": key[2],
                "success_before": round(old_ok / old_n, 3),
                "success_now": round(new_ok / new_n, 3),
                "runs": [old_n, new_n],
                "p_value": round(p, 4),
                "regression": p < alpha,
            }
        )
        b_tok = before["splits"]["all"]["tokens_per_task"]
        n_tok = r["report"]["splits"]["all"]["tokens_per_task"]
        if b_tok and n_tok > b_tok * (1 + token_tolerance):
            warnings.append(
                f"{key[0]} / {key[1]} / {key[2]}: tokens per task rose {n_tok / b_tok - 1:+.0%} "
                f"({b_tok:.0f} to {n_tok:.0f})"
            )
        for task_id, t in r["report"]["tasks"].items():
            was = before["tasks"].get(task_id)
            if was and t["success_rate"] < was["success_rate"]:
                warnings.append(
                    f"{key[0]} / {key[1]} / {key[2]}: task {task_id} fell from "
                    f"{was['success_rate']:.0%} to {t['success_rate']:.0%}"
                )
    missing = sorted(
        f"{s} / {m} / {c}"
        for (s, m, c) in index
        if (s, m, c)
        not in {(r["suite"], r["model"], r["condition"]) for r in new.get("results", [])}
    )
    return {
        "gate": "release gate 6: no statistically meaningful drop in task success",
        "alpha": alpha,
        "passed": not any(c["regression"] for c in checks) and bool(checks),
        "checks": checks,
        "warnings": warnings,
        "not_compared": missing,
    }


def render_comparison(result: Mapping[str, Any]) -> str:
    verdict = "passed" if result["passed"] else "FAILED"
    lines = [f"Gate 6 {verdict}: one-sided Fisher exact test, alpha {result['alpha']}"]
    for c in result["checks"]:
        flag = "REGRESSION" if c["regression"] else "ok"
        lines.append(
            f"  {flag:<10} {c['suite']} / {c['model']} / {c['condition']}: "
            f"{c['success_before']:.0%} -> {c['success_now']:.0%} (p={c['p_value']})"
        )
    lines += [f"  warning: {w}" for w in result["warnings"]]
    if result["not_compared"]:
        lines.append(f"  not in the new run: {', '.join(result['not_compared'])}")
    if not result["checks"]:
        lines.append("  nothing to compare: the reports share no suite, model and condition")
    return "\n".join(lines)
