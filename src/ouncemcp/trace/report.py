"""Trace reports for ``ouncemcp trace`` (spec DX-5, proposal 0018).

Reads traces written by ``JsonlSink`` and renders them as a terminal summary or as one
self-contained HTML file: summary figures, a per-tool table (calls, errors by
category, truncations, result tokens, latency) and every call with its outcome,
tokens against budget, attempts, truncation decisions and error code. The HTML has no
external resources, so it opens offline and can be attached to an issue.
"""

from __future__ import annotations

import dataclasses
import html
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ouncemcp.trace import CallTrace
from ouncemcp.trace.metrics import MetricsSink

_FIELDS = {f.name for f in dataclasses.fields(CallTrace)}


def load_traces(path: str | Path) -> list[CallTrace]:
    """Read a JSONL trace file. Lines that are not traces are skipped."""
    traces: list[CallTrace] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            traces.append(CallTrace(**{k: v for k, v in data.items() if k in _FIELDS}))
        except (json.JSONDecodeError, TypeError):
            continue
    return traces


def summarize(traces: Iterable[CallTrace]) -> dict[str, Any]:
    sink = MetricsSink()
    items = list(traces)
    for trace in items:
        sink.emit(trace)
    errors = sum(1 for t in items if t.outcome == "error")
    return {
        "calls": len(items),
        "errors": errors,
        "truncated": sum(1 for t in items if t.truncated),
        "result_tokens": sum(t.result_tokens for t in items),
        "tools": sink.snapshot(),
        "tokenizers": sorted({t.tokenizer for t in items if t.tokenizer}),
    }


def render_text(traces: Sequence[CallTrace]) -> str:
    s = summarize(traces)
    tokenizers = ", ".join(s["tokenizers"]) or "no tokenizer"
    headline = (
        f"{s['calls']} calls, {s['errors']} errors, {s['truncated']} truncated, "
        f"{s['result_tokens']:,} result tokens ({tokenizers})"
    )
    header = (
        f"{'Tool':<32} {'Calls':>6} {'Errors':>7} {'Trunc':>6} {'Tokens avg':>11} "
        f"{'p50 ms':>8} {'p95 ms':>8}"
    )
    lines = [headline, "", header]
    for tool, m in s["tools"].items():
        lines.append(
            f"{tool[:32]:<32} {m['calls']:>6} {sum(m['errors'].values()):>7} "
            f"{m['truncated']:>6} {m['result_tokens_avg']:>11} {m['latency_ms_p50']:>8.1f} "
            f"{m['latency_ms_p95']:>8.1f}"
        )
    return "\n".join(lines)


_STYLE = """
:root { --bg:#ffffff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --card:#f6f8fa;
  --ok:#1a7f37; --err:#cf222e; --warn:#9a6700; --accent:#0969da; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1;
  --line:#30363d; --card:#151b23; --ok:#3fb950; --err:#f85149; --warn:#d29922; --accent:#4493f8; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width:1200px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:32px 0 8px; }
.muted { color:var(--muted); }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; margin-top:16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; }
.card b { display:block; font-size:22px; font-variant-numeric:tabular-nums; }
.scroll { overflow-x:auto; border:1px solid var(--line); border-radius:8px; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); white-space:nowrap; vertical-align:top; }
th { background:var(--card); font-weight:600; position:sticky; top:0; }
td.num, th.num { text-align:right; }
tr:last-child td { border-bottom:none; }
.ok { color:var(--ok); } .error { color:var(--err); } .warn { color:var(--warn); }
.controls { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0; }
select, input { font:inherit; color:var(--fg); background:var(--bg); border:1px solid var(--line);
  border-radius:6px; padding:4px 8px; }
details summary { cursor:pointer; color:var(--accent); }
code { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }
"""

_SCRIPT = """
const data = JSON.parse(document.getElementById('trace-data').textContent);
const body = document.getElementById('calls');
const toolSel = document.getElementById('tool'), outSel = document.getElementById('outcome');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
for (const t of [...new Set(data.map(d => d.tool))].sort()) toolSel.add(new Option(t, t));
function render() {
  const tool = toolSel.value, outcome = outSel.value;
  const rows = data.filter(d => (!tool || d.tool === tool) && (!outcome || d.outcome === outcome));
  body.innerHTML = rows.map(d => {
    const when = new Date(d.started_at * 1000).toISOString().replace('T', ' ').slice(0, 23);
    const budget = d.budget ? ` / ${d.budget}` : '';
    const notes = [];
    if (d.truncated) notes.push('<span class="warn">truncated</span>');
    if (d.replayed) notes.push('replayed');
    if (d.resolved_by) notes.push('resolved by ' + esc(d.resolved_by));
    if (d.parent) notes.push('via ' + esc(d.parent));
    const detail = d.truncation ? `<details><summary>what was removed</summary><code>${esc(JSON.stringify(d.truncation))}</code></details>` : '';
    const err = d.error_code ? `${esc(d.error_category)}: ${esc(d.error_code)}` : '';
    return `<tr><td>${when}</td><td>${esc(d.tool)}</td><td class="${d.outcome === 'error' ? 'error' : 'ok'}">${esc(d.outcome)}</td>
      <td class="num">${d.result_tokens}${budget}</td><td class="num">${d.duration_ms.toFixed(1)}</td>
      <td class="num">${d.attempts}</td><td>${err}</td><td>${notes.join(', ')}${detail}</td></tr>`;
  }).join('');
  document.getElementById('shown').textContent = `${rows.length} of ${data.length} calls`;
}
toolSel.onchange = outSel.onchange = render; render();
"""


def render_html(traces: Sequence[CallTrace], *, title: str = "OunceMCP trace") -> str:
    s = summarize(traces)
    tool_rows = "".join(
        "<tr><td>{}</td><td class='num'>{}</td><td>{}</td><td class='num'>{}</td>"
        "<td class='num'>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
        "<td class='num'>{}</td></tr>".format(
            html.escape(tool),
            m["calls"],
            html.escape(", ".join(f"{k} {v}" for k, v in sorted(m["errors"].items())) or "none"),
            m["truncated"],
            m["result_tokens_avg"],
            m["result_tokens_max"],
            f"{m['latency_ms_p50']:.1f}",
            f"{m['latency_ms_p95']:.1f}",
        )
        for tool, m in s["tools"].items()
    )
    payload = json.dumps([t.to_dict() for t in traces], default=str).replace("</", "<\\/")
    cards = [
        ("Calls", f"{s['calls']:,}"),
        ("Errors", f"{s['errors']:,}"),
        ("Truncated", f"{s['truncated']:,}"),
        ("Result tokens", f"{s['result_tokens']:,}"),
    ]
    card_html = "".join(
        f"<div class='card'><span class='muted'>{k}</span><b>{v}</b></div>" for k, v in cards
    )
    tokenizers = html.escape(", ".join(s["tokenizers"]) or "unknown")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{_STYLE}</style></head>
<body><main>
<h1>{html.escape(title)}</h1>
<p class="muted">Result tokens counted with {tokenizers}. Principals are digests; arguments,
results and error messages are not recorded unless the tracer was set to record arguments.</p>
<div class="cards">{card_html}</div>
<h2>By tool</h2>
<div class="scroll"><table><thead><tr><th>Tool</th><th class="num">Calls</th><th>Errors</th>
<th class="num">Truncated</th><th class="num">Tokens avg</th><th class="num">Tokens max</th>
<th class="num">p50 ms</th><th class="num">p95 ms</th></tr></thead><tbody>{tool_rows}</tbody></table></div>
<h2>Calls</h2>
<div class="controls"><label>Tool <select id="tool"><option value="">all</option></select></label>
<label>Outcome <select id="outcome"><option value="">all</option><option>ok</option>
<option>error</option><option>input_required</option></select></label>
<span class="muted" id="shown"></span></div>
<div class="scroll"><table><thead><tr><th>Started (UTC)</th><th>Tool</th><th>Outcome</th>
<th class="num">Tokens / budget</th><th class="num">ms</th><th class="num">Attempts</th>
<th>Error</th><th>Notes</th></tr></thead><tbody id="calls"></tbody></table></div>
</main>
<script type="application/json" id="trace-data">{payload}</script>
<script>{_SCRIPT}</script>
</body></html>
"""
