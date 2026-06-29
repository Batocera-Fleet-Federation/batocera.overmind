"""In-memory presence registry for Drones connected to this Edge node.

The authoritative *live* set of connections is held in memory on each Edge node.
Optional ``on_connect`` / ``on_disconnect`` hooks let a node mirror presence to
Redis (for cross-node lookup) and to Postgres (for the Overmind UI / last_seen)
without this module depending on either -- keeping it trivially testable.

Cross-node signaling routing (Redis pub/sub) is introduced with the relay in
Phase 2; Phase 1 only needs per-node liveness.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class PresenceEntry:
    device_id: str
    session_id: str
    reflexive_addr: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    lan_addrs: List[str] = field(default_factory=list)
    connected_at: float = 0.0
    last_seen: float = 0.0

    def to_public(self) -> dict:
        return {
            "device_id": self.device_id,
            "session_id": self.session_id,
            "reflexive_addr": self.reflexive_addr,
            "capabilities": list(self.capabilities),
            "lan_addrs": list(self.lan_addrs),
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
        }


PresenceHook = Callable[[PresenceEntry], None]


class PresenceRegistry:
    """Thread-safe registry of currently-connected Drones on this node."""

    def __init__(
        self,
        *,
        on_connect: Optional[PresenceHook] = None,
        on_disconnect: Optional[PresenceHook] = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._entries: Dict[str, PresenceEntry] = {}
        self._lock = threading.Lock()
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._now = now

    def register(self, entry: PresenceEntry) -> None:
        """Record a connection. A new session for an existing device replaces the
        old entry (the previous connection is assumed superseded)."""
        timestamp = self._now()
        if not entry.connected_at:
            entry.connected_at = timestamp
        entry.last_seen = timestamp
        with self._lock:
            self._entries[entry.device_id] = entry
        if self._on_connect is not None:
            self._on_connect(entry)

    def deregister(self, device_id: str, session_id: Optional[str] = None) -> bool:
        """Remove a connection. If ``session_id`` is given, only remove when it
        matches the stored entry, so a stale socket closing after the device has
        already reconnected does not evict the newer session. Returns True if an
        entry was removed."""
        with self._lock:
            entry = self._entries.get(device_id)
            if entry is None:
                return False
            if session_id is not None and entry.session_id != session_id:
                return False
            del self._entries[device_id]
        if self._on_disconnect is not None:
            self._on_disconnect(entry)
        return True

    def touch(self, device_id: str) -> None:
        with self._lock:
            entry = self._entries.get(device_id)
            if entry is not None:
                entry.last_seen = self._now()

    def get(self, device_id: str) -> Optional[PresenceEntry]:
        with self._lock:
            return self._entries.get(device_id)

    def is_online(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._entries

    def online_ids(self) -> List[str]:
        with self._lock:
            return list(self._entries.keys())

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [entry.to_public() for entry in self._entries.values()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
