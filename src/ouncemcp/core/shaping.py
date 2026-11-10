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

import json
import types
import typing
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from ouncemcp.core.tokens import TokenCounter, compact_json

ResponseFormat = Literal["concise", "detailed"]

# The full truncation note is used while it costs at most this share of the
# budget; otherwise a short note is used. The note's real size is reserved.
NOTE_SHARE = 0.1
# Passes over the data when the first note estimate turns out too small.
_MAX_PASSES = 3
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


@cache
def _field_plan(model: type[BaseModel]) -> tuple[tuple[str, bool, bool], ...]:
    """(name, is_detail, is_required) per field. Model fields are fixed after class creation."""
    return tuple(
        (name, is_detail_field(info), info.is_required())
        for name, info in model.model_fields.items()
    )


def to_jsonable(value: Any, response_format: ResponseFormat) -> Any:
    """Convert a result to JSON-compatible data, applying the response format.

    In ``concise`` format, detail fields are dropped. In both formats, optional
    fields that are ``None`` are omitted: advertised schemas are compacted
    (spec BUD-8) and do not accept ``null`` for optional fields (spec RES-8).
    """
    concise = response_format == "concise"
    if isinstance(value, BaseModel):
        out: dict[str, Any] = {}
        for name, detail, required in _field_plan(type(value)):
            if concise and detail:
                continue
            item = getattr(value, name)
            if item is None and not required:
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

    def note(self, budget: int, *, short: bool = False) -> str:
        """Tell the agent what was removed and what to do next.

        The short form is used when the full note would take a large share of a
        small budget.
        """
        parts: list[str] = []
        items = list(self.lists.items())
        if short:
            # The first change, then a count of the rest.
            changes = [f"kept {k} of {t} in '{p}'" for p, (k, t) in items]
            changes += [f"omitted '{p}'" for p in self.dropped]
            changes += [f"shortened '{p}'" for p in self.strings]
            if changes:
                parts.append(changes[0])
            if len(changes) > 1:
                parts.append(f"{len(changes) - 1} more changes")
        else:
            for path, (kept, total) in items[:3]:
                parts.append(f"kept {kept} of {total} items in '{path}'")
            if len(items) > 3:
                parts.append(f"trimmed {len(items) - 3} more lists")
            for label, paths in (("shortened text in", self.strings), ("omitted", self.dropped)):
                if paths:
                    shown = ", ".join(f"'{p}'" for p in paths[:3])
                    extra = f" and {len(paths) - 3} more" if len(paths) > 3 else ""
                    parts.append(f"{label} {shown}{extra}")
        if self.text_only:
            parts.append("cut the serialized result")
        advice = self._advice(short)
        if short:
            return f"[ouncemcp] Truncated: {'; '.join(parts)}. {advice}"
        return f"[ouncemcp] Result truncated to fit {budget} tokens: {'; '.join(parts)}. {advice}"

    def _advice(self, short: bool) -> str:
        if self.cursor_rewritten:
            return (
                "Use next_cursor for more."
                if short
                else ("Call again with next_cursor to get the rest.")
            )
        if self.cursor_field and self.lists:
            kept = max(min(k for k, _ in self.lists.values()), 1)
            if short:
                return f"Use limit={kept} to page without gaps."
            return (
                f"'{self.cursor_field}' skips the removed items; repeat this call with "
                f"limit={kept} to page without gaps."
            )
        if self.dropped:
            return (
                "Ask for fewer items for all fields."
                if short
                else ("Ask for fewer items to get every field.")
            )
        return (
            "Narrow the request for more."
            if short
            else ("Narrow the request or ask for the next page to see more.")
        )

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
    tokenizer: str = ""

    def meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "tokenizer": self.tokenizer,
            "result_tokens": self.tokens,
            "budget": self.budget,
            "format": self.response_format,
            "truncated": bool(self.truncation),
        }
        if self.truncation:
            meta["truncation"] = self.truncation.to_dict()
        return meta


class ResultTooLarge(Exception):
    """The smallest schema-valid result is still over the budget (spec RES-8).

    Attributes:
        min_tokens: Size of the smallest valid result, including its note.
        budget: The result budget for the call.
        truncation: What shaping removed before giving up.
        items: Items left in the largest list, or ``None`` when no list is left
            to trim. A lower ``limit`` can only help when this is above 1.
    """

    def __init__(
        self, min_tokens: int, budget: int, truncation: Truncation, items: int | None = None
    ) -> None:
        self.min_tokens = min_tokens
        self.budget = budget
        self.truncation = truncation
        self.items = items
        super().__init__(f"smallest valid result is {min_tokens} tokens; budget is {budget}")

    @property
    def reason(self) -> str:
        """``item_over_budget``, ``min_items_over_budget`` or ``fixed_over_budget``."""
        if self.items is None:
            return "fixed_over_budget"
        return "item_over_budget" if self.items <= 1 else "min_items_over_budget"


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


def _collect_lists(value: Any, path: Path, out: list, open_paths: set[Path], floor: int) -> None:
    """Collect lists, looking inside a list only once it is trimmed or too short to trim.

    A list nested in another list is smaller than its container, so it can never
    be the largest candidate before its container is trimmed. Skipping untrimmed
    lists keeps each scan proportional to what is left, not to the whole result.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            _collect_lists(v, (*path, k), out, open_paths, floor)
    elif isinstance(value, list):
        out.append((path, value))
        if path in open_paths or len(value) <= floor:
            for i, v in enumerate(value):
                _collect_lists(v, (*path, i), out, open_paths, floor)


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
    """Shape ``value`` so its serialized text, plus any truncation note, fits in
    ``budget`` tokens.

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
    over = _over_budget_lower_bound(text, budget, counter)
    if over is None:
        tokens = counter.count(text)
        if tokens <= budget:
            return ShapedResult(
                data, text, tokens, budget, response_format, Truncation(), counter.name
            )
        over = tokens

    schema = (
        validator if validator is not None and (assume_valid or validator.is_valid(data)) else None
    )
    fitter = _Fitter(
        counter=counter,
        schema=schema,
        min_items=min_items,
        page=page,
        protect=set(protect),
        cursor_hint=cursor_hint,
        # A lower bound is enough here: sizes are only compared with targets under budget.
        sizes={text: over},
    )
    # Reserve the note's real size. Start from the note a trimmed page usually gets,
    # and refit from the original data if the actual note is larger.
    reserve = _note_tokens(_likely_note(data, page, cursor_hint), budget, counter)
    for attempt in range(_MAX_PASSES):
        work = data if attempt == 0 else json.loads(text)
        target = max(budget - reserve, 1)
        shaped, truncation = fitter.fit(work, target)
        body = compact_json(shaped)
        current = fitter.size_of(body)
        note = _choose_note(truncation, budget, counter)
        full = f"{body}\n{note}"
        total = counter.count(full)
        if current > target:
            if strict:
                raise ResultTooLarge(total, budget, truncation, _largest_list_len(shaped))
            # Last resort, only without an advertised schema: cut the serialized text.
            truncation.text_only = True
            note = _choose_note(truncation, budget, counter)
            cut = counter.truncate_text(body, max(budget - counter.count(note) - 1, 0))
            full = f"{cut}\n{note}"
            return ShapedResult(
                None, full, counter.count(full), budget, response_format, truncation, counter.name
            )
        if not truncation:  # nothing needed trimming after all (the bound was loose)
            exact = counter.count(body)
            if exact <= budget:
                return ShapedResult(
                    shaped, body, exact, budget, response_format, truncation, counter.name
                )
        if total <= budget:
            return ShapedResult(
                shaped, full, total, budget, response_format, truncation, counter.name
            )
        reserve += total - budget
    # The note grew on every pass (rare). Keep the JSON whole and shorten the note.
    if strict:
        raise ResultTooLarge(total, budget, truncation, _largest_list_len(shaped))
    full = counter.truncate_text(full, budget)
    return ShapedResult(
        shaped, full, counter.count(full), budget, response_format, truncation, counter.name
    )


# Results longer than this many characters per budget token are checked with a
# prefix first. JSON averages 3 to 4 characters per token.
_PREFIX_CHARS_PER_TOKEN = 6


def _over_budget_lower_bound(text: str, budget: int, counter: TokenCounter) -> int | None:
    """Return a token count above ``budget`` that ``text`` is known to reach, or None.

    Counting a large result in full costs time in proportion to its size, only to
    learn that it is over budget. Counting a prefix that ends before a comma is
    enough: tokens of a prefix can exceed the same span of the full text by at
    most a token or two at the cut, which the margin covers.
    """
    limit = budget * _PREFIX_CHARS_PER_TOKEN
    if len(text) <= limit:
        return None
    cut = text.rfind(",", 0, limit)
    if cut <= 0:
        return None
    n = counter.count(text[:cut])
    return n - 4 if n - 4 > budget else None


def _likely_note(data: Any, page: PageSpec | None, cursor_hint: str | None) -> Truncation:
    """The note a trimmed result will most likely get, used to reserve its size.

    It assumes the largest top-level list is trimmed. Kept counts are written with
    as many digits as the full length, so the guess is rarely too small.
    """
    path, total = "items", 100
    if isinstance(data, dict):
        lists = [(k, len(v)) for k, v in data.items() if isinstance(v, list)]
        if page is not None:
            lists = [(page.list_key, len(data.get(page.list_key) or []))]
        if lists:
            path, total = max(lists, key=lambda item: item[1])
    total = max(total, 1)
    guess = Truncation(lists={path: (total, total)})
    if page is not None:
        guess.cursor_rewritten = True
    elif isinstance(data, dict):
        for key in (cursor_hint,) if cursor_hint else CURSOR_FIELDS:
            if data.get(key) is not None:
                guess.cursor_field = key
                break
    return guess


def _note_tokens(truncation: Truncation, budget: int, counter: TokenCounter) -> int:
    return counter.count(_choose_note(truncation, budget, counter)) + 1  # +1 for the newline


def _choose_note(truncation: Truncation, budget: int, counter: TokenCounter) -> str:
    full = truncation.note(budget)
    if counter.count(full) <= budget * NOTE_SHARE:
        return full
    return truncation.note(budget, short=True)


def _largest_list_len(data: Any) -> int | None:
    lists, _, _ = _scan(data)
    non_empty = [lst for _, lst in lists if lst]
    if not non_empty:
        return None
    return len(max(non_empty, key=lambda lst: len(compact_json(lst))))


@dataclass
class _Fitter:
    """Runs the degrade steps (lists, strings, optional fields) against a target."""

    counter: TokenCounter
    schema: Validator | None
    min_items: int
    page: PageSpec | None
    protect: set[str]
    cursor_hint: str | None
    sizes: dict[str, int]  # token counts by serialized text, shared across passes

    def size_of(self, text: str) -> int:
        n = self.sizes.get(text)
        if n is None:
            n = self.sizes[text] = self.counter.count(text)
        return n

    def fit(self, data: Any, target: int) -> tuple[Any, Truncation]:
        truncation = Truncation()
        page = self.page
        cursor_keys = {k for k in (self.cursor_hint, page.cursor_key if page else None) if k}
        no_shorten = self.protect | cursor_keys
        no_drop = no_shorten | ({page.list_key} if page else set())

        def size() -> int:
            return self.size_of(compact_json(data))

        def valid() -> bool:
            return self.schema is None or self.schema.is_valid(data)

        def under(path: Path, roots: set[str]) -> bool:
            return bool(path) and isinstance(path[0], str) and path[0] in roots

        # 1. Trim lists, largest first.
        visited: set[Path] = set()
        while size() > target:
            lists: list = []
            _collect_lists(data, (), lists, visited, self.min_items)
            candidates = [
                (p, lst) for p, lst in lists if len(lst) > self.min_items and p not in visited
            ]
            if not candidates:
                break
            path, lst = (
                candidates[0]
                if len(candidates) == 1
                else max(candidates, key=lambda item: len(compact_json(item[1])))
            )
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

            def fits(k: int, apply: Callable[[int], None] = apply) -> bool:
                apply(k)
                return size() <= target

            guess = self._estimate(original, apply, size, target)
            lo = _largest_fitting(self.min_items, len(original), fits, guess)
            apply(lo)
            floor = self._trim_floor(path, pg)
            if floor is not None:  # a prefix is known to stay valid from `floor` items
                lo = min(max(lo, floor), len(original))
                apply(lo)
            else:
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
        if self.schema is not None:
            dropped: set[str] = set()
            while size() > target:
                _, _, keys = _scan(data)
                groups: dict[str, list[tuple[dict, str]]] = {}
                for parent, key, path in keys:
                    if under(path, self.protect) or (len(path) == 1 and path[0] in no_drop):
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

        if truncation.lists and isinstance(data, dict) and page is None:
            for key in (self.cursor_hint,) if self.cursor_hint else CURSOR_FIELDS:
                if data.get(key) is not None:
                    truncation.cursor_field = key
                    break
        return data, truncation

    def _trim_floor(self, path: Path, pg: PageSpec | None) -> int | None:
        if self.schema is None:
            return 0
        return list_trim_floor(
            getattr(self.schema, "schema", None), path, pg.cursor_key if pg else None
        )

    def _estimate(
        self,
        original: list,
        apply: Callable[[int], None],
        size: Callable[[], int],
        target: int,
    ) -> int:
        """Estimate how many items fit by counting items one at a time from the start.

        Only the items that could fit are encoded, instead of the whole list on every
        step of a search. Items counted alone usually take a token or so more than in
        place (commas usually merge with the closing brace), so the estimate is close
        and is then checked exactly.
        """
        apply(0)
        room = target - size()
        used = 0
        kept = 0
        for item in original:
            used += self.counter.count(compact_json(item))
            if used > room:
                break
            kept += 1
        return kept


# Keywords that a valid array, object or cursor schema may use and still stay valid
# when an array is cut to its first k items (k >= minItems). Anything else, such
# as contains, anyOf or if/then, is checked with the validator instead.
_ANNOTATIONS = {"title", "description", "default", "examples", "$comment", "deprecated"}
_ARRAY_KEYS = {"type", "items", "maxItems", "uniqueItems", "minItems"} | _ANNOTATIONS
_OBJECT_KEYS = {"type", "properties", "required", "additionalProperties", "$defs"} | _ANNOTATIONS
_CURSOR_KEYS = {"type"} | _ANNOTATIONS


def _deref(root: Any, node: Any) -> dict | None:
    for _ in range(32):
        if not isinstance(node, dict):
            return None
        ref = node.get("$ref")
        if ref is None:
            return node
        if set(node) - ({"$ref"} | _ANNOTATIONS) or not ref.startswith("#/$defs/"):
            return None
        node = root.get("$defs", {}).get(ref[len("#/$defs/") :])
    return None


def _is_type(node: dict, name: str) -> bool:
    t = node.get("type")
    return t == name or (isinstance(t, list) and name in t)


def list_trim_floor(schema: Any, path: Path, cursor_key: str | None = None) -> int | None:
    """Fewest items the list at ``path`` can be cut to and stay valid, or ``None``.

    Cutting an array to its first k items keeps a valid instance valid unless a
    keyword depends on the items removed or on the array's length. When the path
    uses only the keywords known to be safe, the answer is the array's
    ``minItems`` (0 if unset) and shaping can skip the validator. ``None`` means
    the shape is not understood and every change must be validated.

    With ``cursor_key``, the top-level cursor field must also accept any string,
    since the framework rewrites it.
    """
    if not isinstance(schema, dict):
        return None
    node = _deref(schema, schema)
    for part in path:
        if node is None:
            return None
        if isinstance(part, str):
            if set(node) - _OBJECT_KEYS or not _is_type(node, "object"):
                return None
            node = _deref(schema, node.get("properties", {}).get(part))
        else:
            if set(node) - _ARRAY_KEYS or not _is_type(node, "array"):
                return None
            node = _deref(schema, node.get("items"))
    if node is None or set(node) - _ARRAY_KEYS or not _is_type(node, "array"):
        return None
    if cursor_key is not None:
        top = _deref(schema, schema)
        if top is None or cursor_key in top.get("required", ()):
            return None
        cursor = _deref(schema, top.get("properties", {}).get(cursor_key))
        if cursor is None or set(cursor) - _CURSOR_KEYS or not _is_type(cursor, "string"):
            return None
    floor = node.get("minItems", 0)
    return floor if isinstance(floor, int) else None


def _largest_fitting(lo: int, hi: int, fits: Callable[[int], bool], guess: int) -> int:
    """Largest ``k`` in ``[lo, hi]`` with ``fits(k)``, or ``lo`` if none fits.

    ``fits`` is monotone (more items never fit better). The search starts at
    ``guess`` and moves out in growing steps, so a close estimate needs two or
    three checks instead of a binary search over the whole list.
    """
    guess = min(max(guess, lo), hi)
    if fits(guess):
        step = 1
        lo = guess
        while lo < hi:
            probe = min(lo + step, hi)
            if not fits(probe):
                hi = probe - 1
                break
            lo = probe
            step *= 2
    else:
        if guess <= lo:
            return lo
        hi = guess - 1
        if fits(hi):
            return hi
        hi -= 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo
