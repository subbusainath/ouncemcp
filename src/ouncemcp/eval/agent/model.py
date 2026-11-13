"""Models the eval runner talks to (spec EVAL-3, proposal 0019).

A ``Model`` sees the conversation and the tool definitions and answers with text, tool
calls, or both. The runner calls tools, appends their results and asks again until the
model answers without tool calls or the task's turn limit is reached.

``ReplayModel`` follows a script written in the suite, so a suite runs in CI with no
API key, no cost and the same result every time. It checks the server side of a task:
tool names, argument schemas, results, errors, budgets and state changes. Measuring how
well a real model picks tools needs a real model; those adapters implement the same
protocol.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Turn:
    """One model answer: optional text and any tool calls it asks for."""

    text: str = ""
    tool_calls: Sequence[ToolCall] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None


@runtime_checkable
class Model(Protocol):
    """What the runner needs from a model."""

    name: str

    async def respond(
        self,
        task_id: str,
        conversation: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> Turn: ...


class ReplayModel:
    """Plays back each task's ``script`` from the suite, one step per turn.

    Steps:

    - ``{"call": "tool", "args": {...}}``: one tool call. A string argument written
      ``"{{last.<tool>.<path>}}"`` is replaced by that path in the tool's last result,
      for example ``"{{last.inventory.search.items.0.id}}"``.
    - ``{"call": "tool", "args": {...}, "page": "next_cursor"}``: call, then keep calling
      with ``cursor`` set to the result's ``next_cursor`` until it is empty
      (``cursor_arg`` renames the argument).
    - ``{"calls": [...]}``: several calls in one turn.
    - ``{"say": "text"}``: answer and finish.

    When the script runs out, the model finishes with an empty answer.
    """

    name = "replay"

    def __init__(self, scripts: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        self._scripts = {k: list(v) for k, v in scripts.items()}
        self._positions: dict[str, int] = {}
        self._paging: dict[str, bool] = {}

    def reset(self, task_id: str) -> None:
        self._positions[task_id] = 0
        self._paging[task_id] = False

    async def respond(
        self,
        task_id: str,
        conversation: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> Turn:
        script = self._scripts.get(task_id, [])
        position = self._positions.get(task_id, 0)
        results = _last_results(conversation)
        if self._paging.get(task_id):
            step = script[position - 1]
            found, cursor = _lookup(results.get(str(step["call"])), str(step["page"]))
            if found and cursor:
                args = _fill(dict(step.get("args") or {}), results)
                args[str(step.get("cursor_arg", "cursor"))] = cursor
                return Turn(tool_calls=[ToolCall(str(step["call"]), args)])
            self._paging[task_id] = False
        if position >= len(script):
            return Turn()
        self._positions[task_id] = position + 1
        step = script[position]
        if "say" in step:
            return Turn(text=str(step["say"]))
        if "call" in step:
            if "page" in step:
                self._paging[task_id] = True
            args = _fill(dict(step.get("args") or {}), results)
            return Turn(tool_calls=[ToolCall(str(step["call"]), args)])
        if "calls" in step:
            return Turn(
                tool_calls=[
                    ToolCall(str(c["call"]), _fill(dict(c.get("args") or {}), results))
                    for c in step["calls"]
                ]
            )
        raise ValueError(f"task {task_id}: script step {position + 1} needs call, calls or say")


def _last_results(conversation: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The last successful structured result of each tool in the conversation."""
    out: dict[str, Any] = {}
    for message in conversation:
        if message.get("role") == "tool" and message.get("ok", True):
            try:
                out[str(message.get("name"))] = json.loads(str(message.get("content") or "null"))
            except json.JSONDecodeError:
                continue
    return out


def _lookup(value: Any, path: str) -> tuple[bool, Any]:
    from ouncemcp.eval.agent.suite import lookup

    return lookup(value, path)


def _fill(args: dict[str, Any], results: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in list(args.items()):
        if isinstance(value, str) and value.startswith("{{last.") and value.endswith("}}"):
            ref = value[len("{{last.") : -2].strip()
            tool, path = _split_ref(ref, results)
            found, resolved = _lookup(results.get(tool), path)
            args[key] = resolved if found else None
    return args


def _split_ref(ref: str, results: Mapping[str, Any]) -> tuple[str, str]:
    """Split ``inventory.search.items.0.id`` into the longest known tool name and a path."""
    parts = ref.split(".")
    for i in range(len(parts), 0, -1):
        tool = ".".join(parts[:i])
        if tool in results:
            return tool, ".".join(parts[i:])
    return parts[0], ".".join(parts[1:])
