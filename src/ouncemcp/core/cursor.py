"""Opaque pagination cursors.

M1 step 1 (RES-9): versioned, opaque offset cursors. SEC-12 replaces the payload
with a signed, principal-bound format behind the same two functions.
"""

from __future__ import annotations

import base64
import binascii

_PREFIX = "o1."


class InvalidCursor(ValueError):
    """Raised when a cursor was not issued by this server or was modified."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "Invalid cursor: pass next_cursor from the previous result unchanged, "
            "or omit cursor to start from the beginning." + (f" ({detail})" if detail else "")
        )


def encode_offset(offset: int) -> str:
    if offset < 0:
        raise ValueError("offset must be >= 0")
    raw = base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")
    return _PREFIX + raw


def decode_offset(cursor: str | None) -> int:
    """Return the offset for ``cursor``; ``None`` means the first page."""
    if cursor is None:
        return 0
    if not cursor.startswith(_PREFIX):
        raise InvalidCursor("unknown format")
    raw = cursor[len(_PREFIX) :]
    try:
        value = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        offset = int(value)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursor("corrupted") from exc
    if offset < 0 or str(offset) != value:
        raise InvalidCursor("corrupted")
    return offset
