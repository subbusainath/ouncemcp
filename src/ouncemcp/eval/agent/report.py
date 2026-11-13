"""Eval reports (spec EVAL-4, EVAL-5, EVAL-6, EVAL-7; proposal 0019).

Every report starts with what produced it (EVAL-5): model, host, tokenizer, OunceMCP
and FastMCP versions, the server target and its git commit, Python, platform, the suite
name and digest, and the time. Results are given per task and per split; the gap
between the train and held-out splits shows descriptions tuned to the training tasks
(EVAL-6). ``compare`` lists regressions between two reports (EVAL-7).
"""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ouncemcp.eval.agent.runner import RunRecord
from ouncemcp.eval.agent.suite import Task

REPORT_VERSION = 1


def _git_commit(target: str) -> str | None:
    path = Path(target.partition(":")[0])
    if not path.exists():
        return None
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(path if path.is_dir() else path.parent),
                "rev-parse",
                "--short",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def environment(target: str) -> dict[str, Any]:
    from importlib.metadata import PackageNotFoundError, version

    def pkg(name: str) -> str | None:
        try:
            return version(name)
        except PackageNotFoundError:
            return None

    host = (
        "http"
        if target.startswith(("http://", "https://"))
        else "stdio"
        if target.startswith("-- ")
        else "in-process"
    )
    return {
        "host": f"ouncemcp eval ({host})",
        "ouncemcp": pkg("ouncemcp"),
        "fastmcp": pkg("fastmcp"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "server_commit": _git_commit(target),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 2) if values else 0.0


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 1)


def _aggregate(runs: Sequence[RunRecord], tasks: Mapping[str, Task]) -> dict[str, Any]:
    correct = total = calls = invalid = 0
    errors: Counter[str] = Counter()
    for run in runs:
        c, t = run.selection(tasks[run.task_id].expect_tools)
        correct, total = correct + c, total + t
        calls += len(run.calls)
        invalid += run.invalid_arguments
        errors.update(run.errors)
    return {
        "runs": len(runs),
        "success_rate": round(sum(r.success for r in runs) / len(runs), 3) if runs else 0.0,
        "tool_selection_accuracy": round(correct / total, 3) if total else None,
        "argument_validity": round(1 - invalid / calls, 3) if calls else None,
        "calls_per_task": _mean([len(r.calls) for r in runs]),
        "turns_per_task": _mean([r.turns for r in runs]),
        "tokens_per_task": _mean([r.tokens for r in runs]),
        "definition_tokens": _mean([r.definition_tokens for r in runs]),
        "latency_ms_p50": round(statistics.median([r.latency_ms for r in runs]), 1)
        if runs
        else 0.0,
        "latency_ms_p95": _p95([r.latency_ms for r in runs]),
        "errors": dict(errors),
        "harness_errors": sum(1 for r in runs if r.harness_error),
    }


@dataclass
class EvalReport:
    suite: str
    suite_digest: str
    model: str
    target: str
    tokenizer: str
    environment: dict[str, Any]
    tasks: dict[str, Task]
    runs: list[RunRecord]
    recall: dict[str, Any] | None = None

    def splits(self) -> dict[str, dict[str, Any]]:
        out = {"all": _aggregate(self.runs, self.tasks)}
        for split in ("train", "heldout"):
            runs = [r for r in self.runs if r.split == split]
            if runs:
                out[split] = _aggregate(runs, self.tasks)
        return out

    def per_task(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for task_id, task in self.tasks.items():
            runs = [r for r in self.runs if r.task_id == task_id]
            data = _aggregate(runs, self.tasks)
            data["split"] = task.split
            failed: Counter[str] = Counter()
            for run in runs:
                failed.update(label for label, ok in run.checks if not ok)
                if run.harness_error:
                    failed[run.harness_error] += 1
            data["failed_checks"] = dict(failed)
            out[task_id] = data
        return out

    @property
    def passed(self) -> bool:
        return all(r.success for r in self.runs)

    def to_dict(self) -> dict[str, Any]:
        splits = self.splits()
        gap = None
        if "train" in splits and "heldout" in splits:
            gap = round(splits["train"]["success_rate"] - splits["heldout"]["success_rate"], 3)
        return {
            "report_version": REPORT_VERSION,
            "header": {
                "suite": self.suite,
                "suite_digest": self.suite_digest,
                "model": self.model,
                "target": self.target,
                "tokenizer": self.tokenizer,
                **self.environment,
            },
            "splits": splits,
            "train_heldout_gap": gap,
            "tasks": self.per_task(),
            "recall": self.recall,
        }

    def render_text(self) -> str:
        data = self.to_dict()
        h = data["header"]
        columns = (
            f"{'Split':<9} {'Runs':>5} {'Success':>8} {'Select':>7} {'Args ok':>8} {'Calls':>6} "
            f"{'Turns':>6} {'Tokens':>8} {'p50 ms':>8}"
        )
        lines = [
            f"Eval {h['suite']} ({h['suite_digest']}) with model {h['model']} on {h['host']}",
            f"Target {h['target']}  commit {h['server_commit'] or '?'}  tokenizer {h['tokenizer']}",
            f"OunceMCP {h['ouncemcp']}, FastMCP {h['fastmcp']}, Python {h['python']}, {h['time']}",
            "",
            columns,
        ]
        for name, s in data["splits"].items():
            select = (
                "-"
                if s["tool_selection_accuracy"] is None
                else f"{s['tool_selection_accuracy']:.0%}"
            )
            args = "-" if s["argument_validity"] is None else f"{s['argument_validity']:.0%}"
            lines.append(
                f"{name:<9} {s['runs']:>5} {s['success_rate']:>8.0%} {select:>7} {args:>8} "
                f"{s['calls_per_task']:>6} {s['turns_per_task']:>6} {s['tokens_per_task']:>8} "
                f"{s['latency_ms_p50']:>8}"
            )
        if data["train_heldout_gap"] is not None:
            lines.append(f"Train minus held-out success: {data['train_heldout_gap']:+.0%}")
        if self.recall:
            lines.append(f"Search recall: {self.recall.get('mean_recall', self.recall)}")
        failing = {k: v for k, v in data["tasks"].items() if v["success_rate"] < 1}
        if failing:
            lines += ["", "Failing tasks:"]
            for task_id, t in failing.items():
                why = ", ".join(f"{k} x{n}" for k, n in t["failed_checks"].items()) or "?"
                lines.append(f"  {task_id} ({t['split']}): {t['success_rate']:.0%} success; {why}")
        return "\n".join(lines)

    def a2ui_surface(self) -> Any:
        """The report as an A2UI surface (proposal 0020)."""
        from ouncemcp.ui.a2ui import Surface, bind

        data = self.to_dict()
        h = data["header"]

        def pct(value: float | None) -> str:
            return "-" if value is None else f"{value:.0%}"

        splits = [
            {
                "name": {"all": "All tasks", "train": "Train", "heldout": "Held out"}[name],
                "success": f"{pct(s['success_rate'])} success over {s['runs']} runs",
                "choice": f"Tool selection {pct(s['tool_selection_accuracy'])} · "
                f"arguments valid {pct(s['argument_validity'])}",
                "cost": f"{s['calls_per_task']} calls · {s['turns_per_task']} turns · "
                f"{s['tokens_per_task']:,.0f} tokens per task ({s['definition_tokens']:,.0f} in definitions)",
                "latency": f"Latency p50 {s['latency_ms_p50']} ms, p95 {s['latency_ms_p95']} ms"
                + (
                    f" · errors {', '.join(f'{k} {v}' for k, v in s['errors'].items())}"
                    if s["errors"]
                    else ""
                ),
            }
            for name, s in data["splits"].items()
        ]
        tasks = [
            {
                "id": task_id,
                "result": f"{'passed' if t['success_rate'] == 1 else 'failed'} · {pct(t['success_rate'])}",
                "facts": f"{t['split']} · {t['calls_per_task']} calls · {t['turns_per_task']} turns · "
                f"{t['tokens_per_task']:,.0f} tokens",
                "failed": "; ".join(f"{c} x{n}" for c, n in t["failed_checks"].items()),
            }
            for task_id, t in data["tasks"].items()
        ]
        notes = []
        if data["train_heldout_gap"] is not None:
            notes.append(f"Train minus held-out success: {data['train_heldout_gap']:+.0%}.")
        if self.recall:
            notes.append(f"Search recall: {self.recall.get('mean_recall', self.recall)}.")
        header = [{"key": k, "value": "-" if v is None else str(v)} for k, v in h.items()]

        ui = Surface("eval", theme={"primaryColor": "#0969da", "agentDisplayName": "OunceMCP"})
        ui.data = {"splits": splits, "tasks": tasks, "header": header}
        ui.text("title", f"Eval: {self.suite}", "h1")
        ui.text(
            "about",
            f"Model {self.model} · {h['host']} · target {self.target} · commit "
            f"{h['server_commit'] or '?'} · {h['time']}",
            "caption",
        )
        verdict = "All runs passed." if self.passed else "Some runs failed."
        ui.text("verdict", " ".join([verdict, *notes]))
        ui.text("s_name", bind("name"), "h3")
        for key in ("success", "choice", "cost", "latency"):
            ui.text(f"s_{key}", bind(key), "body" if key == "success" else "caption")
        ui.column("s_col", ["s_name", "s_success", "s_choice", "s_cost", "s_latency"])
        ui.card("s_card", "s_col")
        ui.repeat("splits", "s_card", "/splits")
        ui.text("k_id", bind("id"), "h4", weight=1)
        ui.text("k_result", bind("result"))
        ui.row("k_head", ["k_id", "k_result"])
        ui.text("k_facts", bind("facts"), "caption")
        ui.text("k_failed", bind("failed"), "caption")
        ui.column("k_col", ["k_head", "k_facts", "k_failed"])
        ui.card("k_card", "k_col")
        ui.repeat("tasks", "k_card", "/tasks")
        ui.text("h_key", bind("key"), "caption", weight=1)
        ui.text("h_value", bind("value"), weight=3)
        ui.row("h_row", ["h_key", "h_value"])
        ui.repeat("header", "h_row", "/header")
        ui.tabs("tabs", [("Results", "splits"), ("Tasks", "tasks"), ("Environment", "header")])
        ui.column("root", ["title", "about", "verdict", "tabs"])
        return ui

    def a2ui_messages(self) -> list[dict[str, Any]]:
        return self.a2ui_surface().messages()  # type: ignore[no-any-return]

    def render_html(self) -> str:
        """One HTML file that renders the report with the bundled A2UI renderer."""
        from ouncemcp.ui.a2ui import render_page

        return render_page(f"Eval {self.suite}", self.a2ui_messages(), fallback=self.render_text())


def compare(
    old: Mapping[str, Any], new: Mapping[str, Any], *, tolerance: float = 0.05
) -> list[str]:
    """Regressions from report ``old`` to ``new`` (EVAL-7)."""
    out: list[str] = []
    for split, now in new.get("splits", {}).items():
        before = old.get("splits", {}).get(split)
        if not before:
            continue
        if now["success_rate"] < before["success_rate"]:
            out.append(
                f"{split}: success fell from {before['success_rate']:.0%} to {now['success_rate']:.0%}"
            )
        for key, label in (
            ("tokens_per_task", "tokens per task"),
            ("calls_per_task", "calls per task"),
        ):
            if before[key] and now[key] > before[key] * (1 + tolerance):
                out.append(f"{split}: {label} rose from {before[key]} to {now[key]}")
    for task_id, now in new.get("tasks", {}).items():
        before = old.get("tasks", {}).get(task_id)
        if before and now["success_rate"] < before["success_rate"]:
            out.append(
                f"task {task_id}: success fell from {before['success_rate']:.0%} to {now['success_rate']:.0%}"
            )
    return out


def load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())  # type: ignore[no-any-return]
