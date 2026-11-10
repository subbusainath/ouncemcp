# Host verification

The M0 and M1 exit criteria need the inventory example to work in real hosts, with versions recorded in the [compatibility matrix](../compatibility.md). This needs a person with the host apps; it cannot run in CI.

For each host (Claude Desktop, VS Code, and one more):

1. Configure the inventory example over stdio (see [quickstart](../quickstart.md), step 4), with `uv run --directory <repo> python examples/inventory/server.py`.
2. Record the host name and version, the OS, the OunceMCP commit and the FastMCP version (`uv pip show fastmcp`).
3. Ask the host's model, in a fresh chat:
   - "List running VMs in ap-south-1." (expects `inventory.search`, one or more pages)
   - "Show full details for the first one." (expects `inventory.get`, then `response_format=detailed`)
   - "Page through all databases in us-east-1." (expects the agent to follow `next_cursor`)
4. Check and record:
   - the tools appear, with the effect hints shown as read-only;
   - results render, and a truncated page shows its note and is followed correctly;
   - which definition fields the host shows the model, if it exposes them (BUD-7 host study: does it show `outputSchema`? `title`? annotations?);
   - whether the host lists tools lazily, which changes the discovery numbers (DISC-6 follow-up).
5. Add a row to the compatibility matrix and fill `verified_hosts` when the BUD-7 findings are clear.
