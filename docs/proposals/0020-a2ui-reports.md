# 0020: Reports built from A2UI components

Status: proposed for M3. Applies to the trace report (proposal 0018) and the eval report (proposal 0019).

## Problem

The trace and eval reports were hand-written HTML. Each report had its own markup and script, and agents or hosts that already render generated UI could not show them.

## Proposal

Reports are [A2UI](https://a2ui.org) v0.9 surfaces built only from the A2UI basic catalog: `Column`, `Row`, `Card`, `List`, `Tabs`, `Text`, `Button`. `ouncemcp.ui.a2ui.Surface` builds the three messages of a surface: `createSurface`, `updateDataModel` with the report's data, and `updateComponents` with the component tree. Lists of calls, tools, splits and tasks are `List` templates bound to the data model, so the size of the component tree does not grow with the data.

- **HTML.** `render_page` puts the messages in one HTML file with the A2UI Lit renderer (`@a2ui/lit` and `@a2ui/web_core` 0.15.0). The renderer is bundled with OunceMCP (`ouncemcp/ui/static/a2ui-render.js`, about 500 KB, 140 KB gzipped), so the page opens offline. `ui/a2ui/build.sh` rebuilds it from the pinned versions in `ui/a2ui/package-lock.json`.
- **Filters.** Buttons send A2UI `show` events. The page answers by sending `updateDataModel` messages with a precomputed view, such as the errors, the truncated calls or one tool's calls.
- **A2UI hosts.** `ouncemcp trace FILE --a2ui out.jsonl` and `ouncemcp eval SUITE --a2ui out.jsonl` write the messages as JSON lines for any host or agent that renders A2UI.
- **Theme.** Light and dark follow the system through A2UI's own CSS variables. Rows and button groups wrap on phone screens.
- **Checks.** `validate(messages)` reports unknown components, missing required properties, duplicate ids, missing references and a missing `root`. Tests also validate every message against the A2UI v0.9 JSON schemas shipped with the renderer.

## Security

Report values reach the page only as data-model values shown by `Text`, which renders text, never HTML. No markdown renderer is configured. The JSON in the page escapes `<`, `>` and `&`. The page's Content-Security-Policy is `default-src 'none'` and allows only its two scripts, by hash. So a page cannot load anything from the network, even if a trace contains crafted tool names.

## Alternatives considered

- **Loading the renderer from a CDN.** That would give smaller files, but the report would no longer open offline and would depend on a third party when it is viewed.
- **A2UI v0.8.** The A2UI project advises against v0.8 for new work.

## Impact on tokens and task success

None.

## Migration notes

`render_html` keeps its signature. Reports now need JavaScript; with scripts disabled they show the text summary.
