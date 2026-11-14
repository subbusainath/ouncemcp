# 0024: Context and audit pages

Status: proposed for M4. Covers the web part of spec DX-4 (P1) and the HTML report of AUD-10 (P2).

## Problem

`ouncemcp context` showed one definition at a time in the terminal, and `ouncemcp audit` had terminal, JSON and Markdown output only. Comparing tools, or sharing what a server costs, meant reading JSON.

## Proposal

Both commands write a page with the report app from [proposal 0020](0020-report-ui.md):

- **`ouncemcp context server.py [tool] --html context.html [--call JSON]`.**
    - Startup context against the budget, the discovery mode and why, the tokenizer and the counting scope.
    - Every tool with a bar showing where its tokens go (description, input schema, output schema, name and structure), its status and any budget override (BUD-4).
    - For the selected tool: the counted definition exactly as clients receive it in `tools/list`, and the fields that are sent but not counted (`title`, `annotations`, `_meta`), since hosts may show them to the model.
    - With `--call`: the result's content as the model reads it, its token count, the structured content and `_meta.ouncemcp`.
    - The tool name is optional with `--html`; when given, the page opens on it.
- **`ouncemcp audit TARGET --html audit.html`:** startup context, tools with the same token bars, findings, the discovery recommendation, `tools/list` latency, sampled result sizes, resources and prompts, and any regressions from `--compare`.

`ouncemcp.ui.views` builds the data (`context_data`, `audit_data`, `call_result`).

## Alternatives considered

- **A live web server for `context`.** A page is enough for one server, can be attached to a review, and needs no port. `ouncemcp dev` can later serve the same page and refresh it on reload.

## Impact on tokens and task success

None.

## Migration notes

None. The terminal output of both commands is unchanged.
