# 12. Large catalogs

Shelf has nine tools in 1,322 tokens, so a host gets every definition. A company-wide server can have 200 tools and 40,000 tokens of definitions: more than many models can use well, and paid on every turn.

## Automatic discovery

With `discovery="auto"`, the default, a server sends every definition while the total fits `Budget.startup_tokens` (4,000). Above that, it sends three small meta-tools instead:

| Tool | Does |
| --- | --- |
| `search_tools` | Finds tools by what they do, at `name`, `summary` or `full` detail |
| `describe_tool` | Gives one tool's full definition |
| `call_tool` | Calls a tool by name |

They cost 279 tokens whatever the catalog size, and their definitions never change, so host prompt caches stay valid when you add tools. `ouncemcp check` prints which mode a server will use and why:

```text
Discovery        direct (direct costs 1,322 tokens, within the 4,000-token threshold)
```

Force a mode with `discovery="direct"` or `discovery="search"`.

## Pinning the common tools

Some tools are used in almost every session. Keep them directly visible next to the meta-tools:

```python
server = OunceServer("library", discovery="auto", pinned=["books.search", "books.get"])
```

## Search respects policies

`search_tools` only finds tools the caller may use, and `describe_tool` only describes them. A member searching for "loans" never learns that `loans.open` exists. And as in chapter 7, hiding is not the protection: `call_tool("loans.open")` runs the same policy.

## Does search find the right tool?

Search is only useful if it finds what the model needs. Measure it with cases from real tasks:

```python
from ouncemcp import RecallCase

report = await server.measure_recall([
    RecallCase("which books are overdue at my branch", expected=["loans.open"]),
    RecallCase("borrow a book", expected=["loans.borrow"]),
])
print(report.render_text())
```

Low recall usually means descriptions that do not use the words people use. Fix the description, not the search.

## Summary

- Below 4,000 tokens, send every definition; above it, discovery switches to three meta-tools automatically.
- Pin the handful of tools almost every session needs.
- Measure search recall with cases from real tasks.
