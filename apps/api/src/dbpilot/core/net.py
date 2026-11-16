"""Where a configured host really points, judged the way the operating system resolves it.

Connected databases and model endpoints are reached from inside the deployment, so a host
aimed at instance metadata would hand out the task's credentials. Hosts are canonicalized
first: glibc and macOS accept IPv4 written as one integer (2852039166), in hex
(0xa9fea9fe), in octal (0251.0376.0251.0376) or with a trailing dot, and every one of those
is 169.254.169.254. A host is one name or one address: drivers read more into the field
than a resolver does (libpq tries every host in a comma-separated list), so anything else
is refused. And ``vet_host`` returns the address it approved, so the gateway connects to
that address, not to whatever the name resolves to a moment later.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket

IP = ipaddress.IPv4Address | ipaddress.IPv6Address

METADATA_HOSTS = frozenset({"metadata", "metadata.google.internal", "instance-data"})
_AWS_METADATA_V6 = ipaddress.ip_address("fd00:ec2::254")
_NUMERIC_V4 = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$")
_LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
_HOSTNAME = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")


class HostRefusedError(ValueError):
    """The host may not be connected to, or does not resolve. ``code`` says which."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def canonical_host(host: str) -> str:
    """Lower case, no brackets or trailing dot, numeric IPv4 forms as dotted quads."""
    host = host.strip().strip("[]").lower().rstrip(".")
    if _NUMERIC_V4.match(host):
        try:
            return socket.inet_ntoa(socket.inet_aton(host))
        except OSError:
            return host
    return host


def as_ip(host: str) -> IP | None:
    """The address ``host`` spells, if it is one (an IPv4-mapped IPv6 address as the IPv4
    address it carries, so ``::ffff:169.254.169.254`` is judged as what it reaches)."""
    try:
        ip = ipaddress.ip_address(canonical_host(host))
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_forbidden_ip(ip: IP) -> bool:
    """Link-local (instance metadata), unspecified and the AWS IPv6 metadata address."""
    return ip.is_link_local or ip.is_unspecified or ip == _AWS_METADATA_V6


def check_host(value: str) -> str:
    """One host name or IP address that does not point at instance metadata; raises
    ValueError. Used at the API boundary when a connection is saved, and again at connect
    time."""
    host = canonical_host(value)
    ip = as_ip(host)
    if ip is None and (not host or len(host) > 253 or not _HOSTNAME.match(host)):
        raise ValueError("enter one host name or IP address, without a scheme, port, path or list")
    if ip is not None and is_forbidden_ip(ip):
        raise ValueError("link-local and instance metadata addresses are not allowed")
    if host in METADATA_HOSTS:
        raise ValueError("instance metadata hosts are not allowed")
    return host


async def vet_host(host: str, port: int, *, allow_private: bool = True) -> str:
    """The address to connect to for ``host``: one it resolves to, judged when the
    connection is opened (a DNS name that points at metadata is refused too, and with
    ``allow_private`` False so are private and loopback addresses). Raises
    HostRefusedError, also when the name does not resolve."""
    try:
        name = check_host(host)
    except ValueError as exc:
        raise HostRefusedError(f"This deployment refuses that host: {exc}", "host_refused") from exc
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HostRefusedError(
            "The host name could not be resolved. Check the spelling, or use an IP address",
            "host_unresolved",
        ) from exc
    addresses: list[IP] = []
    for info in infos:
        ip = as_ip(str(info[4][0]).split("%")[0])
        if ip is None:
            continue
        if is_forbidden_ip(ip):
            raise HostRefusedError(
                "The host resolves to a link-local or instance metadata address, which this "
                "deployment refuses",
                "host_refused",
            )
        if not allow_private and (ip.is_private or ip.is_loopback):
            raise HostRefusedError(
                "The host resolves to a private address, which this deployment refuses "
                "(SOURCE_ALLOW_PRIVATE=false)",
                "host_refused",
            )
        addresses.append(ip)
    if not addresses:
        raise HostRefusedError(
            "The host name could not be resolved. Check the spelling, or use an IP address",
            "host_unresolved",
        )
    ipv4 = [a for a in addresses if a.version == 4]
    return str((ipv4 or addresses)[0])


async def resolves_safely(host: str, port: int, *, allow_private: bool = True) -> bool:
    """Whether ``vet_host`` accepts ``host``."""
    try:
        await vet_host(host, port, allow_private=allow_private)
    except HostRefusedError:
        return False
    return True
