# The OunceMCP Book

This book teaches you to build MCP servers that agents use well: small tool definitions, results that fit the context window, writes that never happen twice, and security that holds when the model is wrong or the input is hostile.

You will build one project the whole way through: **Shelf**, a server for a two-branch lending library. It starts as two read-only tools and ends as a complete server with borrowing, branch isolation, a background report, resources and a prompt. Each chapter adds one idea, and every stage is a real file in [`examples/book/shelf/`](https://github.com/subbusainath/ouncemcp/tree/main/examples/book/shelf) that the test suite runs, so the code in this book always works.

## Who this book is for

You know Python and have used an LLM with tools. You do not need to know the Model Context Protocol in detail; chapter 1 explains the parts you need. If you already run a FastMCP server, you can read chapters 1 to 3 and then jump to [chapter 14](ch14-migrating.md).

## How to read it

Read chapters 1 to 7 in order: they build the server. Chapters 8 to 15 stand on their own and can be read when you need them.

| Part | Chapters | You will learn |
| --- | --- | --- |
| Foundations | [1 Getting started](ch01-getting-started.md), [2 A first server](ch02-first-server.md), [3 Budgets](ch03-budgets.md) | What a tool costs the model, and how OunceMCP measures it |
| Results and errors | [4 Shaping results](ch04-shaping.md), [5 Errors the model can use](ch05-errors.md) | Paging, concise and detailed results, error categories and hints |
| Changing things | [6 Safe writes](ch06-writes.md), [7 Callers and policies](ch07-security.md) | Approvals, idempotency, verified identity, tenant isolation |
| Bigger jobs | [8 Background jobs and handles](ch08-jobs.md), [9 Resources and prompts](ch09-resources-prompts.md) | Work that outlives a call, context the host can attach |
| Proving it works | [10 Testing](ch10-testing.md), [11 Measuring with agents](ch11-measuring.md) | Unit tests, security tests, task suites, benchmarks, traces |
| Production | [12 Large catalogs](ch12-discovery.md), [13 Deploying](ch13-deploying.md), [14 Migrating](ch14-migrating.md), [15 Clients](ch15-clients.md) | Discovery, HTTP and replicas, moving a FastMCP server, the broker |
| Appendices | [A Command line](appendix-a-cli.md), [B Errors and lints](appendix-b-errors-lints.md), [C Glossary](appendix-c-glossary.md) | Quick reference |

## Conventions

Code blocks marked `bash` are commands for your shell. Output blocks show real output from the files in this book; your timings will differ.

!!! note
    Notes like this one point out something that is easy to miss.

!!! warning
    Warnings mark a mistake that leads to wrong or unsafe behaviour.

Every chapter ends with a short summary and the next step. Let's begin.
