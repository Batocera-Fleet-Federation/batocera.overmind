"""Response formatting helpers for Overmind API and UI views."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from overmind.managed_configs import merge_managed_configs


def _refresh(data_store: Any) -> None:
    refresh = getattr(data_store, "refresh_persistent_state", None)
    if callable(refresh):
        refresh()


def admin_user_row(user: dict, *, data_store: Any, super_admin_email: str) -> dict:
    _refresh(data_store)
    user_id = user.get("id")
    # One swarm maps to one user; surface its name on the user row. Prefer the
    # swarm the user owns, falling back to any swarm they belong to.
    owned_swarm = next((swarm for swarm in data_store.swarms.values() if swarm.get("owner_id") == user_id), None)
    swarm_name = None
    if owned_swarm:
        swarm_name = owned_swarm.get("name")
    else:
        for swarm_id, members in data_store.swarm_memberships.items():
            if user_id in members:
                swarm_name = (data_store.swarms.get(swarm_id) or {}).get("name")
                break
    return {
        "id": user_id,
        "email": user.get("email"),
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "auth_provider": user.get("auth_provider"),
        "email_verified": bool(user.get("email_verified")),
        "is_active": bool(user.get("is_active")),
        "created_at": user.get("created_at"),
        "swarm_name": swarm_name,
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


def device_response(
    device: dict,
    *,
    data_store: Any,
    offline_threshold_seconds: int,
    include_inventory_counts: bool = True,
    include_emulator_configs: bool = True,
) -> dict:
    """Return the public device shape for the Overmind UI."""
    last_seen = device.get("last_seen")
    cutoff = datetime.utcnow() - timedelta(seconds=offline_threshold_seconds)
    online = False
    if isinstance(last_seen, datetime):
        if last_seen.tzinfo is None and cutoff.tzinfo is not None:
            last_seen = last_seen.replace(tzinfo=cutoff.tzinfo)
        elif last_seen.tzinfo is not None and cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=last_seen.tzinfo)
        online = last_seen >= cutoff
    cert = dict(device.get("certificate") or {})
    cert.pop("private_key", None)
    cert.pop("key", None)
    network = device.get("network") if isinstance(device.get("network"), dict) else {}
    public_ip = network.get("public_ip") or network.get("public")
    public_reachability = device.get("public_reachability") if isinstance(device.get("public_reachability"), dict) else {}
    is_peer_resolvable = getattr(data_store, "is_drone_peer_resolvable", None)
    peer_resolvable = bool(is_peer_resolvable(device.get("device_id") or "")) if callable(is_peer_resolvable) else False
    public_resolvable = peer_resolvable
    get_peer_resolvers = getattr(data_store, "get_peer_resolvers", None)
    peer_resolved_by = get_peer_resolvers(device.get("device_id") or "") if callable(get_peer_resolvers) else []
    response = {
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
        "auto_sync_policy": device.get("auto_sync_policy") or {"enabled": False, "systems": []},
        "last_speed_sample": device.get("last_speed_sample"),
        "log_sources": device.get("log_sources"),
        "game_logs": device.get("game_logs"),
        "token_rotated_at": device.get("token_rotated_at"),
        "api_port": device.get("api_port"),
        "scheme": device.get("scheme") or "https",
        "reachable_url": device.get("reachable_url"),
        "public_resolvable": public_resolvable,
        "peer_resolvable": peer_resolvable,
        "peer_resolved_by": peer_resolved_by,
        "public_reachability": public_reachability,
        "certificate": cert or None,
        "peer_checks": data_store.get_latest_peer_checks(device.get("device_id")) if device.get("device_id") else [],
        "online": online,
        "status": "online" if online else "offline",
    }
    if include_inventory_counts:
        rom_count = 0
        try:
            count_device_roms = getattr(data_store, "count_device_roms", None)
            if callable(count_device_roms):
                rom_count = int(count_device_roms(device.get("device_id")) or 0)
            else:
                rom_count = len(data_store.get_device_roms(device.get("device_id")))
        except Exception:
            rom_count = 0
        # "Games" = distinct titles EmulationStation shows (gamelist entries), as
        # opposed to "ROM files" (rom_count) which counts every file on disk.
        game_count = rom_count
        try:
            count_device_games = getattr(data_store, "count_device_games", None)
            if callable(count_device_games):
                game_count = int(count_device_games(device.get("device_id")) or 0)
        except Exception:
            game_count = rom_count
        response["rom_count"] = rom_count
        response["game_count"] = game_count
    if include_emulator_configs:
        emulator_configs = device.get("emulator_configs")
        try:
            load_configs = getattr(data_store, "get_device_emulator_configs", None)
            if callable(load_configs):
                emulator_configs = load_configs(device.get("device_id"))
        except Exception:
            pass
        # Always present the curated managed-config set (availability + version
        # counts), even before the drone has uploaded anything.
        response["emulator_configs"] = merge_managed_configs(emulator_configs)
    return response
