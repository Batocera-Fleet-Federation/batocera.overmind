"""Short-lived signed tokens that authorize a single relayed transfer.

Minted by the control plane and validated **offline** by the Edge (and the sender
Drone) using the shared ``SECRET_KEY`` -- no DB round-trip needed to authorize a
transfer. Same HMAC-SHA256 ``payload.signature`` scheme as the OAuth-state token
in ``main.py``.

A token binds ``{session_id, from_device, to_device, asset, exp}`` so a leaked
token cannot be replayed for a different asset, peer, or session, and it expires
on its own. ``verify_transfer_token`` returns the payload only if the signature
is valid and the token has not expired.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Mapping, Optional


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def mint_transfer_token(
    secret: str,
    *,
    session_id: str,
    from_device: str,
    to_device: str,
    asset: Mapping[str, Any],
    ttl_seconds: int = 300,
    now: Optional[float] = None,
) -> str:
    """Return a signed transfer token authorizing one transfer."""
    issued = int(now if now is not None else time.time())
    payload = {
        "sid": session_id,
        "from": from_device,
        "to": to_device,
        "asset": dict(asset),
        "exp": issued + int(ttl_seconds),
    }
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload_b64}.{_b64encode(signature)}"


def verify_transfer_token(
    secret: str, token: str, *, now: Optional[float] = None
) -> Optional[dict]:
    """Return the token payload if the signature is valid and it has not expired."""
    try:
        payload_b64, signature_b64 = str(token).split(".", 1)
        expected = hmac.new(
            secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(signature_b64)):
            return None
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error):
        return None
    expiry = payload.get("exp")
    if not isinstance(expiry, (int, float)):
        return None
    if (now if now is not None else time.time()) > expiry:
        return None
    return payload
