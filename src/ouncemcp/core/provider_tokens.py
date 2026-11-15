"""Token counts from a model provider's own counting API (spec BUD-6, P1).

tiktoken encodings are exact for OpenAI models and an estimate for everyone else.
For Claude models, ``AnthropicCounter`` asks Anthropic's token-counting endpoint
(``messages.count_tokens``) through the official ``anthropic`` SDK, so ``check``,
``context`` and cost reports show what Claude actually counts::

    from ouncemcp.core.provider_tokens import AnthropicCounter
    server = OunceServer("shelf", tokenizer=AnthropicCounter("claude-opus-5-5"))

or, on the command line, ``ouncemcp check server.py --tokenizer anthropic:claude-opus-5-5``.
Install the extra with ``pip install "ouncemcp[anthropic]"``; the SDK reads
``ANTHROPIC_API_KEY`` (or an ``ant auth login`` profile) as usual.

Every distinct text is one request, so counts are cached. That suits ``check``,
``context``, ``audit`` and reports. For a server that shapes results on every call, a
local tokenizer is faster; measure with the provider counter, serve with tiktoken.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from ouncemcp.core.tokens import TokenCounter

PREFIX = "anthropic:"
DEFAULT_CACHE_SIZE = 4096
_PROBE = "a"  # one token in every Claude tokenizer; used to measure the message framing


class AnthropicCounter(TokenCounter):
    """Counts tokens with Anthropic's ``messages.count_tokens`` for one Claude model.

    The endpoint counts a whole request, so each text is sent as one user message and
    the fixed cost of that message framing is measured once and subtracted. ``client``
    is an ``anthropic.Anthropic`` instance; without one, a default client is created,
    which reads credentials from the environment.
    """

    def __init__(
        self,
        model: str,
        *,
        client: Any = None,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("AnthropicCounter needs a model id, such as 'claude-opus-5-5'")
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on the install
                raise ImportError(
                    'AnthropicCounter needs the anthropic SDK: pip install "ouncemcp[anthropic]"'
                ) from exc
            client = anthropic.Anthropic()
        self.model = model.strip()
        self.name = f"{PREFIX}{self.model}"
        self._client = client
        self._cache: OrderedDict[str, int] = OrderedDict()
        self._cache_size = max(cache_size, 1)
        self._framing: int | None = None

    def _request(self, text: str) -> int:
        response = self._client.messages.count_tokens(
            model=self.model,
            messages=[{"role": "user", "content": text}],
        )
        return int(response.input_tokens)

    @property
    def framing_tokens(self) -> int:
        """Tokens the endpoint adds for wrapping a text in one user message."""
        if self._framing is None:
            self._framing = max(self._request(_PROBE) - 1, 0)
        return self._framing

    def count(self, text: str) -> int:
        if not text:
            return 0  # the endpoint rejects empty content; an empty text has no tokens
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached
        n = max(self._request(text) - self.framing_tokens, 0)
        self._cache[text] = n
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return n


def provider_counter(name: str) -> TokenCounter | None:
    """The provider counter a ``--tokenizer`` value names, or ``None`` for an encoding."""
    if name.startswith(PREFIX):
        return AnthropicCounter(name[len(PREFIX) :])
    return None
