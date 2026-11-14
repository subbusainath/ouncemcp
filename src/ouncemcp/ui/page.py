"""One-file HTML reports built with React and shadcn/ui (proposal 0020).

``static/report.html`` is the report app, built from ``ui/report`` in the repository
(``ui/report/build.sh``) with its scripts and styles inlined. ``render_report`` copies
it, puts the report data in its ``report-data`` element and adds a
Content-Security-Policy that allows only the page's own script, by hash, and blocks
every network request. The page opens offline and can be attached to an issue.

Report values reach the page only as JSON data that React renders as text, never as
HTML.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any

KINDS = ("trace", "eval", "context", "audit")
_PLACEHOLDER = '<script type="application/json" id="report-data">{}</script>'
_SCRIPT = re.compile(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", re.DOTALL)


@cache
def _template() -> str:
    page = (
        resources.files("ouncemcp.ui").joinpath("static", "report.html").read_text(encoding="utf-8")
    )
    if page.count(_PLACEHOLDER) != 1:
        raise RuntimeError("ouncemcp/ui/static/report.html has no report-data element; rebuild it")
    return page


def _hash(script: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"


def _json(value: Any) -> str:
    """JSON that is safe inside a <script> element."""
    text = json.dumps(value, separators=(",", ":"), default=str)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


@cache
def _csp() -> str:
    scripts = " ".join(_hash(s) for s in _SCRIPT.findall(_template()))
    return (
        "default-src 'none'; "
        f"script-src {scripts}; "
        "style-src 'unsafe-inline'; img-src data:; font-src data:; "
        "base-uri 'none'; form-action 'none'"
    )


def render_report(data: Mapping[str, Any]) -> str:
    """The report page with ``data``: ``{"kind": ..., "title": ..., ...}``."""
    if data.get("kind") not in KINDS:
        raise ValueError(f"report data needs kind {', '.join(repr(k) for k in KINDS)}")
    page = _template().replace(
        _PLACEHOLDER,
        f'<script type="application/json" id="report-data">{_json(dict(data))}</script>',
    )
    meta = f'<meta http-equiv="Content-Security-Policy" content="{_csp()}" />'
    return page.replace('<meta charset="UTF-8" />', f'<meta charset="UTF-8" />\n    {meta}', 1)
