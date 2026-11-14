"""ouncemcp bench (spec section 13, PERF-6): conditions, tool maps, one budget, comparisons."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from ouncemcp.cli import main
from ouncemcp.eval.agent.openrouter import OpenRouterModel
from ouncemcp.eval.bench import compare_conditions, load_bench, run_bench

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "bench.yaml"


def test_the_benchmark_file_loads():
    config = load_bench(BENCH)
    assert config.max_cost_usd == 5.0 and config.repeats == 5
    assert config.models == (
        "anthropic/claude-haiku-5.5",
        "anthropic/claude-sonnet-5.5",
        "openai/gpt-5.4-mini",
        "deepseek/deepseek-v4.1-flash",
    )
    inventory = config.suites[0]
    assert len(inventory.suite.tasks) >= 10 and any(
        t.split == "heldout" for t in inventory.suite.tasks
    )
    plain = {c.name: c for c in inventory.conditions}["plain"]
    assert plain.tool_map["inventory.search"] == "search_resources"
    assert Path(plain.server.partition(":")[0]).is_file()


@pytest.mark.parametrize("name", ["inventory", "ticketing"])
def test_benchmark_tasks_pass_against_the_ounce_server_with_replay(name: str):
    assert (
        main(["eval", str(ROOT / "benchmarks" / "tasks" / f"{name}.yaml"), "--repeats", "1"]) == 0
    )


def answer_all(request: httpx2.Request) -> httpx2.Response:
    """A fake model: calls the first tool once, then answers."""
    body = json.loads(request.content)
    if any(m["role"] == "tool" for m in body["messages"]):
        message = {"content": "There are 12."}
    else:
        tool = body["tools"][0]["function"]["name"]
        args = {"region": "ap-south-1"} if "search" in tool else {}
        message = {
            "content": None,
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(args)},
                }
            ],
        }
    return httpx2.Response(
        200,
        json={
            "model": body["model"],
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.01},
        },
    )


@pytest.mark.anyio
async def test_conditions_share_one_budget_and_are_compared():
    config = load_bench(BENCH)
    config = type(config)(**{**config.__dict__, "suites": config.suites[:1]})

    def factory(model_id, pool):
        return OpenRouterModel(
            model_id,
            api_key="test",
            pool=pool,
            max_cost_usd=10,
            transport=httpx2.MockTransport(answer_all),
        )

    report = await run_bench(
        config, models=["a/one", "b/two"], repeats=1, max_cost_usd=0.25, model_factory=factory
    )
    data = report.to_dict()
    # Two requests per run at $0.01 each: the $0.25 budget stops the benchmark early.
    assert data["usage"]["cost_usd"] <= 0.25 + 0.02 * 6
    assert data["usage"]["notes"]
    conditions = {(r["model"], r["condition"]) for r in data["results"]}
    assert conditions <= {
        ("a/one", "ounce"),
        ("a/one", "plain"),
        ("b/two", "ounce"),
        ("b/two", "plain"),
    }
    plain_calls = [
        c
        for r in report.results
        if r["condition"] == "plain"
        for c in r["report"]["tasks"].values()
    ]
    assert plain_calls  # the plain server ran under its own tool names


def test_perf6_comparison():
    def report(startup: float, tokens: float, success: float) -> dict:
        return {
            "splits": {
                "all": {
                    "definition_tokens": startup,
                    "tokens_per_task": tokens,
                    "success_rate": success,
                    "calls_per_task": 1,
                    "turns_per_task": 2,
                    "latency_ms_p50": 1,
                    "cost_usd_per_task": 0.001,
                }
            }
        }

    met = compare_conditions(report(200, 900, 1.0), report(1000, 1500, 0.9))
    assert met["startup_tokens"]["change"] == -0.8 and met["perf6"]["met"]
    missed = compare_conditions(report(500, 900, 1.0), report(1000, 800, 1.0))
    assert missed["perf6"] == {
        "startup_reduction_ok": False,
        "task_tokens_ok": False,
        "success_ok": True,
        "met": False,
    }


def test_bench_cli_with_replay(tmp_path: Path, capsys):
    out = tmp_path / "bench.json"
    assert (
        main(["bench", str(BENCH), "--models", "replay", "--repeats", "1", "--output", str(out)])
        == 0
    )
    data = json.loads(out.read_text())
    assert data["perf6"]["pairs"] == 2 and data["header"]["candidate"] == "ounce"
    assert "PERF-6 met for" in capsys.readouterr().out


def test_published_results_render(tmp_path: Path, capsys):
    page = tmp_path / "bench.html"
    results = ROOT / "benchmarks" / "results" / "m3-bench.json"
    assert main(["bench", "--from", str(results), "--html", str(page)]) == 0
    assert "PERF-6 met for" in capsys.readouterr().out
    assert '"kind":"bench"' in page.read_text()
    data = json.loads(results.read_text())
    assert data["perf6"]["pairs"] == 8 and data["usage"]["cost_usd"] <= 5
