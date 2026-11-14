"""OAuth for clients of protected MCP servers (spec SEC-10, proposal 0025).

OunceMCP uses FastMCP's maintained OAuth client (authorization code with PKCE, token
refresh, protected-resource and authorization-server discovery) and fixes the order in
which a client gets its identity, as the MCP authorization spec recommends:

1. **Pre-registration:** a ``client_id`` (and secret, for confidential clients) issued
   by the authorization server's owner.
2. **Client ID Metadata Document:** an HTTPS URL with a non-root path that hosts the
   client's metadata and serves as its ``client_id``.
3. **Dynamic Client Registration:** the client registers itself. It is deprecated in the
   MCP spec; pass ``allow_dynamic=False`` to refuse it.

``OunceOAuth.registration`` records which one was used. Use it as ``auth=`` on
``audit``, ``connect_target`` or a ``Broker`` server.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from fastmcp.client.auth import OAuth

Registration = Literal["pre-registered", "client-id-metadata-document", "dynamic"]


def registration_method(
    *,
    client_id: str | None = None,
    client_metadata_url: str | None = None,
    allow_dynamic: bool = True,
) -> Registration:
    """The registration a client with these settings uses, in the MCP spec's order."""
    if client_id:
        return "pre-registered"
    if client_metadata_url:
        parts = urlsplit(client_metadata_url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("client_metadata_url must be an https URL")
        if parts.path in ("", "/"):
            raise ValueError("client_metadata_url needs a path, e.g. /oauth/client.json")
        return "client-id-metadata-document"
    if not allow_dynamic:
        raise ValueError(
            "No client_id or client_metadata_url, and dynamic registration is not allowed"
        )
    return "dynamic"


class OunceOAuth(OAuth):
    """FastMCP's ``OAuth`` with the SEC-10 registration order.

    Only the chosen method's settings reach the flow: with a ``client_id``, a
    ``client_metadata_url`` is ignored. Other arguments (``scopes``, ``client_name``,
    ``token_storage``, ``callback_port``...) are FastMCP's.
    """

    def __init__(
        self,
        mcp_url: str | None = None,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        client_metadata_url: str | None = None,
        allow_dynamic: bool = True,
        **kwargs: Any,
    ) -> None:
        self.registration: Registration = registration_method(
            client_id=client_id,
            client_metadata_url=client_metadata_url,
            allow_dynamic=allow_dynamic,
        )
        pre = self.registration == "pre-registered"
        super().__init__(
            mcp_url,
            client_id=client_id if pre else None,
            client_secret=client_secret if pre else None,
            client_metadata_url=(
                client_metadata_url if self.registration == "client-id-metadata-document" else None
            ),
            **kwargs,
        )
