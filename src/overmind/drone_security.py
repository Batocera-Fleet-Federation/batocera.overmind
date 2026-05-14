"""Drone bearer-token helpers."""

import hashlib
import hmac
import secrets


def generate_drone_token() -> str:
    """Generate the raw token shown once to an Overlord."""
    return secrets.token_urlsafe(32)


def hash_drone_token(token: str) -> str:
    """Hash a raw Drone token for storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_drone_token(raw_token: str, stored_hash: str) -> bool:
    """Compare a raw bearer token with the stored hash without logging it."""
    if not raw_token or not stored_hash:
        return False
    return hmac.compare_digest(hash_drone_token(raw_token), stored_hash)
