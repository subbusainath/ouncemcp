"""Bound request state: handles, approvals and continuation tokens (spec SEC-6).

Anything the server hands to a client and later accepts back, such as a result handle
or an approval, must only work for the caller it was issued to, for the request it was
issued for, and only until it expires. ``StateSigner`` issues such tokens::

    b1.<base64url(payload)>.<base64url(mac)>

The payload is compact JSON: kind, expiry, a nonce, digests of the principal and of
the originating request, and the data. The MAC is HMAC-SHA256 with the cursor keys
under a separate domain, so a cursor can never be passed off as state or the reverse.
The data is signed, not encrypted: do not put secrets in it.

``consume=True`` makes a token single-use within this process, which blocks replay of
an approval. Replicas need a shared ``used`` store for the same guarantee.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, Literal

from pydantic_core import to_jsonable_python

from ouncemcp.core.cursor import CursorSigner
from ouncemcp.core.errors import ErrorCategory, OunceError

_PREFIX = "b1."
_DOMAIN = b"ouncemcp-state-v1\x00"
_MAC = 16
DEFAULT_STATE_TTL_S = 900
MAX_STATE_BYTES = 2048

StateProblem = Literal[
    "malformed", "signature", "expired", "kind", "principal", "request", "replayed"
]

_MESSAGES: dict[str, str] = {
    "malformed": "The token is not valid. Pass it back exactly as the server returned it.",
    "signature": "The token was not issued by this server, or it was modified.",
    "expired": "The token has expired. Start the operation again to get a new one.",
    "kind": "The token is for a different kind of operation.",
    "principal": "The token was issued to a different caller. Use only tokens you received.",
    "request": "The token was issued for a different request. Repeat the original request.",
    "replayed": "The token was already used. Start the operation again to get a new one.",
}


class InvalidState(OunceError):
    """A handle, approval or continuation token cannot be used for this call."""

    category: ErrorCategory = "validation"
    code = "invalid_state"

    def __init__(self, reason: StateProblem) -> None:
        self.reason: StateProblem = reason
        super().__init__(_MESSAGES[reason])


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if _b64(raw) != text:
        raise ValueError("non-canonical base64")
    return raw


def request_digest(request: Any) -> str:
    """A short, stable digest of whatever identifies the originating request."""
    plain = to_jsonable_python(request, fallback=str)
    text = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(b"request\x00" + text.encode()).hexdigest()[:16]


def _principal_digest(principal: str) -> str:
    return hashlib.sha256(b"principal\x00" + principal.encode()).hexdigest()[:16]


class StateSigner:
    """Issues and verifies bound state tokens with the server's cursor keys."""

    def __init__(
        self,
        keys: CursorSigner,
        *,
        ttl_s: int = DEFAULT_STATE_TTL_S,
        clock: Callable[[], float] = time.time,
        used: MutableMapping[str, float] | None = None,
    ) -> None:
        if not isinstance(ttl_s, int) or ttl_s < 1:
            raise ValueError("ttl_s must be a positive number of seconds")
        self._keys = keys
        self.ttl_s = ttl_s
        self._clock = clock
        self._used: MutableMapping[str, float] = {} if used is None else used

    def _mac(self, key: bytes, body: bytes) -> bytes:
        return hmac.new(key, _DOMAIN + body, hashlib.sha256).digest()[:_MAC]

    def issue(
        self,
        kind: str,
        *,
        principal: str,
        request: Any = None,
        data: Mapping[str, Any] | None = None,
        ttl_s: int | None = None,
    ) -> str:
        """Issue a token of ``kind`` for ``principal`` and ``request`` (any JSON value)."""
        if not kind:
            raise ValueError("kind must be a non-empty string")
        self._keys._warn_if_ephemeral()
        payload = {
            "k": kind,
            "e": int(self._clock()) + (ttl_s or self.ttl_s),
            "n": _b64(secrets.token_bytes(9)),
            "p": _principal_digest(principal),
            "r": request_digest(request),
            "d": to_jsonable_python(dict(data or {}), fallback=str),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        if len(body) > MAX_STATE_BYTES:
            raise ValueError(
                f"state data is {len(body)} bytes; keep it under {MAX_STATE_BYTES} and store "
                "large state server-side, keyed by an id in the token"
            )
        return f"{_PREFIX}{_b64(body)}.{_b64(self._mac(self._keys._keys[0], body))}"

    def verify(
        self,
        token: str,
        *,
        kind: str,
        principal: str,
        request: Any = None,
        consume: bool = False,
    ) -> dict[str, Any]:
        """Check ``token`` and return its data, or raise ``InvalidState``.

        ``consume=True`` accepts the token at most once (per process).
        """
        if not isinstance(token, str) or not token.startswith(_PREFIX):
            raise InvalidState("malformed")
        body_text, _, mac_text = token[len(_PREFIX) :].partition(".")
        try:
            body, mac = _unb64(body_text), _unb64(mac_text)
            payload = json.loads(body)
        except (binascii.Error, ValueError):
            raise InvalidState("malformed") from None
        if not any(hmac.compare_digest(mac, self._mac(k, body)) for k in self._keys._keys):
            raise InvalidState("signature")
        if not isinstance(payload, dict):
            raise InvalidState("malformed")
        now = self._clock()
        if int(payload.get("e", 0)) < int(now):
            raise InvalidState("expired")
        if payload.get("k") != kind:
            raise InvalidState("kind")
        if not hmac.compare_digest(str(payload.get("p")), _principal_digest(principal)):
            raise InvalidState("principal")
        if not hmac.compare_digest(str(payload.get("r")), request_digest(request)):
            raise InvalidState("request")
        if consume:
            self._forget_expired(now)
            nonce = str(payload.get("n"))
            if nonce in self._used:
                raise InvalidState("replayed")
            self._used[nonce] = float(payload["e"])
        data = payload.get("d")
        return dict(data) if isinstance(data, dict) else {}

    def _forget_expired(self, now: float) -> None:
        for nonce in [n for n, exp in self._used.items() if exp < now]:
            del self._used[nonce]
