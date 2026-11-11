"""Outbound URL checks against server-side request forgery (part of spec SEC-9).

A tool that fetches a URL the model chose can be pointed at the cloud metadata service,
the server's own admin ports or the private network. ``check_url`` refuses those before
anything is fetched:

- only allowed schemes (default ``https`` and ``http``);
- an optional host allowlist (exact names, or ``.example.com`` for subdomains);
- every address the host resolves to must be public: loopback, private, link-local
  (which includes ``169.254.169.254``), shared, reserved and multicast ranges are
  refused unless ``allow_private=True``; known metadata hostnames are always refused.

Limits: the check resolves the name once, so a DNS answer that changes between the
check and the fetch (DNS rebinding) is not caught. Fetch through a client that connects
to the checked address, or keep an allowlist, when that matters. Redirects must be
checked again: pass each redirect target through ``check_url``.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from urllib.parse import urlsplit

from ouncemcp.core.errors import ErrorCategory, OunceError

METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
Resolver = Callable[[str], Iterable[str]]


class URLNotAllowed(OunceError):
    category: ErrorCategory = "validation"
    code = "url_not_allowed"


def _system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _host_allowed(host: str, allow_hosts: Sequence[str]) -> bool:
    for entry in allow_hosts:
        entry = entry.lower()
        if entry.startswith(".") and (host.endswith(entry) or host == entry[1:]):
            return True
        if host == entry:
            return True
    return False


def check_url(
    url: str,
    *,
    schemes: Sequence[str] = ("https", "http"),
    allow_hosts: Sequence[str] | None = None,
    allow_private: bool = False,
    resolve: Resolver | None = None,
) -> str:
    """Return ``url`` if it may be fetched; raise ``URLNotAllowed`` otherwise."""

    def refuse(reason: str) -> URLNotAllowed:
        return URLNotAllowed(reason, hint="Use a public http or https address.")

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise refuse("The URL is not valid.") from exc
    scheme = parts.scheme.lower()
    if scheme not in {s.lower() for s in schemes}:
        allowed = ", ".join(schemes)
        raise URLNotAllowed(
            f"URLs with scheme '{scheme or 'none'}' are not allowed.",
            hint=f"Allowed schemes: {allowed}.",
        )
    if scheme == "data":
        return url  # inline content, nothing is fetched
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise refuse("The URL has no host.")
    if host in METADATA_HOSTS:
        raise refuse("Cloud metadata addresses cannot be fetched.")
    if allow_hosts is not None and not _host_allowed(host, allow_hosts):
        raise URLNotAllowed(
            "That host is not on this server's allowlist.",
            hint="Ask the server owner which sites can be fetched.",
        )
    if allow_private:
        return url
    try:
        addresses: Sequence[Any] = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = list((resolve or _system_resolver)(host))
        except OSError as exc:
            raise refuse("The host name could not be resolved.") from exc
    if not addresses:
        raise refuse("The host name could not be resolved.")
    if not all(_public(a) for a in addresses):
        raise refuse("Private, loopback and link-local addresses cannot be fetched.")
    return url
