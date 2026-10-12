"""SSRF-safe outbound URL validation.

* Integration adapters may only call hosts on the static allowlist below (plus the
  configured OpenAI-compatible base URL host).
* User-supplied webhook URLs must be https, must not use credentials or odd ports, and
  must resolve exclusively to public IP addresses (no loopback, RFC1918, link-local,
  cloud metadata, CGNAT, multicast or reserved ranges). Hosts in OUTBOUND_ALLOWED_HOSTS
  skip the DNS check only if they are explicitly listed by the operator.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from ..settings import get_settings

INTEGRATION_HOSTS = frozenset(
    {
        "services.leadconnectorhq.com",
        "graph.facebook.com",
        "business-api.tiktok.com",
        "api.anthropic.com",
        "api.openai.com",
        "hooks.slack.com",
        "discord.com",
        "discordapp.com",
    }
)

Resolver = Callable[[str], list[str]]


class UnsafeURLError(ValueError):
    pass


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({str(info[4][0]) for info in infos})


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or addr in ipaddress.ip_network("100.64.0.0/10")  # CGNAT
        or addr in ipaddress.ip_network("169.254.0.0/16")  # metadata
    )


def allowed_integration_hosts() -> set[str]:
    s = get_settings()
    hosts = set(INTEGRATION_HOSTS)
    for base in (s.openai_base_url, s.ghl_api_base, s.tiktok_api_base):
        host = urlsplit(base).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def check_integration_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise UnsafeURLError("integration calls must use https")
    if host not in allowed_integration_hosts():
        raise UnsafeURLError(f"host not in integration allowlist: {host}")
    return url


def check_webhook_url(url: str, resolver: Resolver | None = None) -> str:
    s = get_settings()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise UnsafeURLError("webhook URL must use https")
    if not host:
        raise UnsafeURLError("webhook URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials in URL are not allowed")
    if parts.port not in (None, 443, 8443):
        raise UnsafeURLError("non-standard port not allowed")
    if host in {h.lower() for h in s.outbound_allowed_hosts} or host in INTEGRATION_HOSTS:
        return url
    if s.outbound_allow_private:
        return url
    try:
        ips = (resolver or _default_resolver)(host)
    except OSError as exc:
        raise UnsafeURLError(f"cannot resolve host: {host}") from exc
    if not ips:
        raise UnsafeURLError("host resolved to no addresses")
    bad = [ip for ip in ips if not is_public_ip(ip)]
    if bad:
        raise UnsafeURLError(f"host resolves to non-public address: {bad[0]}")
    return url
