"""Network payload normalization for Drone alive messages."""

from __future__ import annotations

import ipaddress
from typing import Any


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
