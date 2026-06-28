"""Tiny STUN-like UDP reflector for NAT hole punching.

A Drone that wants to hole-punch a direct UDP path to a peer first needs to learn
the public ``ip:port`` its NAT assigns to the *specific* UDP socket it will punch
from (the mux's reflexive address is a different socket's mapping and can't be
reused). It sends a datagram from that socket to this reflector, which echoes
back the source address it observed. The Drone shares that candidate with the
peer (via MSG_SIGNAL) and both sides send to each other's candidate to punch.

This is intentionally minimal (not full RFC 5389 STUN): one request -> one reply
with the observed address as JSON. It runs as a UDP datagram endpoint alongside
the Edge mux TCP server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional, Tuple


def _noop(_message: str) -> None:
    return None


class StunReflectorProtocol(asyncio.DatagramProtocol):
    """Replies to every datagram with the sender's observed ``{ip, port}``."""

    def __init__(self, log: Callable[[str], None] = _noop) -> None:
        self._log = log
        self._transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self._transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        if self._transport is None:
            return
        reply = json.dumps({"ip": addr[0], "port": int(addr[1])}).encode("utf-8")
        try:
            self._transport.sendto(reply, addr)
        except OSError as error:
            self._log(f"stun reply failed to {addr}: {error}")


async def start_stun_reflector(
    host: str, port: int, *, log: Callable[[str], None] = _noop
) -> asyncio.DatagramTransport:
    """Bind the STUN reflector and return its transport (close() to stop)."""
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: StunReflectorProtocol(log), local_addr=(host, port)
    )
    sockname = transport.get_extra_info("sockname")
    log(f"edge STUN reflector listening on {sockname}")
    return transport
