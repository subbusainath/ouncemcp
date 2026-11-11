# 0012: Safe writes: preview, approve, commit

Status: proposed for M2 (step 2 of [proposal 0010](0010-m2-plan.md)). Covers spec section 10 (approval receipts bind the exact action, expire, are single-use), SEC-6 and SEC-7 for approvals, and release gates 3 and 4. Builds on [proposal 0011](0011-store-and-idempotency.md).

## Problem

A write tool had no way to ask a person before it acted, and nothing stopped a model from simply calling it. Asking inside the handler was possible with `ctx.require_input`, but every author had to get the receipt, the binding and the replay rules right.

## Proposal

```python
async def close_preview(ticket_id: str, ctx: ToolContext) -> str:
    ticket = await ctx.deps.get(ticket_id)
    return f"Close ticket {ticket_id}: '{ticket.title}'?"

@server.tool(name="tickets.close", effect=Effect.WRITE,
             approval="required", preview=close_preview, idempotency=True)
async def close(ticket_id: str, ctx: ToolContext) -> TicketOut: ...
```

- `approval` is `"never"` (default), `"required"`, or a function of `ApprovalRequest(tool, effect, arguments, identity, summary)` that returns whether this call needs approval. A function that raises requires approval.
- `preview` has the handler's signature and returns the text the person approves; it should only read. Without it, the summary is the tool name, effect and arguments.
- The gate runs **before the handler and before the idempotency claim**, so nothing can change before the answer: SEC-7 holds by construction.

How the question reaches a person:

| Client | Flow |
| --- | --- |
| MCP 2026-07-28 | `input_required` with a confirmation (`approve: boolean`) and a signed receipt bound to the tool, the arguments and the caller. The retry must carry an accepted answer with `approve: true` and the receipt. The receipt is consumed in the store, so it works once, on any replica sharing the store. FastMCP's own request-state sealing also rejects a receipt sent with other arguments |
| Older clients with elicitation | Asked directly with FastMCP's `ctx.elicit` |
| Neither | `OunceServer(approver=fn)` decides (a ticket queue, a chat bot), else the call fails with `approval_unavailable` |

A declined or cancelled answer gives `approval_declined`. Both errors are in the `permission` category and say nothing was changed. If the handler itself asks for more input after approval, its state carries the approval, so the person is not asked twice.

**Gate 4.** The model cannot approve on its own: there is no approval argument (`approval` and `approved` are reserved parameter names, CAP-6), an answer without a valid receipt only asks again, and a receipt cannot be reused or moved. Approvals are enforced on every transport, including stdio with `stdio_policy="trust"` (proposal 0010, decision 3).

**Gate 3.** Approval tools are usually also `idempotency=True`, so a retried approved write is replayed, not repeated.

## Alternatives considered

- **A two-call preview/commit pattern with a receipt argument.** The plan in proposal 0010 listed it as a fallback for older clients. It was dropped: the model would hold the receipt and could pass it back without any person seeing the preview, which breaks gate 4. Older clients use elicitation or the server's `approver` instead.
- **Approval inside the handler (`await ctx.approve(...)`).** Easy to call after an effect by mistake. A gate before the handler cannot be.

## Impact on tokens and task success

Definitions do not change. An approved write costs one extra round trip and the summary text.

## Migration notes

None; `approval` defaults to `"never"`.
