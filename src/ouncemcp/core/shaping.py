"""Response shaping (spec section 8.1.4).

Results are converted to compact JSON, reduced to the concise format by default,
and fitted to the token budget with a note that says what was omitted.

When a validator for the advertised output schema is supplied, every change is
checked against it and reverted if it would break the schema (RES-8). Steps, in
order: trim lists (never below ``min_items``, RES-10), shorten long strings, drop
optional fields across all items. If the smallest valid result is still over
budget, ``ResultTooLarge`` is raised in strict mode; otherwise the serialized
text is cut as a last resort (allowed only without an advertised schema).
"""

from __future__ import annotations

import types
import typing
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from ouncemcp.core.tokens import TokenCounter, compact_json

ResponseFormat = Literal["concise", "detailed"]

# Tokens reserved for the truncation note so the final text stays within budget.
NOTE_RESERVE = 80
# Strings shorter than this are not worth shortening (ids, names, enums).
MIN_SHORTEN_CHARS = 64
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

    In ``concise`` format, detail fields are dropped. In both formats, optional
    fields that are ``None`` are omitted: advertised schemas are compacted
    (spec BUD-8) and do not accept ``null`` for optional fields (spec RES-8).
    """
    concise = response_format == "concise"
    if isinstance(value, BaseModel):
        out: dict[str, Any] = {}
        for name, field_info in type(value).model_fields.items():
            if concise and is_detail_field(field_info):
                continue
            item = getattr(value, name)
            if item is None and not field_info.is_required():
                continue
            out[name] = to_jsonable(item, response_format)
        return out
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, response_format) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, response_format) for v in value]
    return to_jsonable_python(value)


Path = tuple[str | int, ...]


def format_path(path: Path, *, pattern: bool = False) -> str:
    out = ""
    for part in path:
        if isinstance(part, int):
            out += "[*]" if pattern else f"[{part}]"
        else:
            out += f".{part}" if out else part
    return out or "$"


@dataclass
class Truncation:
    """What was removed to fit the budget."""

    lists: dict[str, tuple[int, int]] = field(default_factory=dict)  # path -> (kept, total)
    strings: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # field patterns, e.g. items[*].tags
    text_only: bool = False
    cursor_field: str | None = None  # handler-managed cursor that now skips items
    cursor_rewritten: bool = False  # framework moved the cursor to the last kept item

    def __bool__(self) -> bool:
        return bool(self.lists or self.strings or self.dropped or self.text_only)

    def note(self, budget: int) -> str:
        parts: list[str] = []
        items = list(self.lists.items())
        for path, (kept, total) in items[:3]:
            parts.append(f"kept {kept} of {total} items in '{path}'")
        if len(items) > 3:
            parts.append(f"trimmed {len(items) - 3} more lists")
        for label, paths in (("shortened text in", self.strings), ("omitted", self.dropped)):
            if paths:
                shown = ", ".join(f"'{p}'" for p in paths[:3])
                more = f" and {len(paths) - 3} more" if len(paths) > 3 else ""
                parts.append(f"{label} {shown}{more}")
        if self.text_only:
            parts.append("cut the serialized result")
        if self.cursor_rewritten:
            advice = "Call again with next_cursor to get the rest."
        elif self.cursor_field and self.lists:
            kept = min(k for k, _ in self.lists.values())
            advice = (
                f"'{self.cursor_field}' skips the removed items; repeat this call with "
                f"limit={max(kept, 1)} to page without gaps."
            )
        elif self.dropped:
            advice = "Ask for fewer items to get every field."
        else:
            advice = "Narrow the request or ask for the next page to see more."
        return f"[ouncemcp] Result truncated to fit {budget} tokens: {'; '.join(parts)}. {advice}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lists": {p: {"kept": k, "total": t} for p, (k, t) in self.lists.items()},
            "strings": list(self.strings),
            "dropped": list(self.dropped),
            "text_only": self.text_only,
            "cursor_field": self.cursor_field,
            "cursor_rewritten": self.cursor_rewritten,
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


class ResultTooLarge(Exception):
    """The smallest schema-valid result is still over the budget (spec RES-8)."""

    def __init__(self, min_tokens: int, budget: int, truncation: Truncation) -> None:
        self.min_tokens = min_tokens
        self.budget = budget
        self.truncation = truncation
        super().__init__(f"smallest valid result is {min_tokens} tokens; budget is {budget}")


@dataclass(frozen=True)
class PageSpec:
    """Lets shaping keep a framework-issued cursor consistent with trimmed pages."""

    list_key: str
    cursor_key: str
    start: int
    count: int
    encode: Callable[[int], str]


class Validator(typing.Protocol):
    """Anything with ``is_valid(instance) -> bool``, such as a jsonschema validator."""

    def is_valid(self, instance: Any) -> bool: ...


def _collect(value: Any, path: Path, lists: list, strings: list, keys: list) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            child = (*path, k)
            keys.append((value, k, child))
            _collect(v, child, lists, strings, keys)
    elif isinstance(value, list):
        lists.append((path, value))
        for i, v in enumerate(value):
            _collect(v, (*path, i), lists, strings, keys)
    elif isinstance(value, str):
        strings.append(path)


def _resolve(root: Any, path: Path) -> tuple[Any, Any]:
    container = root
    for part in path[:-1]:
        container = container[part]
    return container, path[-1]


def _scan(data: Any) -> tuple[list, list, list]:
    lists: list = []
    strings: list = []
    keys: list = []
    _collect(data, (), lists, strings, keys)
    return lists, strings, keys


def shape_result(
    value: Any,
    *,
    response_format: ResponseFormat,
    budget: int,
    counter: TokenCounter,
    validator: Validator | None = None,
    strict: bool | None = None,
    min_items: int = 1,
    page: PageSpec | None = None,
    protect: Collection[str] = (),
    cursor_hint: str | None = None,
    assume_valid: bool = False,
) -> ShapedResult:
    """Shape ``value`` so its serialized text fits in ``budget`` tokens.

    Args:
        validator: Validator for the advertised output schema. Every change is
            checked against it and reverted if it would make the result invalid.
        strict: Raise ``ResultTooLarge`` instead of cutting the text when the
            smallest valid result does not fit. Defaults to ``validator is not None``.
        min_items: Never trim a non-empty list below this many items (RES-10).
        page: Framework-issued pagination to keep consistent while trimming.
        protect: Top-level keys that are never shortened or dropped.
        cursor_hint: Name of a handler-managed cursor field, used to warn that it
            skips trimmed items when ``page`` is not available.
        assume_valid: Treat the untouched result as valid instead of checking it
            first. Set it only when ``value`` is a model instance Pydantic already
            validated. Each change is still checked.
    """
    if min_items < 1:
        raise ValueError("min_items must be >= 1")
    strict = validator is not None if strict is None else strict
    data = to_jsonable(value, response_format)
    text = compact_json(data)
    tokens = counter.count(text)
    truncation = Truncation()
    if tokens <= budget:
        return ShapedResult(data, text, tokens, budget, response_format, truncation)

    target = max(budget - NOTE_RESERVE, 1)
    # Checking the whole result is the slowest step. Callers that already validated
    # the value against the same model (the server does) can skip it.
    schema = (
        validator if validator is not None and (assume_valid or validator.is_valid(data)) else None
    )
    cursor_keys = {k for k in (cursor_hint, page.cursor_key if page else None) if k}
    no_shorten = set(protect) | cursor_keys
    no_drop = no_shorten | ({page.list_key} if page else set())

    def size() -> int:
        return counter.count(compact_json(data))

    def valid() -> bool:
        return schema is None or schema.is_valid(data)

    def under(path: Path, roots: set[str]) -> bool:
        return bool(path) and isinstance(path[0], str) and path[0] in roots

    # 1. Trim lists, largest first. Binary search the number of items to keep.
    visited: set[Path] = set()
    while size() > target:
        lists, _, _ = _scan(data)
        candidates = [(p, lst) for p, lst in lists if len(lst) > min_items and p not in visited]
        if not candidates:
            break
        path, lst = max(candidates, key=lambda item: len(compact_json(item[1])))
        visited.add(path)
        original = list(lst)
        pg = (
            page
            if page is not None and path == (page.list_key,) and isinstance(data, dict)
            else None
        )
        original_cursor = data.get(pg.cursor_key, ...) if pg is not None else ...

        def apply(
            k: int,
            lst: list = lst,
            original: list = original,
            pg: PageSpec | None = pg,
            original_cursor: Any = original_cursor,
        ) -> None:
            lst[:] = original[:k]
            if pg is None:
                return
            if k < min(pg.count, len(original)):
                data[pg.cursor_key] = pg.encode(pg.start + k)
            elif original_cursor is ...:
                data.pop(pg.cursor_key, None)
            else:
                data[pg.cursor_key] = original_cursor

        lo, hi = min_items, len(original)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            apply(mid)
            if size() <= target:
                lo = mid
            else:
                hi = mid - 1
        apply(lo)
        while lo < len(original) and not valid():  # e.g. minItems in the schema
            lo += 1
            apply(lo)
        if lo < len(original):
            truncation.lists[format_path(path)] = (lo, len(original))
            if pg is not None and lo < pg.count:
                truncation.cursor_rewritten = True

    # 2. Shorten the longest strings, unless that breaks the schema (patterns, enums).
    tried: set[Path] = set()
    while size() > target:
        _, strings, _ = _scan(data)
        text_candidates: list[tuple[Path, Any, Any]] = []
        for p in strings:
            if p in tried or not p or under(p, no_shorten):
                continue
            parent, key = _resolve(data, p)
            if len(parent[key]) >= MIN_SHORTEN_CHARS:
                text_candidates.append((p, parent, key))
        if not text_candidates:
            break
        path, parent, key = max(text_candidates, key=lambda c: len(c[1][c[2]]))
        tried.add(path)
        original_text = parent[key]
        lo, hi = 0, len(original_text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            parent[key] = original_text[:mid] + _ELLIPSIS
            if size() <= target:
                lo = mid
            else:
                hi = mid - 1
        parent[key] = original_text[:lo] + _ELLIPSIS
        if valid():
            truncation.strings.append(format_path(path))
        else:
            parent[key] = original_text

    # 3. Drop optional fields, the same field across all items, largest first.
    if schema is not None:
        dropped: set[str] = set()
        while size() > target:
            _, _, keys = _scan(data)
            groups: dict[str, list[tuple[dict, str]]] = {}
            for parent, key, path in keys:
                if under(path, set(protect)) or (len(path) == 1 and path[0] in no_drop):
                    continue
                pattern = format_path(path, pattern=True)
                if pattern not in dropped:
                    groups.setdefault(pattern, []).append((parent, key))
            if not groups:
                break
            pattern, members = max(
                groups.items(),
                key=lambda g: sum(len(compact_json(p[k])) for p, k in g[1] if k in p),
            )
            dropped.add(pattern)
            saved = [(p, k, p[k]) for p, k in members if k in p]
            for p, k, _ in saved:
                del p[k]
            if valid():
                truncation.dropped.append(pattern)
            else:
                for p, k, v in saved:
                    p[k] = v

    text = compact_json(data)
    current = counter.count(text)
    if current > target:
        if strict:
            raise ResultTooLarge(current + NOTE_RESERVE, budget, truncation)
        # Last resort, only without an advertised schema: cut the serialized text.
        text = counter.truncate_text(text, target)
        truncation.text_only = True
        data = None

    if truncation.lists and isinstance(data, dict) and page is None:
        for key in (cursor_hint,) if cursor_hint else CURSOR_FIELDS:
            if data.get(key) is not None:
                truncation.cursor_field = key
                break

    note = truncation.note(budget)
    full = f"{text}\n{note}"
    if counter.count(full) > budget:  # pragma: no cover - NOTE_RESERVE is generous
        full = counter.truncate_text(full, budget)
    return ShapedResult(data, full, counter.count(full), budget, response_format, truncation)
