"""Network payload normalization for Drone heartbeat messages."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any


def tcp_port_open(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to ``host:port`` succeeds within ``timeout``.

    Used by Overmind's public-reachability probe: a successful connect to the
    Drone's registered public IP and HTTPS port means it is reachable from the
    internet. Plain TCP connect only (no TLS handshake) to stay lean; the call is
    bounded by ``timeout`` so probes can never hang and back up.
    """
    host = str(host or "").strip().split("%", 1)[0]
    if not host:
        return False
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if not 0 < port < 65536:
        return False
    try:
        with socket.create_connection((host, port), timeout=max(0.1, float(timeout))):
            return True
    except OSError:
        return False


def resolve_reported_network(network: dict[str, Any] | None) -> dict[str, Any]:
    """Return valid IPv4/IPv6 addresses from a Drone network payload."""
    network = network if isinstance(network, dict) else {}
    resolved = {"ipv4": [], "ipv6": []}
    for family in ("ipv4", "ipv6"):
        values = network.get(family)
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for raw in values:
            value = str(raw or "").split("%", 1)[0].strip()
            if not value:
                continue
            try:
                parsed = ipaddress.ip_address(value)
            except ValueError:
                continue
            if family == "ipv4" and parsed.version != 4:
                continue
            if family == "ipv6" and parsed.version != 6:
                continue
            normalized = str(parsed)
            if normalized not in resolved[family]:
                resolved[family].append(normalized)
    return {
        "reported": network,
        "resolved": resolved,
        "swarm_connected": bool(resolved["ipv4"] or resolved["ipv6"]),
    }
