"""EVAL-3 to EVAL-7 and DISC-6: agent task suites with the replay model."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from conftest import EXAMPLE

from ouncemcp.cli import main
from ouncemcp.eval.agent import ReplayModel, SuiteError, load_suite, run_suite
from ouncemcp.eval.agent.report import compare

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = EXAMPLE / "eval.yaml"
TICKETING = ROOT / "examples" / "ticketing" / "eval.yaml"


@pytest.mark.anyio
@pytest.mark.parametrize("path", [INVENTORY, TICKETING], ids=["inventory", "ticketing"])
async def test_pilot_suites_pass_with_the_replay_model(path):
    report = await run_suite(load_suite(path), repeats=1)
    data = report.to_dict()
    assert report.passed, report.render_text()
    assert data["splits"]["train"]["runs"] and data["splits"]["heldout"]["runs"]
    assert data["splits"]["all"]["tool_selection_accuracy"] == 1.0
    assert data["splits"]["all"]["argument_validity"] == 1.0
    header = data["header"]
    for key in ("model", "host", "tokenizer", "ouncemcp", "fastmcp", "python", "platform", "time"):
        assert header[key], key
    assert header["model"] == "replay" and header["tokenizer"] == "o200k_base"


@pytest.mark.anyio
async def test_recall_is_reported():
    report = await run_suite(load_suite(INVENTORY), repeats=1, split="heldout")
    assert report.recall is not None and report.recall["mean_recall"] == 1.0


@pytest.mark.anyio
async def test_each_run_gets_a_fresh_server():
    """Closing T-1 in one run must not leave it closed for the next."""
    report = await run_suite(load_suite(TICKETING), repeats=3)
    closes = [r for r in report.runs if r.task_id == "close-approved"]
    assert len(closes) == 3 and all(r.success for r in closes)


@pytest.mark.anyio
async def test_the_same_suite_fails_against_a_server_without_those_tools():
    plain = f"{EXAMPLE / 'plain_server.py'}:mcp"
    report = await run_suite(load_suite(INVENTORY), repeats=1, server=plain)
    data = report.to_dict()
    assert not report.passed
    assert data["splits"]["all"]["success_rate"] == 0.0
    assert data["splits"]["all"]["errors"]["not_found"] > 0
    assert data["tasks"]["running-vms"]["failed_checks"] == {
        "called inventory.search": 1,
        "result inventory.search total": 1,
        "result inventory.search items.0.id": 1,
    }


@pytest.mark.anyio
async def test_wrong_expectations_fail_and_say_why(tmp_path: Path):
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        f"""
name: wrong
server: {EXAMPLE / "server.py"}
tasks:
  - id: count
    prompt: count running VMs
    expect_tools: [inventory.get]
    checks:
      - result: inventory.search
        path: total
        equals: 99
    script:
      - call: inventory.search
        args: {{region: ap-south-1, kind: vm, status: running}}
"""
    )
    report = await run_suite(load_suite(suite))
    data = report.to_dict()
    assert data["tasks"]["count"]["failed_checks"] == {"result inventory.search total": 1}
    assert data["splits"]["all"]["tool_selection_accuracy"] == 0.0


def test_replay_fills_references_and_follows_cursors():
    model = ReplayModel(
        {
            "t": [
                {"call": "a.search", "args": {"q": "x"}, "page": "next_cursor"},
                {"call": "a.get", "args": {"id": "{{last.a.search.items.0.id}}"}},
            ]
        }
    )
    model.reset("t")

    def tool_msg(content: dict) -> dict:
        return {"role": "tool", "name": "a.search", "ok": True, "content": json.dumps(content)}

    first = anyio.run(model.respond, "t", [], [])
    assert first.tool_calls[0].arguments == {"q": "x"}
    page2 = anyio.run(
        model.respond, "t", [tool_msg({"items": [{"id": "1"}], "next_cursor": "c1"})], []
    )
    assert page2.tool_calls[0].arguments == {"q": "x", "cursor": "c1"}
    done = anyio.run(
        model.respond, "t", [tool_msg({"items": [{"id": "9"}], "next_cursor": None})], []
    )
    assert done.tool_calls[0].name == "a.get" and done.tool_calls[0].arguments == {"id": "9"}
    assert anyio.run(model.respond, "t", [], []).tool_calls == ()


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("name: x\n", "missing 'server'"),
        ("name: x\nserver: s.py\ntasks: [{id: a}]\n", "needs id and prompt"),
        ("name: x\nserver: s.py\ntasks: [{id: a, prompt: p, split: dev}]\n", "split"),
        ("name: x\nserver: s.py\ntasks: [{id: a, prompt: p, checks: [{result: t}]}]\n", "equals"),
        ("name: x\nserver: s.py\ntasks: [{id: a, prompt: p}, {id: a, prompt: q}]\n", "duplicate"),
    ],
)
def test_suite_errors(tmp_path: Path, text: str, match: str):
    path = tmp_path / "s.yaml"
    path.write_text(text)
    with pytest.raises(SuiteError, match=match):
        load_suite(path)


def test_compare_flags_regressions():
    old = {
        "splits": {"all": {"success_rate": 1.0, "tokens_per_task": 1000, "calls_per_task": 2}},
        "tasks": {"a": {"success_rate": 1.0}},
    }
    new = {
        "splits": {"all": {"success_rate": 0.5, "tokens_per_task": 1100, "calls_per_task": 2}},
        "tasks": {"a": {"success_rate": 0.5}},
    }
    found = compare(old, new)
    assert any("success fell" in r for r in found)
    assert any("tokens per task rose" in r for r in found)
    assert compare(old, old) == []


def test_eval_command(tmp_path: Path, capsys):
    out, page = tmp_path / "r.json", tmp_path / "r.html"
    assert (
        main(["eval", str(INVENTORY), "--repeats", "1", "--output", str(out), "--html", str(page)])
        == 0
    )
    text = capsys.readouterr().out
    assert "Eval inventory" in text and "heldout" in text
    data = json.loads(out.read_text())
    assert data["regressions"] == [] and page.read_text().lower().startswith("<!doctype html>")
    assert (
        main(
            [
                "eval",
                str(INVENTORY),
                "--repeats",
                "1",
                "--compare",
                str(out),
                "--fail-on-regression",
            ]
        )
        == 0
    )
    plain = f"{EXAMPLE / 'plain_server.py'}:mcp"
    assert main(["eval", str(INVENTORY), "--repeats", "1", "--server", plain]) == 1
    assert (
        main(["eval", str(INVENTORY), "--repeats", "1", "--server", plain, "--allow-failures"]) == 0
    )
    assert main(["eval", str(tmp_path / "none.yaml")]) == 2


def test_answer_checks_ignore_typographic_quotes():
    from ouncemcp.eval.agent.runner import RunRecord, _evaluate
    from ouncemcp.eval.agent.suite import Check

    run = RunRecord("t", "train", 0, final_text="I couldn’t find vm‑99999.")
    check = Check("answer", any_of=("couldn't find",), contains=("vm-99999",))
    assert _evaluate(check, run, None)
