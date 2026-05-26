"""Authorization policy checks shared by Overmind API routes."""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import HTTPException, status


def ensure_active_user(user: dict) -> None:
    if not user.get("is_active") or not user.get("email_verified"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required")


def selected_swarm_id(user: dict, swarm_id: Optional[str] = None, *, data_store: Any) -> str:
    candidate = swarm_id or data_store.default_swarm_id(user["id"])
    if not candidate or not data_store.get_swarm_member(candidate, user["id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Swarm access denied")
    return candidate


def require_swarm_role(user: dict, swarm_id: str, roles: set[str], *, data_store: Any, owner_role: str) -> dict:
    normalized_roles = {owner_role if role == "overlord" else role for role in roles}
    member = data_store.require_swarm_role(swarm_id, user["id"], roles)
    if not member:
        member = data_store.require_swarm_role(swarm_id, user["id"], normalized_roles)
    if not member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient swarm permission")
    return member


def require_device_admin(user: dict, device: dict, *, data_store: Any, role_checker: Callable[[dict, str, set[str]], dict]) -> dict:
    if data_store.user_has_device_admin_claim(user["id"], device.get("device_id")):
        return {"user_id": user["id"], "role": "overlord", "source": "device_ownership_claim"}
    return role_checker(user, device["swarm_id"], {"overlord"})


def require_super_admin(authorization: Optional[str], *, current_user: Callable[[Optional[str]], dict], super_admin_email: str) -> dict:
    user = current_user(authorization)
    if str(user.get("email") or "").strip().lower() != super_admin_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    return user
