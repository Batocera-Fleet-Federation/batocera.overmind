"""Response formatting helpers for Overmind API and UI views."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def admin_user_row(user: dict, *, data_store: Any, super_admin_email: str) -> dict:
    user_id = user.get("id")
    return {
        "id": user_id,
        "email": user.get("email"),
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "auth_provider": user.get("auth_provider"),
        "email_verified": bool(user.get("email_verified")),
        "is_active": bool(user.get("is_active")),
        "created_at": user.get("created_at"),
        "swarm_count": sum(1 for members in data_store.swarm_memberships.values() if user_id in members),
        "owned_swarm_count": sum(1 for swarm in data_store.swarms.values() if swarm.get("owner_id") == user_id),
        "drone_count": sum(1 for device in data_store.devices.values() if device.get("user_id") == user_id),
        "is_super_admin": str(user.get("email") or "").strip().lower() == super_admin_email,
    }


def admin_swarm_row(swarm: dict, *, data_store: Any) -> dict:
    owner = data_store.get_user(swarm.get("owner_id")) or {}
    swarm_id = swarm.get("id")
    return {
        "id": swarm_id,
        "name": swarm.get("name"),
        "owner_id": swarm.get("owner_id"),
        "owner_email": owner.get("email"),
        "created_at": swarm.get("created_at"),
        "member_count": len(data_store.swarm_memberships.get(swarm_id, {})),
        "drone_count": sum(1 for device in data_store.devices.values() if device.get("swarm_id") == swarm_id),
    }


def admin_drone_row(device: dict, *, data_store: Any) -> dict:
    owner = data_store.get_user(device.get("user_id")) or {}
    swarm = data_store.swarms.get(device.get("swarm_id")) or {}
    return {
        "id": device.get("id"),
        "device_id": device.get("device_id"),
        "device_name": device.get("device_name"),
        "user_id": device.get("user_id"),
        "owner_email": owner.get("email"),
        "swarm_id": device.get("swarm_id"),
        "swarm_name": swarm.get("name"),
        "approval_status": device.get("approval_status", "approved"),
        "swarm_connected": bool(device.get("swarm_connected")),
        "registered_at": device.get("registered_at"),
        "last_seen": device.get("last_seen"),
        "reachable_url": device.get("reachable_url"),
    }


def public_swarm_name(swarm: dict) -> str:
    name = str(swarm.get("name") or "Swarm")
    return "Personal Swarm" if "@" in name else name


def device_response(device: dict, *, data_store: Any, offline_threshold_seconds: int) -> dict:
    """Return the public device shape for the Overmind UI."""
    last_seen = device.get("last_seen")
    online = False
    try:
        online = bool(last_seen and last_seen >= datetime.utcnow() - timedelta(seconds=offline_threshold_seconds))
    except Exception:
        online = False
    cert = dict(device.get("certificate") or {})
    cert.pop("private_key", None)
    cert.pop("key", None)
    return {
        "id": device["id"],
        "device_id": device["device_id"],
        "device_name": device["device_name"],
        "batocera_info": device["batocera_info"],
        "system_info": device.get("system_info") or {},
        "registered_at": device["registered_at"],
        "last_seen": device["last_seen"],
        "network": device.get("network") or {},
        "resolved_network": device.get("resolved_network") or {"ipv4": [], "ipv6": []},
        "swarm_connected": bool(device.get("swarm_connected")),
        "rom_systems": device.get("rom_systems") or [],
        "auto_sync_policy": device.get("auto_sync_policy") or {"enabled": False, "systems": []},
        "last_speed_sample": device.get("last_speed_sample"),
        "emulator_configs": device.get("emulator_configs"),
        "log_sources": device.get("log_sources"),
        "game_logs": device.get("game_logs"),
        "token_rotated_at": device.get("token_rotated_at"),
        "api_port": device.get("api_port"),
        "scheme": device.get("scheme") or "https",
        "reachable_url": device.get("reachable_url"),
        "certificate": cert or None,
        "peer_checks": data_store.get_latest_peer_checks(device.get("device_id")) if device.get("device_id") else [],
        "online": online,
        "status": "online" if online else "offline",
    }
