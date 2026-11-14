# 0025: OAuth with the spec's client registration order

Status: proposed for M4. Covers spec SEC-10.

## Problem

SEC-10 asks for maintained OAuth integrations and a fixed client registration order: pre-registration, then Client ID Metadata Documents (CIMD), then the deprecated Dynamic Client Registration (DCR). OunceMCP's clients (`audit`, the `Broker`) only supported static bearer tokens, and nothing recorded which registration a client used.

## Proposal

**Servers** keep using FastMCP's maintained auth providers through `OunceServer(auth=...)`: GitHub, Google, Azure, Auth0, WorkOS, Keycloak, JWT verification and others. OunceMCP does not write its own OAuth server. Its policies (SEC-2) read the verified identity those providers produce (SEC-1).

**Clients** use `ouncemcp.client.OunceOAuth`, a thin layer over FastMCP's `OAuth` client: authorization code with PKCE, refresh, and protected-resource and authorization-server discovery.

- **Registration order.** A `client_id` (pre-registered, with an optional secret) wins. Otherwise a `client_metadata_url` (an https URL with a path) is used as the client id. Otherwise the client registers dynamically, unless `allow_dynamic=False`. Only the chosen method's settings reach the flow, so a client with a `client_id` can never fall back to DCR.
- **`registration` attribute.** It records which method was used, for logs and tests.
- **Where it plugs in.** `audit(url, auth=...)`, `connect_target(..., auth=...)` and `Broker(servers, auth={"id": OunceOAuth(...)})`.
- **CLI.** `ouncemcp audit URL --oauth [--client-id ID] [--client-metadata-url URL] [--no-dynamic-registration] [--scope S]` signs in through the browser. The client secret is read from `OUNCEMCP_OAUTH_CLIENT_SECRET`, never from an argument, so it stays out of shell history.

**Integration tests** run the whole flow over HTTP against FastMCP's in-memory OAuth provider (one provider, as SEC-10 asks):

- an unauthenticated audit is refused;
- dynamic registration signs in and lists the tools;
- a pre-registered client calls a tool through the `Broker` without registering anything new.

## Alternatives considered

- **Only bearer tokens.** Tokens have to be fetched and refreshed by hand, and nothing enforces the registration order.
- **Our own OAuth client.** FastMCP's is maintained and already follows the MCP authorization spec. OunceMCP adds only the ordering rule.

## Impact on tokens and task success

None.

## Migration notes

None. `bearer=` still works and is used when no `auth=` is given.
