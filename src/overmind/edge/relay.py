"""Edge relay: forward file bytes between two Drones when no direct path exists.

Both Drones already hold a persistent outbound mux to the Edge. For a relayed
transfer they each send a RELAY_OPEN naming the same transfer ``session_id`` and
their role (``sender`` / ``receiver``). The :class:`RelayHub` pairs the two legs;
once both are present the Edge tells each leg RELAY_READY, then forwards every
relay DATA frame from one leg to the other -- streamed chunk by chunk, never
buffering the whole file, and optionally bandwidth-limited.

The Edge does not parse AssetFetch; it only routes opaque DATA frames by session
id, so relayed payloads can be end-to-end encrypted between the Drones without
the Edge being able to read them.

No locks: the Edge runs a single-threaded asyncio loop, and every method mutates
``_sessions`` synchronously (awaits happen only after the needed references are
captured), so cooperative scheduling already serializes access.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from . import protocol

#: ``async send(frame_bytes)`` -- writes raw frame bytes to a leg's mux connection.
LegSend = Callable[[bytes], Awaitable[None]]

ROLE_SENDER = "sender"
ROLE_RECEIVER = "receiver"
_ROLES = (ROLE_SENDER, ROLE_RECEIVER)
_OTHER_ROLE = {ROLE_SENDER: ROLE_RECEIVER, ROLE_RECEIVER: ROLE_SENDER}


class RateLimiter:
    """Token-bucket limiter. ``rate_bps <= 0`` means unlimited (acquire is a no-op)."""

    def __init__(
        self,
        rate_bps: float,
        *,
        capacity: Optional[float] = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._rate = max(0.0, float(rate_bps))
        # Allow a burst at least as large as one max frame so a single chunk can
        # never exceed capacity (which would otherwise loop forever).
        floor = float(protocol.MAX_FRAME_PAYLOAD)
        self._capacity = float(capacity) if capacity is not None else max(self._rate, floor)
        self._tokens = self._capacity
        self._now = now
        self._sleep = sleep
        self._last = now()

    async def acquire(self, amount: int) -> None:
        if self._rate <= 0 or amount <= 0:
            return
        want = min(float(amount), self._capacity)
        while True:
            now = self._now()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= want:
                self._tokens -= want
                return
            await self._sleep((want - self._tokens) / self._rate)


@dataclass
class RelayLeg:
    role: str
    device_id: str
    conn_id: int
    send: LegSend


@dataclass
class RelaySession:
    session_id: str
    limiter: RateLimiter
    legs: Dict[str, RelayLeg] = field(default_factory=dict)

    def is_ready(self) -> bool:
        return ROLE_SENDER in self.legs and ROLE_RECEIVER in self.legs

    def leg_for_conn(self, conn_id: int) -> Optional[RelayLeg]:
        return next((leg for leg in self.legs.values() if leg.conn_id == conn_id), None)

    def peer_of_conn(self, conn_id: int) -> Optional[RelayLeg]:
        leg = self.leg_for_conn(conn_id)
        if leg is None:
            return None
        return self.legs.get(_OTHER_ROLE[leg.role])


class RelayHub:
    def __init__(
        self,
        *,
        bw_limit_bps: float = 0,
        limiter_factory: Optional[Callable[[], RateLimiter]] = None,
    ) -> None:
        self._sessions: Dict[str, RelaySession] = {}
        self._bw_limit_bps = bw_limit_bps
        self._limiter_factory = limiter_factory or (lambda: RateLimiter(self._bw_limit_bps))

    def open_leg(
        self, session_id: str, role: str, device_id: str, conn_id: int, send: LegSend
    ) -> RelaySession:
        if role not in _ROLES:
            raise ValueError(f"invalid relay role: {role!r}")
        session = self._sessions.get(session_id)
        if session is None:
            session = RelaySession(session_id, self._limiter_factory())
            self._sessions[session_id] = session
        session.legs[role] = RelayLeg(role, device_id, conn_id, send)
        return session

    async def forward(self, session_id: str, conn_id: int, data_payload: bytes) -> bool:
        """Forward a relay DATA frame payload from one leg to its peer.

        Returns False if there is no paired peer yet (the frame is dropped --
        the receiver waits for RELAY_READY before sending, so this is rare).
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False
        peer = session.peer_of_conn(conn_id)
        if peer is None:
            return False
        limiter = session.limiter
        # Limit on the data bytes (exclude the session-id prefix).
        await limiter.acquire(max(0, len(data_payload) - protocol.RELAY_SESSION_ID_LEN))
        try:
            await peer.send(protocol.encode_frame(protocol.FRAME_DATA, data_payload))
        except (ConnectionError, OSError):
            # Peer connection is gone; don't let that tear down the forwarding
            # side. The peer's own handler will clean up and notify via close.
            return False
        return True

    def close_session(self, session_id: str) -> List[RelayLeg]:
        session = self._sessions.pop(session_id, None)
        return list(session.legs.values()) if session else []

    def drop_connection(self, conn_id: int) -> List[Tuple[str, RelayLeg]]:
        """Remove all legs on a dropped connection; return (session_id, peer_leg)
        pairs to notify with RELAY_CLOSE."""
        notify: List[Tuple[str, RelayLeg]] = []
        for session_id in list(self._sessions):
            session = self._sessions[session_id]
            roles_here = [role for role, leg in session.legs.items() if leg.conn_id == conn_id]
            if not roles_here:
                continue
            for role in roles_here:
                peer = session.legs.get(_OTHER_ROLE[role])
                if peer is not None and peer.conn_id != conn_id:
                    notify.append((session_id, peer))
                session.legs.pop(role, None)
            if not session.legs:
                self._sessions.pop(session_id, None)
        return notify

    def session_count(self) -> int:
        return len(self._sessions)
