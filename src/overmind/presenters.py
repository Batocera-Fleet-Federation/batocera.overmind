"""Response formatting helpers for Overmind API and UI views."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _refresh(data_store: Any) -> None:
    refresh = getattr(data_store, "refresh_persistent_state", None)
    if callable(refresh):
        refresh()


def admin_user_row(user: dict, *, data_store: Any, super_admin_email: str) -> dict:
    _refresh(data_store)
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
    _refresh(data_store)
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
    _refresh(data_store)
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


def profile_response(user: dict) -> dict:
    """Return editable user profile and settings fields."""
    return {
        "id": user["id"],
        "email": user["email"],
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "avatar_data_url": user.get("avatar_data_url"),
        "fleet_settings": user.get("fleet_settings", {}),
        "notification_settings": user.get("notification_settings", {}),
    }


def hive_response(user: dict, *, data_store: Any) -> dict:
    """Return a privacy-safe swarm directory for the Hive view."""
    _refresh(data_store)
    user_swarms = {row["id"]: row for row in data_store.get_user_swarms(user["id"])}
    current_swarm_id = data_store.default_swarm_id(user["id"])
    rows = []
    for swarm in data_store.swarms.values():
        owner = data_store.get_user(swarm.get("owner_id")) or {}
        member = data_store.get_swarm_member(swarm["id"], user["id"])
        rows.append({
            "swarm_id": swarm["id"],
            "swarm_name": public_swarm_name(swarm),
            "owner_username": owner.get("username") or owner.get("full_name") or "Overlord",
            "owner_avatar_data_url": owner.get("avatar_data_url"),
            "drone_count": len([
                device
                for device in data_store.devices.values()
                if device.get("swarm_id") == swarm["id"] and device.get("approval_status", "approved") == "approved"
            ]),
            "current": swarm["id"] == current_swarm_id,
            "viewer_role": member.get("role") if member else None,
            "can_view": bool(member),
        })
    rows.sort(key=lambda row: str(row.get("swarm_name") or "").lower())
    return {
        "hive": rows,
        "swarms": [{"id": row.get("id"), "name": public_swarm_name(row), "role": row.get("role")} for row in user_swarms.values()],
    }


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
    network = device.get("network") if isinstance(device.get("network"), dict) else {}
    public_ip = network.get("public_ip") or network.get("public")
    public_reachability = device.get("public_reachability") if isinstance(device.get("public_reachability"), dict) else {}
    public_resolvable = bool(public_reachability.get("resolvable")) and str(public_reachability.get("public_ip") or "") == str(public_ip or "")
    rom_count = 0
    try:
        rom_count = len(data_store.get_device_roms(device.get("device_id")))
    except Exception:
        rom_count = 0
    return {
        "id": device["id"],
        "device_id": device["device_id"],
        "device_name": device["device_name"],
        "batocera_info": device["batocera_info"],
        "system_info": device.get("system_info") or {},
        "registered_at": device["registered_at"],
        "last_seen": device["last_seen"],
        "network": network,
        "resolved_network": device.get("resolved_network") or {"ipv4": [], "ipv6": []},
        "swarm_connected": bool(device.get("swarm_connected")),
        "rom_systems": device.get("rom_systems") or [],
        "rom_count": rom_count,
        "auto_sync_policy": device.get("auto_sync_policy") or {"enabled": False, "systems": []},
        "last_speed_sample": device.get("last_speed_sample"),
        "emulator_configs": device.get("emulator_configs"),
        "log_sources": device.get("log_sources"),
        "game_logs": device.get("game_logs"),
        "token_rotated_at": device.get("token_rotated_at"),
        "api_port": device.get("api_port"),
        "scheme": device.get("scheme") or "https",
        "reachable_url": device.get("reachable_url"),
        "public_resolvable": public_resolvable,
        "public_reachability": public_reachability,
        "certificate": cert or None,
        "peer_checks": data_store.get_latest_peer_checks(device.get("device_id")) if device.get("device_id") else [],
        "online": online,
        "status": "online" if online else "offline",
    }
