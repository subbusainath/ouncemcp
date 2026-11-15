# Appendix C: glossary

**Approval.** A person's yes or no to a write, asked by the server before the handler runs (chapter 6).

**Approver.** A function that answers approvals when the client cannot ask a person: a ticket queue, a chat bot, a test.

**Budget.** The token limits for definitions and results (chapter 3).

**Concise / detailed.** The two response formats. `Detail` fields are only in detailed results (chapter 4).

**Cursor.** A signed, expiring token for the next page of a list, bound to the tool, caller and query.

**Deadline.** The time a call may take before it is cancelled (default 30 s).

**Discovery.** Whether the host gets every tool definition (`direct`) or three meta-tools to search them (`search`) (chapter 12).

**Effect.** What a tool does to the world: `READ`, `WRITE` or `DESTRUCTIVE`.

**Handle.** A signed reference to a stored result the model reads in pages with `handles.read` (chapter 8).

**Host.** The AI application that connects to MCP servers and talks to the model.

**Idempotency.** Running a write at most once per caller and arguments; repeats replay the first result.

**Identity.** The verified caller (`ctx.identity`): subject, tenant, scopes. It comes from the token, never from arguments.

**Job.** Background work started by a tool and polled with `jobs.get`; named jobs restart after a crash.

**Lint.** A check by `ouncemcp check` for definitions a model will misuse.

**Policy.** A rule that decides whether a caller may see and call a tool (chapter 7).

**Preview.** The text a person sees when asked to approve a write.

**Resource.** Data the host can attach to a conversation (chapter 9).

**Shaping.** Trimming a result to its budget while keeping it valid against its schema.

**Startup context.** The tokens of every definition the host sends at the start of a session.

**Store.** Where idempotency records, receipts, handles and jobs live; shared across replicas in production.

**Tenant.** The group a caller belongs to (a branch, a company); data never crosses tenants.

**Trace.** A record of one tool call: tokens, latency, outcome.
