"""Network payload normalization for Drone heartbeat messages."""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


def _normalized_host_port(host: str, port: int) -> Optional[tuple[str, int]]:
    """Validate/normalize a host (strip IPv6 zone id) and port, or return None."""
    host = str(host or "").strip().split("%", 1)[0]
    if not host:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 0 < port < 65536:
        return None
    return host, port


def probe_drone_identity(host: str, port: int, timeout: float) -> Optional[str]:
    """Return the ``drone_id`` reported by ``https://host:port/health``, or None.

    Used by Overmind's public-reachability probe to confirm the machine answering
    at a Drone's public IP:port is actually *that* Drone -- not another device that
    happens to share the public IP behind the same NAT/port-forward. The Drone's
    ``/health`` endpoint is unauthenticated and the server cert is self-signed, so
    we connect with verification disabled and identify the responder by the
    ``drone_id`` it returns. Bounded by ``timeout``; returns None on any failure so
    the probe can never hang or raise.
    """
    normalized = _normalized_host_port(host, port)
    if normalized is None:
        return None
    host, port = normalized
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    context = ssl._create_unverified_context()
    request = Request(
        f"https://{netloc}/health",
        headers={"User-Agent": "overmind-reachability/1.0", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=max(0.1, float(timeout)), context=context) as response:
            raw = response.read(8192)
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (URLError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    drone_id = str(payload.get("drone_id") or "").strip()
    return drone_id or None


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
