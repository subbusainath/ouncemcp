# `missing-usage-guidance`

Severity: Warning. Reported by `ouncemcp check`.

## What it finds

In a server with several tools, a description never says when to use the tool or what to use instead.

## Why it matters

Without guidance, models confuse similar tools or call the wrong one first (spec CAP-5).

## How to fix it

Add a pointer such as "Use tickets.search first to find ids." or "Use this only for open tickets."
