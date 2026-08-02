"""SSRF protection for outbound webhook targets."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class SSRFValidationError(ValueError):
    """URL rejected by SSRF policy."""


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """Validated target connection info."""

    url: str
    scheme: str
    hostname: str
    port: int
    resolved_ips: tuple[str, ...]


# Always blocked — never relaxed by allow_private_targets.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
    }
)


def validate_webhook_url(
    url: str,
    *,
    allow_private_targets: bool = False,
    resolve_dns: bool = True,
) -> ResolvedTarget:
    """Validate webhook URL before any outbound request.

    Always (independent of ``allow_private_targets``):
    - schemes http/https only
    - no embedded credentials
    - block loopback hostnames and addresses (127.0.0.1, ::1, localhost)
    - block link-local (incl. cloud metadata 169.254.169.254)
    - block multicast / unspecified
    - block metadata hostnames

    When ``allow_private_targets=False`` (production default):
    - also block private RFC1918 / ULA ranges

    When ``allow_private_targets=True`` (lab / on-LAN smoke only):
    - permit private RFC1918 / ULA (e.g. 10.x, 192.168.x) so a receiver can
      bind 0.0.0.0 and be reached via the host LAN address
    - **loopback remains blocked**
    """

    if not url or not isinstance(url, str):
        raise SSRFValidationError("URL is required.")
    raw = url.strip()
    if len(raw) > 2048:
        raise SSRFValidationError("URL exceeds maximum length.")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFValidationError(
            "Only http and https schemes are allowed for webhook targets."
        )
    if parsed.username is not None or parsed.password is not None:
        raise SSRFValidationError(
            "Embedded credentials in webhook URLs are not allowed."
        )
    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("Webhook URL must include a hostname.")
    host_l = hostname.lower().rstrip(".")
    if host_l in _BLOCKED_HOSTNAMES or host_l.endswith(".localhost"):
        raise SSRFValidationError(
            "Localhost and metadata hostnames are blocked."
        )

    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 1 <= port <= 65535:
        raise SSRFValidationError("Invalid port.")

    # Literal IP in hostname
    literal_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    resolved: list[str] = []
    if literal_ip is not None:
        _assert_ip_allowed(literal_ip, allow_private=allow_private_targets)
        resolved.append(str(literal_ip))
    elif resolve_dns:
        try:
            infos = socket.getaddrinfo(
                hostname, port, type=socket.SOCK_STREAM
            )
        except socket.gaierror as error:
            raise SSRFValidationError(
                f"Unable to resolve webhook hostname: {hostname}"
            ) from error
        if not infos:
            raise SSRFValidationError(
                f"No addresses resolved for hostname: {hostname}"
            )
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            _assert_ip_allowed(ip, allow_private=allow_private_targets)
            resolved.append(str(ip))
        if not resolved:
            raise SSRFValidationError(
                f"No valid addresses resolved for hostname: {hostname}"
            )
    else:
        # Structural validation only (tests without DNS).
        pass

    return ResolvedTarget(
        url=raw,
        scheme=scheme,
        hostname=hostname,
        port=port,
        resolved_ips=tuple(resolved),
    )


def _assert_ip_allowed(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private: bool,
) -> None:
    # Loopback is always blocked (not covered by allow_private_targets).
    if ip.is_loopback:
        raise SSRFValidationError("Loopback addresses are blocked.")
    if ip.is_link_local:
        raise SSRFValidationError("Link-local addresses are blocked.")
    if ip.is_multicast:
        raise SSRFValidationError("Multicast addresses are blocked.")
    if ip.is_unspecified:
        raise SSRFValidationError("Unspecified addresses are blocked.")
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in ipaddress.ip_network("169.254.0.0/16"):
            raise SSRFValidationError("Link-local addresses are blocked.")
        if ip in ipaddress.ip_network("0.0.0.0/8"):
            raise SSRFValidationError("Unspecified addresses are blocked.")
    # Private RFC1918 / ULA — only relaxed by allow_private_targets.
    if ip.is_private or ip.is_site_local:
        if not allow_private:
            raise SSRFValidationError(
                "Private network addresses are blocked "
                "(set allow_private_targets=true to permit)."
            )
