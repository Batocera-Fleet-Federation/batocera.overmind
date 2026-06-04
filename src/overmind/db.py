"""Overmind repository facade with optional PostgreSQL persistence."""

import os
import hashlib
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from overmind import auth
from overmind.drone_security import generate_drone_token, hash_drone_token, verify_drone_token
from overmind.networking import resolve_reported_network
from overmind.postgres_store import postgres_store
from overmind import notification_delivery
from overmind.notification_delivery import DEFAULT_NOTIFICATION_TYPES
from overmind.models import User, Device, RomMetadata, GamePlay
from overmind.device_snapshots import (
    _is_excluded_emulator_config_path,
    append_game_log_sessions,
    merge_emulator_configs,
    merge_game_logs,
    merge_log_sources,
    merge_rom_metadata_hash_patch,
)
try:
    from overmind import cache as _cache
except Exception:
    _cache = None  # type: ignore[assignment]

ROM_INVENTORY_FINGERPRINT_ALGORITHM = "rom-inventory-sha256-v1"


def _normalize_rom_inventory_path(value: object) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./").lower()


def compute_rom_inventory_fingerprint(roms: list[dict]) -> str:
    rows = []
    for row in roms or []:
        if not isinstance(row, dict):
            continue
        system = str(row.get("system") or row.get("system_name") or "").strip().lower()
        path = _normalize_rom_inventory_path(
            row.get("file_path")
            or row.get("relative_path")
            or row.get("rom_path")
            or row.get("rom_file")
            or row.get("rom_name")
            or row.get("name")
        )
        if not system or not path:
            continue
        entry_type = str(row.get("entry_type") or "file").strip().lower()
        md5_value = str(row.get("rom_md5") or row.get("md5") or row.get("hash") or "").strip().lower()
        file_size = row.get("file_size") if row.get("file_size") is not None else row.get("byte_count")
        size_value = str(int(file_size)) if isinstance(file_size, (int, float)) else str(file_size or "").strip()
        rows.append("\t".join((system, path, entry_type, md5_value, size_value)))
    digest = hashlib.sha256()
    for value in sorted(rows):
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()

class OvermindDatabase:
    """Application repository backed by PostgreSQL as the source of truth.

    The process may materialize rows briefly while serving a request, but public
    repository operations refresh from PostgreSQL before they read or mutate
    state so workers do not rely on process-local cached data.
    """
    _PERSISTED_FIELDS = (
        "users",
        "user_by_email",
        "devices",
        "device_admin_claims",
        "user_devices",
        "roms",
        "bios",
        "artwork",
        "gamelogs",
        "log_sources",
        "device_actions",
        "speed_samples",
        "device_events",
        "peer_checks",
        "integration_tokens",
        "approved_drone_tokens",
        "rom_sync_activity",
        "download_states",
        "pending_drone_connections",
        "email_verifications",
        "password_resets",
        "swarms",
        "swarm_memberships",
        "invitations",
        "notifications",
    )
    _PERSIST_AFTER_METHODS = {
        "create_user",
        "update_user_profile",
        "update_user_fleet_settings",
        "update_user_notification_settings",
        "create_integration_token",
        "claim_integration_token",
        "verify_integration_token",
        "revoke_integration_token",
        "create_pending_drone_connection",
        "accept_pending_drone_connection",
        "deny_pending_drone_connection",
        "create_device",
        "add_device_admin_claim",
        "update_device_last_seen",
        "update_device_public_reachability",
        "update_device_rom_inventory_fingerprint",
        "rotate_device_token",
        "set_device_authorization_token",
        "update_device_auto_sync_policy",
        "update_device_name",
        "delete_device",
        "create_device_action",
        "claim_next_device_action",
        "claim_pending_device_actions",
        "complete_device_action",
        "store_action_result",
        "store_rom_metadata",
        "add_speed_sample",
        "add_device_event",
        "add_peer_checks",
        "add_roms",
        "add_bios",
        "add_artwork",
        "add_rom_sync_activity",
        "store_download_state",
        "log_gameplay",
        "create_email_verification",
        "verify_email_code",
        "create_password_reset",
        "consume_password_reset",
        "create_swarm",
        "invite_to_swarm",
        "rotate_pending_invitation",
        "remove_pending_invitation",
        "accept_invitation",
        "accept_invitation_for_user",
        "remove_swarm_member",
        "update_swarm_member_role",
        "update_swarm_name",
        "admin_delete_device",
        "admin_delete_swarm",
        "admin_delete_user",
        "add_swarm_notification",
        "mark_notifications_read",
        "dismiss_notifications",
    }
    _SKIP_AUTO_REFRESH_METHODS = {
        "get_user_by_email",
        "get_user",
        "user_exists",
        "username_exists",
        "get_or_create_social_user",
    }

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if (
            callable(attr)
            and not name.startswith("_")
            and name not in {"refresh_persistent_state"}
        ):
            def persisted_method(*args, **kwargs):
                depth = object.__getattribute__(self, "_operation_depth")
                is_outermost = depth == 0
                if is_outermost:
                    if not getattr(postgres_store, "url", None) and name not in object.__getattribute__(self, "_SKIP_AUTO_REFRESH_METHODS"):
                        object.__getattribute__(self, "_refresh_from_source_of_truth")()
                    object.__setattr__(self, "_operation_dirty", False)
                object.__setattr__(self, "_operation_depth", depth + 1)
                try:
                    result = attr(*args, **kwargs)
                    if name in object.__getattribute__(self, "_PERSIST_AFTER_METHODS"):
                        object.__setattr__(self, "_operation_dirty", True)
                    return result
                finally:
                    object.__setattr__(self, "_operation_depth", depth)
                    if is_outermost and object.__getattribute__(self, "_operation_dirty"):
                        object.__getattribute__(self, "_persist_state")()
                        object.__setattr__(self, "_operation_dirty", False)
            return persisted_method
        return attr
    
    def __init__(self):
        # Storage
        self.users: Dict[str, dict] = {}
        self.user_by_email: Dict[str, str] = {}  # email -> user_id
        self.devices: Dict[str, dict] = {}
        self.device_admin_claims: Dict[str, list] = {}  # internal device_id -> user_ids with direct admin claim
        self.user_devices: Dict[str, List[str]] = {}  # user_id -> list of device_ids
        self.roms: Dict[str, list] = {}  # device_id -> list of roms
        self.bios: Dict[str, list] = {}  # device_id -> list of bios files
        self.artwork: Dict[str, list] = {}  # device_id -> gamelist artwork availability rows
        self._asset_inventory_staging: Dict[str, dict] = {}
        self.gamelogs: Dict[str, list] = {}  # device_id -> list of game plays
        self.log_sources: Dict[str, dict] = {}  # internal device_id -> persistent Drone logs
        self.device_actions: Dict[str, list] = {}  # internal device_id -> queued actions
        self.speed_samples: Dict[str, list] = {}  # internal device_id -> speed samples
        self.device_events: Dict[str, list] = {}  # internal device_id -> telemetry events
        self.peer_checks: Dict[str, list] = {}  # internal device_id -> peer check results
        self.integration_tokens: Dict[str, list] = {}
        self.approved_drone_tokens: Dict[str, str] = {}
        self.rom_sync_activity: Dict[str, list] = {}
        self.download_states: Dict[str, dict] = {}
        self.pending_drone_connections: Dict[str, dict] = {}
        self.email_verifications: Dict[str, dict] = {}
        self.password_resets: Dict[str, dict] = {}
        self.swarms: Dict[str, dict] = {}
        self.swarm_memberships: Dict[str, Dict[str, dict]] = {}
        self.invitations: Dict[str, dict] = {}
        self.notifications: Dict[str, list] = {}
        self._operation_depth = 0
        self._operation_dirty = False
        self._last_source_refresh_at = 0.0
        self._load_persistent_state()

    def _state_snapshot(self) -> dict:
        skipped = {"roms", "bios", "artwork"} if self._asset_store_enabled() else set()
        return {field: getattr(self, field) for field in self._PERSISTED_FIELDS if field not in skipped}

    def _load_persistent_state(self) -> None:
        if getattr(postgres_store, "url", None):
            return
        state = postgres_store.load_app_state()
        if not isinstance(state, dict):
            return
        for field in self._PERSISTED_FIELDS:
            if self._asset_store_enabled() and field in {"roms", "bios", "artwork"}:
                continue
            value = state.get(field)
            if isinstance(value, dict):
                setattr(self, field, value)

    def refresh_persistent_state(self) -> None:
        """Reload shared persisted state after runtime database config is available."""
        if getattr(postgres_store, "url", None):
            return
        self._load_persistent_state()

    def refresh_admin_overview_state(self) -> None:
        if postgres_store.available():
            state = postgres_store.load_admin_overview_state()
            if isinstance(state, dict):
                for field in ("users", "user_by_email", "swarms", "swarm_memberships", "devices", "user_devices"):
                    value = state.get(field)
                    if isinstance(value, dict):
                        setattr(self, field, value)
                return
        self.refresh_persistent_state()

    @staticmethod
    def _is_lambda_runtime() -> bool:
        return (os.getenv("OVERMIND_RUNTIME") or "").strip().lower() == "lambda" or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))

    def _source_refresh_min_seconds(self) -> float:
        default = "5" if self._is_lambda_runtime() else "0"
        try:
            return max(0.0, float(os.getenv("OVERMIND_STATE_REFRESH_MIN_SECONDS", default)))
        except (TypeError, ValueError):
            return float(default)

    def _refresh_from_source_of_truth(self) -> None:
        if getattr(postgres_store, "url", None):
            return
        min_seconds = self._source_refresh_min_seconds()
        now = time.monotonic()
        if min_seconds and self._last_source_refresh_at and now - self._last_source_refresh_at < min_seconds:
            return
        if postgres_store.available():
            self._load_persistent_state()
            self._last_source_refresh_at = now

    def _persist_state(self) -> None:
        try:
            postgres_store.store_app_state(self._state_snapshot())
        except Exception as error:
            print(f"Overmind PostgreSQL state persistence failed: {error}")

    def _store_game_log_sessions(self, device_id: str, sessions: list) -> None:
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return
        internal_id = internal_device["id"]
        existing = self.gamelogs.get(internal_id, [])
        self.gamelogs[internal_id] = append_game_log_sessions(existing, device_id, sessions)

    def _device_label(self, device: Optional[dict], fallback: Optional[str] = None) -> str:
        if not device:
            return str(fallback or "")
        info = device.get("system_info") if isinstance(device.get("system_info"), dict) else {}
        return str(device.get("device_name") or info.get("hostname") or device.get("device_id") or fallback or "")

    def _notification_devices(self, devices: list) -> list:
        rows = []
        for item in devices:
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("device_id") or item.get("drone_id") or "").strip()
            if not device_id:
                continue
            device = self.get_device_by_device_id(device_id) or item
            rows.append({"device_id": device_id, "device_name": self._device_label(device, device_id)})
        return rows

    def add_swarm_notification(self, swarm_id: str, event_type: str, title: str, message: str, details: Optional[dict] = None, actor_user_id: Optional[str] = None) -> dict:
        if not swarm_id:
            return {}
        if event_type in {"drone_online", "drone_offline"} and self._recent_drone_status_notification_exists(swarm_id, event_type, details):
            return {}
        entry = {
            "id": str(uuid.uuid4()),
            "swarm_id": swarm_id,
            "event_type": event_type,
            "title": title,
            "message": message,
            "short_description": title,
            "full_description": message,
            "details": details if isinstance(details, dict) else {},
            "actor_user_id": actor_user_id,
            "created_at": datetime.utcnow(),
            "read_by": {},
            "delivery_pending": event_type not in notification_delivery.REALTIME_EVENT_TYPES,
        }
        bucket = self.notifications.setdefault(swarm_id, [])
        bucket.append(entry)
        overflow = max(0, len(bucket) - 500)
        removable_indexes = [
            index for index, notification in enumerate(bucket)
            if notification.get("delivery_pending") is not True
        ][:overflow]
        for index in reversed(removable_indexes):
            bucket.pop(index)
        if event_type in notification_delivery.REALTIME_EVENT_TYPES:
            notification_delivery.deliver_notification(self, entry)
            entry["delivery_completed_at"] = datetime.utcnow()
        return entry

    def _recent_drone_status_notification_exists(self, swarm_id: str, event_type: str, details: Optional[dict]) -> bool:
        device = details.get("device") if isinstance(details, dict) else None
        device_id = str(device.get("device_id") or "").strip() if isinstance(device, dict) else ""
        if not device_id:
            return False
        try:
            dedupe_seconds = int(os.getenv("OVERMIND_STATUS_NOTIFICATION_DEDUPE_SECONDS", "900"))
        except ValueError:
            dedupe_seconds = 900
        if dedupe_seconds <= 0:
            return False
        cutoff = datetime.utcnow() - timedelta(seconds=dedupe_seconds)
        for notification in reversed(self.notifications.get(swarm_id, [])):
            if notification.get("event_type") != event_type:
                continue
            created_at = notification.get("created_at")
            if isinstance(created_at, datetime) and created_at < cutoff:
                break
            notification_device = notification.get("details", {}).get("device") if isinstance(notification.get("details"), dict) else None
            if isinstance(notification_device, dict) and str(notification_device.get("device_id") or "").strip() == device_id:
                return True
        return False

    def claim_pending_notifications_for_delivery(self, notification_ids: list[str], limit: int = 0) -> Optional[set[str]]:
        claimed = postgres_store.claim_pending_notifications(notification_ids, limit=limit)
        if claimed is None:
            return None
        claimed_ids = set(claimed)
        if not claimed_ids:
            return set()
        for rows in self.notifications.values():
            for notification in rows:
                notification_id = notification.get("id")
                if notification_id in claimed:
                    notification["delivery_pending"] = False
                    notification["delivery_completed_at"] = claimed[notification_id]
        return claimed_ids

    def get_user_notifications(self, user_id: str, limit: int = 50) -> List[dict]:
        if postgres_store.available():
            relational = postgres_store.list_user_notifications(user_id, limit=limit)
            if relational is not None:
                return relational
        swarm_ids = {row["id"] for row in self.get_user_swarms(user_id)}
        rows = []
        for swarm_id in swarm_ids:
            swarm = self.swarms.get(swarm_id) or {}
            for entry in self.notifications.get(swarm_id, []):
                read_by = entry.get("read_by") if isinstance(entry.get("read_by"), dict) else {}
                dismissed_by = entry.get("dismissed_by") if isinstance(entry.get("dismissed_by"), dict) else {}
                if dismissed_by.get(user_id):
                    continue
                rows.append({
                    **entry,
                    "swarm_name": swarm.get("name"),
                    "short_description": entry.get("short_description") or entry.get("title") or "",
                    "full_description": entry.get("full_description") or entry.get("message") or "",
                    "read": bool(read_by.get(user_id)),
                })
        rows.sort(key=lambda row: row.get("created_at") or datetime.min, reverse=True)
        return rows[: max(1, int(limit))]

    def mark_notifications_read(self, user_id: str, notification_ids: Optional[list] = None) -> int:
        if postgres_store.available():
            relational = postgres_store.mark_notifications_read(user_id, notification_ids)
            if relational is not None:
                return relational
        allowed_swarms = {row["id"] for row in self.get_user_swarms(user_id)}
        wanted = {str(item) for item in notification_ids or [] if str(item)}
        count = 0
        now = datetime.utcnow()
        for swarm_id in allowed_swarms:
            for entry in self.notifications.get(swarm_id, []):
                if wanted and entry.get("id") not in wanted:
                    continue
                read_by = entry.setdefault("read_by", {})
                if not read_by.get(user_id):
                    read_by[user_id] = now
                    count += 1
        return count

    def dismiss_notifications(self, user_id: str, notification_ids: Optional[list] = None) -> int:
        if postgres_store.available():
            relational = postgres_store.dismiss_notifications(user_id, notification_ids)
            if relational is not None:
                return relational
        allowed_swarms = {row["id"] for row in self.get_user_swarms(user_id)}
        wanted = {str(item) for item in notification_ids or [] if str(item)}
        count = 0
        now = datetime.utcnow()
        for swarm_id in allowed_swarms:
            for entry in self.notifications.get(swarm_id, []):
                if wanted and entry.get("id") not in wanted:
                    continue
                dismissed_by = entry.setdefault("dismissed_by", {})
                if dismissed_by.get(user_id):
                    continue
                dismissed_by[user_id] = now
                entry.setdefault("read_by", {})[user_id] = now
                count += 1
        return count

    def _master_snapshot_for_swarm(self, swarm_id: str, asset_type: str) -> Dict[tuple, dict]:
        rows: Dict[tuple, dict] = {}
        devices = [device for device in self.devices.values() if device.get("swarm_id") == swarm_id and device.get("approval_status", "approved") == "approved"]
        for device in devices:
            internal_id = device.get("id")
            if asset_type == "rom":
                for rom in self._asset_rows_for_device_internal(internal_id, "rom"):
                    key = self._rom_key(rom)
                    if not key[1]:
                        continue
                    row = rows.setdefault(key, {
                        "asset_type": "rom",
                        "system_name": rom.get("system_name"),
                        "name": rom.get("rom_name") or rom.get("file_path"),
                        "path": rom.get("file_path") or rom.get("rom_name"),
                        "md5": rom.get("rom_md5"),
                        "devices": [],
                    })
                    row["devices"].append({"device_id": device.get("device_id"), "device_name": self._device_label(device)})
            elif asset_type == "bios":
                for bios in self._asset_rows_for_device_internal(internal_id, "bios"):
                    key = self._bios_key(bios)
                    if not key[1]:
                        continue
                    row = rows.setdefault(key, {
                        "asset_type": "bios",
                        "name": bios.get("bios_name") or bios.get("file_path"),
                        "path": bios.get("file_path") or bios.get("bios_name"),
                        "md5": bios.get("bios_md5") or bios.get("md5"),
                        "devices": [],
                    })
                    row["devices"].append({"device_id": device.get("device_id"), "device_name": self._device_label(device)})
            elif asset_type == "artwork":
                for artwork in self._asset_rows_for_device_internal(internal_id, "artwork"):
                    for artwork_type in artwork.get("artwork_types", []):
                        key = self._artwork_key(artwork, artwork_type)
                        if not key[0] or not key[1] or not key[2]:
                            continue
                        row = rows.setdefault(key, {
                            "asset_type": "artwork",
                            "system_name": artwork.get("system_name") or artwork.get("system"),
                            "name": artwork.get("rom_name") or artwork.get("rom_path"),
                            "path": artwork.get("rom_path") or artwork.get("file_path"),
                            "artwork_type": artwork_type,
                            "devices": [],
                        })
                        row["devices"].append({"device_id": device.get("device_id"), "device_name": self._device_label(device)})
        return rows

    def _asset_store_enabled(self) -> bool:
        return postgres_store.assets_enabled()

    def _asset_rows_for_device_internal(self, internal_id: str, asset_type: str, system_name: Optional[str] = None) -> List[dict]:
        if self._asset_store_enabled():
            return postgres_store.list_device_assets(internal_id, asset_type, system_name=system_name)
        bucket = {"rom": self.roms, "bios": self.bios, "artwork": self.artwork}.get(asset_type, {})
        rows = list(bucket.get(internal_id, []))
        if system_name and asset_type == "rom":
            rows = [row for row in rows if str(row.get("system_name") or "").lower() == system_name.lower()]
        return rows

    def _asset_rows_for_devices(self, devices: List[dict], asset_type: str) -> List[dict]:
        if self._asset_store_enabled():
            return postgres_store.list_assets_for_devices([device.get("id") for device in devices], asset_type)
        rows = []
        bucket = {"rom": self.roms, "bios": self.bios, "artwork": self.artwork}.get(asset_type, {})
        for device in devices:
            for row in bucket.get(device.get("id"), []):
                rows.append({**row, "_device_internal_id": device.get("id")})
        return rows

    def _clear_device_assets(self, internal_id: str) -> None:
        if self._asset_store_enabled():
            postgres_store.clear_device_assets(internal_id)
        self.roms[internal_id] = []
        self.bios[internal_id] = []
        self.artwork[internal_id] = []

    def clear_device_asset_metadata(self, user_id: str, device_id: str) -> bool:
        device = self.get_device_by_device_id(device_id)
        if not device or device.get("user_id") != user_id:
            return False
        self._clear_device_assets(device["id"])
        self._asset_inventory_staging.pop(device["id"], None)
        device["rom_metadata"] = {}
        device["rom_systems"] = []
        return True

    def _store_asset_inventory_chunk(
        self,
        device_id: str,
        device: dict,
        metadata: dict,
        grouped: Dict[str, list],
    ) -> None:
        internal_id = device["id"]
        inventory_id = str(metadata.get("inventory_id") or "").strip()
        if not inventory_id:
            return
        chunk_index = int(metadata.get("chunk_index") or 0)
        complete = bool(metadata.get("inventory_complete"))
        cleaned = {"rom": [], "bios": [], "artwork": []}
        for system_name, rows in grouped.items():
            cleaned["rom"].extend(self._clean_rom_rows(device_id, system_name, rows))
        if isinstance(metadata.get("bios"), list):
            cleaned["bios"] = self._clean_bios_rows(device_id, metadata.get("bios") or [])
        if isinstance(metadata.get("artwork"), list):
            cleaned["artwork"] = self._clean_artwork_rows(device_id, metadata.get("artwork") or [])

        if self._asset_store_enabled():
            if chunk_index == 0:
                postgres_store.begin_device_asset_inventory(internal_id, inventory_id)
            for asset_type, rows in cleaned.items():
                postgres_store.stage_device_assets(internal_id, device_id, inventory_id, asset_type, rows)
            if complete:
                postgres_store.publish_device_asset_inventory(internal_id, inventory_id)
            return

        staged = self._asset_inventory_staging.get(internal_id)
        if chunk_index == 0 or not staged or staged.get("inventory_id") != inventory_id:
            staged = {"inventory_id": inventory_id, "rom": [], "bios": [], "artwork": []}
            self._asset_inventory_staging[internal_id] = staged
        for asset_type, rows in cleaned.items():
            staged[asset_type].extend(rows)
        if complete:
            self.roms[internal_id] = staged["rom"]
            self.bios[internal_id] = staged["bios"]
            self.artwork[internal_id] = staged["artwork"]
            self._asset_inventory_staging.pop(internal_id, None)

    def _delete_asset_rows(self, device_id: str, asset_type: str, rows: list) -> None:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return
        internal_id = device["id"]
        if self._asset_store_enabled():
            postgres_store.delete_device_asset_rows(internal_id, asset_type, rows)
            return
        if asset_type == "rom":
            keys = {
                (
                    str(row.get("system") or row.get("system_name") or "").strip().lower(),
                    str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or "").replace("\\", "/").strip().lstrip("./").lower(),
                )
                for row in rows if isinstance(row, dict)
            }
            self.roms[internal_id] = [
                row for row in self.roms.get(internal_id, [])
                if (
                    str(row.get("system_name") or "").strip().lower(),
                    str(row.get("file_path") or "").replace("\\", "/").strip().lstrip("./").lower(),
                ) not in keys
            ]
        elif asset_type == "bios":
            keys = {self._bios_key(row) for row in rows if isinstance(row, dict)}
            self.bios[internal_id] = [row for row in self.bios.get(internal_id, []) if self._bios_key(row) not in keys]
        elif asset_type == "artwork":
            keys = {
                (
                    str(row.get("system_name") or row.get("system") or "").strip().lower(),
                    str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower(),
                )
                for row in rows if isinstance(row, dict)
            }
            self.artwork[internal_id] = [
                row for row in self.artwork.get(internal_id, [])
                if (
                    str(row.get("system_name") or row.get("system") or "").strip().lower(),
                    str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower(),
                ) not in keys
            ]

    def _notify_master_list_changes(self, swarm_id: Optional[str], before: Dict[str, Dict[tuple, dict]], after: Dict[str, Dict[tuple, dict]]) -> None:
        if not swarm_id:
            return
        labels = {"rom": "ROM", "bios": "BIOS", "artwork": "artwork"}
        for asset_type, label in labels.items():
            previous = before.get(asset_type) or {}
            current = after.get(asset_type) or {}
            for key in sorted(set(current) - set(previous), key=str):
                row = current[key]
                devices = row.get("devices") or []
                names = ", ".join(device.get("device_name") or device.get("device_id") for device in devices)
                asset_label = row.get("path") or row.get("name") or label
                if asset_type == "artwork" and row.get("artwork_type"):
                    asset_label = f"{asset_label} ({row.get('artwork_type')})"
                self.add_swarm_notification(
                    swarm_id,
                    f"master_{asset_type}_added",
                    f"New {label} in master list",
                    f"{asset_label} was added to the swarm master list from {names or 'a Drone'}.",
                    {"asset": row, "devices": devices},
                )
            for key in sorted(set(previous) - set(current), key=str):
                row = previous[key]
                devices = row.get("devices") or []
                names = ", ".join(device.get("device_name") or device.get("device_id") for device in devices)
                asset_label = row.get("path") or row.get("name") or label
                if asset_type == "artwork" and row.get("artwork_type"):
                    asset_label = f"{asset_label} ({row.get('artwork_type')})"
                self.add_swarm_notification(
                    swarm_id,
                    f"master_{asset_type}_removed",
                    f"{label} removed from master list",
                    f"{asset_label} was removed from the swarm master list after disappearing from {names or 'all Drones'}.",
                    {"asset": row, "devices": devices},
                )
    
    # User operations
    def create_user(self, email: str, hashed_password: str, full_name: Optional[str] = None, verified: bool = False, auth_provider: str = "password", username: Optional[str] = None) -> str:
        """Create a new user."""
        user_id = str(uuid.uuid4())
        user = {
            "id": user_id,
            "email": email,
            "password": hashed_password,
            "email_verified": bool(verified),
            "is_active": bool(verified),
            "auth_provider": auth_provider,
            "username": username,
            "full_name": full_name,
            "avatar_data_url": None,
            "fleet_settings": {
                "auto_sync_roms": True,
            },
            "notification_settings": {
                "notify_slack": False,
                "notify_discord": False,
                "notify_email": True,
                "slack_webhook": "",
                "discord_webhook": "",
                "types": dict(DEFAULT_NOTIFICATION_TYPES),
            },
            "created_at": datetime.utcnow(),
        }
        relational_user = postgres_store.create_user_record(
            user_id=user_id,
            email=email,
            password_hash=hashed_password,
            full_name=full_name,
            verified=verified,
            auth_provider=auth_provider,
            username=username,
            notification_types=dict(DEFAULT_NOTIFICATION_TYPES),
        )
        if relational_user:
            user = relational_user
        self.users[user_id] = user
        self.user_by_email[email] = user_id
        self.user_devices[user_id] = []
        if verified:
            self.ensure_personal_swarm(user_id)
            self.accept_invitations_for_email(email, user_id)
        return user_id

    def get_or_create_social_user(self, email: str, full_name: Optional[str], provider: str) -> dict:
        """Create or return a user authenticated by a configured social provider."""
        requested_username = self.available_username(full_name or email.split("@", 1)[0])
        if postgres_store.url:
            user_id = str(uuid.uuid4())
            result = postgres_store.complete_social_login(
                email=email,
                full_name=full_name,
                provider=provider,
                user_id=user_id,
                password_hash=auth.hash_password(str(uuid.uuid4())),
                username=requested_username,
                notification_types=dict(DEFAULT_NOTIFICATION_TYPES),
            )
            if not result or not result.get("user"):
                raise RuntimeError("PostgreSQL is unavailable while completing social login")
            user = result["user"]
            self.users[user["id"]] = user
            self.user_by_email[user["email"]] = user["id"]
            self.user_devices.setdefault(user["id"], [])
            self._record_social_login_relationships(user, result.get("swarm"), result.get("accepted_invitations") or [])
            return user

        existing = self.get_user_by_email(email)
        if existing:
            existing["auth_provider"] = existing.get("auth_provider") or provider
            if full_name and not existing.get("full_name"):
                existing["full_name"] = full_name
            if not existing.get("username"):
                existing["username"] = self.available_username(full_name or email.split("@", 1)[0], exclude_user_id=existing["id"])
            existing["email_verified"] = True
            existing["is_active"] = True
            self._ensure_social_login_relationships(existing, email)
            return existing

        username = requested_username
        user_id = str(uuid.uuid4())
        user = {
            "id": user_id,
            "email": email,
            "password": auth.hash_password(str(uuid.uuid4())),
            "full_name": full_name,
            "username": username,
            "email_verified": True,
            "is_active": True,
            "auth_provider": provider,
            "fleet_settings": {"auto_sync_roms": True},
            "notification_settings": {
                "notify_slack": False,
                "notify_discord": False,
                "notify_email": True,
                "slack_webhook": "",
                "discord_webhook": "",
                "email_address": email,
                "types": dict(DEFAULT_NOTIFICATION_TYPES),
            },
            "created_at": datetime.utcnow(),
        }
        relational_user = postgres_store.create_user_record(
            user_id=user_id,
            email=email,
            password_hash=user["password"],
            full_name=full_name,
            verified=True,
            auth_provider=provider,
            username=username,
            notification_types=dict(DEFAULT_NOTIFICATION_TYPES),
        )
        if relational_user:
            user = relational_user
            user_id = user["id"]
        elif postgres_store.url:
            raise RuntimeError("PostgreSQL is unavailable while completing social login")
        self.users[user_id] = user
        self.user_by_email[email] = user_id
        self.user_devices.setdefault(user_id, [])
        user["auth_provider"] = provider
        user["email_verified"] = True
        user["is_active"] = True
        self._ensure_social_login_relationships(user, email)
        return user

    def _ensure_social_login_relationships(self, user: dict, email: str) -> None:
        user_id = user.get("id")
        if not user_id:
            return
        swarm_name = f"{user.get('full_name') or user.get('email') or 'Overlord'}'s Swarm"
        relational_swarm = postgres_store.ensure_personal_swarm(user_id, swarm_name)
        if relational_swarm:
            swarm_id = relational_swarm["id"]
            self.swarms[swarm_id] = relational_swarm
            self.swarm_memberships.setdefault(swarm_id, {})[user_id] = {
                "user_id": user_id,
                "role": "overlord",
                "created_at": relational_swarm.get("created_at") or datetime.utcnow(),
            }
        else:
            self.ensure_personal_swarm(user_id)
        accepted = postgres_store.accept_invitations_for_email(email, user_id)
        for invite in accepted:
            swarm_id = invite.get("swarm_id")
            if swarm_id:
                self.swarm_memberships.setdefault(swarm_id, {})[user_id] = {
                    "user_id": user_id,
                    "role": invite.get("role") or "overseer",
                    "created_at": invite.get("accepted_at") or datetime.utcnow(),
                }
        if not accepted:
            self.accept_invitations_for_email(email, user_id)

    def _record_social_login_relationships(self, user: dict, swarm: Optional[dict], accepted: list[dict]) -> None:
        user_id = user.get("id")
        if swarm and user_id:
            swarm_id = swarm["id"]
            self.swarms[swarm_id] = swarm
            self.swarm_memberships.setdefault(swarm_id, {})[user_id] = {
                "user_id": user_id,
                "role": "overlord",
                "created_at": swarm.get("created_at") or datetime.utcnow(),
            }
        for invite in accepted:
            swarm_id = invite.get("swarm_id")
            if swarm_id and user_id:
                self.swarm_memberships.setdefault(swarm_id, {})[user_id] = {
                    "user_id": user_id,
                    "role": invite.get("role") or "overseer",
                    "created_at": invite.get("accepted_at") or datetime.utcnow(),
                }

    def set_user_verified(self, user_id: str) -> Optional[dict]:
        relational = postgres_store.set_user_verified(user_id)
        if relational:
            self.users[user_id] = relational
            self.user_by_email[relational["email"]] = user_id
            self.ensure_personal_swarm(user_id)
            self.accept_invitations_for_email(relational["email"], user_id)
            return relational
        user = self.get_user(user_id)
        if not user:
            return None
        user["email_verified"] = True
        user["is_active"] = True
        self.ensure_personal_swarm(user_id)
        self.accept_invitations_for_email(user["email"], user_id)
        return user

    def create_email_verification(self, user_id: str, code: str, token_hash: str, expires_at: datetime) -> dict:
        entry = {"user_id": user_id, "code": code, "token_hash": token_hash, "expires_at": expires_at, "created_at": datetime.utcnow(), "used_at": None}
        self.email_verifications[user_id] = entry
        return entry

    def verify_email_code(self, email: str, code: str) -> bool:
        user = self.get_user_by_email(email)
        if not user:
            return False
        entry = self.email_verifications.get(user["id"])
        if not entry or entry.get("used_at") or datetime.utcnow() > entry.get("expires_at"):
            return False
        if str(entry.get("code")) != str(code).strip():
            return False
        entry["used_at"] = datetime.utcnow()
        self.set_user_verified(user["id"])
        return True

    def verify_email_token(self, token: str, verifier) -> Optional[dict]:
        for user_id, entry in self.email_verifications.items():
            if entry.get("used_at") or datetime.utcnow() > entry.get("expires_at"):
                continue
            if verifier(token, entry.get("token_hash") or ""):
                entry["used_at"] = datetime.utcnow()
                return self.set_user_verified(user_id)
        return None

    def create_password_reset(self, user_id: str, token_hash: str, expires_at: datetime) -> dict:
        reset_id = str(uuid.uuid4())
        entry = {"id": reset_id, "user_id": user_id, "token_hash": token_hash, "expires_at": expires_at, "created_at": datetime.utcnow(), "used_at": None}
        self.password_resets[reset_id] = entry
        return entry

    def consume_password_reset(self, token: str, new_password_hash: str, verifier) -> bool:
        for entry in self.password_resets.values():
            if entry.get("used_at") or datetime.utcnow() > entry.get("expires_at"):
                continue
            if verifier(token, entry.get("token_hash") or ""):
                user = self.get_user(entry["user_id"])
                if not user:
                    return False
                user["password"] = new_password_hash
                entry["used_at"] = datetime.utcnow()
                return True
        return False

    def create_swarm(self, owner_id: str, name: str) -> dict:
        swarm_id = str(uuid.uuid4())
        swarm = {"id": swarm_id, "name": name.strip(), "owner_id": owner_id, "created_at": datetime.utcnow()}
        self.swarms[swarm_id] = swarm
        self.swarm_memberships.setdefault(swarm_id, {})[owner_id] = {"user_id": owner_id, "role": "overlord", "created_at": datetime.utcnow()}
        return swarm

    def ensure_personal_swarm(self, user_id: str) -> dict:
        existing = [swarm for swarm in self.swarms.values() if swarm.get("owner_id") == user_id]
        existing.sort(key=lambda row: str(row.get("created_at") or ""))
        if existing:
            return existing[0]
        user = self.get_user(user_id) or {}
        return self.create_swarm(user_id, f"{user.get('full_name') or user.get('email') or 'Overlord'}'s Swarm")

    def get_user_swarms(self, user_id: str) -> List[dict]:
        if postgres_store.available():
            relational = postgres_store.list_user_swarms(user_id)
            if relational is not None:
                return relational
        rows = []
        for swarm_id, members in self.swarm_memberships.items():
            member = members.get(user_id)
            swarm = self.swarms.get(swarm_id)
            if member and swarm:
                self._normalize_member_role(member)
                rows.append({**swarm, "role": member["role"]})
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows

    def get_swarm(self, swarm_id: str) -> Optional[dict]:
        if postgres_store.available():
            relational = postgres_store.get_swarm(swarm_id)
            if relational is not None:
                self.swarms[swarm_id] = relational
                return relational
        return self.swarms.get(swarm_id)

    def get_swarm_member(self, swarm_id: str, user_id: str) -> Optional[dict]:
        if postgres_store.available():
            relational = postgres_store.get_swarm_member(swarm_id, user_id)
            if relational is not None:
                return relational
        member = self.swarm_memberships.get(swarm_id, {}).get(user_id)
        if member:
            self._normalize_member_role(member)
        return member

    def _normalize_member_role(self, member: dict) -> dict:
        legacy_owner_role = "".join(("master", "mind", "_", "over", "lord"))
        if member.get("role") == legacy_owner_role:
            member["role"] = "overlord"
        if member.get("role") not in {"overlord", "overseer"}:
            member["role"] = "overseer"
        return member

    def require_swarm_role(self, swarm_id: str, user_id: str, allowed: set[str]) -> Optional[dict]:
        member = self.get_swarm_member(swarm_id, user_id)
        if not member or member.get("role") not in allowed:
            return None
        return member

    def default_swarm_id(self, user_id: str) -> Optional[str]:
        if postgres_store.available():
            relational = postgres_store.default_swarm_id(user_id)
            if relational is not None:
                return relational
        owned = [swarm for swarm in self.swarms.values() if swarm.get("owner_id") == user_id]
        owned.sort(key=lambda row: str(row.get("created_at") or ""))
        if owned:
            return owned[0]["id"]
        swarms = self.get_user_swarms(user_id)
        return swarms[0]["id"] if swarms else None

    def invite_to_swarm(self, swarm_id: str, email: str, role: str, token_hash: str, expires_at: datetime, invited_by: str) -> dict:
        invite_id = str(uuid.uuid4())
        invite = {"id": invite_id, "swarm_id": swarm_id, "email": email.lower(), "role": role, "token_hash": token_hash, "status": "pending", "created_at": datetime.utcnow(), "expires_at": expires_at, "invited_by": invited_by}
        self.invitations[invite_id] = invite
        return invite

    def rotate_pending_invitation(self, swarm_id: str, invitation_id: str, token_hash: str, expires_at: datetime) -> Optional[dict]:
        invite = self.invitations.get(invitation_id)
        if not invite or invite.get("swarm_id") != swarm_id or invite.get("status") != "pending":
            return None
        invite["token_hash"] = token_hash
        invite["expires_at"] = expires_at
        invite["resent_at"] = datetime.utcnow()
        return invite

    def remove_pending_invitation(self, swarm_id: str, invitation_id: str) -> Optional[dict]:
        invite = self.invitations.get(invitation_id)
        if not invite or invite.get("swarm_id") != swarm_id or invite.get("status") != "pending":
            return None
        return self.invitations.pop(invitation_id)

    def list_swarm_access(self, swarm_id: str) -> dict:
        members = []
        for user_id, member in self.swarm_memberships.get(swarm_id, {}).items():
            self._normalize_member_role(member)
            user = self.get_user(user_id) or {}
            members.append({
                **member,
                "email": user.get("email"),
                "full_name": user.get("full_name"),
                "username": user.get("username"),
                "registration_status": "registered" if user else "unknown",
                "status": "accepted",
            })
        invites = []
        for invite in self.invitations.values():
            if invite.get("swarm_id") != swarm_id:
                continue
            row = {k: v for k, v in invite.items() if k != "token_hash"}
            invited_user = self.get_user_by_email(str(invite.get("email") or ""))
            row["role"] = "overseer"
            row["registration_status"] = "registered" if invited_user else "invited"
            row["approval_status"] = invite.get("status") or "pending"
            invites.append(row)
        return {"members": members, "invitations": invites}

    def accept_invitation(self, token: str, user_id: str, verifier) -> Optional[dict]:
        user = self.get_user(user_id)
        if not user:
            return None
        for invite in self.invitations.values():
            if invite.get("status") != "pending" or datetime.utcnow() > invite.get("expires_at"):
                continue
            if invite.get("email") != str(user.get("email") or "").lower():
                continue
            if verifier(token, invite.get("token_hash") or ""):
                self.swarm_memberships.setdefault(invite["swarm_id"], {})[user_id] = {"user_id": user_id, "role": "overseer", "created_at": datetime.utcnow()}
                invite["status"] = "accepted"
                invite["accepted_at"] = datetime.utcnow()
                return invite
        return None

    def find_invitation_by_token(self, token: str, verifier) -> Optional[dict]:
        for invite in self.invitations.values():
            if verifier(token, invite.get("token_hash") or ""):
                return invite
        return None

    def accept_invitation_for_user(self, invite: dict, user_id: str) -> Optional[dict]:
        user = self.get_user(user_id)
        if not user or not invite:
            return None
        if invite.get("status") != "pending" or datetime.utcnow() > invite.get("expires_at"):
            return None
        if invite.get("email") != str(user.get("email") or "").lower():
            return None
        self.swarm_memberships.setdefault(invite["swarm_id"], {})[user_id] = {"user_id": user_id, "role": "overseer", "created_at": datetime.utcnow()}
        invite["status"] = "accepted"
        invite["accepted_at"] = datetime.utcnow()
        return invite

    def accept_invitations_for_email(self, email: str, user_id: str) -> None:
        for invite in self.invitations.values():
            if invite.get("status") == "pending" and invite.get("email") == email.lower() and datetime.utcnow() <= invite.get("expires_at"):
                self.swarm_memberships.setdefault(invite["swarm_id"], {})[user_id] = {"user_id": user_id, "role": "overseer", "created_at": datetime.utcnow()}
                invite["status"] = "accepted"
                invite["accepted_at"] = datetime.utcnow()

    def remove_swarm_member(self, swarm_id: str, target_user_id: str) -> bool:
        swarm = self.swarms.get(swarm_id)
        if not swarm or swarm.get("owner_id") == target_user_id:
            return False
        return self.swarm_memberships.get(swarm_id, {}).pop(target_user_id, None) is not None

    def update_swarm_member_role(self, swarm_id: str, target_user_id: str, role: str) -> bool:
        member = self.swarm_memberships.get(swarm_id, {}).get(target_user_id)
        swarm = self.swarms.get(swarm_id)
        if not member or not swarm or swarm.get("owner_id") == target_user_id:
            return False
        member["role"] = role
        return True
    
    def get_user_by_email(self, email: str) -> Optional[dict]:
        """Get user by email."""
        relational = postgres_store.get_user_by_email(email)
        if relational:
            self.users[relational["id"]] = relational
            self.user_by_email[relational["email"]] = relational["id"]
            return relational
        user_id = self.user_by_email.get(email)
        if user_id:
            return self.users.get(user_id)
        return None
    
    def get_user(self, user_id: str) -> Optional[dict]:
        """Get user by ID."""
        relational = postgres_store.get_user(user_id)
        if relational:
            self.users[user_id] = relational
            self.user_by_email[relational["email"]] = user_id
            return relational
        return self.users.get(user_id)
    
    def user_exists(self, email: str) -> bool:
        """Check if user exists by email."""
        if postgres_store.user_exists(email):
            return True
        return email in self.user_by_email

    def username_exists(self, username: str, exclude_user_id: Optional[str] = None) -> bool:
        if postgres_store.username_exists(username, exclude_user_id=exclude_user_id):
            return True
        normalized = str(username or "").strip().casefold()
        if not normalized:
            return False
        return any(
            user_id != exclude_user_id and str(user.get("username") or "").strip().casefold() == normalized
            for user_id, user in self.users.items()
        )

    def available_username(self, preferred: str, exclude_user_id: Optional[str] = None) -> str:
        base = str(preferred or "").strip() or "user"
        candidate = base
        suffix = 2
        while self.username_exists(candidate, exclude_user_id=exclude_user_id):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def update_user_profile(
        self,
        user_id: str,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
        avatar_data_url: Optional[str] = None,
    ) -> Optional[dict]:
        """Update profile fields for a user."""
        relational = postgres_store.update_user_profile(user_id, username, full_name, avatar_data_url)
        if relational:
            self.users[user_id] = relational
            self.user_by_email[relational["email"]] = user_id
            return relational
        user = self.get_user(user_id)
        if not user:
            return None
        if full_name is not None:
            user["full_name"] = full_name
        if username is not None:
            user["username"] = username
        if avatar_data_url is not None:
            user["avatar_data_url"] = avatar_data_url
        return user

    def update_swarm_name(self, swarm_id: str, name: str) -> Optional[dict]:
        cleaned = name.strip()
        if postgres_store.available():
            relational = postgres_store.update_swarm_name(swarm_id, cleaned)
            if relational:
                self.swarms[swarm_id] = relational
                return relational
        swarm = self.swarms.get(swarm_id)
        if not swarm:
            return None
        swarm["name"] = cleaned
        return swarm

    def update_user_fleet_settings(self, user_id: str, fleet_settings: dict) -> Optional[dict]:
        """Update fleet settings for a user."""
        relational = postgres_store.update_user_fleet_settings(user_id, fleet_settings)
        if relational:
            self.users[user_id] = relational
            self.user_by_email[relational["email"]] = user_id
            return relational
        user = self.get_user(user_id)
        if not user:
            return None
        user["fleet_settings"] = {**user.get("fleet_settings", {}), **fleet_settings}
        return user

    def update_user_notification_settings(self, user_id: str, notification_settings: dict) -> Optional[dict]:
        """Update notification settings for a user."""
        relational = postgres_store.update_user_notification_settings(user_id, notification_settings)
        if relational:
            self.users[user_id] = relational
            self.user_by_email[relational["email"]] = user_id
            return relational
        user = self.get_user(user_id)
        if not user:
            return None
        current = user.get("notification_settings", {})
        merged = {**current, **{k: v for k, v in notification_settings.items() if k not in {"types", "email_address"}}}
        if "types" in notification_settings and isinstance(notification_settings["types"], dict):
            merged["types"] = {**current.get("types", {}), **notification_settings["types"]}
        user["notification_settings"] = merged
        return user
    
    # Drone onboarding token operations
    def create_integration_token(self, user_id: str, label: str = "Drone onboarding") -> dict:
        raw_token = generate_drone_token()
        entry = {
            "id": str(uuid.uuid4()),
            "label": label or "Drone onboarding",
            "token_hash": hash_drone_token(raw_token),
            "created_at": datetime.utcnow(),
            "last_used_at": None,
            "revoked_at": None,
            "raw_token_once": raw_token,
        }
        postgres_store.create_integration_token_record(user_id, entry)
        self.integration_tokens.setdefault(user_id, []).append(entry)
        return entry

    def get_integration_tokens(self, user_id: str) -> List[dict]:
        relational = postgres_store.get_integration_tokens(user_id)
        if relational is not None:
            self.integration_tokens[user_id] = relational
            return [
                {k: v for k, v in token.items() if k not in {"token_hash", "raw_token_once"}}
                for token in relational
            ]
        return [
            {k: v for k, v in token.items() if k not in {"token_hash", "raw_token_once"}}
            for token in self.integration_tokens.get(user_id, [])
        ]

    def verify_integration_token(self, email: Optional[str], token: Optional[str]) -> Optional[dict]:
        claimed = self.claim_integration_token(email, token, None)
        return claimed["user"] if claimed else None

    def claim_integration_token(
        self,
        email: Optional[str],
        token: Optional[str],
        device_id: Optional[str],
        device_fingerprint: Optional[str] = None,
    ) -> Optional[dict]:
        relational = postgres_store.claim_integration_token(email, token, device_id, device_fingerprint)
        if relational:
            user = relational["user"]
            entry = relational["token"]
            self.users[user["id"]] = user
            self.user_by_email[user["email"]] = user["id"]
            rows = self.integration_tokens.setdefault(user["id"], [])
            existing = next((row for row in rows if row.get("id") == entry.get("id")), None)
            if existing:
                existing.update(entry)
            else:
                rows.append(entry)
            return relational
        if not token:
            return None
        fingerprint = str(device_fingerprint or "").strip()
        candidates = []
        seen_user_ids = set()
        if email:
            user = self.get_user_by_email(email)
            if user:
                candidates.append((user["id"], user))
                seen_user_ids.add(user["id"])
        for user_id in self.integration_tokens.keys():
            if user_id not in seen_user_ids:
                candidates.append((user_id, self.get_user(user_id)))
        for user_id, user in candidates:
            if not user:
                continue
            for entry in self.integration_tokens.get(user_id, []):
                if entry.get("revoked_at"):
                    continue
                if not verify_drone_token(token, entry.get("token_hash")):
                    continue
                bound_device = entry.get("bound_device_id")
                if bound_device and device_id and bound_device != device_id:
                    return None
                bound_fingerprint = str(entry.get("bound_device_fingerprint") or "").strip()
                if bound_fingerprint and fingerprint and bound_fingerprint != fingerprint:
                    return None
                if fingerprint and not bound_fingerprint:
                    entry["bound_device_fingerprint"] = fingerprint
                if device_id and not bound_device:
                    entry["bound_device_id"] = device_id
                    entry["bound_at"] = datetime.utcnow()
                entry["last_used_at"] = datetime.utcnow()
                return {"user": user, "token": entry}
        return None

    def revoke_integration_token(self, user_id: str, token_id: str) -> bool:
        relational = postgres_store.revoke_integration_token(user_id, token_id)
        if relational is not None:
            if relational:
                for entry in self.integration_tokens.get(user_id, []):
                    if entry.get("id") == token_id:
                        entry["revoked_at"] = datetime.utcnow()
            return bool(relational)
        for entry in self.integration_tokens.get(user_id, []):
            if entry.get("id") == token_id and not entry.get("revoked_at"):
                entry["revoked_at"] = datetime.utcnow()
                return True
        return False

    # Device operations
    def _merge_device_bucket(self, bucket: Dict[str, list], keep_id: str, remove_id: str) -> None:
        keep_rows = bucket.setdefault(keep_id, [])
        for row in bucket.get(remove_id, []):
            if row not in keep_rows:
                keep_rows.append(row)
        bucket.pop(remove_id, None)

    def _dedupe_device_records(self, device_id: str, preferred_id: Optional[str] = None) -> Optional[dict]:
        matches = [device for device in self.devices.values() if device.get("device_id") == device_id]
        if not matches:
            return None

        def sort_key(device: dict):
            return (
                1 if preferred_id and device.get("id") == preferred_id else 0,
                device.get("last_seen") or datetime.min,
                device.get("registered_at") or datetime.min,
            )

        keep = sorted(matches, key=sort_key, reverse=True)[0]
        keep_id = keep["id"]
        duplicate_ids = [device["id"] for device in matches if device.get("id") != keep_id]
        if not duplicate_ids:
            return keep

        for duplicate_id in duplicate_ids:
            duplicate = self.devices.get(duplicate_id)
            if not duplicate:
                continue
            if (duplicate.get("last_seen") or datetime.min) > (keep.get("last_seen") or datetime.min):
                keep["last_seen"] = duplicate.get("last_seen")
            if not keep.get("registered_at") or (
                duplicate.get("registered_at") and duplicate.get("registered_at") < keep.get("registered_at")
            ):
                keep["registered_at"] = duplicate.get("registered_at")
            for key in ("rom_systems",):
                merged = list(dict.fromkeys((keep.get(key) or []) + (duplicate.get(key) or [])))
                if merged:
                    keep[key] = merged
            for key in ("auto_sync_policy", "system_info", "certificate", "network", "resolved_network"):
                if not keep.get(key) and duplicate.get(key):
                    keep[key] = duplicate.get(key)
            for key in ("api_port", "scheme", "reachable_url", "authorization_token_id", "drone_token_hash", "swarm_id", "user_id"):
                if duplicate.get(key) and not keep.get(key):
                    keep[key] = duplicate.get(key)
            if duplicate.get("swarm_connected"):
                keep["swarm_connected"] = True
            claims = self.device_admin_claims.setdefault(keep_id, [])
            for claim_user_id in self.device_admin_claims.get(duplicate_id, []):
                if claim_user_id not in claims:
                    claims.append(claim_user_id)
            self.device_admin_claims.pop(duplicate_id, None)
            for bucket in (
                self.roms,
                self.bios,
                self.artwork,
                self.gamelogs,
                self.device_actions,
                self.speed_samples,
                self.device_events,
                self.peer_checks,
                self.rom_sync_activity,
            ):
                self._merge_device_bucket(bucket, keep_id, duplicate_id)
            self.download_states.pop(duplicate_id, None)
            self.devices.pop(duplicate_id, None)

        for owner_id, ids in list(self.user_devices.items()):
            cleaned = []
            for internal_id in ids:
                replacement = keep_id if internal_id in duplicate_ids else internal_id
                if replacement not in cleaned:
                    cleaned.append(replacement)
            self.user_devices[owner_id] = cleaned
        self._persist_state()
        return keep

    def _dedupe_all_device_records(self) -> None:
        for device_id in list({device.get("device_id") for device in self.devices.values() if device.get("device_id")}):
            self._dedupe_device_records(device_id)

    def create_pending_drone_connection(
        self,
        device_id: str,
        device_name: str,
        batocera_info: dict,
        user_id: Optional[str] = None,
        authorization_token_id: Optional[str] = None,
        drone_token_hash: Optional[str] = None,
        recovery_reason: Optional[str] = None,
    ) -> dict:
        """Record that a drone is attempting to connect to Overmind."""
        if postgres_store.available():
            relational = postgres_store.upsert_pending_drone_connection(
                device_id,
                device_name,
                batocera_info,
                user_id=user_id,
                authorization_token_id=authorization_token_id,
                drone_token_hash=drone_token_hash,
                recovery_reason=recovery_reason,
            )
            if relational is not None:
                self.pending_drone_connections[device_id] = relational
                return relational
        now = datetime.utcnow()
        existing = self.pending_drone_connections.get(device_id)
        if existing:
            existing.update({
                "device_name": device_name,
                "batocera_info": batocera_info,
                "user_id": user_id if user_id is not None else existing.get("user_id"),
                "authorization_token_id": authorization_token_id or existing.get("authorization_token_id"),
                "drone_token_hash": drone_token_hash or existing.get("drone_token_hash"),
                "recovery_reason": recovery_reason or existing.get("recovery_reason"),
                "last_seen": now,
                "status": "pending",
            })
            return existing

        connection = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "device_id": device_id,
            "device_name": device_name,
            "batocera_info": batocera_info,
            "detected_at": now,
            "last_seen": now,
            "status": "pending",
            "authorization_token_id": authorization_token_id,
            "drone_token_hash": drone_token_hash,
            "recovery_reason": recovery_reason,
        }
        self.pending_drone_connections[device_id] = connection
        return connection

    def get_pending_drone_connections(self, user_id: str) -> List[dict]:
        """Return pending drone connections visible to this user."""
        if postgres_store.available():
            relational = postgres_store.get_pending_drone_connections(user_id)
            if relational is not None:
                self.pending_drone_connections.update({row["device_id"]: row for row in relational})
                return relational
        visible = [
            conn for conn in self.pending_drone_connections.values()
            if conn.get("status") == "pending" and conn.get("user_id") == user_id
        ]
        visible.sort(key=lambda row: row.get("last_seen"), reverse=True)
        return visible

    def get_all_pending_drone_connections(self) -> List[dict]:
        """Return all pending drone connections for super-admin recovery."""
        if postgres_store.available():
            relational = postgres_store.get_all_pending_drone_connections()
            if relational is not None:
                self.pending_drone_connections.update({row["device_id"]: row for row in relational})
                return relational
        visible = [
            conn for conn in self.pending_drone_connections.values()
            if conn.get("status") == "pending"
        ]
        visible.sort(key=lambda row: row.get("last_seen"), reverse=True)
        return visible

    def admin_assign_pending_drone_connection(self, device_id: str, swarm_id: str) -> Optional[dict]:
        """Assign an unclaimed pending Drone request to a swarm and approve its current token."""
        connection = self.pending_drone_connections.get(device_id)
        if postgres_store.available():
            relational = postgres_store.get_all_pending_drone_connections()
            if relational is not None:
                self.pending_drone_connections.update({row["device_id"]: row for row in relational})
                connection = self.pending_drone_connections.get(device_id)
        if not connection or connection.get("status") != "pending":
            return None
        swarm = self.get_swarm(swarm_id)
        if not swarm:
            return None
        owner_id = swarm.get("owner_id")
        if not owner_id:
            return None
        token_hash = connection.get("drone_token_hash")
        if not token_hash:
            return None
        internal_id = self.create_device(
            owner_id,
            connection["device_id"],
            connection.get("device_name") or connection["device_id"],
            connection.get("batocera_info") if isinstance(connection.get("batocera_info"), dict) else {},
            token_hash=token_hash,
            authorization_token_id=connection.get("authorization_token_id"),
            swarm_id=swarm_id,
        )
        self.pending_drone_connections.pop(device_id, None)
        if postgres_store.available():
            postgres_store.delete_any_pending_drone_connection(device_id)
        return self.get_device(internal_id)

    def accept_pending_drone_connection(self, user_id: str, device_id: str) -> Optional[dict]:
        """Accept a pending drone connection and register it to the Overlord."""
        connection = self.pending_drone_connections.get(device_id)
        if postgres_store.available():
            relational = postgres_store.get_pending_drone_connection(user_id, device_id)
            if relational is not None:
                connection = relational
                self.pending_drone_connections[device_id] = relational
        if not connection or connection.get("status") != "pending":
            return None
        if connection.get("user_id") != user_id:
            return None
        authorization_token_id = connection.get("authorization_token_id")
        backing_token_hash = None
        if authorization_token_id:
            backing = next(
                (
                    row for row in self.integration_tokens.get(user_id, [])
                    if row.get("id") == authorization_token_id and not row.get("revoked_at")
                ),
                None,
            )
            if backing:
                backing_token_hash = backing.get("token_hash")
        existing = self.get_device_by_device_id(device_id)
        if existing and existing.get("user_id") == user_id:
            if backing_token_hash:
                existing["drone_token_hash"] = backing_token_hash
                existing["authorization_token_id"] = authorization_token_id
                print(f"Approving existing Drone {device_id}: preserved bound onboarding credential id={authorization_token_id}")
            existing["approval_status"] = "approved"
            existing["last_seen"] = datetime.utcnow()
            existing["device_name"] = connection.get("device_name") or existing.get("device_name")
            existing["batocera_info"] = connection.get("batocera_info") or existing.get("batocera_info") or {}
            if existing["id"] not in self.user_devices.get(user_id, []):
                self.user_devices.setdefault(user_id, []).append(existing["id"])
            self.pending_drone_connections.pop(device_id, None)
            if postgres_store.available():
                postgres_store.delete_pending_drone_connection(user_id, device_id)
            return existing
        if self.device_exists(user_id, device_id):
            self.pending_drone_connections.pop(device_id, None)
            if postgres_store.available():
                postgres_store.delete_pending_drone_connection(user_id, device_id)
            return self.get_device_by_device_id(device_id)

        raw_token = None
        token_hash = None
        if backing_token_hash:
            token_hash = backing_token_hash
            print(f"Approving Drone {device_id}: preserving bound onboarding credential id={authorization_token_id}")
        if not token_hash:
            raw_token = generate_drone_token()
            print(f"Approving Drone {device_id}: generated replacement Drone credential")
        internal_id = self.create_device(
            user_id,
            connection["device_id"],
            connection["device_name"],
            connection.get("batocera_info") if isinstance(connection.get("batocera_info"), dict) else {},
            raw_token=raw_token,
            token_hash=token_hash,
            authorization_token_id=authorization_token_id,
        )
        self.pending_drone_connections.pop(device_id, None)
        if postgres_store.available():
            postgres_store.delete_pending_drone_connection(user_id, device_id)
        device = self.get_device(internal_id)
        if raw_token:
            device["raw_token_once"] = raw_token
            self.approved_drone_tokens[device_id] = raw_token
        if _cache:
            _cache.invalidate_user_devices(user_id)
        return device

    def deny_pending_drone_connection(self, user_id: str, device_id: str) -> bool:
        """Deny a pending drone connection."""
        connection = self.pending_drone_connections.get(device_id)
        if postgres_store.available():
            relational_deleted = postgres_store.delete_pending_drone_connection(user_id, device_id, status="denied")
            if relational_deleted is not None:
                self.pending_drone_connections.pop(device_id, None)
                return relational_deleted
        if not connection or connection.get("user_id") != user_id:
            return False
        self.pending_drone_connections.pop(device_id, None)
        return True

    def create_device(
        self,
        user_id: str,
        device_id: str,
        device_name: str,
        batocera_info: dict,
        raw_token: Optional[str] = None,
        token_hash: Optional[str] = None,
        authorization_token_id: Optional[str] = None,
        swarm_id: Optional[str] = None,
    ) -> str:
        """Register a new device, or update the existing record for this Drone ID."""
        selected_swarm_id = swarm_id or self.default_swarm_id(user_id) or self.ensure_personal_swarm(user_id)["id"]
        network_state = resolve_reported_network(batocera_info.get("network") if isinstance(batocera_info, dict) else None)
        certificate = self._clean_device_certificate(
            batocera_info.get("certificate") if isinstance(batocera_info, dict) else None
        )
        existing = self._dedupe_device_records(device_id)
        if existing:
            internal_id = existing["id"]
            existing.update({
                "user_id": user_id,
                "swarm_id": selected_swarm_id,
                "device_name": device_name,
                "batocera_info": batocera_info,
                "last_seen": datetime.utcnow(),
                "network": network_state["reported"],
                "resolved_network": network_state["resolved"],
                "swarm_connected": network_state["swarm_connected"],
                "authorization_token_id": authorization_token_id,
                "api_port": batocera_info.get("api_port") if isinstance(batocera_info, dict) else None,
                "scheme": (batocera_info.get("scheme") if isinstance(batocera_info, dict) else None) or "https",
                "reachable_url": batocera_info.get("reachable_url") if isinstance(batocera_info, dict) else None,
                "certificate": certificate,
                "approval_status": "approved",
            })
            if raw_token or token_hash:
                existing["drone_token_hash"] = token_hash or hash_drone_token(raw_token or "")
            for owner_id, ids in list(self.user_devices.items()):
                self.user_devices[owner_id] = [row for row in ids if row != internal_id]
            self.user_devices.setdefault(user_id, [])
            if internal_id not in self.user_devices[user_id]:
                self.user_devices[user_id].append(internal_id)
            return internal_id

        internal_id = str(uuid.uuid4())
        self.devices[internal_id] = {
            "id": internal_id,
            "user_id": user_id,
            "swarm_id": selected_swarm_id,
            "device_id": device_id,
            "device_name": device_name,
            "batocera_info": batocera_info,
            "registered_at": datetime.utcnow(),
            "last_seen": datetime.utcnow(),
            "network": network_state["reported"],
            "resolved_network": network_state["resolved"],
            "swarm_connected": network_state["swarm_connected"],
            "rom_systems": [],
            "auto_sync_policy": {"enabled": False, "systems": []},
            "drone_token_hash": token_hash or hash_drone_token(raw_token or generate_drone_token()),
            "authorization_token_id": authorization_token_id,
            "api_port": batocera_info.get("api_port") if isinstance(batocera_info, dict) else None,
            "scheme": (batocera_info.get("scheme") if isinstance(batocera_info, dict) else None) or "https",
            "reachable_url": batocera_info.get("reachable_url") if isinstance(batocera_info, dict) else None,
            "certificate": certificate,
            "peer_checks": [],
            "approval_status": "approved",
        }
        self.user_devices.setdefault(user_id, []).append(internal_id)
        self.roms[internal_id] = []
        self.bios[internal_id] = []
        self.artwork[internal_id] = []
        self.gamelogs[internal_id] = []
        self.device_actions[internal_id] = []
        self.speed_samples[internal_id] = []
        self.device_events[internal_id] = []
        self.peer_checks[internal_id] = []
        self.rom_sync_activity[internal_id] = []
        self.devices[internal_id]["last_known_status"] = "online"
        self.add_swarm_notification(
            selected_swarm_id,
            "drone_added",
            "Drone added to swarm",
            f"{device_name or device_id} was added to the swarm.",
            {"device": {"device_id": device_id, "device_name": device_name or device_id}},
        )
        return internal_id

    def add_device_admin_claim(self, user_id: str, device_id: str) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        bucket = self.device_admin_claims.setdefault(device["id"], [])
        if user_id not in bucket:
            bucket.append(user_id)
        device["admin_claimed_at"] = datetime.utcnow()
        return {"device_id": device_id, "user_id": user_id, "role": "overlord"}

    def user_has_device_admin_claim(self, user_id: str, device_id: str) -> bool:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return False
        return user_id in set(self.device_admin_claims.get(device["id"], []))

    def _clean_device_certificate(self, certificate: Optional[dict]) -> Optional[dict]:
        if not isinstance(certificate, dict):
            return None
        clean_cert = dict(certificate)
        clean_cert.pop("private_key", None)
        clean_cert.pop("key", None)
        clean_cert.pop("key_file", None)
        clean_cert["last_seen"] = datetime.utcnow()
        return clean_cert
    
    def get_device(self, internal_id: str) -> Optional[dict]:
        """Get device by internal ID."""
        if postgres_store.available():
            relational = postgres_store.get_device(internal_id)
            if relational is not None:
                return relational
        return self.devices.get(internal_id)
    
    def get_device_by_device_id(self, device_id: str) -> Optional[dict]:
        """Get device by device_id (unique per user)."""
        if postgres_store.available():
            relational = postgres_store.get_device_by_device_id(device_id)
            if relational is not None:
                return relational
        return self._dedupe_device_records(device_id)
    
    def get_user_devices(self, user_id: str, swarm_id: Optional[str] = None) -> List[dict]:
        """Get all devices for a user."""
        if postgres_store.available():
            relational = postgres_store.list_user_devices(user_id, swarm_id=swarm_id)
            if relational is not None:
                return relational
        self._dedupe_all_device_records()
        visible_swarm_ids = {swarm_id} if swarm_id else {row["id"] for row in self.get_user_swarms(user_id)}
        return [
            device for device in self.devices.values()
            if device.get("approval_status", "approved") == "approved"
            and (
                device.get("swarm_id") in visible_swarm_ids
                or (not swarm_id and user_id in set(self.device_admin_claims.get(device["id"], [])))
                or (swarm_id and device.get("swarm_id") == swarm_id and user_id in set(self.device_admin_claims.get(device["id"], [])))
            )
        ]

    def user_can_access_device(self, user_id: str, device_id: str, swarm_id: Optional[str] = None) -> Optional[dict]:
        if postgres_store.available():
            relational = postgres_store.user_can_access_device(user_id, device_id, swarm_id=swarm_id)
            if relational is not None:
                return relational
        device = self.get_device_by_device_id(device_id)
        if not device or device.get("approval_status", "approved") != "approved":
            return None
        if swarm_id and device.get("swarm_id") != swarm_id:
            return None
        if not self.get_swarm_member(device.get("swarm_id"), user_id) and not self.user_has_device_admin_claim(user_id, device_id):
            return None
        return device
    
    def update_device_last_seen(
        self,
        internal_id: str,
        network: Optional[dict] = None,
        rom_systems: Optional[list] = None,
        api_port: Optional[int] = None,
        scheme: Optional[str] = None,
        reachable_url: Optional[str] = None,
        certificate: Optional[dict] = None,
        system_info: Optional[dict] = None,
    ):
        """Update last_seen timestamp for device."""
        if internal_id in self.devices:
            device = self.devices[internal_id]
            previous_status = device.get("last_known_status")
            device["last_seen"] = datetime.utcnow()
            if previous_status == "offline":
                device["last_known_status"] = "online"
                self.add_swarm_notification(
                    device.get("swarm_id"),
                    "drone_online",
                    "Drone online",
                    f"{self._device_label(device)} is online.",
                    {"device": {"device_id": device.get("device_id"), "device_name": self._device_label(device)}, "status": "online"},
                )
            elif not previous_status:
                device["last_known_status"] = "online"
            if network is not None:
                network_state = resolve_reported_network(network)
                device["network"] = network_state["reported"]
                device["resolved_network"] = network_state["resolved"]
                device["swarm_connected"] = network_state["swarm_connected"]
            if isinstance(rom_systems, list):
                names = []
                for system in rom_systems:
                    if isinstance(system, dict):
                        name = str(system.get("name") or system.get("system_name") or "").strip()
                    else:
                        name = str(system or "").strip()
                    if name and name not in names:
                        names.append(name)
                device["rom_systems"] = sorted(names)
            if api_port is not None:
                device["api_port"] = api_port
            if scheme:
                device["scheme"] = scheme
            if reachable_url:
                device["reachable_url"] = reachable_url
            if isinstance(certificate, dict):
                device["certificate"] = self._clean_device_certificate(certificate)
            if isinstance(system_info, dict):
                clean_info = dict(system_info)
                clean_info["last_system_info_update"] = datetime.utcnow()
                device["system_info"] = clean_info
        if postgres_store.available() and any([system_info, network, api_port, scheme, reachable_url]):
            postgres_store.update_device_heartbeat_data(
                internal_id,
                system_info=system_info,
                network=network,
                api_port=api_port,
                scheme=scheme,
                reachable_url=reachable_url,
            )

    def update_device_rom_inventory_fingerprint(
        self,
        device_id: str,
        *,
        drone_fingerprint: Optional[str] = None,
        overmind_fingerprint: Optional[str] = None,
        compute_overmind: bool = False,
    ) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        if compute_overmind:
            overmind_fingerprint = compute_rom_inventory_fingerprint(self.get_device_roms(device_id))
        updated_at = datetime.utcnow()
        if overmind_fingerprint:
            device["rom_inventory_fingerprint"] = overmind_fingerprint
            device["rom_inventory_fingerprint_algorithm"] = ROM_INVENTORY_FINGERPRINT_ALGORITHM
            device["rom_inventory_fingerprint_at"] = updated_at
        if drone_fingerprint:
            device["drone_rom_inventory_fingerprint"] = drone_fingerprint
            device["drone_rom_inventory_fingerprint_at"] = updated_at
        if postgres_store.available():
            postgres_store.update_device_rom_inventory_fingerprint(
                device["id"],
                drone_fingerprint=drone_fingerprint,
                overmind_fingerprint=overmind_fingerprint,
                algorithm=ROM_INVENTORY_FINGERPRINT_ALGORITHM if (drone_fingerprint or overmind_fingerprint) else None,
            )
        return device

    def ensure_rom_metadata_sync_action_for_fingerprint(self, device_id: str, drone_fingerprint: str) -> Optional[dict]:
        device = self.update_device_rom_inventory_fingerprint(device_id, drone_fingerprint=drone_fingerprint)
        if not device:
            return None
        overmind_fingerprint = str(device.get("rom_inventory_fingerprint") or "").strip()
        mismatch = not overmind_fingerprint or overmind_fingerprint != drone_fingerprint
        if not mismatch:
            return None
        active_actions = self.get_device_actions(device.get("user_id"), device_id) or []
        for action in active_actions:
            if action.get("action") in {"purge_asset_cache", "rebuild_asset_metadata", "collect_rom_metadata"}:
                return None
        # Use purge_asset_cache (not rebuild_asset_metadata): it forces the same
        # full re-scan and full Overmind re-upload but keeps the Drone's cached
        # md5 values, so a routine fingerprint mismatch does not re-hash every ROM.
        return self.create_device_action(
            device.get("user_id"),
            device_id,
            "purge_asset_cache",
            {
                "reason": "rom_inventory_fingerprint_mismatch",
                "drone_rom_inventory_fingerprint": drone_fingerprint,
                "overmind_rom_inventory_fingerprint": overmind_fingerprint or None,
                "fingerprint_algorithm": ROM_INVENTORY_FINGERPRINT_ALGORITHM,
            },
        )

    def update_device_status_notifications(self, offline_seconds: int, limit: int = 0) -> None:
        cutoff = datetime.utcnow() - timedelta(seconds=max(1, int(offline_seconds)))
        devices = [
            device for device in self.devices.values()
            if device.get("approval_status", "approved") == "approved"
        ]
        devices.sort(key=lambda item: str(item.get("last_status_checked_at") or item.get("last_seen") or ""))
        limit = max(0, int(limit or 0))
        if limit:
            devices = devices[:limit]
        checked_at = datetime.utcnow()
        for device in devices:
            if device.get("approval_status", "approved") != "approved":
                continue
            device["last_status_checked_at"] = checked_at
            last_seen = device.get("last_seen")
            if isinstance(last_seen, datetime) and isinstance(cutoff, datetime):
                if last_seen.tzinfo is None and cutoff.tzinfo is not None:
                    last_seen = last_seen.replace(tzinfo=cutoff.tzinfo)
                elif last_seen.tzinfo is not None and cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=last_seen.tzinfo)

            is_online = bool(last_seen and last_seen >= cutoff)
            current = "online" if is_online else "offline"
            previous = device.get("last_known_status") or current
            if previous == current:
                device["last_known_status"] = current
                continue
            device["last_known_status"] = current
            self.add_swarm_notification(
                device.get("swarm_id"),
                f"drone_{current}",
                "Drone online" if current == "online" else "Drone offline",
                f"{self._device_label(device)} is {current}.",
                {"device": {"device_id": device.get("device_id"), "device_name": self._device_label(device)}, "status": current},
            )

    def update_device_public_reachability(self, internal_id: str, result: dict) -> bool:
        device = self.devices.get(internal_id)
        if not device:
            return False
        device["public_reachability"] = dict(result) if isinstance(result, dict) else {}
        if device["public_reachability"].get("resolvable") and device["public_reachability"].get("api_port"):
            try:
                device["api_port"] = int(device["public_reachability"]["api_port"])
                scheme = device.get("scheme") or "https"
                public_ip = (device.get("network") or {}).get("public_ip") or device["public_reachability"].get("public_ip")
                if public_ip:
                    host = str(public_ip)
                    if ":" in host and not host.startswith("["):
                        host = f"[{host}]"
                    port_suffix = "" if device["api_port"] == 443 and scheme == "https" else f":{device['api_port']}"
                    device["reachable_url"] = f"{scheme}://{host}{port_suffix}"
            except (TypeError, ValueError):
                pass
        return True

    def get_swarm_for_device(self, device_id: str, offline_seconds: int = 90) -> List[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return []
        cutoff = datetime.utcnow() - timedelta(seconds=max(1, int(offline_seconds)))
        # Build a set of peer-resolvable device_ids from the peer_checks buckets
        peer_resolvable_ids: set = set()
        for bucket in self.peer_checks.values():
            for check in bucket:
                if check.get("status") == "pass":
                    target = check.get("target_drone_id")
                    if target:
                        peer_resolvable_ids.add(str(target))
        output = []
        for peer in self.get_user_devices(device["user_id"]):
            resolved = peer.get("resolved_network") or {}
            ipv4 = resolved.get("ipv4") or []
            reported = peer.get("network") or {}
            public_ip = reported.get("public_ip") or reported.get("public")
            scheme = peer.get("scheme") or "https"
            api_port = peer.get("api_port") or 443
            public_host = str(public_ip or "").strip()
            if ":" in public_host and not public_host.startswith("["):
                public_host = f"[{public_host}]"
            peer_device_id = str(peer.get("device_id") or "")
            public_resolvable = peer_device_id in peer_resolvable_ids and bool(public_ip)
            port_suffix = "" if api_port == 443 and scheme == "https" else f":{api_port}"
            public_reachable_url = f"{scheme}://{public_host}{port_suffix}" if public_host and public_resolvable else None
            last_seen = peer.get("last_seen")
            peer_cutoff = cutoff
            if isinstance(last_seen, datetime) and isinstance(peer_cutoff, datetime):
                if last_seen.tzinfo is None and peer_cutoff.tzinfo is not None:
                    last_seen = last_seen.replace(tzinfo=peer_cutoff.tzinfo)
                elif last_seen.tzinfo is not None and peer_cutoff.tzinfo is None:
                    peer_cutoff = peer_cutoff.replace(tzinfo=last_seen.tzinfo)
            online = bool(last_seen and last_seen >= peer_cutoff)
            output.append({
                "drone_id": peer_device_id,
                "name": peer.get("device_name"),
                "local_ip": ipv4[0] if ipv4 else (peer.get("batocera_info") or {}).get("ip_address"),
                "public_ip": public_ip,
                "public_reachable_url": public_reachable_url,
                "public_resolvable": public_resolvable,
                "api_port": api_port,
                "scheme": scheme,
                "reachable_url": peer.get("reachable_url"),
                "online": online,
                "rom_systems": peer.get("rom_systems") or [],
                "last_speed_sample": peer.get("last_speed_sample"),
            })
        output.sort(key=lambda row: (not row["online"], str(row.get("name") or row.get("drone_id") or "").lower()))
        return output

    def verify_device_token(self, device_id: str, raw_token: str) -> Optional[dict]:
        """Return the device if its bearer token is valid."""
        from overmind.drone_security import verify_drone_token

        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        if device.get("approval_status", "approved") != "approved":
            return None
        if not verify_drone_token(raw_token, device.get("drone_token_hash") or ""):
            return None
        auth_token_id = device.get("authorization_token_id")
        if auth_token_id:
            token_rows = self.integration_tokens.get(device.get("user_id"), [])
            if postgres_store.available():
                token_rows = postgres_store.get_integration_tokens(device.get("user_id")) or token_rows
            backing = next((row for row in token_rows if row.get("id") == auth_token_id), None)
            if not backing or backing.get("revoked_at"):
                return None
        return device

    def rotate_device_token(self, user_id: str, device_id: str) -> Optional[dict]:
        """Rotate a Drone token and return the raw token once."""
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        raw_token = generate_drone_token()
        device["drone_token_hash"] = hash_drone_token(raw_token)
        device["token_rotated_at"] = datetime.utcnow()
        return {"device": device, "token": raw_token}

    def set_device_authorization_token(
        self,
        user_id: str,
        device_id: str,
        token_id: Optional[str],
        token_hash: Optional[str] = None,
        device_name: Optional[str] = None,
    ) -> bool:
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return False
        previous = device.get("authorization_token_id")
        if previous and previous != token_id:
            self.revoke_integration_token(user_id, previous)
        if postgres_store.available():
            relational = postgres_store.update_device_authorization(
                user_id,
                device_id,
                authorization_token_id=token_id,
                drone_token_hash=token_hash,
                device_name=device_name,
            )
            if relational:
                self.devices[relational["id"]] = relational
                return True
        device["authorization_token_id"] = token_id
        if token_hash:
            device["drone_token_hash"] = token_hash
        if device_name:
            device["device_name"] = device_name
        return True

    def update_device_auto_sync_policy(self, user_id: str, device_id: str, enabled: bool, systems: list) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        cleaned = sorted({str(system).strip() for system in systems if str(system).strip()})
        device["auto_sync_policy"] = {"enabled": bool(enabled), "systems": cleaned}
        return device["auto_sync_policy"]

    def update_device_name(self, device_id: str, device_name: str) -> bool:
        """Update a device's display name."""
        device = self.get_device_by_device_id(device_id)
        if not device:
            return False
        device["device_name"] = device_name
        return True

    def delete_device(self, user_id: str, device_id: str) -> bool:
        """Remove a user's device from the active swarm without deleting its identity."""
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return False

        internal_id = device["id"]
        swarm_id = device.get("swarm_id")
        device_label = self._device_label(device, device_id)
        device["approval_status"] = "removed"
        device["removed_at"] = datetime.utcnow()
        self.device_actions[internal_id] = []
        self.user_devices[user_id] = [
            did for did in self.user_devices.get(user_id, [])
            if did != internal_id
        ]
        self.pending_drone_connections.pop(device_id, None)
        self.add_swarm_notification(
            swarm_id,
            "drone_removed",
            "Drone removed from swarm",
            f"{device_label} was removed from the swarm.",
            {"device": {"device_id": device_id, "device_name": device_label}},
        )
        self._persist_state()
        return True

    def admin_delete_device(self, device_id: str) -> bool:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return False
        internal_id = device["id"]
        owner_id = device.get("user_id")
        self.devices.pop(internal_id, None)
        self.device_admin_claims.pop(internal_id, None)
        if self._asset_store_enabled():
            postgres_store.clear_device_assets(internal_id)
        for bucket in (
            self.roms,
            self.bios,
            self.artwork,
            self.gamelogs,
            self.device_actions,
            self.speed_samples,
            self.device_events,
            self.peer_checks,
            self.rom_sync_activity,
        ):
            bucket.pop(internal_id, None)
        self.download_states.pop(internal_id, None)
        self.pending_drone_connections.pop(device_id, None)
        self.approved_drone_tokens.pop(device_id, None)
        if owner_id in self.user_devices:
            self.user_devices[owner_id] = [row for row in self.user_devices.get(owner_id, []) if row != internal_id]
        if _cache and owner_id:
            _cache.invalidate_user_devices(owner_id)
        return True

    def admin_delete_swarm(self, swarm_id: str) -> bool:
        swarm = self.swarms.get(swarm_id)
        if not swarm:
            return False
        for device in list(self.devices.values()):
            if device.get("swarm_id") == swarm_id:
                self.admin_delete_device(device.get("device_id"))
        self.swarms.pop(swarm_id, None)
        self.swarm_memberships.pop(swarm_id, None)
        for invite_id, invite in list(self.invitations.items()):
            if invite.get("swarm_id") == swarm_id:
                self.invitations.pop(invite_id, None)
        return True

    def admin_delete_user(self, user_id: str) -> bool:
        user = self.users.get(user_id)
        if not user:
            return False
        for device in list(self.devices.values()):
            if device.get("user_id") == user_id:
                self.admin_delete_device(device.get("device_id"))
        for swarm_id, swarm in list(self.swarms.items()):
            if swarm.get("owner_id") == user_id:
                self.admin_delete_swarm(swarm_id)
        for members in self.swarm_memberships.values():
            members.pop(user_id, None)
        for invite_id, invite in list(self.invitations.items()):
            if invite.get("invited_by") == user_id or invite.get("email") == str(user.get("email") or "").lower():
                self.invitations.pop(invite_id, None)
        self.integration_tokens.pop(user_id, None)
        self.user_devices.pop(user_id, None)
        self.email_verifications.pop(user_id, None)
        for reset_id, reset in list(self.password_resets.items()):
            if reset.get("user_id") == user_id:
                self.password_resets.pop(reset_id, None)
        self.user_by_email.pop(user.get("email"), None)
        self.users.pop(user_id, None)
        return True

    def create_device_action(
        self,
        user_id: str,
        device_id: str,
        action_type: str,
        payload: Optional[dict] = None,
    ) -> Optional[dict]:
        """Queue an action for a user's device."""
        if postgres_store.available():
            relational = postgres_store.create_device_action(user_id, device_id, action_type, payload)
            if relational is not None:
                return relational
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None

        internal_id = device["id"]
        action = {
            "id": str(uuid.uuid4()),
            "device_id": device_id,
            "action": action_type,
            "status": "pending",
            "payload": payload or {},
            "created_at": datetime.utcnow(),
            "claimed_at": None,
            "completed_at": None,
            "message": None,
            "result": None,
            "result_received_at": None,
        }
        self.device_actions.setdefault(internal_id, []).append(action)
        return action

    def get_device_actions(self, user_id: str, device_id: str) -> Optional[List[dict]]:
        """Get currently queued or running actions for a user's device."""
        if postgres_store.available():
            return postgres_store.list_device_actions(user_id, device_id)
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        active = [
            action
            for action in self.device_actions.get(device["id"], [])
            if action.get("status") in {"pending", "in_progress"}
        ]
        return list(reversed(active))

    def clear_device_actions(self, user_id: str, device_id: str) -> Optional[int]:
        """Remove currently queued or running actions for a user's device."""
        if postgres_store.available():
            return postgres_store.clear_device_actions(user_id, device_id)
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        actions = self.device_actions.get(device["id"], [])
        retained = [
            action for action in actions
            if action.get("status") not in {"pending", "in_progress"}
        ]
        deleted_count = len(actions) - len(retained)
        self.device_actions[device["id"]] = retained
        return deleted_count

    def claim_next_device_action(self, device_id: str) -> Optional[dict]:
        """Claim the oldest pending action for a device."""
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        for action in self.device_actions.get(device["id"], []):
            if action.get("status") == "pending":
                action["status"] = "in_progress"
                action["claimed_at"] = datetime.utcnow()
                return action
        return None

    def claim_pending_device_actions(self, device_id: str, limit: int = 25) -> List[dict]:
        """Claim all currently pending actions for a device, bounded for one poll."""
        if postgres_store.available():
            relational = postgres_store.claim_pending_device_actions(device_id, limit=limit)
            if relational is not None:
                return relational
        device = self.get_device_by_device_id(device_id)
        if not device:
            return []
        claimed = []
        for action in self.device_actions.get(device["id"], []):
            if len(claimed) >= max(1, int(limit)):
                break
            if action.get("status") == "pending":
                action["status"] = "in_progress"
                action["claimed_at"] = datetime.utcnow()
                claimed.append(action)
        return claimed

    def complete_device_action(
        self,
        device_id: str,
        action_id: str,
        status: str,
        message: Optional[str] = None,
        result: Optional[dict] = None,
    ) -> Optional[dict]:
        """Mark an action completed or failed."""
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        if postgres_store.available():
            relational = postgres_store.complete_device_action(device_id, action_id, status, message, result)
            if relational is not None:
                if not relational.get("_already_terminal"):
                    self._notify_device_action_completion(device, device_id, relational, status, message)
                return relational
            return None
        for action in self.device_actions.get(device["id"], []):
            if action.get("id") == action_id:
                if action.get("status") in {"completed", "failed"}:
                    return action
                action["status"] = status
                action["completed_at"] = datetime.utcnow()
                action["message"] = message
                action["result"] = result
                action["result_received_at"] = datetime.utcnow() if result is not None else None
                if isinstance(result, dict):
                    self.store_action_result(device, result)
                    try:
                        postgres_store.store_action_result(device_id, action_id, result)
                    except Exception as error:
                        print(f"Overmind PostgreSQL action result persistence failed: {error}")
                self._notify_device_action_completion(device, device_id, action, status, message)
                return action
        return None

    def _notify_device_action_completion(self, device: dict, device_id: str, action: dict, status: str, message: Optional[str]) -> None:
        if str(action.get("action") or "") in {"sync_rom", "sync_system", "sync_bios", "sync_artwork", "cancel_download"}:
            return
        action_label = {
            "restart": "Remote Restart",
            "enable_kiosk": "Enable Kiosk Mode",
            "disable_kiosk": "Disable Kiosk Mode",
        }.get(
            str(action.get("action") or ""),
            str(action.get("action") or "action").replace("_", " ").title(),
        )
        device_label = self._device_label(device, device_id)
        status_label = "completed" if status == "completed" else "failed"
        message_text = f"{action_label} {status_label} on {device_label}."
        if message:
            message_text = f"{message_text} {message}"
        self.add_swarm_notification(
            device.get("swarm_id"),
            f"device_action_{status_label}",
            f"Remote action {status_label}",
            message_text,
            {
                "action_id": action.get("id"),
                "action": action.get("action"),
                "status": status,
                "device": {"device_id": device_id, "device_name": device_label},
            },
        )

    def store_action_result(self, device: dict, result: dict) -> None:
        """Persist returned action data on the device record for UI use."""
        result_type = result.get("type")
        if result_type in {"rom_metadata", "asset_metadata"}:
            self.store_rom_metadata(device.get("device_id"), result)
        if result_type == "emulator_configs":
            self.store_device_emulator_configs(device.get("device_id"), result)
            device["emulator_configs"] = merge_emulator_configs(device.get("emulator_configs"), result)
        if result_type == "log_sources":
            self.store_device_log_sources(device.get("device_id"), result)
            return
        if result_type == "game_logs":
            device["game_logs"] = merge_game_logs(device.get("game_logs"), result)
            self._store_game_log_sessions(device.get("device_id"), result.get("sessions") if isinstance(result.get("sessions"), list) else [])
        if result_type in {"rom_sync", "bios_sync", "artwork_sync"}:
            for activity in result.get("activity") if isinstance(result.get("activity"), list) else []:
                if isinstance(activity, dict):
                    self.add_rom_sync_activity(device.get("device_id"), activity)

    def store_rom_metadata(self, device_id: str, metadata: dict) -> None:
        device = self.get_device_by_device_id(device_id)
        if not device or not isinstance(metadata, dict):
            return
        swarm_id = device.get("swarm_id")
        update_mode = metadata.get("update_mode")
        is_inventory_chunk = update_mode == "inventory_chunk"
        is_inventory_delta = update_mode == "inventory_delta"
        if update_mode == "rom_hash_patch":
            self._apply_rom_metadata_hash_patch(device, metadata)
            progress = metadata.get("hash_progress") if isinstance(metadata.get("hash_progress"), dict) else {}
            if progress.get("complete"):
                self.update_device_rom_inventory_fingerprint(device_id, compute_overmind=True)
            self.update_device_last_seen(device["id"])
            return
        before = {
            "rom": self._master_snapshot_for_swarm(swarm_id, "rom"),
            "bios": self._master_snapshot_for_swarm(swarm_id, "bios"),
            "artwork": self._master_snapshot_for_swarm(swarm_id, "artwork"),
        } if swarm_id and not is_inventory_chunk else {}
        row_metadata = metadata
        if update_mode == "inventory_chunk":
            row_metadata = metadata
            metadata = self._merge_rom_metadata_inventory_chunk(device, metadata)
        systems = metadata.get("systems") if isinstance(metadata.get("systems"), list) else []
        self.update_device_last_seen(device["id"], rom_systems=systems)
        device["rom_metadata"] = metadata

        grouped: Dict[str, list] = {}
        for item in row_metadata.get("roms") if isinstance(row_metadata.get("roms"), list) else []:
            if not isinstance(item, dict):
                continue
            system_name = str(item.get("system") or item.get("system_name") or "").strip()
            if not system_name:
                continue
            rom_name = str(item.get("rom_name") or item.get("name") or item.get("title") or "").strip()
            file_path = str(item.get("file_path") or item.get("relative_path") or item.get("rom_path") or item.get("rom_file") or rom_name).strip()
            grouped.setdefault(system_name, []).append({
                "rom_name": rom_name or file_path,
                "rom_md5": item.get("rom_md5") or item.get("md5") or item.get("hash"),
                "file_path": file_path,
                "file_size": item.get("file_size") or item.get("byte_count") or item.get("size"),
                "entry_type": item.get("entry_type") or "file",
                "metadata_source": item.get("metadata_source"),
                "source": item.get("source"),
                "modified_time": item.get("modified_time") or item.get("mtime"),
            })
        replace_all = bool(row_metadata.get("replace_all"))
        if is_inventory_chunk and replace_all:
            self._store_asset_inventory_chunk(device_id, device, row_metadata, grouped)
        else:
            if replace_all:
                self._clear_device_assets(device["id"])
            if is_inventory_delta or not replace_all:
                self._delete_asset_rows(device_id, "rom", row_metadata.get("roms") or [])
                self._delete_asset_rows(device_id, "bios", row_metadata.get("bios") or [])
                self._delete_asset_rows(device_id, "artwork", row_metadata.get("artwork") or [])
            for system_name, rows in grouped.items():
                self.add_roms(device_id, system_name, rows, replace=False)
            if row_metadata.get("bios"):
                self.add_bios(device_id, row_metadata["bios"], replace=False)
            if row_metadata.get("artwork"):
                self.add_artwork(device_id, row_metadata["artwork"], replace=False)
            deleted = row_metadata.get("deleted") if is_inventory_delta and isinstance(row_metadata.get("deleted"), dict) else {}
            self._delete_asset_rows(device_id, "rom", deleted.get("roms") or [])
            self._delete_asset_rows(device_id, "bios", deleted.get("bios") or [])
            self._delete_asset_rows(device_id, "artwork", deleted.get("artwork") or [])
        drone_fingerprint = str(row_metadata.get("rom_inventory_fingerprint") or "").strip() or None
        delta_complete = False
        if is_inventory_delta:
            delta_total = row_metadata.get("delta_total")
            delta_index = row_metadata.get("delta_index")
            delta_complete = bool(row_metadata.get("inventory_complete"))
            if delta_total is None:
                delta_complete = True
            elif not delta_complete:
                try:
                    delta_complete = int(delta_index or 0) >= int(delta_total) - 1
                except (TypeError, ValueError):
                    delta_complete = True
        should_compute_fingerprint = (
            (is_inventory_chunk and bool(row_metadata.get("inventory_complete")))
            or update_mode == "inventory"
            or (is_inventory_delta and delta_complete)
            or update_mode is None
        )
        if drone_fingerprint or should_compute_fingerprint:
            self.update_device_rom_inventory_fingerprint(
                device_id,
                drone_fingerprint=drone_fingerprint,
                compute_overmind=should_compute_fingerprint,
            )
            if drone_fingerprint:
                metadata["rom_inventory_fingerprint"] = drone_fingerprint
                metadata["rom_inventory_fingerprint_algorithm"] = (
                    row_metadata.get("rom_inventory_fingerprint_algorithm") or ROM_INVENTORY_FINGERPRINT_ALGORITHM
                )
        after = {
            "rom": self._master_snapshot_for_swarm(swarm_id, "rom"),
            "bios": self._master_snapshot_for_swarm(swarm_id, "bios"),
            "artwork": self._master_snapshot_for_swarm(swarm_id, "artwork"),
        } if swarm_id and not is_inventory_chunk else {}
        if not is_inventory_chunk:
            self._notify_master_list_changes(swarm_id, before, after)

    def _merge_rom_metadata_inventory_chunk(self, device: dict, incoming: dict) -> dict:
        """Retain lightweight inventory progress without copying every asset row."""
        chunk_index = int(incoming.get("chunk_index") or 0)
        previous = device.get("rom_metadata") if isinstance(device.get("rom_metadata"), dict) else {}
        merged = dict(previous) if chunk_index else {}
        for key in ("type", "device_id", "update_mode", "inventory_id", "chunk_total", "inventory_counts", "collected_at", "roms_root", "bios_root", "cache", "rom_inventory_fingerprint", "rom_inventory_fingerprint_algorithm"):
            if key in incoming:
                merged[key] = incoming[key]
        for key in ("systems", "gamelists"):
            if key in incoming:
                merged[key] = incoming[key]
        merged["chunk_index"] = chunk_index
        merged["inventory_complete"] = bool(incoming.get("inventory_complete"))
        received = dict(merged.get("inventory_received") or {}) if chunk_index else {"roms": 0, "bios": 0, "artwork": 0}
        received["roms"] = int(received.get("roms") or 0) + len(incoming.get("roms") if isinstance(incoming.get("roms"), list) else [])
        received["bios"] = int(received.get("bios") or 0) + len(incoming.get("bios") if isinstance(incoming.get("bios"), list) else [])
        received["artwork"] = int(received.get("artwork") or 0) + len(incoming.get("artwork") if isinstance(incoming.get("artwork"), list) else [])
        merged["inventory_received"] = received
        return merged

    def _apply_rom_metadata_hash_patch(self, device: dict, metadata: dict) -> None:
        """Apply ROM hash patches directly to stored ROM rows without rebuilding snapshots."""
        internal_id = device.get("id")
        patches = metadata.get("roms") if isinstance(metadata.get("roms"), list) else []
        if self._asset_store_enabled():
            postgres_store.update_rom_hashes(internal_id, patches)
            summary = dict(device.get("rom_metadata") or {})
            summary["type"] = metadata.get("type") or summary.get("type") or "asset_metadata"
            summary["update_mode"] = "rom_hash_patch"
            summary["collected_at"] = metadata.get("collected_at") or summary.get("collected_at")
            summary["hash_progress"] = metadata.get("hash_progress") or summary.get("hash_progress")
            device["rom_metadata"] = summary
            return
        rows = self.roms.setdefault(internal_id, [])

        def row_key(row: dict) -> tuple:
            system = str(row.get("system") or row.get("system_name") or "").strip().lower()
            path = str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_file") or row.get("rom_name") or "").replace("\\", "/").lstrip("./").lower()
            return system, path

        by_key = {row_key(row): row for row in rows}
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            key = row_key(patch)
            if not key[0] or not key[1]:
                continue
            md5_value = patch.get("rom_md5") or patch.get("md5") or patch.get("hash")
            current = by_key.get(key)
            if current is None:
                current = {
                    "id": str(uuid.uuid4()),
                    "device_id": device.get("device_id"),
                    "system_name": patch.get("system") or patch.get("system_name"),
                    "rom_name": patch.get("rom_name") or patch.get("name") or patch.get("title") or patch.get("file_path"),
                    "file_path": patch.get("file_path") or patch.get("relative_path") or patch.get("rom_path") or patch.get("rom_file"),
                    "file_size": patch.get("file_size") or patch.get("byte_count") or patch.get("size"),
                    "entry_type": patch.get("entry_type") or "file",
                    "added_at": datetime.utcnow(),
                }
                rows.append(current)
                by_key[key] = current
            current["rom_md5"] = md5_value
            current["last_seen"] = datetime.utcnow()
        summary = dict(device.get("rom_metadata") or {})
        summary["type"] = metadata.get("type") or summary.get("type") or "asset_metadata"
        summary["update_mode"] = "rom_hash_patch"
        summary["collected_at"] = metadata.get("collected_at") or summary.get("collected_at")
        summary["hash_progress"] = metadata.get("hash_progress") or summary.get("hash_progress")
        device["rom_metadata"] = summary

    def add_speed_sample(self, device_id: str, sample: dict) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        entry = {
            "id": str(uuid.uuid4()),
            "device_id": device_id,
            "sampled_at": datetime.utcnow(),
            "upload_mbps": sample.get("upload_mbps"),
            "download_mbps": sample.get("download_mbps"),
            "latency_ms": sample.get("latency_ms"),
            "source": sample.get("source") or "drone",
        }
        bucket = self.speed_samples.setdefault(device["id"], [])
        bucket.append(entry)
        del bucket[:-200]
        device["last_speed_sample"] = entry
        return entry

    def add_device_event(self, device_id: str, event: dict) -> Optional[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        entry = {
            "id": str(uuid.uuid4()),
            "device_id": device_id,
            "event_type": event.get("event_type") or event.get("type"),
            "timestamp": event.get("timestamp") or datetime.utcnow(),
            "system": event.get("system"),
            "rom": event.get("rom"),
            "path": event.get("path"),
            "metadata": event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
            "received_at": datetime.utcnow(),
        }
        bucket = self.device_events.setdefault(device["id"], [])
        bucket.append(entry)
        del bucket[:-500]
        return entry

    def add_peer_checks(self, device_id: str, results: list) -> Optional[List[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        cleaned = []
        for result in results:
            if not isinstance(result, dict):
                continue
            status = str(result.get("status") or "").lower()
            if status not in {"pass", "fail"}:
                status = "fail"
            cleaned.append({
                "id": str(uuid.uuid4()),
                "source_drone_id": result.get("source_drone_id") or result.get("source") or device_id,
                "target_drone_id": result.get("target_drone_id") or result.get("target"),
                "target_address": result.get("target_address"),
                "status": status,
                "latency_ms": result.get("latency_ms"),
                "failure_reason": result.get("failure_reason") if status == "fail" else None,
                "checked_at": result.get("checked_at") or datetime.utcnow(),
                "received_at": datetime.utcnow(),
            })
        bucket = self.peer_checks.setdefault(device["id"], [])
        bucket.extend(cleaned)
        del bucket[:-500]
        device["peer_checks"] = list(reversed(bucket))[:50]
        return cleaned

    def get_latest_peer_checks(self, device_id: str) -> List[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return []
        devices_by_id = {row.get("device_id"): row for row in self.get_user_devices(device["user_id"])}
        latest: Dict[str, dict] = {}
        for check in self.peer_checks.get(device["id"], []):
            target_id = check.get("target_drone_id")
            if not target_id or target_id == device_id:
                continue
            previous = latest.get(target_id)
            if previous is None or str(check.get("checked_at") or "") >= str(previous.get("checked_at") or ""):
                peer = devices_by_id.get(target_id) or {}
                info = peer.get("system_info") or {}
                latest[target_id] = {
                    **check,
                    "target_name": peer.get("device_name") or info.get("hostname") or target_id,
                    "source_name": device.get("device_name") or (device.get("system_info") or {}).get("hostname") or device_id,
                }
        return sorted(latest.values(), key=lambda row: str(row.get("target_name") or row.get("target_drone_id") or "").lower())

    def is_drone_peer_resolvable(self, device_id: str) -> bool:
        """Return True if any other drone has a passing peer-check targeting this device."""
        for bucket in self.peer_checks.values():
            for check in bucket:
                if str(check.get("target_drone_id") or "") == device_id and check.get("status") == "pass":
                    return True
        return False

    def get_peer_resolvers(self, device_id: str) -> List[dict]:
        """Return list of drones that have a passing peer-check for this device (latest per source)."""
        latest: Dict[str, dict] = {}
        for bucket in self.peer_checks.values():
            for check in bucket:
                if str(check.get("target_drone_id") or "") != device_id:
                    continue
                src = str(check.get("source_drone_id") or "")
                if not src or src == device_id:
                    continue
                prev = latest.get(src)
                if prev is None or str(check.get("checked_at") or "") >= str(prev.get("checked_at") or ""):
                    latest[src] = check
        devices_by_id = {}
        for internal_id, device in self.devices.items():
            dev_id = device.get("device_id")
            if dev_id:
                devices_by_id[str(dev_id)] = device
        result = []
        for src, check in latest.items():
            if check.get("status") != "pass":
                continue
            peer = devices_by_id.get(src) or {}
            result.append({
                **check,
                "source_name": peer.get("device_name") or (peer.get("system_info") or {}).get("hostname") or src,
            })
        return sorted(result, key=lambda row: str(row.get("source_name") or row.get("source_drone_id") or "").lower())

    def get_speed_samples(self, user_id: str, device_id: str) -> Optional[List[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        return list(reversed(self.speed_samples.get(device["id"], [])))
    
    def device_exists(self, user_id: str, device_id: str) -> bool:
        """Check if device exists for user."""
        if postgres_store.available():
            relational = postgres_store.get_device_by_device_id(device_id)
            if relational is not None:
                return relational["user_id"] == user_id and relational.get("approval_status", "approved") == "approved"
        for device in self.devices.values():
            if device["device_id"] == device_id:
                return device["user_id"] == user_id and device.get("approval_status", "approved") == "approved"
        return False

    def count_device_roms(self, device_id: str) -> int:
        if postgres_store.assets_enabled():
            result = postgres_store.count_device_assets(device_id, "rom")
            if result is not None:
                return result
        if postgres_store.available():
            result = postgres_store.count_device_roms(device_id)
            if result is not None:
                return result
        device = self.get_device_by_device_id(device_id)
        if not device:
            return 0
        return len(self.roms.get(device["id"], []))
    
    # ROM operations
    def _clean_rom_rows(self, device_id: str, system_name: str, roms: list) -> list:
        cleaned = []
        for rom in roms:
            if not isinstance(rom, dict):
                continue
            cleaned.append({
                "id": str(uuid.uuid4()),
                "device_id": device_id,
                "system_name": system_name,
                "rom_name": rom.get("rom_name"),
                "rom_md5": rom.get("rom_md5"),
                "file_path": rom.get("file_path"),
                "file_size": rom.get("file_size"),
                "entry_type": rom.get("entry_type") or "file",
                "metadata_source": rom.get("metadata_source"),
                "source": rom.get("source"),
                "modified_time": rom.get("modified_time"),
                "added_at": datetime.utcnow(),
                "last_seen": datetime.utcnow(),
            })
        return cleaned

    def add_roms(self, device_id: str, system_name: str, roms: list, *, replace: bool = True) -> List[str]:
        """Add ROMs for a device. Returns list of ROM IDs."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []

        internal_id = internal_device["id"]
        cleaned = self._clean_rom_rows(device_id, system_name, roms)
        if self._asset_store_enabled():
            return postgres_store.upsert_device_assets(
                internal_id,
                device_id,
                "rom",
                cleaned,
                replace_system=system_name if replace else None,
            )
        rom_ids = []
        
        if internal_id not in self.roms:
            self.roms[internal_id] = []
        if replace:
            self.roms[internal_id] = [
                r for r in self.roms[internal_id]
                if r.get("system_name") != system_name
            ]
        else:
            incoming_keys = {
                (
                    str(row.get("system_name") or "").strip().lower(),
                    str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower(),
                )
                for row in cleaned
            }
            self.roms[internal_id] = [
                row for row in self.roms[internal_id]
                if (
                    str(row.get("system_name") or "").strip().lower(),
                    str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower(),
                ) not in incoming_keys
            ]
        
        for rom_entry in cleaned:
            rom_id = rom_entry["id"]
            self.roms[internal_id].append(rom_entry)
            rom_ids.append(rom_id)
        
        return rom_ids

    def _rom_key(self, rom: dict) -> tuple:
        system = str(rom.get("system_name") or "").strip().lower()
        md5 = str(rom.get("rom_md5") or "").strip().lower()
        if md5:
            return ("md5", md5)
        path = str(rom.get("file_path") or rom.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return (system, path)

    def _clean_bios_rows(self, device_id: str, bios_entries: list) -> list:
        cleaned = []
        for item in bios_entries:
            if not isinstance(item, dict):
                continue
            file_path = str(item.get("file_path") or item.get("relative_path") or item.get("path") or item.get("name") or "").strip()
            if not file_path:
                continue
            cleaned.append({
                "id": str(uuid.uuid4()),
                "device_id": device_id,
                "bios_name": item.get("bios_name") or item.get("name") or file_path,
                "file_path": file_path,
                "relative_path": file_path,
                "bios_md5": item.get("bios_md5") or item.get("md5") or item.get("hash"),
                "md5": item.get("bios_md5") or item.get("md5") or item.get("hash"),
                "file_size": item.get("file_size") or item.get("byte_count") or item.get("size"),
                "byte_count": item.get("byte_count") or item.get("file_size") or item.get("size"),
                "unique_id": item.get("unique_id"),
                "modified_time": item.get("modified_time") or item.get("mtime"),
                "added_at": datetime.utcnow(),
                "last_seen": datetime.utcnow(),
            })
        return cleaned

    def add_bios(self, device_id: str, bios_entries: list, *, replace: bool = True) -> List[str]:
        """Replace BIOS metadata for a device. Returns list of BIOS row IDs."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        internal_id = internal_device["id"]
        row_ids = []
        cleaned = self._clean_bios_rows(device_id, bios_entries)
        if self._asset_store_enabled():
            return postgres_store.upsert_device_assets(internal_id, device_id, "bios", cleaned, replace=replace)
        if replace or internal_id not in self.bios:
            self.bios[internal_id] = []
        elif cleaned:
            incoming_keys = {
                str(entry.get("file_path") or entry.get("relative_path") or entry.get("bios_name") or "").replace("\\", "/").strip().lstrip("./").lower()
                for entry in cleaned
            }
            self.bios[internal_id] = [
                row for row in self.bios[internal_id]
                if str(row.get("file_path") or row.get("relative_path") or row.get("bios_name") or "").replace("\\", "/").strip().lstrip("./").lower() not in incoming_keys
            ]
        for entry in cleaned:
            self.bios[internal_id].append(entry)
            row_ids.append(entry["id"])
        return row_ids

    def _bios_key(self, bios: dict) -> tuple:
        md5 = str(bios.get("bios_md5") or bios.get("md5") or "").strip().lower()
        if md5:
            return ("md5", md5)
        path = str(bios.get("file_path") or bios.get("relative_path") or bios.get("bios_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return ("path", path)

    def get_device_bios(self, device_id: str) -> List[dict]:
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        return self._asset_rows_for_device_internal(internal_device["id"], "bios")

    def get_master_bios_for_device(self, user_id: str, selected_device_id: str) -> Optional[List[dict]]:
        selected = self.get_device_by_device_id(selected_device_id)
        if not selected or selected["user_id"] != user_id:
            return None
        devices = {device["device_id"]: device for device in self.get_user_devices(user_id)}
        selected_keys = {self._bios_key(row) for row in self._asset_rows_for_device_internal(selected["id"], "bios")}
        master: Dict[tuple, dict] = {}
        for bios in self._asset_rows_for_devices(list(devices.values()), "bios"):
            device = devices.get(bios.get("device_id")) or {}
            if device:
                key = self._bios_key(bios)
                if not key[1]:
                    continue
                row = master.setdefault(key, {
                    "bios_name": bios.get("bios_name") or bios.get("file_path"),
                    "file_path": bios.get("file_path") or bios.get("bios_name"),
                    "relative_path": bios.get("relative_path") or bios.get("file_path"),
                    "bios_md5": bios.get("bios_md5") or bios.get("md5"),
                    "md5": bios.get("bios_md5") or bios.get("md5"),
                    "file_size": bios.get("file_size") or bios.get("byte_count"),
                    "last_seen": bios.get("last_seen") or bios.get("added_at"),
                    "devices": [],
                    "present_on_selected": key in selected_keys,
                })
                info = device.get("system_info") or {}
                row["devices"].append({
                    "device_id": device["device_id"],
                    "device_name": device.get("device_name") or info.get("hostname") or device["device_id"],
                })
                if not row.get("bios_md5") and (bios.get("bios_md5") or bios.get("md5")):
                    row["bios_md5"] = bios.get("bios_md5") or bios.get("md5")
                    row["md5"] = row["bios_md5"]
                if not row.get("file_size") and (bios.get("file_size") or bios.get("byte_count")):
                    row["file_size"] = bios.get("file_size") or bios.get("byte_count")
        rows = list(master.values())
        rows.sort(key=lambda row: str(row.get("file_path") or "").lower())
        return rows

    def get_swarm_master_bios(self, user_id: str) -> List[dict]:
        master: Dict[tuple, dict] = {}
        devices = {device["id"]: device for device in self.get_user_devices(user_id)}
        for bios in self._asset_rows_for_devices(list(devices.values()), "bios"):
            device = devices.get(bios.get("_device_internal_id")) or {}
            if device:
                key = self._bios_key(bios)
                if not key[1]:
                    continue
                row = master.setdefault(key, {
                    "bios_name": bios.get("bios_name") or bios.get("file_path"),
                    "file_path": bios.get("file_path") or bios.get("bios_name"),
                    "relative_path": bios.get("relative_path") or bios.get("file_path"),
                    "filenames": [],
                    "bios_md5": bios.get("bios_md5") or bios.get("md5"),
                    "md5": bios.get("bios_md5") or bios.get("md5"),
                    "file_size": bios.get("file_size") or bios.get("byte_count"),
                    "last_seen": bios.get("last_seen") or bios.get("added_at"),
                    "devices": [],
                })
                filename = bios.get("file_path") or bios.get("bios_name")
                if filename and filename not in row["filenames"]:
                    row["filenames"].append(filename)
                info = device.get("system_info") or {}
                row["devices"].append({
                    "device_id": device["device_id"],
                    "device_name": device.get("device_name") or info.get("hostname") or device["device_id"],
                })
        rows = list(master.values())
        rows.sort(key=lambda row: str(row.get("file_path") or "").lower())
        return rows

    def _clean_artwork_rows(self, device_id: str, artwork_entries: list) -> list:
        cleaned = []
        for item in artwork_entries:
            if not isinstance(item, dict):
                continue
            system = str(item.get("system") or item.get("system_name") or "").strip()
            rom_path = str(item.get("rom_path") or item.get("file_path") or item.get("rom_name") or "").strip()
            types = item.get("artwork_types") if isinstance(item.get("artwork_types"), list) else []
            types = sorted({str(value).strip() for value in types if str(value).strip()})
            if not system or not rom_path or not types:
                continue
            cleaned.append({
                "id": str(uuid.uuid4()),
                "device_id": device_id,
                "asset_type": "artwork",
                "system_name": system,
                "system": system,
                "rom_name": item.get("rom_name") or rom_path,
                "rom_path": rom_path,
                "file_path": rom_path,
                "title": item.get("title") or item.get("rom_name") or rom_path,
                "artwork_types": types,
                "metadata_source": item.get("metadata_source") or "gamelist.xml",
                "added_at": datetime.utcnow(),
                "last_seen": datetime.utcnow(),
            })
        return cleaned

    def add_artwork(self, device_id: str, artwork_entries: list, *, replace: bool = True) -> List[str]:
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        internal_id = internal_device["id"]
        row_ids = []
        cleaned = self._clean_artwork_rows(device_id, artwork_entries)
        if self._asset_store_enabled():
            return postgres_store.upsert_device_assets(internal_id, device_id, "artwork", cleaned, replace=replace)
        if replace or internal_id not in self.artwork:
            self.artwork[internal_id] = []
        elif cleaned:
            incoming_keys = {
                (
                    str(row.get("system_name") or row.get("system") or "").strip().lower(),
                    str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower(),
                )
                for row in cleaned
            }
            self.artwork[internal_id] = [
                row for row in self.artwork[internal_id]
                if (
                    str(row.get("system_name") or row.get("system") or "").strip().lower(),
                    str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower(),
                ) not in incoming_keys
            ]
        for row in cleaned:
            self.artwork[internal_id].append(row)
            row_ids.append(row["id"])
        return row_ids

    def _artwork_key(self, row: dict, artwork_type: str) -> tuple:
        return (
            str(row.get("system_name") or row.get("system") or "").strip().lower(),
            str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower(),
            str(artwork_type or "").strip().lower(),
        )

    def get_master_artwork_for_device(self, user_id: str, selected_device_id: str) -> Optional[List[dict]]:
        selected = self.get_device_by_device_id(selected_device_id)
        if not selected or selected["user_id"] != user_id:
            return None
        devices = {device["device_id"]: device for device in self.get_user_devices(user_id)}
        selected_keys = {
            self._artwork_key(row, artwork_type)
            for row in self._asset_rows_for_device_internal(selected["id"], "artwork")
            for artwork_type in row.get("artwork_types", [])
        }
        master: Dict[tuple, dict] = {}
        for item in self._asset_rows_for_devices(list(devices.values()), "artwork"):
            device = devices.get(item.get("device_id")) or {}
            if device:
                for artwork_type in item.get("artwork_types", []):
                    key = self._artwork_key(item, artwork_type)
                    if not key[0] or not key[1] or not key[2]:
                        continue
                    row = master.setdefault(key, {
                        "asset_type": "artwork",
                        "system_name": item.get("system_name") or item.get("system"),
                        "system": item.get("system_name") or item.get("system"),
                        "rom_name": item.get("rom_name") or item.get("rom_path"),
                        "rom_path": item.get("rom_path") or item.get("file_path"),
                        "file_path": item.get("rom_path") or item.get("file_path"),
                        "title": item.get("title"),
                        "artwork_type": artwork_type,
                        "devices": [],
                        "present_on_selected": key in selected_keys,
                    })
                    info = device.get("system_info") or {}
                    row["devices"].append({
                        "device_id": device["device_id"],
                        "device_name": device.get("device_name") or info.get("hostname") or device["device_id"],
                    })
        rows = list(master.values())
        rows.sort(key=lambda row: (str(row.get("system_name") or "").lower(), str(row.get("rom_path") or "").lower(), str(row.get("artwork_type") or "").lower()))
        return rows

    def get_master_roms_for_device(self, user_id: str, selected_device_id: str) -> Optional[List[dict]]:
        selected = self.get_device_by_device_id(selected_device_id)
        if not selected or selected["user_id"] != user_id:
            return None
        devices = {device["device_id"]: device for device in self.get_user_devices(user_id)}
        selected_keys = {self._rom_key(rom) for rom in self._asset_rows_for_device_internal(selected["id"], "rom")}
        master: Dict[tuple, dict] = {}
        for rom in self._asset_rows_for_devices(list(devices.values()), "rom"):
            device = devices.get(rom.get("device_id")) or {}
            if device:
                key = self._rom_key(rom)
                if not key[0] or not key[1]:
                    continue
                row = master.setdefault(key, {
                    "system_name": rom.get("system_name"),
                    "rom_name": rom.get("rom_name") or rom.get("file_path"),
                    "file_path": rom.get("file_path") or rom.get("rom_name"),
                    "rom_md5": rom.get("rom_md5"),
                    "file_size": rom.get("file_size"),
                    "entry_type": rom.get("entry_type") or "file",
                    "last_seen": rom.get("last_seen") or rom.get("added_at"),
                    "devices": [],
                    "present_on_selected": key in selected_keys,
                })
                info = device.get("system_info") or {}
                row["devices"].append({
                    "device_id": device["device_id"],
                    "device_name": device.get("device_name") or info.get("hostname") or device["device_id"],
                })
                if not row.get("rom_md5") and rom.get("rom_md5"):
                    row["rom_md5"] = rom.get("rom_md5")
                if not row.get("file_size") and rom.get("file_size"):
                    row["file_size"] = rom.get("file_size")
        rows = list(master.values())
        rows.sort(key=lambda row: (str(row.get("system_name") or "").lower(), str(row.get("file_path") or "").lower()))
        return rows

    def get_swarm_master_roms(self, user_id: str) -> List[dict]:
        master: Dict[tuple, dict] = {}
        devices = {device["id"]: device for device in self.get_user_devices(user_id)}
        for rom in self._asset_rows_for_devices(list(devices.values()), "rom"):
            device = devices.get(rom.get("_device_internal_id")) or {}
            if device:
                key = self._rom_key(rom)
                if not key[1]:
                    continue
                row = master.setdefault(key, {
                    "system_name": rom.get("system_name"),
                    "rom_name": rom.get("rom_name") or rom.get("file_path"),
                    "file_path": rom.get("file_path") or rom.get("rom_name"),
                    "filenames": [],
                    "rom_md5": rom.get("rom_md5"),
                    "file_size": rom.get("file_size"),
                    "entry_type": rom.get("entry_type") or "file",
                    "metadata_source": rom.get("metadata_source"),
                    "source": rom.get("source"),
                    "last_seen": rom.get("last_seen") or rom.get("added_at"),
                    "devices": [],
                })
                filename = rom.get("file_path") or rom.get("rom_name")
                if filename and filename not in row["filenames"]:
                    row["filenames"].append(filename)
                info = device.get("system_info") or {}
                row["devices"].append({
                    "device_id": device["device_id"],
                    "device_name": device.get("device_name") or info.get("hostname") or device["device_id"],
                })
                if not row.get("rom_md5") and rom.get("rom_md5"):
                    row["rom_md5"] = rom.get("rom_md5")
                if not row.get("file_size") and rom.get("file_size"):
                    row["file_size"] = rom.get("file_size")
        rows = list(master.values())
        rows.sort(key=lambda row: (str(row.get("system_name") or "").lower(), str(row.get("file_path") or "").lower()))
        return rows

    @staticmethod
    def _filter_master_rows(
        rows: List[dict],
        *,
        asset_type: str,
        query: Optional[str] = None,
        system_name: Optional[str] = None,
        status: Optional[str] = None,
        artwork_type: Optional[str] = None,
    ) -> List[dict]:
        filtered = rows
        clean_query = str(query or "").strip().lower()
        if clean_query:
            filtered = [
                row for row in filtered
                if clean_query in " ".join(str(value or "") for value in (
                    row.get("system_name"),
                    row.get("rom_name"),
                    row.get("file_path") or row.get("rom_path"),
                    row.get("rom_md5") or row.get("bios_md5"),
                    row.get("title"),
                    row.get("artwork_type"),
                    " ".join(row.get("filenames") or []),
                    " ".join((device.get("device_name") or device.get("device_id") or "") for device in (row.get("devices") or [])),
                )).lower()
            ]
        clean_system = str(system_name or "").strip().lower()
        if clean_system:
            filtered = [row for row in filtered if str(row.get("system_name") or "").strip().lower() == clean_system]
        clean_type = str(artwork_type or "").strip().lower()
        if asset_type == "artwork" and clean_type:
            filtered = [row for row in filtered if str(row.get("artwork_type") or "").strip().lower() == clean_type]
        clean_status = str(status or "").strip().lower()
        if clean_status == "missing":
            filtered = [row for row in filtered if not row.get("present_on_selected")]
        elif clean_status == "present":
            filtered = [row for row in filtered if row.get("present_on_selected")]
        return filtered

    def _master_page_from_asset_rows(self, asset_type: str, asset_rows: List[dict], user_id: str, *, include_presence: bool) -> List[dict]:
        devices_by_id = {device["id"]: device for device in self.get_user_devices(user_id)}
        master: Dict[str, dict] = {}
        for item in asset_rows:
            device = devices_by_id.get(item.get("_device_internal_id")) or {}
            if not device:
                continue
            key = str(item.get("_master_key") or "")
            if not key:
                continue
            source = {
                "device_id": device["device_id"],
                "device_name": device.get("device_name") or (device.get("system_info") or {}).get("hostname") or device["device_id"],
            }
            if asset_type == "rom":
                row = master.setdefault(key, {
                    "system_name": item.get("system_name"),
                    "rom_name": item.get("rom_name") or item.get("file_path"),
                    "file_path": item.get("file_path") or item.get("rom_name"),
                    "filenames": [],
                    "rom_md5": item.get("rom_md5"),
                    "file_size": item.get("file_size"),
                    "entry_type": item.get("entry_type") or "file",
                    "last_seen": item.get("last_seen") or item.get("added_at"),
                    "devices": [],
                    **({"present_on_selected": bool(item.get("_present_on_selected"))} if include_presence else {}),
                })
                filename = item.get("file_path") or item.get("rom_name")
                if filename and filename not in row["filenames"]:
                    row["filenames"].append(filename)
                if not row.get("rom_md5") and item.get("rom_md5"):
                    row["rom_md5"] = item.get("rom_md5")
                if not row.get("file_size") and item.get("file_size"):
                    row["file_size"] = item.get("file_size")
            elif asset_type == "bios":
                row = master.setdefault(key, {
                    "bios_name": item.get("bios_name") or item.get("file_path"),
                    "file_path": item.get("file_path") or item.get("bios_name"),
                    "relative_path": item.get("relative_path") or item.get("file_path"),
                    "bios_md5": item.get("bios_md5") or item.get("md5"),
                    "md5": item.get("bios_md5") or item.get("md5"),
                    "file_size": item.get("file_size") or item.get("byte_count"),
                    "last_seen": item.get("last_seen") or item.get("added_at"),
                    "devices": [],
                    "present_on_selected": bool(item.get("_present_on_selected")),
                })
            else:
                row = master.setdefault(key, {
                    "asset_type": "artwork",
                    "system_name": item.get("system_name") or item.get("system"),
                    "system": item.get("system_name") or item.get("system"),
                    "rom_name": item.get("rom_name") or item.get("rom_path"),
                    "rom_path": item.get("rom_path") or item.get("file_path"),
                    "file_path": item.get("rom_path") or item.get("file_path"),
                    "title": item.get("title"),
                    "artwork_type": item.get("_artwork_type"),
                    "devices": [],
                    "present_on_selected": bool(item.get("_present_on_selected")),
                })
            if include_presence and item.get("_present_on_selected"):
                row["present_on_selected"] = True
            if source not in row["devices"]:
                row["devices"].append(source)
        return list(master.values())

    def get_master_assets_page_for_device(
        self,
        user_id: str,
        selected_device_id: str,
        asset_type: str,
        *,
        query: Optional[str] = None,
        system_name: Optional[str] = None,
        status: Optional[str] = None,
        artwork_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> Optional[dict]:
        selected = self.get_device_by_device_id(selected_device_id)
        if not selected or selected["user_id"] != user_id:
            return None
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        if self._asset_store_enabled():
            devices = self.get_user_devices(user_id)
            raw_rows, total = postgres_store.page_master_assets(
                [device["id"] for device in devices],
                asset_type,
                selected_internal_id=selected["id"],
                query=query,
                system_name=system_name,
                status=status,
                artwork_type=artwork_type,
                page=page,
                per_page=per_page,
            )
            return {
                "rows": self._master_page_from_asset_rows(asset_type, raw_rows, user_id, include_presence=True),
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        loaders = {
            "rom": self.get_master_roms_for_device,
            "bios": self.get_master_bios_for_device,
            "artwork": self.get_master_artwork_for_device,
        }
        rows = loaders[asset_type](user_id, selected_device_id) or []
        filtered = self._filter_master_rows(
            rows,
            asset_type=asset_type,
            query=query,
            system_name=system_name,
            status=status,
            artwork_type=artwork_type,
        )
        start = (page - 1) * per_page
        return {"rows": filtered[start:start + per_page], "total": len(filtered), "page": page, "per_page": per_page}

    def get_swarm_master_roms_page(
        self,
        user_id: str,
        *,
        query: Optional[str] = None,
        system_name: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        if self._asset_store_enabled():
            devices = self.get_user_devices(user_id)
            raw_rows, total = postgres_store.page_master_assets(
                [device["id"] for device in devices],
                "rom",
                query=query,
                system_name=system_name,
                page=page,
                per_page=per_page,
            )
            return {
                "rows": self._master_page_from_asset_rows("rom", raw_rows, user_id, include_presence=False),
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        rows = self._filter_master_rows(self.get_swarm_master_roms(user_id), asset_type="rom", query=query, system_name=system_name)
        start = (page - 1) * per_page
        return {"rows": rows[start:start + per_page], "total": len(rows), "page": page, "per_page": per_page}

    def get_swarm_master_bios_page(
        self,
        user_id: str,
        *,
        query: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        if self._asset_store_enabled():
            devices = self.get_user_devices(user_id)
            raw_rows, total = postgres_store.page_master_assets(
                [device["id"] for device in devices],
                "bios",
                query=query,
                page=page,
                per_page=per_page,
            )
            return {
                "rows": self._master_page_from_asset_rows("bios", raw_rows, user_id, include_presence=False),
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        rows = self._filter_master_rows(self.get_swarm_master_bios(user_id), asset_type="bios", query=query)
        start = (page - 1) * per_page
        return {"rows": rows[start:start + per_page], "total": len(rows), "page": page, "per_page": per_page}

    def add_rom_sync_activity(self, device_id: str, payload: dict) -> Optional[dict]:
        if not self.get_device_by_device_id(device_id):
            return None
        entry = {
            "id": payload.get("sync_id") or str(uuid.uuid4()),
            "asset_type": payload.get("asset_type") or ("artwork" if payload.get("artwork_type") else ("bios" if payload.get("bios_name") or payload.get("bios_md5") else "rom")),
            "file_type": payload.get("file_type"),
            "source_drone_id": payload.get("source_drone_id"),
            "target_drone_id": payload.get("target_drone_id") or device_id,
            "system": payload.get("system"),
            "rom_name": payload.get("rom_name") or payload.get("rom_path") or payload.get("bios_name"),
            "rom_path": payload.get("rom_path"),
            "entry_type": payload.get("entry_type"),
            "bios_name": payload.get("bios_name"),
            "artwork_type": payload.get("artwork_type"),
            "relative_path": payload.get("relative_path") or payload.get("rom_path") or payload.get("bios_name"),
            "action": payload.get("action") or "download",
            "status": payload.get("status") or "pending",
            "selected_peer_reason": payload.get("selected_peer_reason"),
            "bytes_transferred": payload.get("bytes_transferred"),
            "file_size": payload.get("file_size"),
            "rom_md5": payload.get("rom_md5") or payload.get("md5"),
            "bios_md5": payload.get("bios_md5") or (payload.get("md5") if payload.get("asset_type") == "bios" else None),
            "download_started_at": payload.get("download_started_at") or payload.get("started_at"),
            "download_completed_at": payload.get("download_completed_at") or payload.get("completed_at"),
            "started_at": payload.get("started_at") or payload.get("download_started_at") or datetime.utcnow(),
            "completed_at": payload.get("completed_at") or payload.get("download_completed_at"),
            "duration_ms": payload.get("duration_ms"),
            "duration_seconds": payload.get("duration_seconds"),
            "inventory_refresh_status": payload.get("inventory_refresh_status"),
            "inventory_refresh_duration_ms": payload.get("inventory_refresh_duration_ms"),
            "inventory_refresh_error": payload.get("inventory_refresh_error"),
            "failure_reason": payload.get("failure_reason"),
            "received_at": datetime.utcnow(),
        }
        for related_id in {entry.get("target_drone_id"), entry.get("source_drone_id")}:
            if not related_id:
                continue
            device = self.get_device_by_device_id(str(related_id))
            if not device:
                continue
            bucket = self.rom_sync_activity.setdefault(device["id"], [])
            existing = next((row for row in bucket if row.get("id") == entry["id"]), None)
            if existing:
                existing.update({key: value for key, value in entry.items() if value is not None})
                existing["received_at"] = entry["received_at"]
            else:
                bucket.append(entry)
            del bucket[:-200]
        return entry

    def store_download_state(self, device_id: str, payload: dict) -> Optional[dict]:
        if postgres_store.available():
            relational = postgres_store.store_download_state(device_id, payload if isinstance(payload, dict) else {})
            if relational is not None:
                return relational
        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        state = payload if isinstance(payload, dict) else {}
        clean = {
            "target_drone_id": state.get("target_drone_id") or device_id,
            "concurrency": state.get("concurrency") if isinstance(state.get("concurrency"), dict) else {"scope": "target_drone", "active_limit": 1},
            "active": state.get("active") if isinstance(state.get("active"), list) else [],
            "queued": state.get("queued") if isinstance(state.get("queued"), list) else [],
            "recent": state.get("recent") if isinstance(state.get("recent"), list) else [],
            "downloads": state.get("downloads") if isinstance(state.get("downloads"), list) else [],
            "received_at": datetime.utcnow(),
        }
        if not clean["downloads"]:
            clean["downloads"] = clean["active"] + clean["queued"] + clean["recent"]
        self.download_states[device["id"]] = clean
        return clean

    def get_download_states(self, user_id: str, device_id: Optional[str] = None) -> List[dict]:
        if postgres_store.available():
            relational = postgres_store.list_download_states(user_id, device_id=device_id)
            if relational is not None:
                return relational
        devices = [self.user_can_access_device(user_id, device_id)] if device_id else self.get_user_devices(user_id)
        rows = []
        for device in devices:
            if not device:
                continue
            state = dict(self.download_states.get(device["id"]) or {})
            if not state:
                state = {
                    "target_drone_id": device.get("device_id"),
                    "concurrency": {"scope": "target_drone", "active_limit": 1},
                    "active": [],
                    "queued": [],
                    "recent": [],
                    "downloads": [],
                    "received_at": None,
                }
            state["target_drone_id"] = state.get("target_drone_id") or device.get("device_id")
            state["device_name"] = device.get("device_name")
            rows.append(state)
        rows.sort(key=lambda row: str(row.get("target_drone_id") or "").lower())
        return rows

    def get_rom_sync_activity(self, user_id: str, device_id: str) -> Optional[List[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        return list(reversed(self.rom_sync_activity.get(device["id"], [])))[:100]

    def search_rom_sync_activity(self, user_id: str, query: Optional[str] = None, status: Optional[str] = None) -> List[dict]:
        rows = []
        seen = set()
        for device in self.get_user_devices(user_id):
            for row in self.rom_sync_activity.get(device["id"], []):
                row_id = row.get("id")
                key = row_id or id(row)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
        if status:
            wanted = status.strip().lower()
            rows = [row for row in rows if str(row.get("status") or "").lower() == wanted]
        if query:
            q = query.strip().lower()
            def haystack(row: dict) -> str:
                return " ".join(str(row.get(field) or "") for field in (
                    "source_drone_id", "target_drone_id", "system", "rom_name", "relative_path",
                    "rom_path", "artwork_type", "rom_md5", "bios_md5", "asset_type", "status", "failure_reason", "duration_ms", "duration_seconds",
                    "started_at", "completed_at", "download_started_at", "download_completed_at",
                )).lower()
            rows = [row for row in rows if q in haystack(row)]
        rows.sort(key=lambda row: str(row.get("completed_at") or row.get("started_at") or row.get("received_at") or ""), reverse=True)
        return rows[:500]
    
    def get_device_roms(self, device_id: str) -> List[dict]:
        """Get all ROMs for a device."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        return self._asset_rows_for_device_internal(internal_device["id"], "rom")
    
    def get_device_roms_by_system(self, device_id: str, system_name: str) -> List[dict]:
        """Get ROMs for a device filtered by system."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        return self._asset_rows_for_device_internal(internal_device["id"], "rom", system_name=system_name)

    def get_device_roms_page(
        self,
        device_id: str,
        *,
        system_name: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> dict:
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return {"rows": [], "total": 0, "page": max(1, int(page)), "per_page": max(1, min(int(per_page), 500))}
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        if self._asset_store_enabled():
            rows, total = postgres_store.page_device_assets(
                internal_device["id"],
                "rom",
                system_name=system_name,
                page=page,
                per_page=per_page,
            )
            return {"rows": rows, "total": total, "page": page, "per_page": per_page}
        rows = self.get_device_roms_by_system(device_id, system_name) if system_name else self.get_device_roms(device_id)
        start = (page - 1) * per_page
        return {"rows": rows[start:start + per_page], "total": len(rows), "page": page, "per_page": per_page}

    def get_user_systems_summary(self, user_id: str) -> List[dict]:
        """Get system summary across all devices owned by a user."""
        devices = self.get_user_devices(user_id)
        if self._asset_store_enabled():
            return postgres_store.summarize_rom_systems([device["id"] for device in devices])
        summary: Dict[str, dict] = {}
        for device in devices:
            internal_id = device["id"]
            device_roms = self._asset_rows_for_device_internal(internal_id, "rom")
            for rom in device_roms:
                system_name = rom.get("system_name")
                if not system_name:
                    continue
                if system_name not in summary:
                    summary[system_name] = {
                        "system_name": system_name,
                        "rom_count": 0,
                        "device_ids": set(),
                    }
                summary[system_name]["rom_count"] += 1
                summary[system_name]["device_ids"].add(device["device_id"])

        systems = []
        for item in summary.values():
            systems.append(
                {
                    "system_name": item["system_name"],
                    "rom_count": item["rom_count"],
                    "device_count": len(item["device_ids"]),
                }
            )
        systems.sort(key=lambda row: row["system_name"])
        return systems

    def get_device_systems_summary(self, device_id: str) -> List[dict]:
        """Get system summary for a single device."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        if self._asset_store_enabled():
            systems = {
                row["system_name"]: {
                    "system_name": row["system_name"],
                    "rom_count": row["rom_count"],
                    "last_played_at": None,
                }
                for row in postgres_store.summarize_rom_systems([internal_device["id"]])
            }
            for log in self.get_device_gamelogs(device_id):
                system_name = log.get("system_name")
                if not system_name:
                    continue
                systems.setdefault(system_name, {"system_name": system_name, "rom_count": 0, "last_played_at": None})
                played_at = log.get("played_at")
                previous = systems[system_name]["last_played_at"]
                if played_at and (previous is None or played_at > previous):
                    systems[system_name]["last_played_at"] = played_at
            output = list(systems.values())
            output.sort(key=lambda row: row["system_name"])
            return output
        systems: Dict[str, dict] = {}
        roms = self.get_device_roms(device_id)
        for rom in roms:
            system_name = rom.get("system_name")
            if not system_name:
                continue
            if system_name not in systems:
                systems[system_name] = {
                    "system_name": system_name,
                    "rom_count": 0,
                    "last_played_at": None,
                }
            systems[system_name]["rom_count"] += 1

        for log in self.get_device_gamelogs(device_id):
            system_name = log.get("system_name")
            if not system_name:
                continue
            if system_name not in systems:
                systems[system_name] = {
                    "system_name": system_name,
                    "rom_count": 0,
                    "last_played_at": None,
                }
            played_at = log.get("played_at")
            previous = systems[system_name]["last_played_at"]
            if played_at and (previous is None or played_at > previous):
                systems[system_name]["last_played_at"] = played_at

        output = list(systems.values())
        output.sort(key=lambda row: row["system_name"])
        return output
    
    # Game play logging
    def log_gameplay(
        self,
        device_id: str,
        system_name: str,
        game_name: str,
        duration_seconds: Optional[int] = None,
        rom_path: Optional[str] = None,
        rom_md5: Optional[str] = None,
        played_at: Optional[datetime] = None,
    ) -> Optional[str]:
        """Log a game play session."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return None
        
        internal_id = internal_device["id"]
        if internal_id not in self.gamelogs:
            self.gamelogs[internal_id] = []
        
        gamelog_id = str(uuid.uuid4())
        gamelog_entry = {
            "id": gamelog_id,
            "device_id": device_id,
            "system_name": system_name,
            "game_name": game_name,
            "rom_path": rom_path,
            "rom_md5": rom_md5,
            "played_at": played_at or datetime.utcnow(),
            "duration_seconds": duration_seconds,
        }
        self.gamelogs[internal_id].append(gamelog_entry)
        self.update_device_last_seen(internal_id)
        return gamelog_id
    
    def get_device_gamelogs(self, device_id: str) -> List[dict]:
        """Get all game logs for a device."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        internal_id = internal_device["id"]
        return self.gamelogs.get(internal_id, [])
    
    def get_device_gamelogs_by_system(self, device_id: str, system_name: str) -> List[dict]:
        """Get game logs for a device filtered by system."""
        all_logs = self.get_device_gamelogs(device_id)
        return [log for log in all_logs if log.get("system_name") == system_name]

    def store_device_log_sources(self, device_id: str, payload: dict) -> None:
        device = self.get_device_by_device_id(device_id)
        if not device or not isinstance(payload, dict):
            return
        internal_id = device["id"]
        postgres_store.store_device_log_sources(internal_id, payload)
        existing = self.log_sources.get(internal_id)
        merged = merge_log_sources(existing, payload, max_lines=1000)
        self.log_sources[internal_id] = merged
        device["log_sources"] = merged

    def get_device_log_sources(self, device_id: str, line_limit: int = 10) -> dict:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return {"type": "log_sources", "logs": []}
        internal_id = device["id"]
        if postgres_store.available():
            payload = postgres_store.get_device_log_sources(internal_id, line_limit=line_limit)
            if payload.get("logs"):
                return payload
        payload = self.log_sources.get(internal_id) or device.get("log_sources") or {"type": "log_sources", "logs": []}
        line_limit = max(1, min(100, int(line_limit or 10)))
        limited = {"type": "log_sources", "logs": []}
        for source in payload.get("logs") if isinstance(payload.get("logs"), list) else []:
            if not isinstance(source, dict):
                continue
            entry = {**source, "files": []}
            for file_row in source.get("files") if isinstance(source.get("files"), list) else []:
                if not isinstance(file_row, dict):
                    continue
                lines = str(file_row.get("content") or "").splitlines()
                entry["files"].append({**file_row, "content": "\n".join(lines[-line_limit:])})
            limited["logs"].append(entry)
        return limited

    def store_device_emulator_configs(self, device_id: str, payload: dict) -> None:
        device = self.get_device_by_device_id(device_id)
        if not device or not isinstance(payload, dict):
            return
        internal_id = device["id"]
        postgres_store.store_device_emulator_configs(internal_id, payload)

    def get_device_emulator_configs(self, device_id: str) -> dict:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return {"type": "emulator_configs", "configs": []}
        internal_id = device["id"]
        def _filter_payload(payload: dict) -> dict:
            if not isinstance(payload, dict):
                return {"type": "emulator_configs", "configs": []}
            source_configs = payload.get("configs") if isinstance(payload.get("configs"), list) else []
            configs = [
                row for row in source_configs
                if isinstance(row, dict)
                and not _is_excluded_emulator_config_path(str(row.get("relative_path") or row.get("path") or row.get("name") or ""))
            ]
            return {**payload, "configs": configs}
        if postgres_store.available():
            payload = postgres_store.get_device_emulator_configs(internal_id)
            if payload.get("configs"):
                return _filter_payload(payload)
        payload = device.get("emulator_configs") or {"type": "emulator_configs", "configs": []}
        return _filter_payload(payload)
    


# Global database instance
db = OvermindDatabase()
