"""SEC-9: outbound fetches connect only to checked addresses and re-check redirects."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx2
import pytest

from ouncemcp.security import URLNotAllowed, safe_client
from ouncemcp.security.fetch import PinnedTransport

ADDRESSES = {
    "docs.example.com": ["93.184.216.34"],
    "cdn.example.com": ["93.184.216.34"],
    "mirror.example.com": ["93.184.216.40"],
    "internal.example": ["10.0.0.5"],
    "mixed.example": ["93.184.216.34", "127.0.0.1"],
}


class Resolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        return ADDRESSES[host]


def _client(handler, resolver=None, **kwargs) -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []
    pools: list[str] = []

    def factory() -> httpx2.AsyncBaseTransport:
        pools.append("pool")

        def record(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return handler(request)

        return httpx2.MockTransport(record)

    client = safe_client(resolve=resolver or Resolver(), transport_factory=factory, **kwargs)
    client.pools = pools  # type: ignore[attr-defined]
    return client, seen


@pytest.mark.anyio
async def test_requests_connect_to_the_checked_address_and_keep_the_host_name():
    resolver = Resolver()
    client, seen = _client(lambda r: httpx2.Response(200, text="ok"), resolver)
    async with client:
        response = await client.get("https://docs.example.com/guide?x=1")
    assert response.text == "ok"
    request = seen[0]
    assert request.url.host == "93.184.216.34" and request.url.path == "/guide"
    assert request.headers["host"] == "docs.example.com"
    assert request.extensions["sni_hostname"] == "docs.example.com"
    assert resolver.calls == ["docs.example.com"]  # resolved once, then pinned


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url",
    [
        "http://internal.example/",
        "http://mixed.example/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "ftp://docs.example.com/",
    ],
)
async def test_unsafe_targets_are_refused_before_connecting(url):
    client, seen = _client(lambda r: httpx2.Response(200))
    async with client:
        with pytest.raises(URLNotAllowed):
            await client.get(url)
    assert seen == []


@pytest.mark.anyio
async def test_every_redirect_is_checked_again():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.headers["host"] == "docs.example.com":
            return httpx2.Response(302, headers={"location": "http://internal.example/admin"})
        return httpx2.Response(200, text="secret")

    client, seen = _client(handler)
    async with client:
        with pytest.raises(URLNotAllowed):
            await client.get("https://docs.example.com/")
    assert [r.headers["host"] for r in seen] == ["docs.example.com"]


@pytest.mark.anyio
async def test_safe_redirects_are_followed_and_limited():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.headers["host"] == "docs.example.com":
            return httpx2.Response(302, headers={"location": "https://mirror.example.com/a"})
        return httpx2.Response(200, text="mirror")

    client, seen = _client(handler)
    async with client:
        assert (await client.get("https://docs.example.com/")).text == "mirror"
    assert seen[1].url.host == "93.184.216.40"

    loop, _ = _client(
        lambda r: httpx2.Response(302, headers={"location": "https://docs.example.com/"}),
        max_redirects=3,
    )
    async with loop:
        with pytest.raises(httpx2.TooManyRedirects):
            await loop.get("https://docs.example.com/")


@pytest.mark.anyio
async def test_hosts_sharing_an_address_never_share_a_connection_pool():
    client, _ = _client(lambda r: httpx2.Response(200))
    async with client:
        await client.get("https://docs.example.com/")
        await client.get("https://docs.example.com/again")
        await client.get("https://cdn.example.com/")  # same IP, different certificate name
    assert len(client.pools) == 2  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_allowlist_applies_to_every_hop():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "https://mirror.example.com/"})

    client, seen = _client(handler, allow_hosts=["docs.example.com"])
    async with client:
        with pytest.raises(URLNotAllowed, match="allowlist"):
            await client.get("https://docs.example.com/")
    assert len(seen) == 1


@pytest.mark.anyio
async def test_a_real_connection_goes_to_the_pinned_address():
    hosts: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hosts.append(self.headers["Host"])
            body = b"pinned"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # A name that does not exist in DNS reaches the local server only because the
        # transport connects to the address the resolver returned.
        async with safe_client(
            resolve=lambda h: ["127.0.0.1"], allow_private=True, allow_hosts=["app.invalid"]
        ) as client:
            response = await client.get(f"http://app.invalid:{port}/")
        assert response.text == "pinned" and hosts == [f"app.invalid:{port}"]
    finally:
        httpd.shutdown()


def test_transport_needs_http_schemes():
    with pytest.raises(ValueError, match="http"):
        PinnedTransport(schemes=("data",))
