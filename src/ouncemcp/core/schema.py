"""Schema compaction: the same contract in fewer tokens.

Pydantic encodes ``x: str | None = None`` as
``{"anyOf": [{"type": "string"}, {"type": "null"}], "default": null}``. For an
optional field, omitting it already means "no value", so OunceMCP advertises
``{"type": "string"}`` and keeps the field out of ``required``. Handlers still
accept an explicit null because validation uses the Pydantic model, not the
advertised schema.
"""

from __future__ import annotations

from typing import Any

_NULL = {"type": "null"}
_MAPPING_KEYS = {"properties", "$defs", "definitions", "patternProperties"}


def compact_schema(schema: Any) -> Any:
    """Return a compacted copy of a JSON Schema; the input is not modified."""
    if isinstance(schema, list):
        return [compact_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _MAPPING_KEYS and isinstance(value, dict):
            out[key] = {name: compact_schema(sub) for name, sub in value.items()}
        elif key == "title":
            continue
        else:
            out[key] = compact_schema(value)
    variants = out.get("anyOf")
    if (
        isinstance(variants, list)
        and len(variants) == 2
        and _NULL in variants
        and out.get("default", ...) is None
    ):
        other = variants[0] if variants[1] == _NULL else variants[1]
        merged = {k: v for k, v in out.items() if k not in ("anyOf", "default")}
        for k, v in other.items():
            merged.setdefault(k, v)
        out = merged
    return out
