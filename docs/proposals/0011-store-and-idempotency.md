# 0011: Shared store, idempotent writes and dependencies

Status: proposed for M2 (step 1 of [proposal 0010](0010-m2-plan.md)). Covers spec EXE-4, the shared half of SEC-6, and the section 9.2 rule that correctness must not depend on process-local state.

## Problem

A write that times out is reported as `ambiguous_write`, and the agent cannot tell whether repeating it will do it twice. Single-use state tokens were single-use per process only. Handlers reached upstream clients through globals.

## Proposal

### Store

`OunceServer(store=...)` takes an `OunceStore`: `get`, `put`, `put_if_absent` and `delete` on JSON objects, namespaced and with an expiry, plus `shared` (whether `put_if_absent` is atomic across processes).

| Store | Shared claims | Use |
| --- | --- | --- |
| `MemoryStore` (default) | No | Development, single replica |
| `RedisStore(redis.asyncio client)` | Yes (`SET NX PX`) | Multiple replicas |
| `KeyValueStore(any py-key-value-aio store)` | No: data is shared, claims are atomic per process | Disk, Postgres, DynamoDB and others, one writer |

Keys are digests, so no caller data appears in key names. `py-key-value-aio` is already a FastMCP 4 dependency; Redis needs the `redis` package, which OunceMCP does not install.

### Idempotent writes (EXE-4)

`@server.tool(effect=Effect.WRITE, idempotency=True)` or `idempotency=Idempotency(ttl_s=86400, grace_s=60, wait_s=5)`. Reads cannot opt in. The key is the principal, the tenant, the tool and a digest of the normalized arguments, or a client key from `_meta` (`ouncemcp/idempotency_key` or `idempotencyKey`) bound to the same caller and tool.

| Record | A duplicate call gets |
| --- | --- |
| `running` | Waits up to `wait_s` for the result, else a retryable `write_in_progress` (`transient`) |
| `done` | The stored result, with `_meta.ouncemcp.replayed = true` |
| `unknown` | `write_status_unknown` (`ambiguous_write`): check the state or use a new key |
| a client key with different arguments | `idempotency_key_reused` (`validation`) |

A failure that cannot have changed anything (validation, authentication, permission, not found, rate limit, or a request never sent) clears the record. Any other failure marks it `unknown`.

**Deadlines.** The call deadline bounds the caller's wait, not the write. A write past its deadline keeps running for up to `grace_s`; the caller gets `write_still_running` (`ambiguous_write`) with "call again with the same arguments: you get its result once it finishes, and it will not run twice". The finished result is stored, so the retry replays it. This is the ticketing pilot's key test, and it is tested here.

### Shared single use (SEC-6)

`await ctx.consume_state(token, kind, request=...)` verifies a state token and records its use in the store, so a second use fails on any replica sharing the store.

### Dependencies

`OunceServer(deps=...)` takes any object; handlers read `ctx.deps`. `ouncemcp.security.guarded_client(...)` returns an `httpx2.AsyncClient` with the passthrough guard installed, for upstream calls.

## Alternatives considered

- **Idempotency on by default for writes.** It would silently turn two intended identical writes (two identical reminders) into one. Opt-in per tool, with the ticketing pilot and docs using it, keeps behavior explicit.
- **Cancel the write at the deadline, as before.** Cancellation is not rollback: the upstream may already have applied it, and the result would be lost. Letting it finish within a grace period and storing the result is what makes the retry safe.
- **Atomic claims through the generic key-value protocol.** It has no compare-and-set, so claims would race across replicas. Redis gets a dedicated adapter instead.

## Impact on tokens and task success

Definitions do not change. Replayed results carry one extra `_meta` field. Agents that retry after a timeout get the result instead of a duplicate.

## Migration notes

None for existing tools. Fixed while building this: passing an empty `PartitionedCache` as `cache=` used to be replaced by a new one (an empty cache is falsy); the server now keeps the object passed.
