"""BUD-6 (P1): provider token counting, tested against recorded responses.

The real ``anthropic`` SDK sends the request; a mock transport answers the way the
``/v1/messages/count_tokens`` endpoint does, so no key or network is needed.
"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field

from ouncemcp import Effect, OunceServer, get_counter
from ouncemcp.cli import main
from ouncemcp.core.provider_tokens import AnthropicCounter

REAL_CLIENT = anthropic.Anthropic

FRAMING = 7  # tokens the endpoint adds for one user message, as recorded


class Recorder:
    """Answers count_tokens like the API: framing plus one token per word."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        text = body["messages"][0]["content"]
        return httpx2.Response(200, json={"input_tokens": FRAMING + len(text.split())})


class Out(BaseModel):
    total: int


def client_for(recorder: Recorder) -> anthropic.Anthropic:
    return REAL_CLIENT(
        api_key="sk-ant-test",
        base_url="https://api.anthropic.com",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(recorder)),
        max_retries=0,
    )


def test_counts_with_the_providers_endpoint_and_subtracts_the_framing():
    recorder = Recorder()
    counter = AnthropicCounter("claude-opus-5-5", client=client_for(recorder))
    assert counter.name == "anthropic:claude-opus-5-5"
    assert counter.count("find books by ada moreau") == 5
    assert counter.count("") == 0

    sent = recorder.requests[0]  # the text itself; the framing probe follows it
    assert sent.method == "POST" and sent.url.path == "/v1/messages/count_tokens"
    assert "sk-ant-test" in (
        sent.headers.get("x-api-key", "") + sent.headers.get("authorization", "")
    )
    assert json.loads(sent.content) == {
        "model": "claude-opus-5-5",
        "messages": [{"role": "user", "content": "find books by ada moreau"}],
    }


def test_each_text_is_requested_once():
    recorder = Recorder()
    counter = AnthropicCounter("claude-opus-5-5", client=client_for(recorder))
    for _ in range(3):
        counter.count("the same text")
    counter.count("another text")
    # one framing probe, then one request per distinct text
    assert len(recorder.requests) == 3


def test_truncation_uses_provider_counts():
    counter = AnthropicCounter("claude-opus-5-5", client=client_for(Recorder()))
    cut = counter.truncate_text("one two three four five six", 3)
    assert counter.count(cut) <= 3 and "one two three".startswith(cut.strip()[:3])


@pytest.mark.anyio
async def test_a_server_reports_with_the_provider_tokenizer():
    counter = AnthropicCounter("claude-opus-5-5", client=client_for(Recorder()))
    server = OunceServer("shelf", tokenizer=counter)

    @server.tool(name="books.count", effect=Effect.READ)
    def count_books(subject: str = Field(description="Subject to count")) -> Out:
        """Count the books on a subject. Returns the total."""
        return Out(total=1)

    report = await server.cost_report()
    assert report.tokenizer == "anthropic:claude-opus-5-5" and report.total > 0


def test_tokenizer_names_select_the_provider(monkeypatch):
    made: list[str] = []

    class FakeClient:
        def __init__(self) -> None:
            self.messages = self

        def count_tokens(self, *, model: str, messages: list) -> object:
            made.append(model)
            return type("R", (), {"input_tokens": FRAMING + 1})()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    counter = get_counter("anthropic:claude-sonnet-5-5")
    assert isinstance(counter, AnthropicCounter) and counter.count("hi") == 1
    assert made == ["claude-sonnet-5-5", "claude-sonnet-5-5"]
    assert get_counter("o200k_base").name == "o200k_base"


def test_check_accepts_a_provider_tokenizer(monkeypatch, capsys):
    monkeypatch.setattr(anthropic, "Anthropic", lambda: client_for(Recorder()), raising=True)
    assert (
        main(
            [
                "check",
                "examples/book/shelf/step1_catalog.py",
                "--tokenizer",
                "anthropic:claude-opus-5-5",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "anthropic:claude-opus-5-5" in out


def test_a_model_id_is_required():
    with pytest.raises(ValueError, match="model id"):
        AnthropicCounter("  ", client=object())
