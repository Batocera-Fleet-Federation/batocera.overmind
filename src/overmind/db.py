"""Fake in-memory database storage."""

import uuid
from datetime import datetime
from typing import Dict, List, Optional
from overmind import auth
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

        internal_id = self.create_device(
            user_id,
            connection["device_id"],
            connection["device_name"],
            connection["batocera_info"],
        )
        self.pending_drone_connections.pop(device_id, None)
        return self.get_device(internal_id)

    def deny_pending_drone_connection(self, user_id: str, device_id: str) -> bool:
        """Deny a pending drone connection."""
        connection = self.pending_drone_connections.get(device_id)
        if not connection or connection.get("user_id") not in (None, user_id):
            return False
        self.pending_drone_connections.pop(device_id, None)
        return True

    def create_device(self, user_id: str, device_id: str, device_name: str, batocera_info: dict) -> str:
        """Register a new device."""
        internal_id = str(uuid.uuid4())
        self.devices[internal_id] = {
            "id": internal_id,
            "user_id": user_id,
            "device_id": device_id,
            "device_name": device_name,
            "batocera_info": batocera_info,
            "registered_at": datetime.utcnow(),
            "last_seen": datetime.utcnow(),
        }
        self.user_devices[user_id].append(internal_id)
        self.roms[internal_id] = []
        self.gamelogs[internal_id] = []
        self.device_actions[internal_id] = []
        return internal_id
    
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
    
    def update_device_last_seen(self, internal_id: str):
        """Update last_seen timestamp for device."""
        if internal_id in self.devices:
            self.devices[internal_id]["last_seen"] = datetime.utcnow()

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
                return action
        return None
    
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
            }
            self.roms[internal_id].append(rom_entry)
            rom_ids.append(rom_id)
        
        return rom_ids
    
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
        
        # Create sample devices for user1
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
                "battery": "N/A"
            }
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
            }
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
            self.add_roms("arcade-cabinet-001", system, [make_rom(system, name, n + i) for i, name in enumerate(names)])
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
        self.log_gameplay("arcade-cabinet-001", "snes", "Super Mario Bros", 1800)
        self.log_gameplay("arcade-cabinet-001", "snes", "The Legend of Zelda", 3600)
        self.log_gameplay("arcade-cabinet-001", "genesis", "Sonic the Hedgehog", 900)
        self.log_gameplay("arcade-cabinet-001", "snes", "Super Metroid", 2700)
        
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
