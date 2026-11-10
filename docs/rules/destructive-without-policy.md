# `destructive-without-policy`

Severity: Warning. Reported by `ouncemcp check`.

## What it finds

A tool declared `effect=Effect.DESTRUCTIVE` has no `policy=` (tool or server) and no `auth=`.

## Why it matters

Anyone who can reach the server can run it. Annotations such as `destructiveHint` are hints for clients and do not restrict access (spec SEC-3).

## How to fix it

Add a policy, for example `policy=require_scopes("admin")`, or a server-wide `policy=writes_require(authenticated)`.
