# Discovery modes

With `discovery="auto"` (the default) a server sends every tool definition while they fit `Budget.startup_tokens`. Above that it sends three meta-tools instead, `search_tools`, `describe_tool` and `call_tool` (279 tokens whatever the catalog size), plus any `pinned` tools.

- Search results come at three detail levels: `name`, `summary`, `full`.
- Only tools the caller may use are found or described (DISC-2). Hiding a tool is not access control: the same policy runs on a call by a guessed name (DISC-3).
- Meta-tool definitions are byte-identical across catalog changes, so host prompt caches stay valid (DISC-5).
- `await server.measure_recall(cases)` reports how often search finds the tools agents need (DISC-6).

`ouncemcp check` reports which mode the server will use and why. Details: [proposal 0006](../proposals/0006-discovery-modes.md).
