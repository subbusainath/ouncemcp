# `stdio-skips-policy`

Severity: Warning. Reported by `ouncemcp check`.

## What it finds

At least one tool has a `policy=` (or `auth=`), and the server uses the default `stdio_policy="trust"`.

## Why it matters

Over stdio, FastMCP skips tool authorization: the server runs as the local user who started it, and stdio requests carry no verified token. With `"trust"`, every tool is visible and callable over stdio whatever its policy. Over HTTP, and for in-process calls, policies apply as written. Approvals for safe writes are enforced on every transport either way.

## How to fix it

- If the server is only served over HTTP, or stdio users are trusted, ignore the warning.
- Otherwise use `OunceServer(..., stdio_policy="enforce")`. Over stdio, every check then runs with the anonymous caller, so a tool whose policy needs a verified caller is hidden and refused there.
