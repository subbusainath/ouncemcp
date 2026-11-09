"""Budget-aware pagination (spec RES-3, RES-9).

Mark the page list and the cursor field on the output model::

    class SearchResult(BaseModel):
        items: Annotated[list[Resource], Paginated]
        total: int
        next_cursor: Cursor = None

and build the page with ``ctx.page(rows, cursor=args.cursor, limit=args.limit)``.
If the result is trimmed to fit the token budget, OunceMCP rewrites ``next_cursor``
so the next call resumes right after the last item actually returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class _Marker:
    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


Paginated = _Marker("Paginated")
"""Mark the list field that holds one page: ``items: Annotated[list[X], Paginated]``."""

_CURSOR = _Marker("Cursor")
Cursor = Annotated[str | None, _CURSOR]
"""Type for the next-page cursor field: ``next_cursor: Cursor = None``."""


@dataclass(frozen=True)
class PageWindow(Generic[T]):
    items: list[T]
    start: int
    total: int | None
    next_cursor: str | None


@dataclass(frozen=True)
class PageFields:
    list_key: str
    cursor_key: str


def _has(field_info: Any, marker: _Marker) -> bool:
    return any(item is marker for item in getattr(field_info, "metadata", ()))


def find_page_fields(model: type[BaseModel], tool_name: str) -> PageFields | None:
    """Return the marked page and cursor fields, validating how they are declared."""
    lists = [n for n, f in model.model_fields.items() if _has(f, Paginated)]
    cursors = [n for n, f in model.model_fields.items() if _has(f, _CURSOR)]
    if not lists and not cursors:
        return None
    if len(lists) != 1 or len(cursors) != 1:
        raise TypeError(
            f"Tool '{tool_name}': a paginated output needs exactly one Paginated list "
            f"field and one Cursor field (found {len(lists)} and {len(cursors)})"
        )
    if model.model_fields[cursors[0]].is_required():
        raise TypeError(f"Tool '{tool_name}': the Cursor field must default to None")
    return PageFields(list_key=lists[0], cursor_key=cursors[0])


def window(rows: Sequence[T], *, start: int, limit: int) -> PageWindow[T]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    from ouncemcp.core.cursor import encode_offset

    items = list(rows[start : start + limit])
    end = start + len(items)
    return PageWindow(
        items=items,
        start=start,
        total=len(rows),
        next_cursor=encode_offset(end) if end < len(rows) else None,
    )
