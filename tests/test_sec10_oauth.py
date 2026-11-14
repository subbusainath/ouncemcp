"""SEC-10: maintained OAuth, registering clients in the order the MCP spec gives:
pre-registration, then Client ID Metadata Documents, then Dynamic Client Registration."""

from __future__ import annotations

import socket

import anyio
import pytest
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from fastmcp.utilities.tests import HeadlessOAuth
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl, BaseModel

from ouncemcp import Effect, OunceServer
from ouncemcp.audit.runner import audit
from ouncemcp.client import Broker
from ouncemcp.client.oauth import OunceOAuth, registration_method


class Out(BaseModel):
    ok: bool = True


class Headless(HeadlessOAuth, OunceOAuth):
    """Completes the browser step by following the redirect itself (tests only)."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_registration_order():
    assert registration_method(client_id="app", client_metadata_url="https://a.example/c.json") == (
        "pre-registered"
    )
    assert registration_method(client_metadata_url="https://a.example/client.json") == (
        "client-id-metadata-document"
    )
    assert registration_method() == "dynamic"
    with pytest.raises(ValueError, match="dynamic"):
        registration_method(allow_dynamic=False)
    with pytest.raises(ValueError, match="https"):
        registration_method(client_metadata_url="http://a.example/client.json")
    with pytest.raises(ValueError, match="path"):
        registration_method(client_metadata_url="https://a.example/")


def test_only_the_chosen_method_reaches_the_flow():
    auth = OunceOAuth(client_id="app", client_metadata_url="https://a.example/client.json")
    assert auth.registration == "pre-registered"
    assert auth._client_metadata_url is None and auth._client_id == "app"
    cimd = OunceOAuth(client_metadata_url="https://a.example/client.json")
    assert cimd.registration == "client-id-metadata-document" and cimd._client_id is None


async def _serve(port: int, provider: InMemoryOAuthProvider, task_status) -> None:  # type: ignore[no-untyped-def]
    server = OunceServer("secured", auth=provider)

    @server.tool(
        name="secured.ping", effect=Effect.READ, description="Check that the call is authorized."
    )
    def ping() -> Out:
        return Out()

    task_status.started()
    await server.fastmcp.run_http_async(
        host="127.0.0.1", port=port, show_banner=False, log_level="error"
    )


def _provider(port: int) -> InMemoryOAuthProvider:
    return InMemoryOAuthProvider(
        base_url=f"http://127.0.0.1:{port}",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )


@pytest.mark.anyio
async def test_dynamic_registration_against_a_real_provider():
    port = free_port()
    provider = _provider(port)
    url = f"http://127.0.0.1:{port}/mcp"
    async with anyio.create_task_group() as group:
        await group.start(_serve, port, provider)
        await anyio.sleep(0.5)
        with pytest.raises(Exception):  # noqa: B017 - refused without credentials
            await audit(url)
        auth = Headless(url)
        report = await audit(url, auth=auth)
        group.cancel_scope.cancel()
    assert auth.registration == "dynamic"
    assert [t.name for t in report.cost.tools] == ["secured.ping"]
    assert len(provider.clients) == 1  # the client registered itself


@pytest.mark.anyio
async def test_pre_registered_client_skips_registration():
    port = free_port()
    provider = _provider(port)
    await provider.register_client(
        OAuthClientInformationFull(
            client_id="ounce-cli",
            client_secret="s3cret",
            redirect_uris=[AnyUrl("http://localhost:9/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="client_secret_post",
        )
    )
    url = f"http://127.0.0.1:{port}/mcp"
    async with anyio.create_task_group() as group:
        await group.start(_serve, port, provider)
        await anyio.sleep(0.5)
        auth = Headless(url, client_id="ounce-cli", client_secret="s3cret", callback_port=9)
        async with Broker({"secured": url}, auth={"secured": auth}) as broker:
            result = await broker.call("secured.secured.ping", {})
        group.cancel_scope.cancel()
    assert auth.registration == "pre-registered"
    assert not result.is_error and result.structured == {"ok": True}
    assert list(provider.clients) == ["ounce-cli"]  # nothing new was registered


def test_audit_cli_refuses_bad_oauth_settings(capsys):
    from ouncemcp.cli import main

    assert (
        main(["audit", "https://mcp.example.com/mcp", "--oauth", "--no-dynamic-registration"]) == 2
    )
    assert "dynamic registration is not allowed" in capsys.readouterr().err
    assert (
        main(
            ["audit", "https://mcp.example.com/mcp", "--client-metadata-url", "http://x.example/c"]
        )
        == 2
    )
