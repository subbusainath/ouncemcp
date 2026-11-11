"""Part of SEC-9: check_url refuses private, loopback, link-local and metadata targets."""

from __future__ import annotations

import pytest

from ouncemcp.eval.security import SSRF_TARGETS
from ouncemcp.security import URLNotAllowed, check_url


def public(host: str) -> list[str]:
    return {"docs.example.com": ["93.184.216.34"], "rebind.example": ["10.0.0.5"]}[host]


@pytest.mark.parametrize("url", SSRF_TARGETS)
def test_the_catalog_targets_are_refused(url):
    with pytest.raises(URLNotAllowed):
        check_url(url, resolve=lambda h: ["127.0.0.1"])


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://224.0.0.1/",
        "http://rebind.example/",
        "https:///nohost",
    ],
)
def test_other_unsafe_urls_are_refused(url):
    with pytest.raises(URLNotAllowed):
        check_url(url, resolve=public)


def test_public_urls_pass_and_allowlists_narrow():
    assert check_url("https://docs.example.com/a", resolve=public)
    assert check_url("https://docs.example.com/a", allow_hosts=[".example.com"], resolve=public)
    with pytest.raises(URLNotAllowed, match="allowlist"):
        check_url("https://other.org/", allow_hosts=["docs.example.com"], resolve=public)
    assert check_url("http://10.0.0.5/", allow_private=True)
    assert check_url("data:text/plain,hi", schemes=["data"], allow_private=True)


def test_unresolvable_hosts_are_refused():
    def fail(host: str) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(URLNotAllowed, match="resolved"):
        check_url("https://nowhere.invalid/", resolve=fail)
