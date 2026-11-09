"""Token accounting shared by the server, ``check``, ``context``, and (later) ``audit``.

Counts are a consistent estimate using an open tokenizer. MCP hosts present
definitions to models differently, so these figures are not an exact host cost
(spec section 8.6, measurement method).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import tiktoken

DEFAULT_ENCODING = "o200k_base"


def compact_json(value: Any) -> str:
    """Serialize ``value`` the way it is measured: compact, key order preserved."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class TokenCounter:
    """Counts tokens with a named tiktoken encoding."""

    def __init__(self, encoding: str = DEFAULT_ENCODING) -> None:
        self.encoding_name = encoding
        self._encoding = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))

    def count_json(self, value: Any) -> int:
        return self.count(compact_json(value))

    def truncate_text(self, text: str, max_tokens: int) -> str:
        """Return the longest prefix of ``text`` that fits in ``max_tokens``."""
        tokens = self._encoding.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return text
        return self._encoding.decode(tokens[: max(max_tokens, 0)])


@lru_cache(maxsize=8)
def get_counter(encoding: str = DEFAULT_ENCODING) -> TokenCounter:
    """Return a cached counter for ``encoding``."""
    return TokenCounter(encoding)
