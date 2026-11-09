"""Response shaping (spec section 8.1.4).

Results are converted to compact JSON, reduced to the concise format by default,
and truncated to fit the token budget with a note that says what was omitted.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from ouncemcp.core.tokens import TokenCounter, compact_json

ResponseFormat = Literal["concise", "detailed"]

# Tokens reserved for the truncation note so the final text stays within budget.
NOTE_RESERVE = 80
CURSOR_FIELDS = ("next_cursor", "nextCursor", "cursor", "next_page_token", "nextPageToken")
_ELLIPSIS = "…"


class _DetailMarker:
    """Annotation marker for fields returned only in the ``detailed`` format."""

    _instance: _DetailMarker | None = None

    def __new__(cls) -> _DetailMarker:  # noqa: PYI034
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Detail"


Detail = _DetailMarker()
"""Mark an output field as detail-only: ``tags: Annotated[dict | None, Detail] = None``."""


def is_detail_field(field_info: Any) -> bool:
    return any(item is Detail for item in getattr(field_info, "metadata", ()))


def _nested_models(annotation: Any) -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    origin = typing.get_origin(annotation)
    if origin is not None or isinstance(annotation, types.UnionType):
        for arg in typing.get_args(annotation):
            found.extend(_nested_models(arg))
    return found


def model_has_detail_fields(model: type[BaseModel], _seen: set[type] | None = None) -> bool:
    """Return True if ``model`` or any nested model declares a ``Detail`` field."""
    seen = _seen if _seen is not None else set()
    if model in seen:
        return False
    seen.add(model)
    for field_info in model.model_fields.values():
        if is_detail_field(field_info):
            return True
        for nested in _nested_models(field_info.annotation):
            if model_has_detail_fields(nested, seen):
                return True
    return False


def validate_detail_fields(model: type[BaseModel], _seen: set[type] | None = None) -> None:
    """Detail fields are omitted in concise results, so they must not be required."""
    seen = _seen if _seen is not None else set()
    if model in seen:
        return
    seen.add(model)
    for name, field_info in model.model_fields.items():
        if is_detail_field(field_info) and field_info.is_required():
            raise ValueError(
                f"{model.__name__}.{name} is marked Detail but is required; "
                "give it a default (for example None) so concise results stay valid"
            )
        for nested in _nested_models(field_info.annotation):
            validate_detail_fields(nested, seen)


def to_jsonable(value: Any, response_format: ResponseFormat) -> Any:
    """Convert a result to JSON-compatible data, applying the response format.

    In ``concise`` format, detail fields are dropped and optional fields that are
    ``None`` are omitted.
    """
    concise = response_format == "concise"
    if isinstance(value, BaseModel):
        out: dict[str, Any] = {}
        for name, field_info in type(value).model_fields.items():
            if concise and is_detail_field(field_info):
                continue
            item = getattr(value, name)
            if concise and item is None and not field_info.is_required():
                continue
            out[name] = to_jsonable(item, response_format)
        return out
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, response_format) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, response_format) for v in value]
    return to_jsonable_python(value)


@dataclass
class Truncation:
    """What was removed to fit the budget."""

    lists: dict[str, tuple[int, int]] = field(default_factory=dict)  # path -> (kept, total)
    strings: list[str] = field(default_factory=list)
    text_only: bool = False
    cursor_field: str | None = None  # set when a trimmed result also carries a cursor

    def __bool__(self) -> bool:
        return bool(self.lists or self.strings or self.text_only)

    def note(self, budget: int) -> str:
        parts: list[str] = []
        items = list(self.lists.items())
        for path, (kept, total) in items[:3]:
            parts.append(f"kept {kept} of {total} items in '{path}'")
        if len(items) > 3:
            parts.append(f"trimmed {len(items) - 3} more lists")
        if self.strings:
            shown = ", ".join(f"'{p}'" for p in self.strings[:3])
            more = f" and {len(self.strings) - 3} more" if len(self.strings) > 3 else ""
            parts.append(f"shortened text in {shown}{more}")
        if self.text_only:
            parts.append("cut the serialized result")
        advice = "Narrow the request or ask for the next page to see more."
        if self.cursor_field and self.lists:
            kept = min(k for k, _ in self.lists.values())
            advice = (
                f"'{self.cursor_field}' skips the removed items; repeat this call with "
                f"limit={max(kept, 1)} to page without gaps."
            )
        return f"[ouncemcp] Result truncated to fit {budget} tokens: {'; '.join(parts)}. {advice}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lists": {p: {"kept": k, "total": t} for p, (k, t) in self.lists.items()},
            "strings": list(self.strings),
            "text_only": self.text_only,
            "cursor_field": self.cursor_field,
        }


@dataclass
class ShapedResult:
    data: Any
    text: str
    tokens: int
    budget: int
    response_format: ResponseFormat
    truncation: Truncation

    def meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "result_tokens": self.tokens,
            "budget": self.budget,
            "format": self.response_format,
            "truncated": bool(self.truncation),
        }
        if self.truncation:
            meta["truncation"] = self.truncation.to_dict()
        return meta


def _walk(value: Any, path: str, lists: list, strings: list) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _walk(v, f"{path}.{k}" if path else k, lists, strings)
    elif isinstance(value, list):
        lists.append((path or "$", value))
        for i, v in enumerate(value):
            _walk(v, f"{path}[{i}]", lists, strings)
    elif isinstance(value, str):
        strings.append(path or "$")


def _get_parent(root: Any, path: str) -> tuple[Any, Any]:
    """Resolve a dotted/indexed path to (container, key) so the value can be replaced."""
    tokens: list[Any] = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if buf:
                tokens.append(buf)
            buf = ""
        elif ch == "[":
            if buf:
                tokens.append(buf)
            buf = ""
            j = path.index("]", i)
            tokens.append(int(path[i + 1 : j]))
            i = j
        else:
            buf += ch
        i += 1
    if buf:
        tokens.append(buf)
    container = root
    for tok in tokens[:-1]:
        container = container[tok]
    return container, tokens[-1]


def shape_result(
    value: Any,
    *,
    response_format: ResponseFormat,
    budget: int,
    counter: TokenCounter,
) -> ShapedResult:
    """Shape ``value`` so its serialized text fits in ``budget`` tokens.

    Strategy, in order: drop items from the end of the largest lists, shorten the
    longest strings, and as a last resort cut the serialized text. The returned
    ``text`` (including any truncation note) never exceeds ``budget`` tokens.
    """
    data = to_jsonable(value, response_format)
    text = compact_json(data)
    tokens = counter.count(text)
    truncation = Truncation()
    if tokens <= budget:
        return ShapedResult(data, text, tokens, budget, response_format, truncation)

    target = max(budget - NOTE_RESERVE, 1)

    def size() -> int:
        return counter.count(compact_json(data))

    # 1. Trim lists, largest first. Binary search the number of items to keep.
    visited: set[str] = set()
    while size() > target:
        lists: list[tuple[str, list]] = []
        _walk(data, "", lists, [])
        candidates = [(p, lst) for p, lst in lists if lst and p not in visited]
        if not candidates:
            break
        path, lst = max(candidates, key=lambda item: len(compact_json(item[1])))
        original = list(lst)
        # Keep at least one item: an oversized item is better shortened (step 2)
        # than dropped, so the agent still sees what it asked for.
        lo, hi = 1, len(original)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lst[:] = original[:mid]
            if size() <= target:
                lo = mid
            else:
                hi = mid - 1
        lst[:] = original[:lo]
        visited.add(path)
        if lo < len(original):
            truncation.lists[path] = (lo, len(original))

    # 2. Shorten the longest strings.
    while size() > target:
        strings: list[str] = []
        _walk(data, "", [], strings)
        candidates = [p for p in strings if p not in truncation.strings]
        if not candidates:
            break

        def strlen(p: str) -> int:
            parent, key = _get_parent(data, p) if p != "$" else (None, None)
            return len(parent[key]) if parent is not None else len(data)

        path = max(candidates, key=strlen)
        if path == "$":
            break
        parent, key = _get_parent(data, path)
        original = parent[key]
        lo, hi = 0, len(original)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            parent[key] = original[:mid] + _ELLIPSIS
            if size() <= target:
                lo = mid
            else:
                hi = mid - 1
        parent[key] = original[:lo] + _ELLIPSIS
        truncation.strings.append(path)

    if truncation.lists and isinstance(data, dict):
        for key in CURSOR_FIELDS:
            if data.get(key) is not None:
                truncation.cursor_field = key
                break

    text = compact_json(data)
    if counter.count(text) > target:
        # 3. Last resort: the structure itself is too large. Return text only.
        text = counter.truncate_text(text, target)
        truncation.text_only = True
        data = None

    note = truncation.note(budget)
    full = f"{text}\n{note}"
    if counter.count(full) > budget:  # pragma: no cover - NOTE_RESERVE is generous
        full = counter.truncate_text(full, budget)
    return ShapedResult(data, full, counter.count(full), budget, response_format, truncation)
