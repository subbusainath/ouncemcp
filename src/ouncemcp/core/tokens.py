"""Token accounting shared by the server, ``check``, ``context``, and (later) ``audit``.

Counts are a consistent estimate. MCP hosts present definitions to models
differently and model families tokenize differently, so these figures are not an
exact host cost (spec section 8.6, measurement method). For that reason the
tokenizer is pluggable (spec BUD-6) and every report names the one it used.

Three ways to choose a tokenizer, all through ``get_counter`` (and the
``tokenizer=`` argument of ``OunceServer``)::

    get_counter("cl100k_base")                    # any tiktoken encoding
    get_counter(my_count, name="my-model-v2")     # any callable: str -> int
    get_counter(MyCounter())                      # a TokenCounter subclass
"""

from __future__ import annotations

import json
import operator
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import tiktoken

DEFAULT_ENCODING = "o200k_base"


def compact_json(value: Any) -> str:
    """Serialize ``value`` the way it is measured: compact, key order preserved."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class TokenCounter(ABC):
    """Counts tokens. Subclass it, or use ``get_counter`` with an encoding or a callable.

    A counter has a ``name`` that appears in every report, and a ``count`` method
    that returns a non-negative integer. Shaping and truncation assume counts grow
    with the text: a prefix of a text never has noticeably more tokens than the whole
    text (a margin of a few tokens is allowed, as at a token boundary).
    """

    name: str

    @abstractmethod
    def count(self, text: str) -> int:
        """Return the number of tokens in ``text``."""

    @property
    def encoding_name(self) -> str:
        """The counter's name (kept for code written before BUD-6)."""
        return self.name

    def count_json(self, value: Any) -> int:
        return self.count(compact_json(value))

    def truncate_text(self, text: str, max_tokens: int) -> str:
        """Return the longest prefix of ``text`` that fits in ``max_tokens``.

        This default searches by prefix length and calls ``count`` about
        ``log2(len(text))`` times. Counters that can cut at token boundaries directly
        should override it.
        """
        limit = max(max_tokens, 0)
        if self.count(text) <= limit:
            return text
        low, high = 0, len(text)  # the best cut is in [low, high); count(text) > limit
        while low < high:
            mid = (low + high + 1) // 2
            if self.count(text[:mid]) <= limit:
                low = mid
            else:
                high = mid - 1
        return text[:low]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


class TiktokenCounter(TokenCounter):
    """Counts tokens with a named tiktoken encoding such as ``o200k_base``."""

    def __init__(self, encoding: str = DEFAULT_ENCODING) -> None:
        try:
            self._encoding = tiktoken.get_encoding(encoding)
        except ValueError as exc:
            known = ", ".join(tiktoken.list_encoding_names())
            raise ValueError(
                f"Unknown tiktoken encoding '{encoding}'. Choose one of: {known}. "
                "For another tokenizer, pass a counting function: "
                "get_counter(fn, name='my-tokenizer')."
            ) from exc
        self.name = encoding

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))

    def truncate_text(self, text: str, max_tokens: int) -> str:
        tokens = self._encoding.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return text
        return self._encoding.decode(tokens[: max(max_tokens, 0)])


class CallableCounter(TokenCounter):
    """Counts tokens with your own function, for example a model vendor's tokenizer.

    ``fn`` takes a string and returns its token count as a non-negative integer.
    ``name`` identifies the tokenizer in every report, so give it the model or
    tokenizer and its version, such as ``"acme-tokenizer-2.1"``.
    """

    def __init__(self, fn: Callable[[str], int], *, name: str) -> None:
        if not callable(fn):
            raise TypeError("fn must be a callable that takes a string and returns an int")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                "A custom tokenizer needs a name, because every report states the tokenizer "
                "used. Pass name='my-tokenizer-v1'."
            )
        self._fn = fn
        self.name = name.strip()

    def count(self, text: str) -> int:
        result = self._fn(text)
        try:
            n = operator.index(result)
        except TypeError:
            n = -1
        if n < 0:
            raise ValueError(
                f"Tokenizer '{self.name}' must return a non-negative integer, got {result!r}"
            )
        return n


@lru_cache(maxsize=8)
def _encoding_counter(encoding: str) -> TiktokenCounter:
    return TiktokenCounter(encoding)


def get_counter(
    tokenizer: str | TokenCounter | Callable[[str], int] = DEFAULT_ENCODING,
    *,
    name: str | None = None,
) -> TokenCounter:
    """Return a counter for a tiktoken encoding name, a ``TokenCounter``, or a callable.

    ``name`` is required for a bare callable and not allowed otherwise.
    """
    if isinstance(tokenizer, str):
        if name is not None:
            raise ValueError("name only applies to a callable tokenizer")
        return _encoding_counter(tokenizer)
    if isinstance(tokenizer, TokenCounter):
        if name is not None:
            raise ValueError("name only applies to a callable tokenizer")
        return tokenizer
    if callable(tokenizer):
        return CallableCounter(tokenizer, name=name or "")
    raise TypeError(
        "tokenizer must be a tiktoken encoding name, a TokenCounter, or a callable "
        f"that takes a string and returns an int, not {type(tokenizer).__name__}"
    )
