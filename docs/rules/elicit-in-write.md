# `elicit-in-write`

Severity: Warning. Reported by `ouncemcp check`.

## What it finds

A write or destructive handler calls `elicit(...)` on the FastMCP context directly.

## Why it matters

Direct elicitation skips the SEC-7 guard, so nothing stops it after an effect.

## How to fix it

Use `ctx.require_input(...)` from the `ToolContext`.
