"""Small LAN helpers."""

from __future__ import annotations

import ipaddress
import socket


def local_ipv4() -> str | None:
    """The PC's address on the home network (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def local_subnet_ips() -> list[str]:
    """Every other address of the PC's /24 home network."""
    own = local_ipv4()
    if own is None:
        return []
    net = ipaddress.ip_network(f"{own}/24", strict=False)
    return [str(h) for h in net.hosts() if str(h) != own]


def local_broadcast() -> str:
    own = local_ipv4()
    if own is None:
        return "255.255.255.255"
    return str(ipaddress.ip_network(f"{own}/24", strict=False).broadcast_address)
