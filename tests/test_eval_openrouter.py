"""EVAL-3 with real models: the OpenRouter adapter, against a mocked API (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from ouncemcp.eval.agent import OpenRouterModel, load_suite, run_suite
from ouncemcp.eval.agent.openrouter import ModelError, safe_tool_names

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "examples" / "inventory" / "eval.yaml"


def reply(message: dict, cost: float = 0.001, prompt: int = 500, completion: int = 20) -> dict:
    return {
        "model": "test/model",
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "cost": cost},
    }


def call(name: str, arguments: dict) -> dict:
    return {
        "content": None,
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


class Scripted:
    """Answers each request from a list, and keeps the requests."""

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.requests: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        answer = self.answers.pop(0)
        if isinstance(answer, httpx2.Response):
            return answer
        return httpx2.Response(200, json=answer)


def model(api: Scripted, **kwargs) -> OpenRouterModel:
    return OpenRouterModel(
        "test/model", api_key="test", transport=httpx2.MockTransport(api), retries=2, **kwargs
    )


def test_tool_names_are_made_safe_and_unique():
    mapping = safe_tool_names(["inventory.search", "inventory__search", "a b"])
    assert mapping == {
        "inventory__search": "inventory.search",
        "inventory__search_2": "inventory__search",
        "a_b": "a b",
    }


@pytest.mark.anyio
async def test_a_real_model_run_is_judged_on_the_server():
    api = Scripted(
        [
            reply(
                call(
                    "inventory__search", {"region": "ap-south-1", "kind": "vm", "status": "running"}
                )
            ),
            reply({"content": "There are 12 running VMs."}),
        ]
    )
    suite = load_suite(INVENTORY)
    report = await run_suite(suite, model(api), repeats=1, split="train")
    first = report.runs[0]
    assert first.task_id == "running-vms" and first.success
    assert first.input_tokens == 1000 and first.output_tokens == 40  # provider usage, not estimates
    assert first.cost_usd == pytest.approx(0.002)
    sent = api.requests[1]
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "tool"]
    assert sent["messages"][3]["tool_call_id"] == sent["messages"][2]["tool_calls"][0]["id"]
    assert '"vm-00040"' in sent["messages"][3]["content"]
    assert {t["function"]["name"] for t in sent["tools"]} == {"inventory__search", "inventory__get"}
    data = report.to_dict()
    assert data["header"]["model"] == "openrouter:test/model"
    assert data["usage"]["served_by"] == {"test/model": 2}


@pytest.mark.anyio
async def test_the_spending_limit_stops_the_suite():
    # Every reply costs $0.30 and the limit is $0.50: the second request is refused.
    answers = [reply(call("inventory__search", {"region": "ap-south-1"}), cost=0.3)] * 3
    api = Scripted(answers + [reply({"content": "done"}, cost=0.3)] * 3)
    suite = load_suite(INVENTORY)
    report = await run_suite(suite, model(api, max_cost_usd=0.5), repeats=1)
    assert len(api.requests) == 2
    assert report.runs == []  # the only run was cut off and left out
    assert any("spending limit" in n for n in report.notes)
    assert report.to_dict()["usage"]["cost_usd"] == pytest.approx(0.6)


@pytest.mark.anyio
async def test_rate_limits_are_retried_and_hard_errors_are_not(monkeypatch):
    import anyio

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(anyio, "sleep", no_sleep)
    api = Scripted([httpx2.Response(429, text="slow down"), reply({"content": "ok"})])
    turn = await model(api).respond("t", [{"role": "user", "content": "hi"}], [])
    assert turn.text == "ok" and len(api.requests) == 2

    refused = Scripted([httpx2.Response(401, text="bad key")])
    with pytest.raises(ModelError, match="401"):
        await model(refused).respond("t", [{"role": "user", "content": "hi"}], [])


@pytest.mark.anyio
async def test_invalid_arguments_reach_the_server_as_validation_errors():
    bad = {
        "content": None,
        "tool_calls": [
            {
                "id": "x",
                "type": "function",
                "function": {"name": "inventory__get", "arguments": "{oops"},
            }
        ],
    }
    api = Scripted([reply(bad), reply({"content": "sorry"})])
    suite = load_suite(INVENTORY)
    report = await run_suite(suite, model(api), repeats=1, split="train")
    first = report.runs[0]
    assert first.calls[0].error_category == "validation" and first.invalid_arguments == 1


def test_cli_needs_a_key(monkeypatch, capsys):
    from ouncemcp.cli import main

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(["eval", str(INVENTORY), "--model", "openrouter:x/y"]) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err
    assert main(["eval", str(INVENTORY), "--model", "gpt"]) == 2


@pytest.mark.anyio
async def test_a_daily_quota_stops_the_suite_without_counting_failures():
    quota = httpx2.Response(
        429, text='{"error":{"message":"Rate limit exceeded: free-models-per-day"}}'
    )
    api = Scripted([reply(call("inventory__search", {"region": "ap-south-1"})), quota])
    report = await run_suite(load_suite(INVENTORY), model(api), repeats=1)
    assert report.runs == [] and len(api.requests) == 2
    assert any("daily request quota" in n for n in report.notes)
