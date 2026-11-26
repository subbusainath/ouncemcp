# 9. Resources and prompts

Tools are for the model to call. MCP has two more things a server can offer, and both are chosen by the **user or host**, not the model:

- **Resources** are data the host can show, attach or let the user pick: a document, a record, a set of rules.
- **Prompts** are message templates the user picks, often from a slash menu.

Neither costs startup context the way tools do: the host lists them, but the model sees one only when it is used.

## Step 5's resources and prompt

```python title="examples/book/shelf/step5_complete.py"
--8<-- "examples/book/shelf/step5_complete.py:resources"
```

- `shelf://rules` is a fixed resource, served as Markdown. A host can attach the lending rules to a conversation so the model answers "how long can I keep a book?" correctly without a tool call.
- `shelf://books/{book_id}` is a **template**: one resource per book. It reuses `books.get`, so it is shaped to the same budget and isolated by branch in the same way. The book's tests read `shelf://books/b-002` as a north member.
- `recommend` is a prompt with one argument. When the user picks it, the host sends the message to the model, which then uses the tools.

Resources and prompts get the same treatment as tools: policies apply, results are shaped and errors are categorized. `check` counts what they cost in the list the host receives.

## When to use which

| You want | Use |
| --- | --- |
| The model to decide when to fetch or change something | A tool |
| The user or host to attach something to the conversation | A resource |
| A repeatable task the user starts by name | A prompt |

A common mistake is a `get_rules` tool. The rules are the same every time; as a tool, the model pays for its definition on every turn and has to remember to call it. As a resource, the host attaches it once.

## The complete Shelf

That is the whole server: nine tools in 1,322 tokens, two resources and a prompt.

```text
Tool            Total   Desc  Input  Output  Other  Status
books.search      226     33     92      89     12  OK
jobs.get          180     15     34     119     12  OK
books.get         179     26     60      80     13  OK
handles.read      140     21     67      40     12  OK
loans.borrow      133     36     34      50     13  OK
loans.return      129     25     40      50     14  OK
loans.open        126     20     14      78     14  OK
loans.mine        122     16     14      78     14  OK
reports.start      87     32     14      28     13  OK
```

A member never sees `loans.open` or `reports.start`, so they pay even less. The rest of the book tests it, measures it with real models, and deploys it.

## Summary

- Resources are data the host attaches; prompts are tasks the user picks.
- Templates (`shelf://books/{book_id}`) expose one resource per record.
- Move anything static out of tools and into resources.

!!! question "Questions about this chapter?"
    Ask in `#help` on the [OunceMCP Discord](https://discord.gg/mQz4ZuZe3u), one post per question.
