"""Redis-backed cache for Overmind. Falls back silently when Redis is unavailable."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("overmind.cache")
_client = None


def _get_client():
    global _client
    if _client is None:
        url = (os.getenv("OVERMIND_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()
        if url:
            try:
                import redis
                _client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_timeout=0.5,
                    socket_connect_timeout=0.5,
                )
            except Exception as exc:
                logger.warning("Redis client init failed: %s", exc)
    return _client


def _key(*parts) -> str:
    return "overmind:" + ":".join(str(p) for p in parts)


def _hash(*parts) -> str:
    return hashlib.md5(
        json.dumps(parts, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def get(key: str) -> Optional[Any]:
    try:
        client = _get_client()
        if not client:
            return None
        raw = client.get(key)
        return json.loads(raw) if raw is not None else None
    except Exception:
        return None


def set(key: str, value: Any, ttl: int = 30) -> None:
    try:
        client = _get_client()
        if not client:
            return
        client.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass


_LOG_TAIL_MAX = 1000


def append_log_tail(stream: str, lines: list[str]) -> None:
    """Append lines to a shared, capped log buffer. Best-effort; never raises or logs.

    Lets the admin runtime-logs view stay consistent across Lambda instances instead
    of flickering as different instances (each with their own in-memory capture) serve
    successive polls.
    """
    try:
        client = _get_client()
        if not client or not lines:
            return
        key = _key("logtail", stream)
        pipe = client.pipeline()
        for line in lines:
            pipe.rpush(key, str(line))
        pipe.ltrim(key, -_LOG_TAIL_MAX, -1)
        pipe.expire(key, 86400)
        pipe.execute()
    except Exception:
        pass


def read_log_tail(stream: str, max_lines: int = _LOG_TAIL_MAX) -> Optional[list[str]]:
    """Return the shared log tail for a stream, or None if Redis is unavailable."""
    try:
        client = _get_client()
        if not client:
            return None
        return client.lrange(_key("logtail", stream), -int(max_lines), -1)
    except Exception:
        return None


def delete_pattern(pattern: str) -> None:
    try:
        client = _get_client()
        if not client:
            return
        keys = client.keys(pattern)
        if keys:
            client.delete(*keys)
    except Exception:
        pass


# ── Typed key builders ───────────────────────────────────────────────────────

def master_assets_key(device_ids: list[str], asset_type: str, **kwargs) -> str:
    return _key("ma", _hash(sorted(device_ids), asset_type, kwargs))


def user_devices_key(user_id: str, swarm_id: Optional[str] = None) -> str:
    return _key("ud", user_id, swarm_id or "")


def count_assets_key(device_id: str, asset_type: str) -> str:
    return _key("ca", device_id, asset_type)


def rom_systems_key(device_ids: list[str]) -> str:
    return _key("rs", _hash(sorted(device_ids)))


# ── Invalidation helpers ─────────────────────────────────────────────────────

def invalidate_user_devices(user_id: str) -> None:
    delete_pattern(_key("ud", user_id, "*"))


def invalidate_master_assets() -> None:
    delete_pattern(_key("ma", "*"))


def invalidate_asset_counts(device_id: str) -> None:
    delete_pattern(_key("ca", device_id, "*"))
