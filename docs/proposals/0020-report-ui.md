# 0020: Report UI built with shadcn/ui

Status: proposed for M3. Applies to the trace report (proposal 0018) and the eval report (proposal 0019).

## Problem

The first trace and eval reports were hand-written HTML tables. They were hard to scan, they had no search, and each report had its own markup and styling.

## Proposal

Both reports are one small React app styled with [shadcn/ui](https://ui.shadcn.com) components: Card, Table, Tabs, Badge, Progress, Select, Input and Button. The component sources are copied from shadcn/ui tag `shadcn@4.23.0` (new-york style). The app lives in `ui/report`. `ui/report/build.sh` builds it with Vite and Tailwind CSS into one HTML file, `ouncemcp/ui/static/report.html`, with the scripts and styles inlined. Every version is pinned in `package-lock.json`.

`ouncemcp.ui.render_report(data)` copies that file and writes the report data into a JSON element. Python needs no Node at run time.

**Trace report** (`ouncemcp trace calls.jsonl`):

- Summary cards for calls, errors, truncated results and result tokens.
- **By tool table:** errors by category, truncations, each tool's share of result tokens, average and maximum tokens, and p50 and p95 latency. Selecting a tool opens its calls.
- **Calls table:**
    - Search by tool or error, and filters for tool, outcome and truncated, retried or replayed calls.
    - Each call shows its result tokens against its budget, and its row expands to show attempts, the error, and what shaping removed.
    - Pages of 50 calls. A page lists up to the last 5,000 calls; the summary covers every call.

**Eval report** (`ouncemcp eval suite.yaml --html report.html`):

- A pass or fail badge, and any regressions against the `--compare` baseline.
- Summary cards for success, tool selection, tokens per task and the train minus held-out gap.
- **Results tab:** one row per split.
- **Tasks tab:** each task's success, calls, turns, tokens and failed checks.
- **Environment tab:** what produced the report (EVAL-5).

Light, dark and system themes. The tables scroll inside their cards on a phone, so the page itself never scrolls sideways.

## Security

- Report values are JSON data that React renders as text. The JSON escapes `<`, `>` and `&`.
- The page's Content-Security-Policy is `default-src 'none'`, and it allows only the page's own script, by hash.
- So the page loads nothing from the network, even when a trace contains crafted tool names. Tests check this.

## Alternatives considered

- **A2UI components.** We tried these first. The A2UI basic catalog has no table, badge or select, so dense trace data was hard to read.
- **Loading React from a CDN.** That would give smaller files, but the report would no longer open offline.

## Impact on tokens and task success

None.

## Migration notes

`render_html` keeps its signature. The page is about 430 KB and needs JavaScript; it shows a notice when the data is missing.
