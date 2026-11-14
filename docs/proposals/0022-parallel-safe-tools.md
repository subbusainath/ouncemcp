# 0022: Marking tools as parallel-safe

Status: proposed for M4. Covers spec EXE-5.

## Problem

Agents often need several independent reads (the details of five resources, the open tickets of three teams). A client that knows those calls are independent can run them together instead of one per turn. MCP's tool annotations describe effects (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) but not whether a call depends on the order of other calls, and the spec left EXE-5 waiting on a convention.

## Proposal

Every tool definition carries `_meta["ouncemcp/parallelSafe"]`:

- **Read tools:** `true` by default. A read that depends on call order (taking the next item from a shared queue, a cursor kept on the server) registers with `parallel_safe=False` and is marked `false`.
- **Write and destructive tools:** always `false`. `parallel_safe=True` on them is refused at registration, because clients must keep writes in order.

`ouncemcp.core.effects.is_parallel_safe(tool)` reads the mark from an MCP `Tool` or its JSON, for clients and brokers. `ToolSpec.parallel_safe` holds the same value on the server.

`_meta` is the MCP field for implementation metadata. The key is namespaced, so hosts that do not know it ignore it. `_meta` is outside the counted fields (BUD-7), so the mark adds no counted tokens.

## Alternatives considered

- **Reusing `readOnlyHint`.** A read can still depend on order, and annotations are hints that hosts may show to the model. A separate key keeps "safe to run together" distinct from "does not change anything".
- **A new annotation field.** Annotations are defined by the MCP schema; custom keys there are not part of the protocol. `_meta` is the documented extension point.
- **Proposing a protocol change.** Worth doing once the convention has users; OunceMCP's key can then map to the standard one.

## Impact on tokens and task success

No counted definition tokens. Clients that use the mark can cut turns for independent reads.

## Migration notes

None. Existing read tools become parallel-safe; set `parallel_safe=False` on any that depend on call order.
