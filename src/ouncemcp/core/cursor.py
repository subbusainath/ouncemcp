"""Signed pagination cursors (spec SEC-12, proposal 0003).

A cursor is ``s1.`` followed by base64url of 42 bytes::

    expiry (4)  offset (4)  tool (6)  principal (6)  query (6)  MAC (16)

The MAC is HMAC-SHA256 over the first 26 bytes, truncated to 16. The three 6-byte
fields are digests of the tool name, the caller, and the normalized query
arguments. The MAC covers them, so a cursor can only be used by the caller and
for the request it was issued for, and the checks after the MAC can tell the agent
which part changed without trusting unsigned data.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_core import to_jsonable_python

logger = logging.getLogger("ouncemcp")

KEYS_ENV = "OUNCEMCP_CURSOR_KEYS"
DEFAULT_TTL_S = 3600
MIN_KEY_BYTES = 16

_PREFIX = "s1."
_LEGACY_PREFIX = "o1."
_DOMAIN = b"ouncemcp-cursor-v1\x00"
_DIGEST = 6
_MAC = 16
_BODY = 4 + 4 + 3 * _DIGEST
_MAX_OFFSET = 2**32 - 1

CursorProblem = Literal["malformed", "legacy", "signature", "expired", "tool", "principal", "query"]

_RESTART = "omit cursor to start from the first page"
_ADVICE: dict[str, str] = {
    "malformed": f"Pass next_cursor from the previous result unchanged, or {_RESTART}.",
    "legacy": (
        "It came from an older version of this server. Omit cursor to start over, "
        "then use the new next_cursor values."
    ),
    "signature": (
        "It was not issued by this server, or it was modified. Pass next_cursor from "
        f"the previous result unchanged, or {_RESTART}."
    ),
    "expired": "Omit cursor to start again from the first page.",
    "tool": f"It belongs to a different tool. Use it only with the tool that returned it, or {_RESTART}.",
    "principal": f"It was issued to a different caller. Use only cursors you received, or {_RESTART}.",
    "query": (
        "The filters changed since it was issued. Keep the same arguments "
        "(only limit may change) and pass next_cursor again, or omit cursor to "
        "start over with the new filters."
    ),
}
_TITLE = {
    "malformed": "Invalid cursor.",
    "legacy": "Outdated cursor.",
    "signature": "Invalid cursor.",
    "expired": "Cursor expired.",
    "tool": "Cursor from another tool.",
    "principal": "Cursor from another caller.",
    "query": "Cursor does not match this request.",
}


class InvalidCursor(ValueError):
    """Raised when a cursor cannot be used for this call.

    ``reason`` says why: ``malformed``, ``legacy``, ``signature``, ``expired``,
    ``tool``, ``principal`` or ``query``. The message tells the agent how to recover.
    """

    def __init__(self, reason: CursorProblem = "malformed") -> None:
        self.reason: CursorProblem = reason
        super().__init__(f"{_TITLE[reason]} {_ADVICE[reason]}")


def _digest(label: bytes, data: bytes) -> bytes:
    return hashlib.sha256(label + b"\x00" + data).digest()[:_DIGEST]


def _canonical_query(query: Mapping[str, Any] | None) -> bytes:
    """Stable bytes for a set of arguments: JSON, sorted keys, unset values dropped."""
    plain = to_jsonable_python(dict(query or {}), fallback=str)
    kept = {k: v for k, v in plain.items() if v is not None}
    return json.dumps(kept, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _as_key(key: str | bytes) -> bytes:
    raw = key.encode() if isinstance(key, str) else bytes(key)
    if len(raw) < MIN_KEY_BYTES:
        raise ValueError(
            f"Cursor signing keys must be at least {MIN_KEY_BYTES} bytes. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return raw


class CursorSigner:
    """Issues and verifies cursors with HMAC keys.

    ``keys`` is newest first: the first key signs, every key verifies, so a key can be
    rotated by adding the new one in front and removing the old one once its cursors
    have expired (after ``ttl_s``).
    """

    def __init__(
        self,
        keys: Sequence[str | bytes],
        *,
        ttl_s: int = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.time,
        _ephemeral: bool = False,
    ) -> None:
        if not keys:
            raise ValueError("CursorSigner needs at least one key")
        if not isinstance(ttl_s, int) or not 0 < ttl_s < 2**31:
            raise ValueError("ttl_s must be a positive number of seconds")
        self._keys = [_as_key(k) for k in keys]
        self.ttl_s = ttl_s
        self._clock = clock
        self.is_ephemeral = _ephemeral
        self._warned = False

    @classmethod
    def ephemeral(
        cls, *, ttl_s: int = DEFAULT_TTL_S, clock: Callable[[], float] = time.time
    ) -> CursorSigner:
        """A random key that lives as long as the process. For development only."""
        return cls([secrets.token_bytes(32)], ttl_s=ttl_s, clock=clock, _ephemeral=True)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        ttl_s: int = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> CursorSigner:
        """Read comma-separated keys, newest first, from ``OUNCEMCP_CURSOR_KEYS``.

        Without that variable the signer uses a random per-process key and warns the
        first time a cursor is issued.
        """
        raw = (os.environ if env is None else env).get(KEYS_ENV, "")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            return cls.ephemeral(ttl_s=ttl_s, clock=clock)
        return cls(keys, ttl_s=ttl_s, clock=clock)

    def bind(
        self, *, tool: str, principal: str, query: Mapping[str, Any] | None = None
    ) -> BoundCursors:
        """Cursors for one call: valid only for this tool, caller and query."""
        return BoundCursors(
            self,
            _digest(b"tool", tool.encode()),
            _digest(b"principal", principal.encode()),
            _digest(b"query", _canonical_query(query)),
        )

    def _mac(self, key: bytes, body: bytes) -> bytes:
        return hmac.new(key, _DOMAIN + body, hashlib.sha256).digest()[:_MAC]

    def _warn_if_ephemeral(self) -> None:
        if self.is_ephemeral and not self._warned:
            self._warned = True
            logger.warning(
                "ouncemcp: no cursor signing key is configured, so cursors use a random key "
                "for this process. They stop working after a restart and across replicas. "
                "Set %s (comma-separated, newest first) or pass cursors=CursorSigner([...]) "
                "before deploying.",
                KEYS_ENV,
            )


@dataclass(frozen=True)
class BoundCursors:
    """Encode and decode offset cursors for one tool, caller and query."""

    signer: CursorSigner
    tool: bytes
    principal: bytes
    query: bytes

    def encode(self, offset: int) -> str:
        if not 0 <= offset <= _MAX_OFFSET:
            raise ValueError(f"offset must be between 0 and {_MAX_OFFSET}")
        signer = self.signer
        signer._warn_if_ephemeral()
        expiry = int(signer._clock()) + signer.ttl_s
        body = (
            expiry.to_bytes(4, "big")
            + offset.to_bytes(4, "big")
            + self.tool
            + self.principal
            + self.query
        )
        raw = body + signer._mac(signer._keys[0], body)
        return _PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def decode(self, cursor: str | None) -> int:
        """Return the offset for ``cursor``; ``None`` means the first page."""
        if cursor is None:
            return 0
        if cursor.startswith(_LEGACY_PREFIX):
            raise InvalidCursor("legacy")
        if not cursor.startswith(_PREFIX):
            raise InvalidCursor("malformed")
        text = cursor[len(_PREFIX) :]
        try:
            raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        except (binascii.Error, ValueError) as exc:
            raise InvalidCursor("malformed") from exc
        if len(raw) != _BODY + _MAC or base64.urlsafe_b64encode(raw).decode().rstrip("=") != text:
            raise InvalidCursor("malformed")
        body, mac = raw[:_BODY], raw[_BODY:]
        signer = self.signer
        if not any(hmac.compare_digest(mac, signer._mac(key, body)) for key in signer._keys):
            raise InvalidCursor("signature")
        if int.from_bytes(body[:4], "big") < int(signer._clock()):
            raise InvalidCursor("expired")
        tool, principal, query = (
            body[8 : 8 + _DIGEST],
            body[8 + _DIGEST : 8 + 2 * _DIGEST],
            body[8 + 2 * _DIGEST :],
        )
        if not hmac.compare_digest(tool, self.tool):
            raise InvalidCursor("tool")
        if not hmac.compare_digest(principal, self.principal):
            raise InvalidCursor("principal")
        if not hmac.compare_digest(query, self.query):
            raise InvalidCursor("query")
        return int.from_bytes(body[4:8], "big")
