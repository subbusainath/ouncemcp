# `inconsistent-tool-names`

Severity: Warning. Reported by `ouncemcp check` (spec DX-3).

## What it finds

The server's tool names use more than one style: dotted namespaces (`tickets.search`), snake case (`create_ticket`) or kebab case (`close-ticket`). Tools OunceMCP adds itself (`handles.read`, `jobs.get`) are not counted.

## Why it matters

Consistent prefixes group related tools, which helps models pick the right one and helps search-based discovery rank them together. Mixed styles usually mean tools were added at different times and their names no longer say which belong together.

## How to fix it

Pick one style, preferably `namespace.verb` (`tickets.search`, `tickets.create`), and rename the odd ones out. Renaming a tool is a breaking change for clients that call it by name, so do it before release or keep the old name for a while.
