"""A2UI surfaces for OunceMCP reports (proposal 0020).

Reports are described as A2UI v0.9 messages (https://a2ui.org): a ``createSurface``,
an ``updateDataModel`` with the report's data, and an ``updateComponents`` with a tree
of components from the A2UI basic catalog (``Column``, ``Row``, ``Card``, ``List``,
``Tabs``, ``Text``, ``Button``, ``Divider``). The same messages can be:

- written as JSON lines (``to_jsonl``) for any host or agent that renders A2UI, or
- rendered into one HTML file (``render_page``) by the A2UI Lit renderer, which is
  bundled with OunceMCP (``static/a2ui-render.js``, built from ``ui/a2ui`` in the
  repository), so the page opens offline and its Content-Security-Policy blocks all
  network requests.

Report values reach the page only as data-model values shown by ``Text`` components,
which render them as text, never as HTML.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from importlib import resources
from typing import Any

VERSION = "v0.9"
CATALOG_ID = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
RENDERER = "@a2ui/lit 0.15.0"


def bind(path: str) -> dict[str, str]:
    """A data binding: absolute (``/calls``) or, inside a list template, relative (``tool``)."""
    return {"path": path}


class Surface:
    """Builds the messages for one A2UI surface from basic-catalog components."""

    def __init__(self, surface_id: str, *, theme: Mapping[str, Any] | None = None) -> None:
        self.id = surface_id
        self.theme = dict(theme or {})
        self.components: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {}

    def add(self, component_id: str, component: str, **props: Any) -> str:
        self.components.append(
            {
                "id": component_id,
                "component": component,
                **{k: v for k, v in props.items() if v is not None},
            }
        )
        return component_id

    def text(
        self,
        component_id: str,
        text: Any,
        variant: str | None = None,
        *,
        weight: float | None = None,
    ) -> str:
        return self.add(component_id, "Text", text=text, variant=variant, weight=weight)

    def column(
        self,
        component_id: str,
        children: Any,
        *,
        align: str | None = "stretch",
        weight: float | None = None,
    ) -> str:
        return self.add(component_id, "Column", children=children, align=align, weight=weight)

    def row(
        self,
        component_id: str,
        children: Any,
        *,
        align: str | None = "center",
        justify: str | None = None,
    ) -> str:
        return self.add(component_id, "Row", children=children, align=align, justify=justify)

    def card(self, component_id: str, child: str, *, weight: float | None = None) -> str:
        return self.add(component_id, "Card", child=child, weight=weight)

    def repeat(self, component_id: str, template: str, path: str) -> str:
        """A List whose children repeat ``template`` once per item at ``path``."""
        return self.add(component_id, "List", children={"componentId": template, "path": path})

    def tabs(self, component_id: str, tabs: Sequence[tuple[str, str]]) -> str:
        return self.add(component_id, "Tabs", tabs=[{"title": t, "child": c} for t, c in tabs])

    def divider(self, component_id: str) -> str:
        return self.add(component_id, "Divider")

    def button(
        self,
        component_id: str,
        label: str,
        event: str,
        context: Mapping[str, Any] | None = None,
        *,
        variant: str | None = None,
    ) -> str:
        label_id = self.text(f"{component_id}_label", label)
        action: dict[str, Any] = {"event": {"name": event}}
        if context:
            action["event"]["context"] = dict(context)
        return self.add(component_id, "Button", child=label_id, action=action, variant=variant)

    def messages(self) -> list[dict[str, Any]]:
        create: dict[str, Any] = {"surfaceId": self.id, "catalogId": CATALOG_ID}
        if self.theme:
            create["theme"] = self.theme
        return [
            {"version": VERSION, "createSurface": create},
            {
                "version": VERSION,
                "updateDataModel": {"surfaceId": self.id, "path": "/", "value": self.data},
            },
            {
                "version": VERSION,
                "updateComponents": {"surfaceId": self.id, "components": self.components},
            },
        ]


def to_jsonl(messages: Iterable[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(m, separators=(",", ":"), default=str) + "\n" for m in messages)


@cache
def _static(name: str) -> str:
    return resources.files("ouncemcp.ui").joinpath("static", name).read_text(encoding="utf-8")


def _script_json(value: Any) -> str:
    """JSON safe inside a <script> element."""
    text = json.dumps(value, separators=(",", ":"), default=str)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _hash(script: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"


_STYLE = """
:root { color-scheme: light dark; --a2ui-color-primary: #0969da; --a2ui-list-padding: 0; }
body { margin: 0; background: light-dark(#ffffff, #0d1117); color: light-dark(#1f2328, #e6edf3);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 16px 16px 48px; }
a2ui-surface { display: block; }
a2ui-row, .a2ui-list-horizontal { flex-wrap: wrap; row-gap: 8px; }
noscript pre { white-space: pre-wrap; }
"""

# Handles the page's own button events: "show" replaces a data-model value with one of
# the page's precomputed views (for example the calls of one tool).
_ACTIONS = """
const views = JSON.parse(document.getElementById('views').textContent);
const messages = JSON.parse(document.getElementById('a2ui-messages').textContent);
OunceA2UI.render(messages, document.getElementById('app'), (action, processor) => {
  if (action.name !== 'show') return;
  const view = views[action.context.view];
  if (!view) return;
  processor.processMessages(Object.entries(view).map(([path, value]) => (
    {version: 'v0.9', updateDataModel: {surfaceId: action.surfaceId, path, value}})));
});
"""


def render_page(
    title: str,
    messages: Sequence[Mapping[str, Any]],
    *,
    fallback: str = "",
    views: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """One self-contained HTML page that renders ``messages`` with the bundled A2UI renderer.

    ``views`` maps a view name to data-model updates (path to value) applied when a
    button with ``event: show`` and ``context: {view: name}`` is pressed. ``fallback``
    is shown when scripts are disabled.
    """
    renderer = _static("a2ui-render.js")
    csp = (
        "default-src 'none'; "
        f"script-src {_hash(renderer)} {_hash(_ACTIONS)}; "
        "style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="OunceMCP with A2UI ({RENDERER})">
<title>{html.escape(title)}</title><style>{_STYLE}</style></head>
<body><main id="app"><noscript><pre>{html.escape(fallback)}</pre></noscript></main>
<script type="application/json" id="a2ui-messages">{_script_json(list(messages))}</script>
<script type="application/json" id="views">{_script_json(dict(views or {}))}</script>
<script>{renderer}</script>
<script>{_ACTIONS}</script>
</body></html>
"""


@cache
def _catalog() -> dict[str, Any]:
    return json.loads(_static("schemas/basic_catalog.json"))  # type: ignore[no-any-return]


def _required(definition: Mapping[str, Any]) -> set[str]:
    required: set[str] = set(definition.get("required", ()))
    for part in definition.get("allOf", ()):
        required |= set(part.get("required", ()))
    return required


def _child_ids(component: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    children = component.get("children")
    if isinstance(children, list):
        out += children
    elif isinstance(children, Mapping):
        out.append(children["componentId"])
    for key in ("child", "trigger", "content"):
        if isinstance(component.get(key), str):
            out.append(component[key])
    for tab in component.get("tabs", ()):
        out.append(tab["child"])
    return out


def validate(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """Problems that would stop a renderer: unknown components, missing required
    properties, duplicate ids, references to missing components, no ``root``."""
    catalog = _catalog()["components"]
    problems: list[str] = []
    components: dict[str, Mapping[str, Any]] = {}
    for message in messages:
        if message.get("version") != VERSION:
            problems.append(f"message without version {VERSION}")
        update = message.get("updateComponents")
        if not update:
            continue
        for component in update["components"]:
            cid, kind = component.get("id"), component.get("component")
            if cid in components:
                problems.append(f"duplicate id {cid}")
            components[str(cid)] = component
            if kind not in catalog:
                problems.append(f"{cid}: {kind} is not in the basic catalog")
                continue
            missing = _required(catalog[kind]) - set(component)
            if missing:
                problems.append(f"{cid}: {kind} needs {', '.join(sorted(missing))}")
    if components and "root" not in components:
        problems.append("no component with id root")
    for cid, component in components.items():
        for child in _child_ids(component):
            if child not in components:
                problems.append(f"{cid}: refers to missing component {child}")
    return problems
