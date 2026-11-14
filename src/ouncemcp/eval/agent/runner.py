"""Run agent task suites (spec EVAL-3 to EVAL-6, DISC-6; proposal 0019).

Each task runs ``repeats`` times against a fresh server: a server file is imported
again for every run, so state changed by one run (a closed ticket) never leaks into
the next; commands start a new process and URLs a new session. Tokens are what the
model would read and write: definitions plus the conversation so far on every turn,
counted with the server's tokenizer, unless the model reports its own usage.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client

from ouncemcp.core.tokens import TokenCounter, compact_json, get_counter
from ouncemcp.eval.agent.model import Model, ReplayModel
from ouncemcp.eval.agent.suite import Check, Suite, Task, lookup, subset


@dataclass
class CallRecord:
    tool: str
    arguments: Mapping[str, Any]
    ok: bool
    structured: Any
    error_category: str | None
    error_code: str | None
    tokens: int
    latency_ms: float
    known: bool = True  # the tool was in the server's tool list


@dataclass
class RunRecord:
    task_id: str
    split: str
    repeat: int
    calls: list[CallRecord] = field(default_factory=list)
    turns: int = 0
    checks: list[tuple[str, bool]] = field(default_factory=list)
    final_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    definition_tokens: int = 0
    latency_ms: float = 0.0
    harness_error: str | None = None
    cost_usd: float | None = None  # reported by the model's provider, when it has one

    @property
    def success(self) -> bool:
        return self.harness_error is None and all(passed for _, passed in self.checks)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def selection(self, expected: Sequence[str]) -> tuple[int, int]:
        """(correct, total) tool choices; with no expectation every call counts as right."""
        if not expected:
            return len(self.calls), len(self.calls)
        return sum(1 for c in self.calls if c.tool in expected), len(self.calls)

    @property
    def invalid_arguments(self) -> int:
        return sum(1 for c in self.calls if c.error_category == "validation")

    @property
    def errors(self) -> Counter[str]:
        return Counter(c.error_category or "unknown" for c in self.calls if not c.ok)


def _approver(approve: bool) -> Any:
    from fastmcp.client.elicitation import ElicitResult

    async def handler(message: str, response_type: Any, params: Any, context: Any) -> Any:
        props = (getattr(params, "requested_schema", None) or {}).get("properties", {})
        if "approve" in props:
            return {"approve": approve}
        return ElicitResult(action="decline")

    return handler


@contextlib.contextmanager
def _caller(task: Task) -> Any:
    if task.caller:
        from ouncemcp.testing import as_caller

        with as_caller(**dict(task.caller)):
            yield
    else:
        yield


def _open(target: str, task: Task) -> tuple[Client, Any]:
    """A client for a fresh server, and the server object when it runs in-process."""
    kwargs = {"elicitation_handler": _approver(task.approve)}
    if target.startswith(("http://", "https://")):
        from ouncemcp.audit.runner import connect_target

        return connect_target(target, **kwargs)[0], None
    if target.startswith("-- "):
        from ouncemcp.audit.runner import connect_target

        return connect_target(target[3:], inherit_env=True, **kwargs)[0], None
    from ouncemcp.cli.loader import load_server

    server = load_server(target)
    return Client(getattr(server, "fastmcp", server), **kwargs), server


def _evaluate(check: Check, run: RunRecord, server: Any) -> bool:
    calls = [c for c in run.calls if c.tool == check.tool]
    if check.kind == "called":
        return any(c.known and subset(check.args, c.arguments) for c in calls)
    if check.kind == "not_called":
        return not calls
    if check.kind == "error":
        # A tool that does not exist also fails as not_found, so the check says whether
        # the tool should be listed for this caller (default) or hidden from them.
        last = calls[-1] if calls else None
        return (
            last is not None
            and last.known is not check.hidden
            and not last.ok
            and last.error_category == check.category
        )
    if check.kind == "result":
        good = [c for c in calls if c.ok]
        if not good:
            return False
        found, value = lookup(good[-1].structured, check.path)
        if check.exists is not None:
            return found is check.exists
        if not found:
            return False
        if check.equals is not None:
            return bool(value == check.equals)
        if isinstance(value, (list, str)):
            return check.contains in value
        return False
    if check.kind == "state":
        assert check.function is not None
        try:
            return bool(check.function(server, run))
        except Exception:  # noqa: BLE001 - a failing check is a failed check
            return False
    return False


async def run_task(
    task: Task, repeat: int, model: Model, target: str, counter: TokenCounter
) -> RunRecord:
    run = RunRecord(task.id, task.split, repeat)
    if isinstance(model, ReplayModel):
        model.reset(task.id)
    started = time.perf_counter()
    with _caller(task):
        client, server = _open(target, task)
        try:
            async with client:
                tools = [
                    t.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for t in await client.list_tools()
                ]
                run.definition_tokens = sum(counter.count(compact_json(t)) for t in tools)
                names = {str(t.get("name")) for t in tools}
                conversation: list[dict[str, Any]] = [{"role": "user", "content": task.prompt}]
                for _ in range(task.max_turns):
                    turn = await model.respond(task.id, conversation, tools)
                    run.turns += 1
                    sent = run.definition_tokens + counter.count(compact_json(conversation))
                    said = counter.count(turn.text) + sum(
                        counter.count(
                            compact_json({"name": c.name, "arguments": dict(c.arguments)})
                        )
                        for c in turn.tool_calls
                    )
                    run.input_tokens += turn.input_tokens if turn.input_tokens is not None else sent
                    run.output_tokens += (
                        turn.output_tokens if turn.output_tokens is not None else said
                    )
                    conversation.append(
                        {
                            "role": "assistant",
                            "content": turn.text,
                            "tool_calls": [
                                {"name": c.name, "arguments": dict(c.arguments)}
                                for c in turn.tool_calls
                            ],
                        }
                    )
                    if not turn.tool_calls:
                        run.final_text = turn.text
                        break
                    for call in turn.tool_calls:
                        record = await _call(client, call.name, dict(call.arguments), counter)
                        record.known = call.name in names
                        run.calls.append(record)
                        conversation.append(
                            {
                                "role": "tool",
                                "name": call.name,
                                "ok": record.ok,
                                "content": _content_for_model(record),
                            }
                        )
        except Exception as exc:  # noqa: BLE001 - recorded as a harness failure of this run
            run.harness_error = f"{type(exc).__name__}: {exc}"[:300]
    run.latency_ms = (time.perf_counter() - started) * 1000
    run.checks = [(c.label, _evaluate(c, run, server)) for c in task.checks]
    return run


def _content_for_model(record: CallRecord) -> str:
    if record.ok:
        return json.dumps(record.structured, default=str) if record.structured is not None else ""
    return f"error: {record.error_category}/{record.error_code}"


async def _call(
    client: Client, name: str, arguments: dict[str, Any], counter: TokenCounter
) -> CallRecord:
    t0 = time.perf_counter()
    result = await client.call_tool_mcp(name, arguments)
    latency = (time.perf_counter() - t0) * 1000
    text = "".join(getattr(c, "text", "") for c in result.content or [])
    meta = (getattr(result, "meta", None) or {}).get("ouncemcp") or {}
    is_error = bool(result.is_error)
    category = meta.get("category") if is_error else None
    if is_error and category is None:
        category = "not_found" if "Unknown tool" in text else "unknown"
    return CallRecord(
        tool=name,
        arguments=arguments,
        ok=not is_error,
        structured=result.structured_content,
        error_category=category,
        error_code=meta.get("error") if is_error else None,
        tokens=counter.count(text),
        latency_ms=latency,
    )


async def run_suite(
    suite: Suite,
    model: Model | None = None,
    *,
    repeats: int | None = None,
    split: str = "all",
    server: str | None = None,
    tokenizer: str | TokenCounter | None = None,
) -> Any:
    """Run ``suite`` and return an ``EvalReport``. ``server`` overrides the suite's."""
    from ouncemcp.eval.agent.report import EvalReport, environment

    target = server or suite.server
    model = model or ReplayModel(suite.scripts())
    counter = get_counter(tokenizer) if tokenizer is not None else _server_counter(target)
    runs: list[RunRecord] = []
    tasks = suite.select(split)
    notes: list[str] = []
    for task in tasks:
        for repeat in range(repeats or suite.repeats):
            if getattr(model, "exhausted", False):
                break
            spent = getattr(model, "spent_usd", None)
            run = await run_task(task, repeat, model, target, counter)
            if spent is not None:
                run.cost_usd = round(float(getattr(model, "spent_usd", 0.0)) - spent, 6)
            if (run.harness_error or "").startswith(("SpendingLimitReached", "QuotaExhausted")):
                notes.append(f"Run {task.id}#{repeat} was cut off by the limit and left out.")
                continue
            runs.append(run)
        if getattr(model, "exhausted", False):
            reason = getattr(model, "quota_reason", None) or (
                f"the spending limit (${getattr(model, 'max_cost_usd', 0):.2f}) was reached"
            )
            notes.append(f"Stopped early: {reason}; later runs were not started.")
            break
    recall = await _recall(suite, target)
    return EvalReport(
        suite=suite.name,
        suite_digest=suite.digest,
        model=model.name,
        target=target,
        tokenizer=counter.name,
        environment=environment(target),
        tasks={t.id: t for t in tasks},
        runs=runs,
        recall=recall,
        notes=notes,
        usage=_usage(model),
    )


def _usage(model: Model) -> dict[str, Any] | None:
    if not hasattr(model, "spent_usd"):
        return None
    return {
        "cost_usd": round(float(model.spent_usd), 6),
        "max_cost_usd": getattr(model, "max_cost_usd", None),
        "requests": getattr(model, "requests", None),
        "served_by": dict(getattr(model, "served_by", {}) or {}),
    }


def _server_counter(target: str) -> TokenCounter:
    if not target.startswith(("http://", "https://", "-- ")):
        from ouncemcp.cli.loader import load_server

        server = load_server(target)
        counter = getattr(server, "counter", None)
        if counter is not None:
            return counter  # type: ignore[no-any-return]
    return get_counter()


async def _recall(suite: Suite, target: str) -> dict[str, Any] | None:
    if not suite.recall or target.startswith(("http://", "https://", "-- ")):
        return None
    from ouncemcp.cli.loader import load_server
    from ouncemcp.discovery import RecallCase

    server = load_server(target)
    measure = getattr(server, "measure_recall", None)
    if measure is None:
        return None
    report = measure([RecallCase(r.query, set(r.expect)) for r in suite.recall])
    if inspect.isawaitable(report):
        report = await report
    return (
        report.to_dict()
        if hasattr(report, "to_dict")
        else {"recall": getattr(report, "recall", None)}
    )
