"""Drone authentication for the Edge mux.

A Drone authenticates its persistent connection in the HELLO frame by presenting
its ``device_id`` and its Overmind drone bearer token. The Edge validates that
pair the same way the REST control plane does: look up the device record and
compare the presented token against the stored ``drone_token_hash`` with
:func:`overmind.drone_security.verify_drone_token`.

The device lookup is injected (rather than importing ``postgres_store`` here) so
this module stays dependency-light and unit-testable; ``edge_app`` wires the
real store in.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol

from overmind.drone_security import verify_drone_token

#: Returns the device record (dict with ``drone_token_hash``) for a device id, or
#: None if unknown.
DeviceLookup = Callable[[str], Optional[dict]]
TokenVerifier = Callable[[str, str], bool]


class Authenticator(Protocol):
    def authenticate(self, device_id: str, token: str) -> bool: ...


class DbAuthenticator:
    """Validate a (device_id, token) pair against stored device records."""

    def __init__(
        self,
        lookup_device: DeviceLookup,
        *,
        verify: TokenVerifier = verify_drone_token,
    ) -> None:
        self._lookup_device = lookup_device
        self._verify = verify

    def authenticate(self, device_id: str, token: str) -> bool:
        device_id = str(device_id or "").strip()
        token = str(token or "").strip()
        if not device_id or not token:
            return False
        try:
            device = self._lookup_device(device_id)
        except Exception:
            # Treat a lookup failure (e.g. transient DB error) as auth failure;
            # the Drone will reconnect and retry.
            return False
        if not device:
            return False
        stored_hash = str(device.get("drone_token_hash") or "")
        if not stored_hash:
            return False
        return bool(self._verify(token, stored_hash))


class AllowAllAuthenticator:
    """Authenticator that accepts everything -- for local/dev use only."""

    def authenticate(self, device_id: str, token: str) -> bool:  # noqa: D401
        return bool(str(device_id or "").strip())
