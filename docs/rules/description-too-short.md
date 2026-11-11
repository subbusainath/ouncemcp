# `description-too-short`

Severity: Warning. Reported by `ouncemcp check`.

## What it finds

A tool description has fewer than five words.

## Why it matters

The description is most of what a model uses to pick a tool. A few words rarely say what the tool does, what it returns, or when it applies (spec CAP-5).

## How to fix it

Say what the tool does and returns in one or two sentences, for example: "Find support tickets by status and assignee. Returns at most 50 per page."
