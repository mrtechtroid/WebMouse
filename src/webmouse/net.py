"""Small networking helpers: LAN address discovery and Host header checks."""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable

_LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa", ".localdomain")


def lan_addresses() -> list[str]:
    """Best-effort list of this machine's private IPv4 addresses, primary first."""
    found: list[str] = []

    def add(addr: str) -> None:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return
        if ip.version == 4 and not ip.is_loopback and ip.is_private and addr not in found:
            found.append(addr)

    # The UDP "connect" trick picks the interface with the default route
    # without sending any packets.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def hostname_of(host_header: str) -> str:
    """Strip the port (and IPv6 brackets) from a Host header value."""
    host = host_header.strip()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def local_host_names(extra: Iterable[str] = ()) -> set[str]:
    names = {"localhost"}
    for getter in (socket.gethostname, socket.getfqdn):
        try:
            names.add(getter().lower())
        except OSError:
            pass
    names.update(h.lower() for h in extra)
    return names


def host_allowed(host_header: str, names: set[str]) -> bool:
    """Accept IP literals and known local names; reject everything else.

    Rejecting arbitrary names blocks DNS-rebinding attacks, where a hostile
    web page makes your browser talk to a LAN address under its own domain.
    """
    if not host_header:
        return False
    host = hostname_of(host_header).lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return host in names or host.endswith(_LOCAL_SUFFIXES)
