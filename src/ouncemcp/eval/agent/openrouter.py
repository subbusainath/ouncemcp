"""A real model for agent task suites, through OpenRouter (spec EVAL-3; proposal 0026).

``OpenRouterModel`` sends each turn to OpenRouter's OpenAI-compatible chat completions
API with the server's tools, and turns the reply into tool calls for the runner. One
adapter reaches many providers (Anthropic, OpenAI, Google, open models), so the same
suite can compare models.

- **Spending cap.** ``max_cost_usd`` (default 1.00) is checked before every request
  against the cost OpenRouter reports for each reply. When it is reached, the model
  raises ``SpendingLimitReached`` and ``run_suite`` stops starting new runs.
- **Real usage.** Input and output tokens come from the provider's own counts, not
  estimates, and the cost of every run is recorded.
- **Tool names.** OpenAI-style function names allow only letters, digits, ``_`` and
  ``-``, so ``inventory.search`` is sent as ``inventory__search`` and mapped back.
- **Rate limits.** 429 and 5xx replies are retried with backoff (honoring
  ``Retry-After``), and ``fallbacks`` are passed to OpenRouter's model routing.
- **Key.** Read from ``OPENROUTER_API_KEY``. Where a network proxy injects the key
  (managed environments), any placeholder value works.

Determinism: ``temperature=0`` by default, but providers do not promise identical
replies, so run several ``repeats`` and compare rates, not single runs.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

import anyio
import httpx2

from ouncemcp.eval.agent.model import ToolCall, Turn

API_URL = "https://openrouter.ai/api/v1/chat/completions"
SYSTEM_PROMPT = (
    "You are an agent working through the tools of one MCP server. Use the tools to "
    "complete the user's task; do not guess data you can look up. When the task is done "
    "or cannot be done, answer briefly without calling a tool."
)
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class SpendingLimitReached(RuntimeError):
    """The model has spent its ``max_cost_usd``; no more requests are sent."""


class QuotaExhausted(SpendingLimitReached):
    """The provider's own quota is used up (for example OpenRouter's daily limit on
    free models); like the spending limit, it stops the suite."""


class ModelError(RuntimeError):
    """The provider refused or failed a request after retries."""


def safe_tool_names(names: Sequence[str]) -> dict[str, str]:
    """Map MCP tool names to OpenAI-style function names (safe name -> MCP name)."""
    out: dict[str, str] = {}
    for name in names:
        base = _SAFE.sub("_", name.replace(".", "__"))[:64] or "tool"
        safe, n = base, 1
        while safe in out:
            n += 1
            safe = f"{base[: 64 - len(str(n)) - 1]}_{n}"
        out[safe] = name
    return out


class OpenRouterModel:
    """A ``Model`` backed by OpenRouter. ``model`` is an OpenRouter id such as
    ``anthropic/claude-haiku-5.5`` or ``nvidia/nemotron-3.5-super-vl:free``."""

    def __init__(
        self,
        model: str,
        *,
        max_cost_usd: float = 1.0,
        fallbacks: Sequence[str] = (),
        temperature: float = 0.0,
        max_tokens: int = 2_000,
        system_prompt: str = SYSTEM_PROMPT,
        api_key: str | None = None,
        retries: int = 5,
        timeout_s: float = 120.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        if max_cost_usd < 0:
            raise ValueError("max_cost_usd must be >= 0")
        self.model = model
        self.name = f"openrouter:{model}"
        self.max_cost_usd = max_cost_usd
        self.fallbacks = tuple(fallbacks)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.retries = retries
        self.timeout_s = timeout_s
        self.spent_usd = 0.0
        self.requests = 0
        self.quota_reason: str | None = None
        self.served_by: dict[str, int] = {}  # model ids that actually answered
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("Set OPENROUTER_API_KEY (or pass api_key=) to use OpenRouter")
        self._headers = {
            "Authorization": f"Bearer {key}",
            "X-Title": "OunceMCP eval",
        }
        self._transport = transport

    @property
    def exhausted(self) -> bool:
        return self.quota_reason is not None or self.spent_usd >= self.max_cost_usd

    def _messages(
        self, conversation: Sequence[Mapping[str, Any]], to_safe: Mapping[str, str]
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        pending: list[str] = []  # tool_call ids waiting for their tool messages, in order
        for index, message in enumerate(conversation):
            role = message.get("role")
            if role == "user":
                messages.append({"role": "user", "content": str(message.get("content", ""))})
            elif role == "assistant":
                calls = []
                for j, call in enumerate(message.get("tool_calls") or []):
                    call_id = f"call_{index}_{j}"
                    pending.append(call_id)
                    calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": to_safe.get(str(call["name"]), str(call["name"])),
                                "arguments": json.dumps(call.get("arguments") or {}),
                            },
                        }
                    )
                entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content") or None,
                }
                if calls:
                    entry["tool_calls"] = calls
                messages.append(entry)
            elif role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": pending.pop(0) if pending else f"call_{index}",
                        "content": str(message.get("content", "")),
                    }
                )
        return messages

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        delay = 2.0
        last = ""
        async with httpx2.AsyncClient(
            headers=self._headers, timeout=self.timeout_s, transport=self._transport
        ) as client:
            for attempt in range(self.retries + 1):
                response = await client.post(API_URL, json=body)
                if response.status_code == 200:
                    data: dict[str, Any] = response.json()
                    if "error" not in data:
                        return data
                    last = json.dumps(data["error"])[:300]
                    code = data["error"].get("code")
                    if code not in (429, 502, 503) and not str(code).startswith("5"):
                        raise ModelError(f"{self.model}: {last}")
                elif response.status_code == 429 and "per-day" in response.text:
                    # A daily quota, not a burst limit: retrying cannot help today.
                    self.quota_reason = "the provider's daily request quota is used up"
                    raise QuotaExhausted(f"{self.model}: {self.quota_reason}")
                elif response.status_code == 429 or response.status_code >= 500:
                    last = f"HTTP {response.status_code}: {response.text[:200]}"
                else:
                    raise ModelError(
                        f"{self.model}: HTTP {response.status_code}: {response.text[:300]}"
                    )
                if attempt == self.retries:
                    break
                wait = float(response.headers.get("retry-after") or delay)
                await anyio.sleep(min(wait, 60.0))
                delay = min(delay * 2, 30.0)
        raise ModelError(f"{self.model}: gave up after {self.retries + 1} attempts ({last})")

    async def respond(
        self,
        task_id: str,
        conversation: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
    ) -> Turn:
        if self.quota_reason is not None:
            raise QuotaExhausted(f"{self.model}: {self.quota_reason}")
        if self.exhausted:
            raise SpendingLimitReached(
                f"spent ${self.spent_usd:.4f} of the ${self.max_cost_usd:.2f} limit"
            )
        from_safe = safe_tool_names([str(t.get("name")) for t in tools])
        to_safe = {mcp: safe for safe, mcp in from_safe.items()}
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(conversation, to_safe),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": to_safe[str(t.get("name"))],
                        "description": str(t.get("description", "")),
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "usage": {"include": True},
        }
        if self.fallbacks:
            body["models"] = [self.model, *self.fallbacks]
        data = await self._post(body)
        self.requests += 1
        usage = data.get("usage") or {}
        self.spent_usd += float(usage.get("cost") or 0.0)
        served = str(data.get("model") or self.model)
        self.served_by[served] = self.served_by.get(served, 0) + 1
        message = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else dict(raw)
                if not isinstance(arguments, dict):
                    arguments = {"_invalid_arguments": raw}
            except json.JSONDecodeError:
                arguments = {"_invalid_arguments": raw}  # the server rejects it: a validation error
            name = str(function.get("name", ""))
            calls.append(ToolCall(from_safe.get(name, name), arguments))
        return Turn(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
