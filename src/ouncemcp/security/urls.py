"""Outbound URL checks against server-side request forgery (part of spec SEC-9).

A tool that fetches a URL the model chose can be pointed at the cloud metadata service,
the server's own admin ports or the private network. ``check_url`` refuses those before
anything is fetched:

- only allowed schemes (default ``https`` and ``http``);
- an optional host allowlist (exact names, or ``.example.com`` for subdomains);
- every address the host resolves to must be public: loopback, private, link-local
  (which includes ``169.254.169.254``), shared, reserved and multicast ranges are
  refused unless ``allow_private=True``; known metadata hostnames are always refused.

Limits: ``check_url`` resolves the name once, so a DNS answer that changes between the
check and the fetch (DNS rebinding) is not caught, and redirects are not followed. Fetch
with ``safe_client`` (``ouncemcp.security.fetch``), which connects to the checked
address and checks every redirect again.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
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


# IPv6 ranges that carry an IPv4 address in their last 32 bits. A gateway may deliver
# them to that IPv4 address, so the embedded address must be public too: NAT64
# (RFC 6052 well-known and RFC 8215 local-use prefixes) and the deprecated
# IPv4-compatible form (::a.b.c.d).
_EMBEDDED_V4 = tuple(ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "::/96"))


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if any(ip in net for net in _EMBEDDED_V4):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_v4(ip)
        if inner is not None:
            # Mapped addresses are the IPv4 address; for the tunnelling forms both the
            # IPv6 address and the IPv4 address inside it must be public.
            if ip.ipv4_mapped is not None:
                return inner.is_global and not inner.is_multicast
            if not (inner.is_global and not inner.is_multicast):
                return False
            if any(ip in net for net in _EMBEDDED_V4):
                return True  # the prefix itself is a translation prefix, not a host range
    return ip.is_global and not ip.is_multicast


def _host_allowed(host: str, allow_hosts: Sequence[str]) -> bool:
    for entry in allow_hosts:
        entry = entry.lower()
        if entry.startswith(".") and (host.endswith(entry) or host == entry[1:]):
            return True
        if host == entry:
            return True
    return False


def checked_addresses(
    url: str,
    *,
    schemes: Sequence[str] = ("https", "http"),
    allow_hosts: Sequence[str] | None = None,
    allow_private: bool = False,
    resolve: Resolver | None = None,
) -> tuple[str, list[str]]:
    """Check ``url`` and return its host and the addresses it may be fetched from.

    Raises ``URLNotAllowed`` like ``check_url``. Unlike ``check_url``, it always resolves
    the host (also with ``allow_private=True``), so a client can connect to exactly the
    addresses that were checked (``safe_client``). ``data:`` URLs return no addresses.
    """

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
        return "", []  # inline content, nothing is fetched
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
    try:
        addresses: list[str] = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = list((resolve or _system_resolver)(host))
        except OSError as exc:
            raise refuse("The host name could not be resolved.") from exc
    if not addresses:
        raise refuse("The host name could not be resolved.")
    if not allow_private and not all(_public(a) for a in addresses):
        raise refuse("Private, loopback and link-local addresses cannot be fetched.")
    return host, addresses


def check_url(
    url: str,
    *,
    schemes: Sequence[str] = ("https", "http"),
    allow_hosts: Sequence[str] | None = None,
    allow_private: bool = False,
    resolve: Resolver | None = None,
) -> str:
    """Return ``url`` if it may be fetched; raise ``URLNotAllowed`` otherwise.

    To fetch, prefer ``safe_client``: it connects to the checked address and checks
    every redirect again, which closes the DNS-rebinding gap described above.
    """
    if allow_private:
        # Names are not resolved when private targets are allowed; only the scheme,
        # metadata hosts and the allowlist apply.
        checked_addresses(
            url,
            schemes=schemes,
            allow_hosts=allow_hosts,
            allow_private=True,
            resolve=lambda host: ["0.0.0.0"],
        )
        return url
    checked_addresses(url, schemes=schemes, allow_hosts=allow_hosts, resolve=resolve)
    return url
