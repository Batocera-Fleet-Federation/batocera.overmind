"""Fake in-memory database storage."""

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from overmind import auth
from overmind.drone_security import generate_drone_token, hash_drone_token, verify_drone_token
from overmind.networking import resolve_reported_network
from overmind.postgres_store import postgres_store
from overmind.models import User, Device, RomMetadata, GamePlay


class FakeDatabase:
    """In-memory database using dictionaries."""
    
    def __init__(self):
        # Storage
        self.users: Dict[str, dict] = {}
        self.user_by_email: Dict[str, str] = {}  # email -> user_id
        self.devices: Dict[str, dict] = {}
        self.user_devices: Dict[str, List[str]] = {}  # user_id -> list of device_ids
        self.roms: Dict[str, list] = {}  # device_id -> list of roms
        self.gamelogs: Dict[str, list] = {}  # device_id -> list of game plays
        self.device_actions: Dict[str, list] = {}  # internal device_id -> queued actions
        self.speed_samples: Dict[str, list] = {}  # internal device_id -> speed samples
        self.device_events: Dict[str, list] = {}  # internal device_id -> telemetry events
        self.peer_checks: Dict[str, list] = {}  # internal device_id -> peer check results
        self.integration_tokens: Dict[str, list] = {}
        self.approved_drone_tokens: Dict[str, str] = {}
        self.rom_sync_activity: Dict[str, list] = {}
        self.pending_drone_connections: Dict[str, dict] = {}
    
    # User operations
    def create_user(self, email: str, hashed_password: str, full_name: Optional[str] = None) -> str:
        """Create a new user."""
        user_id = str(uuid.uuid4())
        self.users[user_id] = {
            "id": user_id,
            "email": email,
            "password": hashed_password,
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
                "email_address": email,
                "types": {
                    "gamelist_update": True,
                    "device_offline": True,
                    "sync_failure": True,
                },
            },
            "created_at": datetime.utcnow(),
        }
        self.user_by_email[email] = user_id
        self.user_devices[user_id] = []
        return user_id

    def get_or_create_social_user(self, email: str, full_name: Optional[str], provider: str) -> dict:
        """Create or return a user authenticated by a configured social provider."""
        existing = self.get_user_by_email(email)
        if existing:
            existing["auth_provider"] = existing.get("auth_provider") or provider
            if full_name and not existing.get("full_name"):
                existing["full_name"] = full_name
            return existing

        user_id = self.create_user(email, auth.hash_password(str(uuid.uuid4())), full_name)
        user = self.users[user_id]
        user["auth_provider"] = provider
        return user
    
    def get_user_by_email(self, email: str) -> Optional[dict]:
        """Get user by email."""
        user_id = self.user_by_email.get(email)
        if user_id:
            return self.users.get(user_id)
        return None
    
    def get_user(self, user_id: str) -> Optional[dict]:
        """Get user by ID."""
        return self.users.get(user_id)
    
    def user_exists(self, email: str) -> bool:
        """Check if user exists by email."""
        return email in self.user_by_email

    def update_user_profile(
        self,
        user_id: str,
        full_name: Optional[str] = None,
        avatar_data_url: Optional[str] = None,
    ) -> Optional[dict]:
        """Update profile fields for a user."""
        user = self.get_user(user_id)
        if not user:
            return None
        if full_name is not None:
            user["full_name"] = full_name
        if avatar_data_url is not None:
            user["avatar_data_url"] = avatar_data_url
        return user

    def update_user_fleet_settings(self, user_id: str, fleet_settings: dict) -> Optional[dict]:
        """Update fleet settings for a user."""
        user = self.get_user(user_id)
        if not user:
            return None
        user["fleet_settings"] = {**user.get("fleet_settings", {}), **fleet_settings}
        return user

    def update_user_notification_settings(self, user_id: str, notification_settings: dict) -> Optional[dict]:
        """Update notification settings for a user."""
        user = self.get_user(user_id)
        if not user:
            return None
        current = user.get("notification_settings", {})
        merged = {**current, **{k: v for k, v in notification_settings.items() if k != "types"}}
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
        self.integration_tokens.setdefault(user_id, []).append(entry)
        return entry

    def get_integration_tokens(self, user_id: str) -> List[dict]:
        return [
            {k: v for k, v in token.items() if k not in {"token_hash", "raw_token_once"}}
            for token in self.integration_tokens.get(user_id, [])
        ]

    def verify_integration_token(self, email: Optional[str], token: Optional[str]) -> Optional[dict]:
        claimed = self.claim_integration_token(email, token, None)
        return claimed["user"] if claimed else None

    def claim_integration_token(self, email: Optional[str], token: Optional[str], device_id: Optional[str]) -> Optional[dict]:
        if not email or not token:
            return None
        user = self.get_user_by_email(email)
        if not user:
            return None
        for entry in self.integration_tokens.get(user["id"], []):
            if entry.get("revoked_at"):
                continue
            if verify_drone_token(token, entry.get("token_hash")):
                bound_device = entry.get("bound_device_id")
                if bound_device and device_id and bound_device != device_id:
                    return None
                if device_id and not bound_device:
                    entry["bound_device_id"] = device_id
                    entry["bound_at"] = datetime.utcnow()
                entry["last_used_at"] = datetime.utcnow()
                return {"user": user, "token": entry}
        return None

    def revoke_integration_token(self, user_id: str, token_id: str) -> bool:
        for entry in self.integration_tokens.get(user_id, []):
            if entry.get("id") == token_id and not entry.get("revoked_at"):
                entry["revoked_at"] = datetime.utcnow()
                return True
        return False

    # Device operations
    def create_pending_drone_connection(
        self,
        device_id: str,
        device_name: str,
        batocera_info: dict,
        user_id: Optional[str] = None,
    ) -> dict:
        """Record that a drone is attempting to connect to Overmind."""
        now = datetime.utcnow()
        existing = self.pending_drone_connections.get(device_id)
        if existing:
            existing.update({
                "device_name": device_name,
                "batocera_info": batocera_info,
                "user_id": user_id,
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
        }
        self.pending_drone_connections[device_id] = connection
        return connection

    def get_pending_drone_connections(self, user_id: str) -> List[dict]:
        """Return pending drone connections visible to this user."""
        visible = [
            conn for conn in self.pending_drone_connections.values()
            if conn.get("status") == "pending" and conn.get("user_id") in (None, user_id)
        ]
        visible.sort(key=lambda row: row.get("last_seen"), reverse=True)
        return visible

    def accept_pending_drone_connection(self, user_id: str, device_id: str) -> Optional[dict]:
        """Accept a pending drone connection and register it to the Overlord."""
        connection = self.pending_drone_connections.get(device_id)
        if not connection or connection.get("status") != "pending":
            return None
        if connection.get("user_id") not in (None, user_id):
            return None
        if self.device_exists(user_id, device_id):
            self.pending_drone_connections.pop(device_id, None)
            return self.get_device_by_device_id(device_id)

        raw_token = generate_drone_token()
        internal_id = self.create_device(
            user_id,
            connection["device_id"],
            connection["device_name"],
            connection["batocera_info"],
            raw_token=raw_token,
        )
        self.pending_drone_connections.pop(device_id, None)
        device = self.get_device(internal_id)
        device["raw_token_once"] = raw_token
        self.approved_drone_tokens[device_id] = raw_token
        return device

    def deny_pending_drone_connection(self, user_id: str, device_id: str) -> bool:
        """Deny a pending drone connection."""
        connection = self.pending_drone_connections.get(device_id)
        if not connection or connection.get("user_id") not in (None, user_id):
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
        authorization_token_id: Optional[str] = None,
    ) -> str:
        """Register a new device."""
        internal_id = str(uuid.uuid4())
        network_state = resolve_reported_network(batocera_info.get("network") if isinstance(batocera_info, dict) else None)
        certificate = self._clean_device_certificate(
            batocera_info.get("certificate") if isinstance(batocera_info, dict) else None
        )
        self.devices[internal_id] = {
            "id": internal_id,
            "user_id": user_id,
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
            "drone_token_hash": hash_drone_token(raw_token or generate_drone_token()),
            "authorization_token_id": authorization_token_id,
            "api_port": batocera_info.get("api_port") if isinstance(batocera_info, dict) else None,
            "scheme": (batocera_info.get("scheme") if isinstance(batocera_info, dict) else None) or "https",
            "reachable_url": batocera_info.get("reachable_url") if isinstance(batocera_info, dict) else None,
            "certificate": certificate,
            "peer_checks": [],
        }
        self.user_devices[user_id].append(internal_id)
        self.roms[internal_id] = []
        self.gamelogs[internal_id] = []
        self.device_actions[internal_id] = []
        self.speed_samples[internal_id] = []
        self.device_events[internal_id] = []
        self.peer_checks[internal_id] = []
        self.rom_sync_activity[internal_id] = []
        return internal_id

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
        return self.devices.get(internal_id)
    
    def get_device_by_device_id(self, device_id: str) -> Optional[dict]:
        """Get device by device_id (unique per user)."""
        for device in self.devices.values():
            if device["device_id"] == device_id:
                return device
        return None
    
    def get_user_devices(self, user_id: str) -> List[dict]:
        """Get all devices for a user."""
        device_ids = self.user_devices.get(user_id, [])
        return [self.devices[did] for did in device_ids if did in self.devices]
    
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
            self.devices[internal_id]["last_seen"] = datetime.utcnow()
            if network is not None:
                network_state = resolve_reported_network(network)
                self.devices[internal_id]["network"] = network_state["reported"]
                self.devices[internal_id]["resolved_network"] = network_state["resolved"]
                self.devices[internal_id]["swarm_connected"] = network_state["swarm_connected"]
            if isinstance(rom_systems, list):
                names = []
                for system in rom_systems:
                    if isinstance(system, dict):
                        name = str(system.get("name") or system.get("system_name") or "").strip()
                    else:
                        name = str(system or "").strip()
                    if name and name not in names:
                        names.append(name)
                self.devices[internal_id]["rom_systems"] = sorted(names)
            if api_port is not None:
                self.devices[internal_id]["api_port"] = api_port
            if scheme:
                self.devices[internal_id]["scheme"] = scheme
            if reachable_url:
                self.devices[internal_id]["reachable_url"] = reachable_url
            if isinstance(certificate, dict):
                self.devices[internal_id]["certificate"] = self._clean_device_certificate(certificate)
            if isinstance(system_info, dict):
                clean_info = dict(system_info)
                clean_info["last_system_info_update"] = datetime.utcnow()
                self.devices[internal_id]["system_info"] = clean_info

    def get_swarm_for_device(self, device_id: str, offline_seconds: int = 90) -> List[dict]:
        device = self.get_device_by_device_id(device_id)
        if not device:
            return []
        cutoff = datetime.utcnow() - timedelta(seconds=max(1, int(offline_seconds)))
        output = []
        for peer in self.get_user_devices(device["user_id"]):
            resolved = peer.get("resolved_network") or {}
            ipv4 = resolved.get("ipv4") or []
            reported = peer.get("network") or {}
            public_ip = reported.get("public_ip") or reported.get("public")
            last_seen = peer.get("last_seen")
            online = bool(last_seen and last_seen >= cutoff)
            cert = dict(peer.get("certificate") or {})
            cert.pop("public_certificate", None)
            cert.pop("certificate_pem", None)
            output.append({
                "drone_id": peer.get("device_id"),
                "device_id": peer.get("device_id"),
                "name": peer.get("device_name"),
                "hostname": (peer.get("batocera_info") or {}).get("hostname"),
                "local_ip": ipv4[0] if ipv4 else (peer.get("batocera_info") or {}).get("ip_address"),
                "private_ip": ipv4,
                "public_ip": public_ip,
                "api_port": peer.get("api_port") or 8443,
                "scheme": peer.get("scheme") or "https",
                "reachable_url": peer.get("reachable_url"),
                "last_alive": last_seen,
                "last_seen": last_seen,
                "online": online,
                "status": "online" if online else "offline",
                "certificate": cert or None,
                "rom_systems": peer.get("rom_systems") or [],
                "last_speed_sample": peer.get("last_speed_sample"),
                "network": peer.get("network") or {},
                "resolved_network": resolved,
                "swarm_connected": bool(peer.get("swarm_connected")),
                "peer_checks": list(reversed(self.peer_checks.get(peer["id"], [])))[:50],
            })
        output.sort(key=lambda row: (not row["online"], str(row.get("name") or row.get("drone_id") or "").lower()))
        return output

    def verify_device_token(self, device_id: str, raw_token: str) -> Optional[dict]:
        """Return the device if its bearer token is valid."""
        from overmind.drone_security import verify_drone_token

        device = self.get_device_by_device_id(device_id)
        if not device:
            return None
        if not verify_drone_token(raw_token, device.get("drone_token_hash") or ""):
            return None
        auth_token_id = device.get("authorization_token_id")
        if auth_token_id:
            token_rows = self.integration_tokens.get(device.get("user_id"), [])
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

    def set_device_authorization_token(self, user_id: str, device_id: str, token_id: Optional[str]) -> bool:
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return False
        previous = device.get("authorization_token_id")
        if previous and previous != token_id:
            self.revoke_integration_token(user_id, previous)
        device["authorization_token_id"] = token_id
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
        """Delete a user's device and associated local data."""
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return False

        internal_id = device["id"]
        self.devices.pop(internal_id, None)
        self.roms.pop(internal_id, None)
        self.gamelogs.pop(internal_id, None)
        self.device_actions.pop(internal_id, None)
        self.speed_samples.pop(internal_id, None)
        self.device_events.pop(internal_id, None)
        self.peer_checks.pop(internal_id, None)
        self.rom_sync_activity.pop(internal_id, None)
        self.user_devices[user_id] = [
            did for did in self.user_devices.get(user_id, [])
            if did != internal_id
        ]
        return True

    def create_device_action(
        self,
        user_id: str,
        device_id: str,
        action_type: str,
        payload: Optional[dict] = None,
    ) -> Optional[dict]:
        """Queue an action for a user's device."""
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
        """Get actions for a user's device."""
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        return list(reversed(self.device_actions.get(device["id"], [])))

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
        for action in self.device_actions.get(device["id"], []):
            if action.get("id") == action_id:
                action["status"] = status
                action["completed_at"] = datetime.utcnow()
                action["message"] = message
                action["result"] = result
                action["result_received_at"] = datetime.utcnow() if result is not None else None
                if isinstance(result, dict):
                    self.store_action_result(device, result)
                    postgres_store.store_action_result(device_id, action_id, result)
                return action
        return None

    def store_action_result(self, device: dict, result: dict) -> None:
        """Persist returned action data on the device record for UI use."""
        result_type = result.get("type")
        if result_type == "rom_metadata":
            self.store_rom_metadata(device.get("device_id"), result)
        if result_type == "emulator_configs":
            device["emulator_configs"] = result
        if result_type == "log_sources":
            device["log_sources"] = result
        if result_type == "rom_sync":
            for activity in result.get("activity") if isinstance(result.get("activity"), list) else []:
                if isinstance(activity, dict):
                    self.add_rom_sync_activity(device.get("device_id"), activity)

    def store_rom_metadata(self, device_id: str, metadata: dict) -> None:
        device = self.get_device_by_device_id(device_id)
        if not device or not isinstance(metadata, dict):
            return
        systems = metadata.get("systems") if isinstance(metadata.get("systems"), list) else []
        self.update_device_last_seen(device["id"], rom_systems=systems)
        device["rom_metadata"] = metadata

        grouped: Dict[str, list] = {}
        for item in metadata.get("roms") if isinstance(metadata.get("roms"), list) else []:
            if not isinstance(item, dict):
                continue
            system_name = str(item.get("system") or item.get("system_name") or "").strip()
            if not system_name:
                continue
            rom_name = str(item.get("rom_name") or item.get("name") or item.get("title") or "").strip()
            file_path = str(item.get("file_path") or item.get("rom_file") or item.get("rom_path") or rom_name).strip()
            grouped.setdefault(system_name, []).append({
                "rom_name": rom_name or file_path,
                "rom_md5": item.get("rom_md5") or item.get("md5") or item.get("hash"),
                "file_path": file_path,
                "file_size": item.get("file_size") or item.get("byte_count") or item.get("size"),
            })
        for system_name, roms in grouped.items():
            self.add_roms(device_id, system_name, roms)

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

    def get_speed_samples(self, user_id: str, device_id: str) -> Optional[List[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        return list(reversed(self.speed_samples.get(device["id"], [])))
    
    def device_exists(self, user_id: str, device_id: str) -> bool:
        """Check if device exists for user."""
        for device in self.get_user_devices(user_id):
            if device["device_id"] == device_id:
                return True
        return False
    
    # ROM operations
    def add_roms(self, device_id: str, system_name: str, roms: list) -> List[str]:
        """Add ROMs for a device. Returns list of ROM IDs."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        
        internal_id = internal_device["id"]
        rom_ids = []
        
        # Clear existing roms for this system
        if internal_id not in self.roms:
            self.roms[internal_id] = []
        
        self.roms[internal_id] = [
            r for r in self.roms[internal_id] 
            if r.get("system_name") != system_name
        ]
        
        for rom in roms:
            rom_id = str(uuid.uuid4())
            rom_entry = {
                "id": rom_id,
                "device_id": device_id,
                "system_name": system_name,
                "rom_name": rom.get("rom_name"),
                "rom_md5": rom.get("rom_md5"),
                "file_path": rom.get("file_path"),
                "file_size": rom.get("file_size"),
                "added_at": datetime.utcnow(),
                "last_seen": datetime.utcnow(),
            }
            self.roms[internal_id].append(rom_entry)
            rom_ids.append(rom_id)
        
        return rom_ids

    def _rom_key(self, rom: dict) -> tuple:
        system = str(rom.get("system_name") or "").strip().lower()
        path = str(rom.get("file_path") or rom.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
        return (system, path)

    def get_master_roms_for_device(self, user_id: str, selected_device_id: str) -> Optional[List[dict]]:
        selected = self.get_device_by_device_id(selected_device_id)
        if not selected or selected["user_id"] != user_id:
            return None
        devices = {device["device_id"]: device for device in self.get_user_devices(user_id)}
        selected_keys = {self._rom_key(rom) for rom in self.roms.get(selected["id"], [])}
        master: Dict[tuple, dict] = {}
        for device in devices.values():
            for rom in self.roms.get(device["id"], []):
                key = self._rom_key(rom)
                if not key[0] or not key[1]:
                    continue
                row = master.setdefault(key, {
                    "system_name": rom.get("system_name"),
                    "rom_name": rom.get("rom_name") or rom.get("file_path"),
                    "file_path": rom.get("file_path") or rom.get("rom_name"),
                    "rom_md5": rom.get("rom_md5"),
                    "file_size": rom.get("file_size"),
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

    def add_rom_sync_activity(self, device_id: str, payload: dict) -> Optional[dict]:
        if not self.get_device_by_device_id(device_id):
            return None
        entry = {
            "id": payload.get("sync_id") or str(uuid.uuid4()),
            "source_drone_id": payload.get("source_drone_id"),
            "target_drone_id": payload.get("target_drone_id") or device_id,
            "system": payload.get("system"),
            "rom_name": payload.get("rom_name") or payload.get("rom_path"),
            "action": payload.get("action") or "download",
            "status": payload.get("status") or "pending",
            "selected_peer_reason": payload.get("selected_peer_reason"),
            "bytes_transferred": payload.get("bytes_transferred"),
            "file_size": payload.get("file_size"),
            "rom_md5": payload.get("rom_md5"),
            "started_at": payload.get("started_at") or datetime.utcnow(),
            "completed_at": payload.get("completed_at"),
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
            bucket.append(entry)
            del bucket[:-200]
        return entry

    def get_rom_sync_activity(self, user_id: str, device_id: str) -> Optional[List[dict]]:
        device = self.get_device_by_device_id(device_id)
        if not device or device["user_id"] != user_id:
            return None
        return list(reversed(self.rom_sync_activity.get(device["id"], [])))[:100]
    
    def get_device_roms(self, device_id: str) -> List[dict]:
        """Get all ROMs for a device."""
        internal_device = self.get_device_by_device_id(device_id)
        if not internal_device:
            return []
        internal_id = internal_device["id"]
        return self.roms.get(internal_id, [])
    
    def get_device_roms_by_system(self, device_id: str, system_name: str) -> List[dict]:
        """Get ROMs for a device filtered by system."""
        all_roms = self.get_device_roms(device_id)
        return [r for r in all_roms if r.get("system_name") == system_name]

    def get_user_systems_summary(self, user_id: str) -> List[dict]:
        """Get system summary across all devices owned by a user."""
        summary: Dict[str, dict] = {}
        for device in self.get_user_devices(user_id):
            internal_id = device["id"]
            device_roms = self.roms.get(internal_id, [])
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
    def log_gameplay(self, device_id: str, system_name: str, game_name: str, 
                    duration_seconds: Optional[int] = None) -> Optional[str]:
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
            "played_at": datetime.utcnow(),
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
    
    def populate_fake_data(self):
        """Populate database with sample data for testing."""
        from overmind.auth import hash_password
        
        # Create sample users
        user1_id = self.create_user(
            "demo@example.com",
            hash_password("DemoPass123"),
            "Demo User"
        )
        user2_id = self.create_user(
            "arcade@example.com",
            hash_password("ArcadePass123"),
            "Arcade Enthusiast"
        )
        
        # Create sample devices for user1. A local demo Drone also uses this
        # machine's MAC so a local batocera.drone instance lines up with it.
        local_demo_device_id = ":".join(f"{(uuid.getnode() >> shift) & 0xff:02x}" for shift in range(40, -1, -8))
        demo_drone_token = "demo-local-drone-token"
        device1_id = self.create_device(
            user1_id,
            "arcade-cabinet-001",
            "Living Room Cabinet",
            {
                "model": "Batocera DevBox",
                "system": "Linux 6.6.0",
                "architecture": "x86_64",
                "cpu_model": "AMD Ryzen 7 7800X3D",
                "cpu_cores": 8,
                "cpu_threads": 16,
                "cpu_max_frequency": "5.00 GHz",
                "temperature": "42 C",
                "memory_available": "28.5 GiB",
                "memory_total": "32 GiB",
                "display_resolution": "1920x1080",
                "display_refresh_rate": "60 Hz",
                "data_partition_available": "850 GiB",
                "ip_address": "192.168.1.100",
                "battery": "N/A",
                "network": {"ipv4": ["127.0.0.1", "192.168.1.100"], "ipv6": ["::1"]},
            },
            raw_token=demo_drone_token,
        )
        if local_demo_device_id != "arcade-cabinet-001":
            self.create_device(
                user1_id,
                local_demo_device_id,
                "Local Demo Drone",
                {
                    "model": "Batocera DevBox",
                    "system": "Linux 6.6.0",
                    "architecture": "x86_64",
                    "cpu_model": "AMD Ryzen 7 7800X3D",
                    "cpu_cores": 8,
                    "cpu_threads": 16,
                    "cpu_max_frequency": "5.00 GHz",
                    "temperature": "42 C",
                    "memory_available": "28.5 GiB",
                    "memory_total": "32 GiB",
                    "display_resolution": "1920x1080",
                    "display_refresh_rate": "60 Hz",
                    "data_partition_available": "850 GiB",
                    "ip_address": "192.168.1.100",
                    "battery": "N/A",
                    "network": {"ipv4": ["127.0.0.1", "192.168.1.100"], "ipv6": ["::1"]},
                },
                raw_token=demo_drone_token,
            )
        
        device2_id = self.create_device(
            user1_id,
            "raspberry-pi-001",
            "Bedroom Pi",
            {
                "model": "Raspberry Pi 4",
                "system": "Linux 5.15.0",
                "architecture": "armv7l",
                "cpu_model": "ARM Cortex-A72",
                "cpu_cores": 4,
                "cpu_threads": 4,
                "cpu_max_frequency": "1.5 GHz",
                "temperature": "38 C",
                "memory_available": "3.8 GiB",
                "memory_total": "4 GiB",
                "display_resolution": "1280x720",
                "display_refresh_rate": "60 Hz",
                "data_partition_available": "60 GiB",
                "ip_address": "192.168.1.101",
                "battery": "N/A"
            },
            raw_token=demo_drone_token,
        )

        for index, suffix in enumerate(("a", "b", "c", "d"), start=1):
            self.create_device(
                user1_id,
                f"local-drone-{suffix}",
                f"Local Drone {suffix.upper()}",
                {
                    "model": "Containerized Batocera-like Drone",
                    "system": "Linux container",
                    "architecture": "x86_64",
                    "cpu_model": "Container CPU",
                    "cpu_cores": 1,
                    "cpu_threads": 1,
                    "cpu_max_frequency": "shared",
                    "memory_available": "384 MiB",
                    "memory_total": "384 MiB",
                    "display_resolution": "N/A",
                    "display_refresh_rate": "N/A",
                    "data_partition_available": "container volume",
                    "ip_address": f"172.20.0.{10 + index}",
                    "battery": "N/A",
                    "network": {"ipv4": [f"172.20.0.{10 + index}"]},
                },
                raw_token=demo_drone_token,
            )
        
        # Create sample device for user2
        device3_id = self.create_device(
            user2_id,
            "arcade-cabinet-002",
            "Game Room Arcade",
            {
                "model": "Custom PC Build",
                "system": "Linux 6.1.0",
                "architecture": "x86_64",
                "cpu_model": "Intel Core i7-12700K",
                "cpu_cores": 12,
                "cpu_threads": 20,
                "cpu_max_frequency": "4.90 GHz",
                "temperature": "45 C",
                "memory_available": "29.2 GiB",
                "memory_total": "32 GiB",
                "display_resolution": "3440x1440",
                "display_refresh_rate": "144 Hz",
                "data_partition_available": "2.0 TiB",
                "ip_address": "192.168.1.102",
                "battery": "N/A"
            }
        )
        
        def make_rom(system: str, name: str, n: int) -> dict:
            return {
                "rom_name": name,
                "rom_md5": f"{n:032x}"[:32],
                "file_path": f"/roms/{system}/{name}.zip",
                "file_size": 262144 + (n % 8) * 65536,
            }

        catalog = {
            "snes": [
                "Super Mario World", "Chrono Trigger", "Final Fantasy VI", "Super Metroid",
                "A Link to the Past", "Donkey Kong Country", "Mega Man X", "F-Zero",
            ],
            "nes": [
                "Super Mario Bros", "Metroid", "Mega Man 2", "Castlevania",
                "Contra", "Ninja Gaiden", "Punch-Out", "Kirbys Adventure",
            ],
            "genesis": [
                "Sonic the Hedgehog", "Sonic 2", "Streets of Rage 2", "Gunstar Heroes",
                "Shining Force", "Comix Zone", "Golden Axe", "Phantasy Star IV",
            ],
            "gba": [
                "Metroid Fusion", "Pokemon Emerald", "Advance Wars", "Castlevania Aria of Sorrow",
                "Golden Sun", "Mario Kart Super Circuit", "WarioWare Inc",
            ],
            "psx": [
                "Final Fantasy VII", "Metal Gear Solid", "Tekken 3", "Crash Bandicoot 2",
                "Castlevania Symphony", "Gran Turismo 2", "Resident Evil 2",
            ],
        }

        n = 1000
        for system, names in catalog.items():
            rom_batch = [make_rom(system, name, n + i) for i, name in enumerate(names)]
            self.add_roms("arcade-cabinet-001", system, rom_batch)
            if local_demo_device_id != "arcade-cabinet-001":
                self.add_roms(local_demo_device_id, system, rom_batch)
            n += 50

        for system, names in catalog.items():
            trimmed = names[:5]
            self.add_roms("raspberry-pi-001", system, [make_rom(system, name, n + i) for i, name in enumerate(trimmed)])
            n += 50

        for system, names in catalog.items():
            rotated = names[2:8]
            self.add_roms("arcade-cabinet-002", system, [make_rom(system, name, n + i) for i, name in enumerate(rotated)])
            n += 50
        
        # Add sample game logs for device1
        for demo_device_id in {"arcade-cabinet-001", local_demo_device_id}:
            self.log_gameplay(demo_device_id, "snes", "Super Mario Bros", 1800)
            self.log_gameplay(demo_device_id, "snes", "The Legend of Zelda", 3600)
            self.log_gameplay(demo_device_id, "genesis", "Sonic the Hedgehog", 900)
            self.log_gameplay(demo_device_id, "snes", "Super Metroid", 2700)
        
        # Add sample game logs for device2
        self.log_gameplay("raspberry-pi-001", "nes", "Super Mario Bros", 1200)
        self.log_gameplay("raspberry-pi-001", "nes", "Donkey Kong", 600)
        
        # Add sample game logs for device3
        self.log_gameplay("arcade-cabinet-002", "snes", "Final Fantasy VI", 5400)
        self.log_gameplay("arcade-cabinet-002", "snes", "Chrono Trigger", 4200)

        # Add sample pending drone connection attempts for the demo Overlord.
        self.create_pending_drone_connection(
            "rogue-signal-001",
            "Basement Recon Drone",
            {
                "model": "Mini PC N100",
                "system": "Linux 6.8.0",
                "architecture": "x86_64",
                "cpu_model": "Intel N100",
                "cpu_cores": 4,
                "cpu_threads": 4,
                "cpu_max_frequency": "3.40 GHz",
                "temperature": "39 C",
                "memory_available": "7.2 GiB",
                "memory_total": "8 GiB",
                "display_resolution": "1920x1080",
                "display_refresh_rate": "60 Hz",
                "data_partition_available": "420 GiB",
                "ip_address": "192.168.1.118",
                "battery": "N/A",
            },
            user1_id,
        )
        self.create_pending_drone_connection(
            "rogue-signal-002",
            "Workshop Handheld Drone",
            {
                "model": "Steam Deck OLED",
                "system": "Linux 6.1.52",
                "architecture": "x86_64",
                "cpu_model": "AMD Custom APU 0405",
                "cpu_cores": 4,
                "cpu_threads": 8,
                "cpu_max_frequency": "3.50 GHz",
                "temperature": "44 C",
                "memory_available": "11.8 GiB",
                "memory_total": "16 GiB",
                "display_resolution": "1280x800",
                "display_refresh_rate": "90 Hz",
                "data_partition_available": "730 GiB",
                "ip_address": "192.168.1.119",
                "battery": "82%",
            },
            user1_id,
        )


# Global database instance
db = FakeDatabase()
