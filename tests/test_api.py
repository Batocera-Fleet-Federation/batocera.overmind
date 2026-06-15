"""Tests for the Batocera Overmind API."""

import io
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from overmind import main as overmind_main
from overmind import db as db_module
from overmind.main import app, ensure_self_signed_cert, TOKEN_HASH_SECRET
from overmind.db import db
from overmind import auth as auth_utils
from overmind.postgres_store import _encode_state


@pytest.fixture
def client(monkeypatch):
    """Create a test client."""
    monkeypatch.setenv("OVERMIND_AUTO_VERIFY_REGISTRATION", "1")
    db.users.clear()
    db.user_by_email.clear()
    db.devices.clear()
    db.device_admin_claims.clear()
    db.user_devices.clear()
    db.roms.clear()
    db.bios.clear()
    db.artwork.clear()
    db._asset_inventory_staging.clear()
    db.gamelogs.clear()
    db.device_actions.clear()
    db.speed_samples.clear()
    db.device_events.clear()
    db.peer_checks.clear()
    db.integration_tokens.clear()
    db.approved_drone_tokens.clear()
    db.rom_sync_activity.clear()
    db.download_states.clear()
    db.pending_drone_connections.clear()
    db.email_verifications.clear()
    db.password_resets.clear()
    db.swarms.clear()
    db.swarm_memberships.clear()
    db.invitations.clear()
    db.notifications.clear()
    overmind_main.oauth_states.clear()
    return TestClient(app)


def mark_source_resolvable(device_id: str, public_ip: str = "8.8.8.8"):
    device = db.get_device_by_device_id(device_id)
    assert device is not None
    device["network"] = {**(device.get("network") or {}), "public_ip": public_ip}
    # Resolvability is now determined by peer checks, not server-side probes.
    # Inject a synthetic passing peer check directly into the peer_checks store.
    sentinel_bucket = db.peer_checks.setdefault("__test_sentinel__", [])
    sentinel_bucket.append({
        "source_drone_id": "__test_sentinel__",
        "target_drone_id": device_id,
        "target_address": f"https://{public_ip}",
        "status": "pass",
        "latency_ms": 1,
        "checked_at": datetime.utcnow(),
    })


def seed_test_fleet():
    user_id = db.create_user(
        "demo@example.com",
        auth_utils.hash_password("DemoPass123"),
        "Demo User",
        verified=True,
        username="demo-at-example.com",
    )
    other_user_id = db.create_user(
        "arcade@example.com",
        auth_utils.hash_password("ArcadePass123"),
        "Arcade User",
        verified=True,
        username="arcade-at-example.com",
    )
    for owner_id, device_id, name in [
        (user_id, "arcade-cabinet-001", "Living Room Cabinet"),
        (user_id, "raspberry-pi-001", "Bedroom Pi"),
        (other_user_id, "arcade-cabinet-002", "Game Room Arcade"),
    ]:
        db.create_device(
            owner_id,
            device_id,
            name,
            {"network": {"ipv4": ["127.0.0.1"]}, "system_info": {"hostname": name}},
            raw_token="demo-local-drone-token" if device_id == "arcade-cabinet-001" else "test-drone-token",
        )
    systems = ["snes", "nes", "genesis", "gba", "psx"]
    for index, system in enumerate(systems, start=1):
        db.add_roms(
            "arcade-cabinet-001",
            system,
            [
                {
                    "rom_name": f"{system.upper()} Game {number}",
                    "rom_fingerprint": f"{index}{number}".ljust(32, "0")[:32],
                    "file_path": f"/roms/{system}/{system}-game-{number}.zip",
                    "file_size": index * 100 + number,
                }
                for number in range(1, 6)
            ],
        )
    db.add_roms("raspberry-pi-001", "snes", [{"rom_name": "SNES Game 1", "rom_fingerprint": "1" * 32, "file_path": "/roms/snes/snes-game-1.zip", "file_size": 1}])
    db.add_roms("arcade-cabinet-002", "genesis", [{"rom_name": "GENESIS Game 1", "rom_fingerprint": "2" * 32, "file_path": "/roms/genesis/genesis-game-1.zip", "file_size": 2}])
    db.log_gameplay("arcade-cabinet-001", "snes", "Super Mario World", 1200)
    db.create_pending_drone_connection("rogue-signal-001", "Basement Recon Drone", {"network": {"ipv4": ["127.0.0.2"]}}, user_id)
    db.create_pending_drone_connection("rogue-signal-002", "Workshop Handheld Drone", {"network": {"ipv4": ["127.0.0.3"]}}, user_id)
    return {"demo_user_id": user_id, "arcade_user_id": other_user_id}


def seed_test_notifications():
    user = db.get_user_by_email("demo@example.com")
    assert user is not None
    swarm_id = db.default_swarm_id(user["id"])
    assert swarm_id is not None
    for index in range(12):
        db.add_swarm_notification(
            swarm_id,
            "master_rom_added",
            "New ROM in master list",
            f"Test ROM {index} joined the master list.",
            {"asset": {"path": f"Test ROM {index}.zip"}},
        )


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"].startswith("v")


def test_ui_header_shows_version_badge_from_version_file(client, monkeypatch):
    monkeypatch.delenv("OVERMIND_VERSION", raising=False)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    version = Path(__file__).resolve().parents[1].joinpath("VERSION").read_text(encoding="utf-8").strip()
    assert 'id="overmind-version-badge"' in html
    assert version in html
    assert "__OVERMIND_VERSION_BADGE__" not in html


def test_ui_fallback_serves_client_routes_but_not_api(client):
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Batocera Overmind" in dashboard.text

    missing_api = client.get("/api/not-a-real-route")
    assert missing_api.status_code == 404


def test_ui_header_shows_environment_version_badge(client, monkeypatch):
    monkeypatch.setenv("OVERMIND_VERSION", "local:console & safe")
    html = client.get("/").text
    assert 'id="overmind-version-badge"' in html
    assert "local:console &amp; safe" in html
    assert "local:console & safe" not in html


def test_ui_header_groups_account_actions_under_avatar_dropdown(client):
    html = client.get("/").text
    css = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/css/overmind.css").read_text(encoding="utf-8")
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert 'id="account-menu"' in html
    assert 'id="nav-profile-avatar"' in html
    assert 'id="nav-profile-avatar-fallback"' in html
    assert 'role="button" class="btn nav-btn active requires-auth" data-tab="devices"' in html
    assert '<nav class="account-menu-panel" aria-label="Account">' in html
    assert '<a href="#/hive" class="account-menu-item nav-btn" data-tab="hive"' in html
    assert html.index('data-tab="profile"') < html.index('data-tab="hive"') < html.index('data-tab="super-admin"')
    assert html.count('data-tab="profile"') == 1
    assert html.count('data-tab="hive"') == 1
    assert html.count('data-tab="super-admin"') == 1
    assert "body:not(.is-authenticated) .sidebar .requires-auth { display: none !important; }" in css
    assert ".layout-shell aside {" in css and "z-index: 10000;" in css
    assert ".account-menu-panel {" in css and "z-index: 10002;" in css
    assert "border: 0 !important;" in css
    assert "background: rgba(255, 255, 255, 0.14) !important;" in css
    assert "renderAccountAvatar();" in js
    assert "closeAccountMenu(); logout()" in html


def test_register_user(client):
    """Test user registration."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "username": "test-at-example.com",
            "password": "testpass123",
            "full_name": "Test User"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["username"] == "test-at-example.com"
    assert "id" in data


def test_registration_requires_unique_username(client):
    missing = client.post(
        "/api/auth/register",
        json={"email": "missing-name@example.com", "password": "testpass123"},
    )
    assert missing.status_code == 422

    first = client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "username": "CabinetMaster", "password": "testpass123"},
    )
    assert first.status_code == 200

    duplicate = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "username": "cabinetmaster", "password": "testpass123"},
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "Username already registered"


def test_duplicate_registration(client):
    """Test duplicate registration fails."""
    # First registration
    client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "username": "test-at-example.com",
            "password": "testpass123",
        }
    )
    
    # Duplicate registration
    response = client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "username": "test-at-example.com",
            "password": "testpass123",
        }
    )
    assert response.status_code == 400


def test_login(client):
    """Test user login."""
    # Register first
    client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "username": "test-at-example.com",
            "password": "testpass123",
        }
    )
    
    # Login
    response = client.post(
        "/api/auth/login",
        json={
            "email": "test@example.com",
            "username": "test-at-example.com",
            "password": "testpass123"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_auth_refresh_requires_and_returns_valid_token(client):
    client.post("/api/auth/register", json={"email": "refresh@example.com", "username": "refresh-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "refresh@example.com", "username": "refresh-at-example.com", "password": "testpass123"}).json()["access_token"]

    denied = client.post("/api/auth/refresh")
    assert denied.status_code == 401

    refreshed = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {token}"})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    assert refreshed.json()["user"]["email"] == "refresh@example.com"


def test_notifications_endpoint_pages_with_limit_offset_and_counts(client):
    client.post("/api/auth/register", json={"email": "paged@example.com", "username": "paged-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "paged@example.com", "username": "paged-at-example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("paged@example.com")
    swarm_id = db.default_swarm_id(user["id"])
    for i in range(7):
        db.add_swarm_notification(swarm_id, "sync_triggered", f"Event {i}", f"message {i}", {})

    page1 = client.get("/api/notifications?limit=3&offset=0", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(page1["notifications"]) == 3
    assert page1["total_count"] == 7
    assert page1["unread_count"] == 7  # accurate total, not limited to the page
    assert page1["limit"] == 3 and page1["offset"] == 0

    page2 = client.get("/api/notifications?limit=3&offset=3", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(page2["notifications"]) == 3
    page3 = client.get("/api/notifications?limit=3&offset=6", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(page3["notifications"]) == 1  # last page

    # Pages are disjoint and newest-first ordering is stable across pages.
    ids = [r["id"] for r in page1["notifications"] + page2["notifications"] + page3["notifications"]]
    assert len(set(ids)) == 7


def test_notifications_capture_master_list_add_and_read(client):
    client.post("/api/auth/register", json={"email": "notify@example.com", "username": "notify-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "notify@example.com", "username": "notify-at-example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("notify@example.com")
    db.create_device(user["id"], "notify-drone", "Notify Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    db.store_rom_metadata("notify-drone", {
        "systems": [{"name": "snes"}],
        "roms": [{"system": "snes", "rom_name": "Chrono Trigger", "file_path": "Chrono Trigger.zip", "rom_fingerprint": "abc"}],
        "bios": [{"file_path": "dc/flash.bin", "bios_md5": "bios-fingerprint"}],
        "artwork": [{"system": "snes", "rom_path": "Chrono Trigger.zip", "artwork_types": ["image"]}],
    })

    response = client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    notifications = response.json()["notifications"]
    event_types = {row["event_type"] for row in notifications}
    assert "master_rom_added" in event_types
    assert "master_bios_added" in event_types
    assert "master_artwork_added" in event_types
    assert any("Notify Drone" in row["message"] and "Chrono Trigger.zip" in row["message"] for row in notifications)
    assert all("short_description" in row and "full_description" in row for row in notifications)
    assert any(row["short_description"] == row["title"] and row["full_description"] == row["message"] for row in notifications)

    read = client.post("/api/notifications/read", headers={"Authorization": f"Bearer {token}"}, json={})
    assert read.status_code == 200
    assert client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"}).json()["unread_count"] == 0
    first_id = notifications[0]["id"]
    dismissed = client.post("/api/notifications/dismiss", headers={"Authorization": f"Bearer {token}"}, json={"ids": [first_id]})
    assert dismissed.status_code == 200
    remaining_ids = {row["id"] for row in client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"}).json()["notifications"]}
    assert first_id not in remaining_ids


def test_notifications_capture_drone_status_transition_and_sync_trigger(client):
    client.post("/api/auth/register", json={"email": "syncnotify@example.com", "username": "syncnotify-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "syncnotify@example.com", "username": "syncnotify-at-example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("syncnotify@example.com")
    db.create_device(user["id"], "source-drone", "Source Drone", {"ip_address": "10.0.0.2"}, raw_token="source-token")
    db.create_device(user["id"], "target-drone", "Target Drone", {"ip_address": "10.0.0.3"}, raw_token="target-token")
    mark_source_resolvable("source-drone")
    db.add_roms("source-drone", "snes", [{"rom_name": "Game.zip", "file_path": "Game.zip", "rom_fingerprint": "abc", "file_size": 8}])
    db.devices[db.get_device_by_device_id("target-drone")["id"]]["last_seen"] = datetime.utcnow() - timedelta(seconds=999)

    devices = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices.status_code == 200
    assert any(row["event_type"] == "drone_offline" and "Target Drone" in row["message"] for row in client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"}).json()["notifications"])

    sync = client.post(
        "/api/devices/target-drone/sync-rom",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "snes", "file_path": "Game.zip", "rom_fingerprint": "abc", "file_size": 8},
    )
    assert sync.status_code == 200
    notifications = client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"}).json()["notifications"]
    assert any(row["event_type"] == "sync_triggered" and "ROM sync" in row["message"] and "Source Drone" in row["message"] for row in notifications)


def test_notification_delivery_uses_enabled_channels_and_selected_event_types(client, monkeypatch):
    sent = []
    webhooks = []

    def mock_send_email(to_email, subject, html_body, text_body, from_email=None):
        sent.append({"to": to_email, "subject": subject, "html": html_body, "text": text_body})
        return True

    def mock_post_webhook(webhook_url, payload):
        webhooks.append({"url": webhook_url, "payload": payload})
        return True

    monkeypatch.setattr(db_module.notification_delivery.emailer, "send_email", mock_send_email)
    monkeypatch.setattr(db_module.notification_delivery, "post_webhook", mock_post_webhook)
    client.post("/api/auth/register", json={"email": "mailnotify@example.com", "username": "mailnotify-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "mailnotify@example.com", "username": "mailnotify-at-example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("mailnotify@example.com")
    swarm_id = db.default_swarm_id(user["id"])

    update = client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "notification_settings": {
                "notify_email": True,
                "notify_slack": True,
                "notify_discord": False,
                "slack_webhook": "https://hooks.slack.com/services/T/B/X",
                "discord_webhook": "https://discord.com/api/webhooks/1/secret",
                "email_address": "not-the-target@example.com",
                "types": {"sync_triggered": True, "drone_status": False},
            }
        },
    )
    assert update.status_code == 200
    assert "email_address" not in update.json()["notification_settings"]

    db.add_swarm_notification(
        swarm_id,
        "sync_triggered",
        "ROM sync triggered",
        "Mail User triggered a ROM sync.",
        {"sync_type": "ROM", "nature": "ROM sync for snes/Game.zip", "targets": [], "sources": []},
        actor_user_id=user["id"],
    )
    db.add_swarm_notification(
        swarm_id,
        "drone_offline",
        "Drone offline",
        "Mail Drone is offline.",
        {"device": {"device_id": "mail-drone", "device_name": "Mail Drone"}, "status": "offline"},
    )

    assert sent == []
    assert webhooks == []
    assert db_module.notification_delivery.deliver_pending_notifications(db) == 2
    assert len(sent) == 1
    assert sent[0]["to"] == "mailnotify@example.com"
    assert "1 swarm update" in sent[0]["subject"]
    assert "ROM sync for snes/Game.zip" in sent[0]["html"]
    assert len(webhooks) == 1
    assert webhooks[0]["url"] == "https://hooks.slack.com/services/T/B/X"
    assert "ROM sync triggered" in webhooks[0]["payload"]["text"]
    assert all("discord.com" not in row["url"] for row in webhooks)

    db.add_swarm_notification(
        swarm_id,
        "device_action_completed",
        "Remote action completed",
        "Remote Restart completed on Mail Drone.",
        {"device": {"device_id": "mail-drone", "device_name": "Mail Drone"}, "status": "completed"},
    )
    db_module.notification_delivery.deliver_pending_notifications(db)
    assert len(sent) == 2
    assert "Remote Restart completed on Mail Drone." in sent[1]["html"]
    assert len(webhooks) == 2
    assert "Remote action completed" in webhooks[1]["payload"]["text"]

    sent.clear()
    webhooks.clear()
    client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "notification_settings": {
                "notify_email": False,
                "notify_slack": False,
                "notify_discord": True,
                "types": {"sync_triggered": True},
            }
        },
    )
    db.add_swarm_notification(
        swarm_id,
        "sync_triggered",
        "BIOS sync triggered",
        "Mail User triggered a BIOS sync.",
        {"sync_type": "BIOS", "nature": "BIOS sync for dc/flash.bin", "targets": [], "sources": []},
        actor_user_id=user["id"],
    )
    assert sent == []
    db_module.notification_delivery.deliver_pending_notifications(db)
    assert len(webhooks) == 1
    assert webhooks[0]["url"] == "https://discord.com/api/webhooks/1/secret"
    assert "BIOS sync triggered" in webhooks[0]["payload"]["embeds"][0]["description"]

    sent.clear()
    webhooks.clear()
    client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"notification_settings": {"notify_email": True, "notify_discord": False, "types": {"drone_status": True}}},
    )
    # drone_online/offline are no longer delivered in real time; they aggregate into
    # the per-channel digest like every other event.
    db.add_swarm_notification(
        swarm_id,
        "drone_online",
        "Drone online",
        "Mail Drone is online.",
        {"device": {"device_id": "mail-drone", "device_name": "Mail Drone"}, "status": "online"},
    )
    assert sent == []
    db_module.notification_delivery.deliver_pending_notifications(db)
    assert len(sent) == 1
    assert "Drone online" in sent[0]["html"]


def test_notification_delivery_batches_multiple_asset_updates_in_one_email(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        db_module.notification_delivery.emailer,
        "send_email",
        lambda to_email, subject, html_body, text_body, from_email=None: sent.append(subject) or True,
    )
    client.post("/api/auth/register", json={"email": "digest@example.com", "username": "digest-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "digest@example.com", "username": "digest-at-example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("digest@example.com")
    swarm_id = db.default_swarm_id(user["id"])
    client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"notification_settings": {"notify_email": True, "types": {"master_rom": True}}},
    )

    for name in ("A.zip", "B.zip"):
        db.add_swarm_notification(swarm_id, "master_rom_added", "New ROM added", name, {"asset": {"path": name}})

    assert sent == []
    db_module.notification_delivery.deliver_pending_notifications(db)
    assert sent == ["Batocera Overmind: 2 swarm updates"]


def test_notification_delivery_only_aggregates_recent_window(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        db_module.notification_delivery.emailer,
        "send_email",
        lambda to_email, subject, html_body, text_body, from_email=None: sent.append(html_body) or True,
    )
    client.post("/api/auth/register", json={"email": "window@example.com", "username": "window-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "window@example.com", "username": "window-at-example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("window@example.com")
    swarm_id = db.default_swarm_id(user["id"])
    client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"notification_settings": {"notify_email": True, "types": {"master_rom": True}}},
    )

    recent = db.add_swarm_notification(swarm_id, "master_rom_added", "Recent ROM", "Recent.zip", {"asset": {"path": "Recent.zip"}})
    stale = db.add_swarm_notification(swarm_id, "master_rom_added", "Stale ROM", "Stale.zip", {"asset": {"path": "Stale.zip"}})
    # Age the stale notification beyond the 3-minute aggregation window.
    stale["created_at"] = datetime.utcnow() - timedelta(minutes=10)

    db_module.notification_delivery.deliver_pending_notifications(db)
    assert len(sent) == 1
    assert "Recent.zip" in sent[0]
    assert "Stale.zip" not in sent[0]
    # The stale notification is retired (not re-summarized on the next run).
    assert stale.get("delivery_pending") is False
    assert recent.get("delivery_pending") is False


def test_notification_delivery_skips_already_read_notifications(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        db_module.notification_delivery.emailer,
        "send_email",
        lambda to_email, subject, html_body, text_body, from_email=None: sent.append(html_body) or True,
    )
    client.post("/api/auth/register", json={"email": "readskip@example.com", "username": "readskip-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "readskip@example.com", "username": "readskip-at-example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("readskip@example.com")
    swarm_id = db.default_swarm_id(user["id"])
    client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"notification_settings": {"notify_email": True, "types": {"master_rom": True}}},
    )

    note = db.add_swarm_notification(swarm_id, "master_rom_added", "Read ROM", "Read.zip", {"asset": {"path": "Read.zip"}})
    note["read_by"] = {user["id"]: datetime.utcnow()}

    db_module.notification_delivery.deliver_pending_notifications(db)
    assert sent == []


def test_notification_delivery_claims_pending_rows_before_sending(client, monkeypatch):
    sent = []
    completed_at = datetime.utcnow()

    client.post("/api/auth/register", json={"email": "claimnotify@example.com", "username": "claimnotify-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("claimnotify@example.com")
    swarm_id = db.default_swarm_id(user["id"])
    db.update_user_notification_settings(user["id"], {"notify_email": True, "types": {"sync_triggered": True}})
    notification = db.add_swarm_notification(
        swarm_id,
        "sync_triggered",
        "Sync queued",
        "Sync event queued",
        {"sync_type": "ROM"},
        actor_user_id=user["id"],
    )

    def claim_pending(notification_ids, limit=0):
        return {notification["id"]: completed_at}

    def send_email(to_email, subject, html_body, text_body, from_email=None):
        row = db.notifications[swarm_id][0]
        assert row["delivery_pending"] is False
        assert row["delivery_completed_at"] == completed_at
        sent.append(subject)
        return True

    monkeypatch.setattr(db_module.postgres_store, "claim_pending_notifications", claim_pending)
    monkeypatch.setattr(db_module.notification_delivery.emailer, "send_email", send_email)

    assert db_module.notification_delivery.deliver_pending_notifications(db) == 1
    assert sent == ["Batocera Overmind: 1 swarm update"]

    sent.clear()
    assert db_module.notification_delivery.deliver_pending_notifications(db) == 0
    assert sent == []


def test_notification_delivery_limits_pending_notifications_per_run(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        db_module.notification_delivery.emailer,
        "send_email",
        lambda to_email, subject, html_body, text_body, from_email=None: sent.append(subject) or True,
    )
    client.post("/api/auth/register", json={"email": "digest-limit@example.com", "username": "digest-limit-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("digest-limit@example.com")
    swarm_id = db.default_swarm_id(user["id"])
    db.update_user_notification_settings(user["id"], {"notify_email": True, "types": {"sync_triggered": True}})

    for index in range(3):
        db.add_swarm_notification(
            swarm_id,
            "sync_triggered",
            f"Sync {index}",
            f"Sync event {index}",
            {"sync_type": "ROM"},
            actor_user_id=user["id"],
        )

    assert db_module.notification_delivery.deliver_pending_notifications(db, limit=2) == 1
    pending = [row for row in db.notifications[swarm_id] if row.get("delivery_pending") is True]
    completed = [row for row in db.notifications[swarm_id] if row.get("delivery_pending") is not True]

    assert len(sent) == 1
    assert len(completed) == 2
    assert len(pending) == 1


def test_device_status_notifications_limits_devices_per_run(client):
    client.post("/api/auth/register", json={"email": "status-limit@example.com", "username": "status-limit-at-example.com", "password": "testpass123"})
    owner = db.get_user_by_email("status-limit@example.com")
    for index in range(3):
        db.create_device(
            owner["id"],
            f"status-limit-{index}",
            f"Status Limit {index}",
            {"network": {"public_ip": f"8.8.8.{index + 1}"}, "api_port": 443, "scheme": "https"},
            raw_token=f"status-limit-token-{index}",
        )
        device = db.get_device_by_device_id(f"status-limit-{index}")
        device["last_seen"] = datetime.utcnow() - timedelta(seconds=999)
        device["last_known_status"] = "online"

    db.update_device_status_notifications(offline_seconds=180, limit=1)

    checked = [
        device for device in db.devices.values()
        if str(device.get("device_id", "")).startswith("status-limit-") and device.get("last_status_checked_at")
    ]
    offline = [
        device for device in db.devices.values()
        if str(device.get("device_id", "")).startswith("status-limit-") and device.get("last_known_status") == "offline"
    ]

    assert len(checked) == 1
    assert len(offline) == 1


def test_seeded_notifications_are_visible(client):
    seed_test_fleet()
    seed_test_notifications()
    token = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    ).json()["access_token"]

    response = client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["notifications"]) >= 10
    assert payload["unread_count"] > 0


def test_invalid_login(client):
    """Test login with invalid credentials."""
    response = client.post(
        "/api/auth/login",
        json={
            "email": "test@example.com",
            "username": "test-at-example.com",
            "password": "wrongpassword"
        }
    )
    assert response.status_code == 401


def test_social_auth_buttons_disabled_without_env(client, monkeypatch):
    """Social auth providers are disabled until required ENV VARs are set."""
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    response = client.get("/api/auth/providers")
    assert response.status_code == 200
    assert response.json()["providers"] == {"google": False, "github": False}


def test_oauth_start_uses_signed_state_for_lambda(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-secret")

    response = client.get("/api/auth/github/start", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    parsed = overmind_main.urllib.parse.urlparse(location)
    params = overmind_main.urllib.parse.parse_qs(parsed.query)
    state = params["state"][0]
    assert "." in state
    assert overmind_main.verify_oauth_state(state, "github") is True
    assert overmind_main.oauth_states == {}


def test_jwt_signing_secret_survives_runtime_secret_refresh(monkeypatch):
    original_secret = auth_utils.SECRET_KEY
    original_jwt_secret = auth_utils.JWT_SIGNING_SECRET
    try:
        auth_utils.SECRET_KEY = "runtime-secret-a"
        auth_utils.JWT_SIGNING_SECRET = "stable-jwt-secret"
        token = auth_utils.create_access_token({"sub": "user-1"})

        auth_utils.SECRET_KEY = "runtime-secret-b"
        auth_utils.JWT_SIGNING_SECRET = "stable-jwt-secret"

        assert auth_utils.decode_token(token)["sub"] == "user-1"
    finally:
        auth_utils.SECRET_KEY = original_secret
        auth_utils.JWT_SIGNING_SECRET = original_jwt_secret


def test_social_auth_activates_existing_unverified_user(client, monkeypatch):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-secret")
    client.post("/api/auth/register", json={"email": "social-existing@example.com", "username": "social-existing-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("social-existing@example.com")
    assert user["email_verified"] is False
    assert user["is_active"] is False

    response = client.post(
        "/api/auth/github",
        json={"email": "social-existing@example.com", "full_name": "Social Existing"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    refreshed = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {token}"})
    assert refreshed.status_code == 200
    user = db.get_user_by_email("social-existing@example.com")
    assert user["email_verified"] is True
    assert user["is_active"] is True
    assert db.default_swarm_id(user["id"])


class OAuthJsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_social_auth_callback_redirects_on_github_user_unauthorized(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-secret")
    overmind_main.oauth_states["state-1"] = "github"

    def mock_urlopen(request, timeout=10):
        if request.full_url == "https://github.com/login/oauth/access_token":
            return OAuthJsonResponse({"access_token": "bad-token"})
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"Bad credentials"}'),
        )

    monkeypatch.setattr(overmind_main.urllib.request, "urlopen", mock_urlopen)
    response = client.get(
        "/api/auth/github/callback?code=code-1&state=state-1",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"].startswith("/#oauth_error=GitHub%20login%20failed%20while%20loading%20account%20details.")
    assert db.get_user_by_email("social-github@example.com") is None


def test_social_auth_callback_completes_github_with_private_primary_email(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-secret")
    overmind_main.oauth_states["state-2"] = "github"
    monkeypatch.setattr(db, "refresh_persistent_state", lambda: (_ for _ in ()).throw(AssertionError("callback should not refresh full state")))

    def mock_urlopen(request, timeout=10):
        if request.full_url == "https://github.com/login/oauth/access_token":
            return OAuthJsonResponse({"access_token": "github-token"})
        if request.full_url == "https://api.github.com/user":
            return OAuthJsonResponse({"login": "octo"})
        if request.full_url == "https://api.github.com/user/emails":
            return OAuthJsonResponse([
                {"email": "social-github@example.com", "primary": True, "verified": True},
            ])
        raise AssertionError(f"Unexpected OAuth URL {request.full_url}")

    monkeypatch.setattr(overmind_main.urllib.request, "urlopen", mock_urlopen)
    response = client.get(
        "/api/auth/github/callback?code=code-2&state=state-2",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"].startswith("/#oauth_token=")
    user = db.get_user_by_email("social-github@example.com")
    assert user["full_name"] == "octo"
    assert user["username"] == "octo"
    assert user["auth_provider"] == "github"


def test_super_admin_overview_and_delete_permissions(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr_jerrodh-at-hotmail.com", "password": "testpass123"})
    admin_token = client.post(
        "/api/auth/login",
        json={"email": "mr_jerrodh@hotmail.com", "username": "mr_jerrodh-at-hotmail.com", "password": "testpass123"},
    ).json()["access_token"]
    client.post("/api/auth/register", json={"email": "regular@example.com", "username": "regular-at-example.com", "password": "testpass123"})
    regular_token = client.post(
        "/api/auth/login",
        json={"email": "regular@example.com", "username": "regular-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    regular_user = db.get_user_by_email("regular@example.com")
    db.create_device(regular_user["id"], "admin-visible-drone", "Admin Visible Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    denied = client.get("/api/admin/overview", headers={"Authorization": f"Bearer {regular_token}"})
    assert denied.status_code == 403

    overview = client.get("/api/admin/overview", headers={"Authorization": f"Bearer {admin_token}"})
    assert overview.status_code == 200
    payload = overview.json()
    assert any(user["email"] == "regular@example.com" for user in payload["users"])
    assert any(drone["device_id"] == "admin-visible-drone" for drone in payload["drones"])

    metrics = client.get("/api/admin/runtime-metrics", headers={"Authorization": f"Bearer {admin_token}"})
    assert metrics.status_code == 200
    assert "cpu" in metrics.json()["metrics"]
    assert "memory" in metrics.json()["metrics"]

    logs = client.get("/api/admin/runtime-logs", headers={"Authorization": f"Bearer {admin_token}"})
    assert logs.status_code == 200
    assert set(logs.json()["logs"]) >= {"stdout", "stderr", "max_lines", "captured_at"}

    self_delete = client.delete(
        f"/api/admin/users/{db.get_user_by_email('mr_jerrodh@hotmail.com')['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert self_delete.status_code == 400

    delete_drone = client.delete("/api/admin/drones/admin-visible-drone", headers={"Authorization": f"Bearer {admin_token}"})
    assert delete_drone.status_code == 200
    assert db.get_device_by_device_id("admin-visible-drone") is None


def test_super_admin_user_row_includes_swarm_name(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr_jerrodh-at-hotmail.com", "password": "testpass123"})
    admin_token = client.post("/api/auth/login", json={"email": "mr_jerrodh@hotmail.com", "password": "testpass123"}).json()["access_token"]
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})

    overview = client.get("/api/admin/overview", headers={"Authorization": f"Bearer {admin_token}"}).json()
    owner_row = next(user for user in overview["users"] if user["email"] == "owner@example.com")
    assert "swarm_name" in owner_row and owner_row["swarm_name"]
    # The delete-swarm endpoint is removed.
    assert client.delete("/api/admin/swarms/anything", headers={"Authorization": f"Bearer {admin_token}"}).status_code in (404, 405)


def test_pending_connections_exclude_approved_drones(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "dup-drone", "Dup Drone", {"ip_address": "1.2.3.4"}, raw_token="t")
    db.pending_drone_connections["dup-drone"] = {
        "device_id": "dup-drone",
        "status": "pending",
        "device_name": "Dup Drone",
        "last_seen": datetime.utcnow(),
    }
    pending = db.get_all_pending_drone_connections()
    assert all(conn["device_id"] != "dup-drone" for conn in pending)
    user_pending = db.get_pending_drone_connections(owner["id"])
    assert all(conn["device_id"] != "dup-drone" for conn in user_pending)


def test_approved_drone_cannot_recreate_pending_connection(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "approved-drone", "Approved Drone", {"ip_address": "1.2.3.4"}, raw_token="t")

    pending = db.create_pending_drone_connection(
        "approved-drone",
        "Approved Drone",
        {"network": {"ipv4": ["1.2.3.4"]}},
        owner["id"],
    )

    assert pending["status"] == "approved"
    assert "approved-drone" not in db.pending_drone_connections
    assert db.get_pending_drone_connections(owner["id"]) == []
    assert db.get_all_pending_drone_connections() == []


def test_super_admin_sync_actions_listing_and_search(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr_jerrodh-at-hotmail.com", "password": "testpass123"})
    admin_token = client.post("/api/auth/login", json={"email": "mr_jerrodh@hotmail.com", "password": "testpass123"}).json()["access_token"]
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "sync-drone", "Sync Drone", {"ip_address": "10.0.0.5"}, raw_token="t1")
    db.create_device_action(owner["id"], "sync-drone", "sync_rom", {"system": "snes", "rom_name": "Chrono Trigger"})
    db.create_device_action(owner["id"], "sync-drone", "sync_artwork", {"system": "gba", "rom_name": "Metroid Fusion"})
    # A finished action must still appear (full history, not just queued).
    done = db.create_device_action(owner["id"], "sync-drone", "sync_bios", {"system": "psx", "rom_name": "scph"})
    done["status"] = "completed"

    resp = client.get("/api/admin/sync-actions", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert {a["system"] for a in body["sync_actions"]} == {"snes", "gba", "psx"}
    assert any(a["status"] == "completed" for a in body["sync_actions"])
    assert all(a["email"] == "owner@example.com" for a in body["sync_actions"])

    # Pagination: first page of 2, then the remaining 1.
    page1 = client.get("/api/admin/sync-actions?limit=2&offset=0", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert len(page1["sync_actions"]) == 2 and page1["total"] == 3
    page2 = client.get("/api/admin/sync-actions?limit=2&offset=2", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert len(page2["sync_actions"]) == 1 and page2["total"] == 3

    by_rom = client.get("/api/admin/sync-actions?q=chrono", headers={"Authorization": f"Bearer {admin_token}"}).json()["sync_actions"]
    assert len(by_rom) == 1 and by_rom[0]["system"] == "snes"

    by_email = client.get("/api/admin/sync-actions?q=owner@example", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert by_email["total"] == 3

    none_match = client.get("/api/admin/sync-actions?q=zzzznope", headers={"Authorization": f"Bearer {admin_token}"}).json()["sync_actions"]
    assert none_match == []

    client.post("/api/auth/register", json={"email": "reg@example.com", "username": "reg-at-example.com", "password": "testpass123"})
    reg_token = client.post("/api/auth/login", json={"email": "reg@example.com", "password": "testpass123"}).json()["access_token"]
    assert client.get("/api/admin/sync-actions", headers={"Authorization": f"Bearer {reg_token}"}).status_code == 403


def test_new_user_registration_alerts_superadmin(client):
    # Superadmin must exist before the new user registers so it can be notified.
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"})
    admin_token = client.post("/api/auth/login", json={"email": "mr_jerrodh@hotmail.com", "password": "testpass123"}).json()["access_token"]
    admin = db.get_user_by_email("mr_jerrodh@hotmail.com")

    client.post("/api/auth/register", json={"email": "newbie@example.com", "username": "newbie", "password": "testpass123"})

    # Bell notification reached the superadmin.
    notifications = client.get("/api/notifications", headers={"Authorization": f"Bearer {admin_token}"}).json()["notifications"]
    user_alerts = [n for n in notifications if n["event_type"] == "admin_user_registered"]
    assert len(user_alerts) == 1
    assert "newbie" in user_alerts[0]["message"]

    # Audit log captured it.
    audit = client.get("/api/admin/audit-log", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert any(e["event_type"] == "user_registered" and e["target_label"] == "newbie@example.com" for e in audit["audit_events"])

    # The hidden admin-alert swarm is not exposed in normal swarm listings.
    visible = {s["id"] for s in db.get_user_swarms(admin["id"])}
    assert "__overmind_admin__" not in visible
    assert "__overmind_admin__" in {s["id"] for s in db.get_user_swarms(admin["id"], include_system=True)}


def test_new_drone_registration_alerts_superadmin_when_not_owned(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"})
    admin_token = client.post("/api/auth/login", json={"email": "mr_jerrodh@hotmail.com", "password": "testpass123"}).json()["access_token"]

    client.post("/api/auth/register", json={"email": "remote-owner@example.com", "username": "remote-owner", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "remote-owner@example.com", "password": "testpass123"}).json()["access_token"]
    auth_token = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"label": "Cabinet"},
    ).json()["token"]["authorization_token"]

    register = client.post(
        "/api/devices/register",
        headers={"Authorization": f"Bearer {auth_token}"},
        json=_device_registration_payload(
            authorization_token=auth_token,
            device_id="guest-arcade",
            device_name="Guest Arcade",
            email="remote-owner@example.com",
        ),
    )
    assert register.status_code == 200

    alerts = [n for n in client.get("/api/notifications", headers={"Authorization": f"Bearer {admin_token}"}).json()["notifications"]
              if n["event_type"] == "admin_drone_registered"]
    assert len(alerts) == 1
    assert "Guest Arcade" in alerts[0]["message"]

    audit = client.get("/api/admin/audit-log?q=guest", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert any(e["event_type"] == "drone_registered" for e in audit["audit_events"])


def test_admin_audit_log_and_sync_summary_are_super_admin_only(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"})
    admin_token = client.post("/api/auth/login", json={"email": "mr_jerrodh@hotmail.com", "password": "testpass123"}).json()["access_token"]
    client.post("/api/auth/register", json={"email": "reg@example.com", "username": "reg", "password": "testpass123"})
    reg_token = client.post("/api/auth/login", json={"email": "reg@example.com", "password": "testpass123"}).json()["access_token"]

    assert client.get("/api/admin/audit-log", headers={"Authorization": f"Bearer {reg_token}"}).status_code == 403
    assert client.get("/api/admin/sync-actions/summary", headers={"Authorization": f"Bearer {reg_token}"}).status_code == 403

    summary = client.get("/api/admin/sync-actions/summary", headers={"Authorization": f"Bearer {admin_token}"})
    assert summary.status_code == 200
    body = summary.json()
    assert "total" in body and "by_status" in body


def test_landing_visits_counted_by_unique_ip(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"})
    admin_token = client.post("/api/auth/login", json={"email": "mr_jerrodh@hotmail.com", "password": "testpass123"}).json()["access_token"]

    # Same IP twice -> one unique visitor, two total visits.
    assert client.post("/api/landing-visit", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200
    assert client.post("/api/landing-visit", headers={"X-Forwarded-For": "203.0.113.10"}).status_code == 200
    # A different IP -> a second unique visitor.
    client.post("/api/landing-visit", headers={"X-Forwarded-For": "203.0.113.20"})

    stats = client.get("/api/admin/landing-visits", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert stats["unique"] == 2
    assert stats["total"] == 3
    ips = {v["ip"] for v in stats["visits"]}
    assert {"203.0.113.10", "203.0.113.20"} <= ips
    repeat = next(v for v in stats["visits"] if v["ip"] == "203.0.113.10")
    assert repeat["visit_count"] == 2

    # Super-admin only.
    client.post("/api/auth/register", json={"email": "reg@example.com", "username": "reg", "password": "testpass123"})
    reg_token = client.post("/api/auth/login", json={"email": "reg@example.com", "password": "testpass123"}).json()["access_token"]
    assert client.get("/api/admin/landing-visits", headers={"Authorization": f"Bearer {reg_token}"}).status_code == 403


def test_drone_reachability_notification_type_registered():
    from overmind import notification_delivery as nd
    from pathlib import Path as _Path

    assert nd.EVENT_TYPE_TO_SETTING["drone_resolvable"] == "drone_reachability"
    assert nd.EVENT_TYPE_TO_SETTING["admin_user_registered"] == "admin_alerts"
    assert nd.EVENT_TYPE_TO_SETTING["drone_unresolvable"] == "drone_reachability"
    assert nd.DEFAULT_NOTIFICATION_TYPES["drone_reachability"] is True
    assert nd._TEMPLATE_BY_EVENT_TYPE["drone_resolvable"] == "notification_drone_reachability.html"
    # Default-on, but respects an explicit opt-out.
    assert nd.notification_type_enabled({}, "drone_resolvable") is True
    assert nd.notification_type_enabled({"types": {"drone_reachability": False}}, "drone_unresolvable") is False
    # The profile page exposes a checkbox for it.
    html = _Path(__file__).resolve().parents[1].joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    assert 'data-notify-type="drone_reachability"' in html


def test_reachability_poll_emits_notification_on_status_flip(monkeypatch):
    from overmind import main as overmind_main

    device = {
        "id": "dev-1", "device_id": "drone-x", "device_name": "Drone X",
        "swarm_id": "swarm-1", "api_port": 443,
        "network": {"public_ip": "1.2.3.4"},
        "public_reachability": {"resolvable": False},
    }
    monkeypatch.setattr(overmind_main.postgres_store, "list_all_approved_devices", lambda **kwargs: [device])
    monkeypatch.setattr(overmind_main.postgres_store, "update_device_reachability", lambda did, result: True)
    captured = {}

    def fake_insert(swarm_id, event_type, title, message, details=None, delivery_pending=True):
        captured.clear()
        captured.update({"swarm_id": swarm_id, "event_type": event_type, "message": message, "details": details})
        return "nid"

    monkeypatch.setattr(overmind_main.postgres_store, "insert_swarm_notification", fake_insert)

    # Not Resolvable -> Resolvable (the responder identifies as this Drone)
    monkeypatch.setattr(overmind_main.networking, "probe_drone_identity", lambda host, port, timeout: "drone-x")
    result = overmind_main.poll_public_reachability_once()
    assert result["changed"] == 1
    assert captured["event_type"] == "drone_resolvable"
    assert captured["swarm_id"] == "swarm-1"
    assert "Drone X" in captured["message"]

    # Resolvable -> Not Resolvable (nothing/no valid identity answers)
    device["public_reachability"]["resolvable"] = True
    monkeypatch.setattr(overmind_main.networking, "probe_drone_identity", lambda host, port, timeout: None)
    overmind_main.poll_public_reachability_once()
    assert captured["event_type"] == "drone_unresolvable"

    # No change -> no notification.
    device["public_reachability"]["resolvable"] = False
    captured.clear()
    overmind_main.poll_public_reachability_once()
    assert captured == {}


def test_reachability_identity_check_handles_shared_public_ip(monkeypatch):
    """Two Drones behind one public IP with 443 forwarded to only DroneA: a bare TCP
    probe marked both reachable. The identity check confirms who actually answers, so
    only the forwarded Drone is Resolvable and the other is correctly Not Resolvable."""
    from overmind import main as overmind_main

    drone_a = {
        "id": "a", "device_id": "drone-a", "device_name": "A", "swarm_id": "s", "api_port": 443,
        "network": {"public_ip": "9.9.9.9"}, "public_reachability": {"resolvable": False},
    }
    drone_b = {  # previously a false positive
        "id": "b", "device_id": "drone-b", "device_name": "B", "swarm_id": "s", "api_port": 443,
        "network": {"public_ip": "9.9.9.9"}, "public_reachability": {"resolvable": True},
    }
    monkeypatch.setattr(overmind_main.postgres_store, "list_all_approved_devices", lambda **k: [drone_a, drone_b])

    writes = {}

    def fake_update(internal_id, result):
        writes[internal_id] = result
        return True

    monkeypatch.setattr(overmind_main.postgres_store, "update_device_reachability", fake_update)
    monkeypatch.setattr(overmind_main.postgres_store, "insert_swarm_notification", lambda *a, **k: "nid")
    # The forwarded port lands on Drone A regardless of which Drone we believe we're probing.
    monkeypatch.setattr(overmind_main.networking, "probe_drone_identity", lambda host, port, timeout: "drone-a")

    overmind_main.poll_public_reachability_once()

    # Drone A's identity matches the responder -> Resolvable.
    assert writes["a"]["resolvable"] is True
    # Drone B: a *different* Drone answered -> Not Resolvable, with diagnostics.
    assert writes["b"]["resolvable"] is False
    assert writes["b"]["identity_mismatch"] is True
    assert writes["b"]["answered_by"] == "drone-a"


def test_device_game_count_vs_rom_file_count(client):
    user_id = db.create_user("games@example.com", auth_utils.hash_password("testpass123"), verified=True, username="games-at-example.com")
    db.create_device(user_id, "games-drone", "Games Drone", {"ip_address": "10.0.0.9"}, raw_token="t")
    token = client.post("/api/auth/login", json={"email": "games@example.com", "password": "testpass123"}).json()["access_token"]

    # snes: 2 gamelist games but 5 rom files (e.g. multi-track/multi-disc).
    db.add_roms("games-drone", "snes", [
        {"rom_name": "Game A", "file_path": "a.sfc", "metadata_source": "gamelist.xml"},
        {"rom_name": "Game B", "file_path": "b.sfc", "metadata_source": "gamelist.xml"},
        {"rom_name": "b-track2", "file_path": "b2.bin", "metadata_source": "filesystem"},
        {"rom_name": "b-track3", "file_path": "b3.bin", "metadata_source": "filesystem"},
        {"rom_name": "b-track4", "file_path": "b4.bin", "metadata_source": "filesystem"},
    ])
    # psx: no gamelist at all -> games falls back to the file count (2).
    db.add_roms("games-drone", "psx", [
        {"rom_name": "Disc1", "file_path": "d1.chd", "metadata_source": "filesystem"},
        {"rom_name": "Disc2", "file_path": "d2.chd", "metadata_source": "filesystem"},
    ])

    assert db.count_device_roms("games-drone") == 7
    # snes -> 2 gamelist games; psx -> 2 (fallback). Total games = 4.
    assert db.count_device_games("games-drone") == 4

    response = client.get("/api/devices/games-drone", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["rom_count"] == 7
    assert body["game_count"] == 4


def test_managed_config_registry_merge():
    from overmind.managed_configs import merge_managed_configs, MANAGED_CONFIG_REGISTRY

    payload = {
        "type": "emulator_configs",
        "configs": [
            {"relative_path": "batocera.conf", "content": "x",
             "versions": [{"content": "a"}, {"content": "b"}, {"content": "c"}]},
            {"relative_path": "some/unmanaged.cfg", "content": "y", "versions": [{"content": "y"}]},
        ],
    }
    merged = merge_managed_configs(payload)
    by_rel = {row["relative_path"]: row for row in merged["configs"]}
    assert merged["managed_total"] == len(MANAGED_CONFIG_REGISTRY)
    # Present, managed, with the right version count and friendly name.
    assert by_rel["batocera.conf"]["present"] is True
    assert by_rel["batocera.conf"]["version_count"] == 3
    assert by_rel["batocera.conf"]["name"] == "Batocera (batocera.conf)"
    # An expected config the drone never uploaded shows as absent.
    assert by_rel["dolphin-emu/Dolphin.ini"]["present"] is False
    assert by_rel["dolphin-emu/Dolphin.ini"]["version_count"] == 0
    # Extra (non-registry) uploads are still shown.
    assert by_rel["some/unmanaged.cfg"]["present"] is True

    # With no upload at all, the full registry still shows (all absent).
    empty = merge_managed_configs(None)
    assert len(empty["configs"]) == len(MANAGED_CONFIG_REGISTRY)
    assert empty["present_total"] == 0


def test_runtime_logs_stderr_uses_shared_buffer(monkeypatch):
    import logging
    from overmind import cache, main as overmind_main

    class _FakePipe:
        def __init__(self, store):
            self.store = store
        def rpush(self, key, value):
            self.store.setdefault(key, []).append(value)
            return self
        def ltrim(self, key, start, end):
            vals = self.store.get(key, [])
            self.store[key] = vals[start:] if end == -1 else vals[start:end + 1]
            return self
        def expire(self, key, ttl):
            return self
        def execute(self):
            return None

    class _FakeRedis:
        def __init__(self):
            self.store = {}
        def pipeline(self):
            return _FakePipe(self.store)
        def lrange(self, key, start, end):
            vals = self.store.get(key, [])
            return vals[start:] if end == -1 else vals[start:end + 1]

    fake = _FakeRedis()
    monkeypatch.setattr(cache, "_get_client", lambda: fake)
    overmind_main.install_stream_log_capture()

    logging.getLogger("overmind.test").error("SHARED-STDERR-MARKER")
    # The shared buffer holds it (cross-instance), and the snapshot reflects it.
    assert any("SHARED-STDERR-MARKER" in line for line in cache.read_log_tail("stderr"))
    assert "SHARED-STDERR-MARKER" in overmind_main.stream_log_snapshot()["stderr"]


def test_super_admin_can_recover_untrusted_pending_drone_connection(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"})
    admin_token = client.post(
        "/api/auth/login",
        json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"},
    ).json()["access_token"]
    client.post("/api/auth/register", json={"email": "regular@example.com", "username": "regular-recovery", "password": "testpass123"})
    regular_token = client.post(
        "/api/auth/login",
        json={"email": "regular@example.com", "username": "regular-recovery", "password": "testpass123"},
    ).json()["access_token"]
    regular_user = db.get_user_by_email("regular@example.com")
    swarm_id = db.default_swarm_id(regular_user["id"])
    old_drone_token = "old-token-from-stranded-drone"

    heartbeat = client.post(
        "/api/devices/stranded-drone/heartbeat",
        headers={"Authorization": f"Bearer {old_drone_token}"},
        json={
            "device_name": "Stranded Drone",
            "network": {"ipv4": ["10.42.0.5"]},
            "reachable_url": "https://10.42.0.5:8443",
        },
    )
    assert heartbeat.status_code == 401
    assert db.get_device_by_device_id("stranded-drone") is None

    regular_pending = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {regular_token}"})
    assert regular_pending.status_code == 200
    assert regular_pending.json()["connections"] == []

    overview = client.get("/api/admin/overview", headers={"Authorization": f"Bearer {admin_token}"})
    assert overview.status_code == 200
    payload = overview.json()
    pending = [row for row in payload["pending_connections"] if row["device_id"] == "stranded-drone"]
    assert len(pending) == 1
    assert pending[0]["device_name"] == "Stranded Drone"
    assert pending[0]["recovery_reason"] == "invalid_drone_token"
    assert pending[0]["batocera_info"]["reachable_url"] == "https://10.42.0.5:8443"
    assert "drone_token_hash" not in pending[0]

    denied_accept = client.post("/api/drone-connections/stranded-drone/accept", headers={"Authorization": f"Bearer {regular_token}"})
    assert denied_accept.status_code == 404

    assign = client.post(
        "/api/admin/drone-connections/stranded-drone/assign",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"swarm_id": swarm_id},
    )
    assert assign.status_code == 200
    assigned = assign.json()["device"]
    assert assigned["device_id"] == "stranded-drone"
    assert db.get_device_by_device_id("stranded-drone")["swarm_id"] == swarm_id

    recovered_heartbeat = client.post(
        "/api/devices/stranded-drone/heartbeat",
        headers={"Authorization": f"Bearer {old_drone_token}"},
        json={"device_name": "Stranded Drone", "network": {"ipv4": ["10.42.0.5"]}},
    )
    assert recovered_heartbeat.status_code == 200
    assert db.get_all_pending_drone_connections() == []


def test_super_admin_can_assign_onboarding_pending_drone_to_any_swarm(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"})
    admin_token = client.post(
        "/api/auth/login",
        json={"email": "mr_jerrodh@hotmail.com", "username": "mr-jerrodh-admin", "password": "testpass123"},
    ).json()["access_token"]
    admin_user = db.get_user_by_email("mr_jerrodh@hotmail.com")
    admin_swarm_id = db.default_swarm_id(admin_user["id"])

    client.post("/api/auth/register", json={"email": "remote-owner@example.com", "username": "remote-owner", "password": "testpass123"})
    owner_token = client.post(
        "/api/auth/login",
        json={"email": "remote-owner@example.com", "username": "remote-owner", "password": "testpass123"},
    ).json()["access_token"]
    token_response = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"label": "Lost cabinet onboarding"},
    )
    auth_token = token_response.json()["token"]["authorization_token"]

    register_response = client.post(
        "/api/devices/register",
        headers={"Authorization": f"Bearer {auth_token}"},
        json=_device_registration_payload(
            authorization_token=auth_token,
            device_id="lost-onboarding-drone",
            device_name="Lost Onboarding Drone",
            reachable_url="https://lost-onboarding-drone:8443",
            batocera_info={
                "model": "Test Model",
                "system": "Linux",
                "architecture": "x86_64",
                "cpu_model": "Test CPU",
                "cpu_cores": 4,
                "cpu_threads": 8,
                "cpu_max_frequency": "3.0 GHz",
                "memory_available": "8 GiB",
                "memory_total": "16 GiB",
                "ip_address": "lost-onboarding-drone",
                "network": {"ipv4": ["10.42.0.9"]},
            },
        ),
    )
    assert register_response.status_code == 200
    assert register_response.json()["status"] == "pending"

    overview = client.get("/api/admin/overview", headers={"Authorization": f"Bearer {admin_token}"})
    assert overview.status_code == 200
    pending = [row for row in overview.json()["pending_connections"] if row["device_id"] == "lost-onboarding-drone"]
    assert len(pending) == 1
    assert "drone_token_hash" not in pending[0]

    assign = client.post(
        "/api/admin/drone-connections/lost-onboarding-drone/assign",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"swarm_id": admin_swarm_id},
    )
    assert assign.status_code == 200
    device = db.get_device_by_device_id("lost-onboarding-drone")
    assert device["user_id"] == admin_user["id"]
    assert device["swarm_id"] == admin_swarm_id
    assert device.get("authorization_token_id") is None

    heartbeat = client.post(
        "/api/devices/lost-onboarding-drone/heartbeat",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"device_name": "Lost Onboarding Drone", "network": {"ipv4": ["10.42.0.9"]}},
    )
    assert heartbeat.status_code == 200
    assert db.get_all_pending_drone_connections() == []


def test_super_admin_delete_user_removes_owned_swarms_and_drones(client):
    client.post("/api/auth/register", json={"email": "mr_jerrodh@hotmail.com", "username": "mr_jerrodh-at-hotmail.com", "password": "testpass123"})
    admin_token = client.post(
        "/api/auth/login",
        json={"email": "mr_jerrodh@hotmail.com", "username": "mr_jerrodh-at-hotmail.com", "password": "testpass123"},
    ).json()["access_token"]
    client.post("/api/auth/register", json={"email": "remove-me@example.com", "username": "remove-me-at-example.com", "password": "testpass123"})
    target = db.get_user_by_email("remove-me@example.com")
    swarm_id = db.default_swarm_id(target["id"])
    db.create_device(target["id"], "remove-me-drone", "Remove Me Drone", {"ip_address": "10.0.0.3"}, raw_token="drone-token")

    response = client.delete(f"/api/admin/users/{target['id']}", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert db.get_user_by_email("remove-me@example.com") is None
    assert swarm_id not in db.swarms
    assert db.get_device_by_device_id("remove-me-drone") is None


def test_email_registration_requires_verification(client, monkeypatch):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    response = client.post(
        "/api/auth/register",
        json={"email": "verify@example.com", "username": "verify-at-example.com", "password": "testpass123"},
    )
    assert response.status_code == 200
    assert db.get_user_by_email("verify@example.com")["is_active"] is False
    assert client.post("/api/auth/login", json={"email": "verify@example.com", "username": "verify-at-example.com", "password": "testpass123"}).status_code == 403

    code = db.email_verifications[db.get_user_by_email("verify@example.com")["id"]]["code"]
    verify = client.post("/api/auth/verify-email", json={"email": "verify@example.com", "code": code})
    assert verify.status_code == 200
    assert client.post("/api/auth/login", json={"email": "verify@example.com", "username": "verify-at-example.com", "password": "testpass123"}).status_code == 200


def test_registration_verification_code_is_not_logged_with_runtime_flags(client, monkeypatch, capsys):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    response = client.post("/api/auth/register", json={"email": "fake-code@example.com", "username": "fake-code-at-example.com", "password": "testpass123"})
    assert response.status_code == 200
    captured = capsys.readouterr()
    assert "registration verification code for fake-code@example.com" not in captured.out


def test_registration_verification_code_is_not_logged(client, monkeypatch, capsys):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    response = client.post("/api/auth/register", json={"email": "real-code@example.com", "username": "real-code-at-example.com", "password": "testpass123"})
    assert response.status_code == 200
    captured = capsys.readouterr()
    assert "registration verification code for real-code@example.com" not in captured.out


def test_expired_verification_code_fails(client, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    client.post("/api/auth/register", json={"email": "expired@example.com", "username": "expired-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("expired@example.com")
    db.email_verifications[user["id"]]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
    code = db.email_verifications[user["id"]]["code"]
    response = client.post("/api/auth/verify-email", json={"email": "expired@example.com", "code": code})
    assert response.status_code == 400


def test_resend_verification_replaces_old_code(client, monkeypatch):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    sent = []
    monkeypatch.setattr("overmind.main.send_verification_email", lambda user, code, token: sent.append((user["email"], code, token)))

    client.post("/api/auth/register", json={"email": "resend@example.com", "username": "resend-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("resend@example.com")
    old_code = db.email_verifications[user["id"]]["code"]

    response = client.post("/api/auth/resend-verification", json={"email": "resend@example.com"})
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    new_code = db.email_verifications[user["id"]]["code"]
    assert new_code != old_code
    assert sent[-1][0] == "resend@example.com"
    assert sent[-1][1] == new_code

    old_verify = client.post("/api/auth/verify-email", json={"email": "resend@example.com", "code": old_code})
    assert old_verify.status_code == 400

    new_verify = client.post("/api/auth/verify-email", json={"email": "resend@example.com", "code": new_code})
    assert new_verify.status_code == 200


def test_resend_verification_noops_for_verified_user(client):
    client.post("/api/auth/register", json={"email": "verified@example.com", "username": "verified-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("verified@example.com")
    assert user["email_verified"] is True

    response = client.post("/api/auth/resend-verification", json={"email": "verified@example.com"})
    assert response.status_code == 200
    assert user["id"] not in db.email_verifications


def test_forgot_password_token_resets_password(client, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    client.post("/api/auth/register", json={"email": "reset@example.com", "username": "reset-at-example.com", "password": "oldpass123"})
    user = db.get_user_by_email("reset@example.com")
    db.set_user_verified(user["id"])

    response = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    assert response.status_code == 200
    raw_token = "manual-reset-token"
    db.create_password_reset(user["id"], auth_utils.hash_password(f"{TOKEN_HASH_SECRET}:{raw_token}"), datetime.utcnow() + timedelta(minutes=30))
    reset = client.post("/api/auth/reset-password", json={"token": raw_token, "password": "newpass123"})
    assert reset.status_code == 200
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "username": "reset-at-example.com", "password": "newpass123"}).status_code == 200


def test_swarm_roles_gate_invites_and_mutations(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    swarm_id = client.get("/api/swarms", headers={"Authorization": f"Bearer {owner_token}"}).json()["swarms"][0]["id"]

    invite = client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "viewer@example.com", "role": "overlord"},
    )
    assert invite.status_code == 200
    assert invite.json()["invitation"]["role"] == "overseer"

    client.post("/api/auth/register", json={"email": "viewer@example.com", "username": "viewer-at-example.com", "password": "testpass123"})
    viewer_token = client.post("/api/auth/login", json={"email": "viewer@example.com", "username": "viewer-at-example.com", "password": "testpass123"}).json()["access_token"]
    denied = client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"email": "other@example.com", "role": "overseer"},
    )
    assert denied.status_code == 403

    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "swarm-drone", "Swarm Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token", swarm_id=swarm_id)
    assert client.get("/api/devices?swarm_id=" + swarm_id, headers={"Authorization": f"Bearer {viewer_token}"}).status_code == 200
    mutate = client.post("/api/devices/swarm-drone/actions", headers={"Authorization": f"Bearer {viewer_token}"}, json={"action": "restart"})
    assert mutate.status_code == 403
    access = client.get(f"/api/swarms/{swarm_id}/access", headers={"Authorization": f"Bearer {viewer_token}"})
    assert access.status_code == 200
    viewer_member = next(row for row in access.json()["access"]["members"] if row["email"] == "viewer@example.com")
    assert viewer_member["role"] == "overseer"


def test_overlord_can_remove_overseer_from_swarm(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    owner = db.get_user_by_email("owner@example.com")
    swarm_id = db.default_swarm_id(owner["id"])

    client.post("/api/auth/register", json={"email": "overseer@example.com", "username": "overseer-at-example.com", "password": "testpass123"})
    overseer = db.get_user_by_email("overseer@example.com")
    db.swarm_memberships.setdefault(swarm_id, {})[overseer["id"]] = {"user_id": overseer["id"], "role": "overseer"}
    overseer_token = client.post("/api/auth/login", json={"email": "overseer@example.com", "username": "overseer-at-example.com", "password": "testpass123"}).json()["access_token"]

    response = client.delete(
        f"/api/swarms/{swarm_id}/members/{overseer['id']}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "removed"
    assert db.get_swarm_member(swarm_id, overseer["id"]) is None
    assert client.get(f"/api/swarms/{swarm_id}/access", headers={"Authorization": f"Bearer {overseer_token}"}).status_code == 403
    notifications = client.get("/api/notifications", headers={"Authorization": f"Bearer {owner_token}"}).json()["notifications"]
    assert any(row["event_type"] == "swarm_member_removed" and "overseer@example.com" in row["message"] for row in notifications)


def test_overlord_can_resend_pending_overseer_invite_with_rotated_link(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "overmind.main.send_invitation_email",
        lambda email, swarm, role, token: sent.append((email, swarm.get("id"), role, token)),
    )
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    owner = db.get_user_by_email("owner@example.com")
    swarm_id = db.default_swarm_id(owner["id"])

    created = client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "pending-overseer@example.com"},
    )
    invitation_id = created.json()["invitation"]["id"]
    original_token = sent[-1][3]

    response = client.post(
        f"/api/swarms/{swarm_id}/invitations/{invitation_id}/resend",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert response.json()["invitation"]["id"] == invitation_id
    resent_token = sent[-1][3]
    assert resent_token != original_token
    assert client.get(f"/api/invitations/status?token={original_token}").status_code == 400
    assert client.get(f"/api/invitations/status?token={resent_token}").status_code == 200

    client.post("/api/auth/register", json={"email": "observer@example.com", "username": "observer-at-example.com", "password": "testpass123"})
    observer = db.get_user_by_email("observer@example.com")
    db.swarm_memberships.setdefault(swarm_id, {})[observer["id"]] = {"user_id": observer["id"], "role": "overseer"}
    observer_token = client.post("/api/auth/login", json={"email": "observer@example.com", "username": "observer-at-example.com", "password": "testpass123"}).json()["access_token"]
    denied = client.post(
        f"/api/swarms/{swarm_id}/invitations/{invitation_id}/resend",
        headers={"Authorization": f"Bearer {observer_token}"},
    )
    assert denied.status_code == 403


def test_overlord_can_remove_pending_overseer_invite_and_revoke_link(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "overmind.main.send_invitation_email",
        lambda email, swarm, role, token: sent.append(token),
    )
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    swarm_id = db.default_swarm_id(db.get_user_by_email("owner@example.com")["id"])

    created = client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "pending-remove@example.com"},
    )
    invitation_id = created.json()["invitation"]["id"]
    invite_token = sent[-1]
    assert client.get(f"/api/invitations/status?token={invite_token}").status_code == 200

    removed = client.delete(
        f"/api/swarms/{swarm_id}/invitations/{invitation_id}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "removed"
    assert invitation_id not in db.invitations
    assert client.get(f"/api/invitations/status?token={invite_token}").status_code == 400
    access = client.get(f"/api/swarms/{swarm_id}/access", headers={"Authorization": f"Bearer {owner_token}"})
    assert not any(row["id"] == invitation_id for row in access.json()["access"]["invitations"])

    client.post("/api/auth/register", json={"email": "observer@example.com", "username": "observer-at-example.com", "password": "testpass123"})
    observer = db.get_user_by_email("observer@example.com")
    db.swarm_memberships.setdefault(swarm_id, {})[observer["id"]] = {"user_id": observer["id"], "role": "overseer"}
    observer_token = client.post("/api/auth/login", json={"email": "observer@example.com", "password": "testpass123"}).json()["access_token"]
    created_again = client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "pending-denied@example.com"},
    )
    denied = client.delete(
        f"/api/swarms/{swarm_id}/invitations/{created_again.json()['invitation']['id']}",
        headers={"Authorization": f"Bearer {observer_token}"},
    )
    assert denied.status_code == 403


def test_swarms_marks_users_home_swarm(client):
    client.post("/api/auth/register", json={"email": "owner-home@example.com", "username": "owner-home-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "owner-home@example.com", "username": "owner-home-at-example.com", "password": "testpass123"}).json()["access_token"]

    response = client.get("/api/swarms", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    swarms = response.json()["swarms"]
    assert swarms
    assert sum(1 for swarm in swarms if swarm.get("current")) == 1
    assert swarms[0]["current"] is True


def test_invited_overseer_home_swarm_is_their_owned_swarm(client):
    client.post("/api/auth/register", json={"email": "owner-home@example.com", "username": "owner-home-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner-home@example.com", "username": "owner-home-at-example.com", "password": "testpass123"}).json()["access_token"]
    owner_swarm_id = db.default_swarm_id(db.get_user_by_email("owner-home@example.com")["id"])

    invite = client.post(
        f"/api/swarms/{owner_swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "overseer-home@example.com"},
    )
    assert invite.status_code == 200

    client.post("/api/auth/register", json={"email": "overseer-home@example.com", "username": "overseer-home-at-example.com", "password": "testpass123"})
    overseer_token = client.post("/api/auth/login", json={"email": "overseer-home@example.com", "username": "overseer-home-at-example.com", "password": "testpass123"}).json()["access_token"]

    response = client.get("/api/swarms", headers={"Authorization": f"Bearer {overseer_token}"})

    assert response.status_code == 200
    swarms = response.json()["swarms"]
    current_swarms = [swarm for swarm in swarms if swarm.get("current")]
    assert len(current_swarms) == 1
    assert current_swarms[0]["owner_id"] == db.get_user_by_email("overseer-home@example.com")["id"]
    assert current_swarms[0]["id"] != owner_swarm_id


def test_drone_ownership_claim_success_and_owner_actions(client, capsys):
    client.post("/api/auth/register", json={"email": "claim-owner@example.com", "username": "claim-owner-at-example.com", "password": "claimpass123"})
    token = client.post("/api/auth/login", json={"email": "claim-owner@example.com", "username": "claim-owner-at-example.com", "password": "claimpass123"}).json()["access_token"]

    response = client.post(
        "/api/drones/claim-ownership",
        json={
            "device_id": "claim-drone",
            "device_name": "Claimed Drone",
            "email": "claim-owner@example.com",
            "username": "claim-owner-at-example.com",
            "password": "claimpass123",
            "batocera_info": {"ip_address": "10.0.0.7"},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "claimed"
    assert response.json()["drone_token"] is None
    assert "claimpass123" not in capsys.readouterr().out

    devices = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices.status_code == 200
    assert devices.json()["devices"][0]["device_id"] == "claim-drone"
    action = client.post("/api/devices/claim-drone/actions", headers={"Authorization": f"Bearer {token}"}, json={"action": "restart"})
    assert action.status_code == 200


def test_drone_ownership_claim_invalid_credentials_and_cross_swarm_admin(client, capsys):
    client.post("/api/auth/register", json={"email": "first-owner@example.com", "username": "first-owner-at-example.com", "password": "claimpass123"})
    client.post("/api/auth/register", json={"email": "second-owner@example.com", "username": "second-owner-at-example.com", "password": "otherpass123"})
    first_token = client.post(
        "/api/auth/login",
        json={"email": "first-owner@example.com", "username": "first-owner-at-example.com", "password": "claimpass123"},
    ).json()["access_token"]
    second_token = client.post(
        "/api/auth/login",
        json={"email": "second-owner@example.com", "username": "second-owner-at-example.com", "password": "otherpass123"},
    ).json()["access_token"]

    bad = client.post(
        "/api/drones/claim-ownership",
        json={"device_id": "owned-drone", "email": "first-owner@example.com", "username": "first-owner-at-example.com", "password": "wrongpass123"},
    )
    assert bad.status_code == 401
    captured = capsys.readouterr()
    assert "wrongpass123" not in captured.out
    assert "wrongpass123" not in captured.err

    claimed = client.post(
        "/api/drones/claim-ownership",
        json={"device_id": "owned-drone", "email": "first-owner@example.com", "username": "first-owner-at-example.com", "password": "claimpass123"},
    )
    assert claimed.status_code == 200

    second_claim = client.post(
        "/api/drones/claim-ownership",
        json={"device_id": "owned-drone", "email": "second-owner@example.com", "username": "second-owner-at-example.com", "password": "otherpass123"},
    )
    assert second_claim.status_code == 200
    assert second_claim.json()["drone_token"] is None

    first_devices = client.get("/api/devices", headers={"Authorization": f"Bearer {first_token}"})
    second_devices = client.get("/api/devices", headers={"Authorization": f"Bearer {second_token}"})
    assert {row["device_id"] for row in first_devices.json()["devices"]} == {"owned-drone"}
    assert {row["device_id"] for row in second_devices.json()["devices"]} == {"owned-drone"}

    action = client.post(
        "/api/devices/owned-drone/actions",
        headers={"Authorization": f"Bearer {second_token}"},
        json={"action": "restart"},
    )
    assert action.status_code == 200


def test_hive_lists_public_swarms_without_private_owner_data(client):
    client.post("/api/auth/register", json={"email": "hive-owner@example.com", "username": "hive-owner-at-example.com", "password": "testpass123", "full_name": "Hive Owner"})
    owner_token = client.post("/api/auth/login", json={"email": "hive-owner@example.com", "username": "hive-owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"username": "hive-owner", "avatar_data_url": "data:image/png;base64,AAA"},
    )
    owner = db.get_user_by_email("hive-owner@example.com")
    swarm_id = db.default_swarm_id(owner["id"])
    db.create_device(owner["id"], "hive-drone", "Hive Drone", {"ip_address": "10.0.0.9"}, raw_token="drone-token", swarm_id=swarm_id)

    client.post("/api/auth/register", json={"email": "visitor@example.com", "username": "visitor-at-example.com", "password": "testpass123"})
    visitor_token = client.post("/api/auth/login", json={"email": "visitor@example.com", "username": "visitor-at-example.com", "password": "testpass123"}).json()["access_token"]
    response = client.get("/api/hive", headers={"Authorization": f"Bearer {visitor_token}"})
    assert response.status_code == 200
    raw = response.text
    assert "hive-owner@example.com" not in raw
    assert "visitor@example.com" not in raw
    row = next(item for item in response.json()["hive"] if item["swarm_id"] == swarm_id)
    assert row["owner_username"] == "hive-owner"
    assert row["owner_avatar_data_url"].startswith("data:image/png;base64")
    assert row["drone_count"] == 1
    assert row["can_view"] is False


def test_hive_overseer_can_view_drone_but_not_mutate(client):
    client.post("/api/auth/register", json={"email": "owner-hive@example.com", "username": "owner-hive-at-example.com", "password": "testpass123"})
    client.post("/api/auth/register", json={"email": "overseer-hive@example.com", "username": "overseer-hive-at-example.com", "password": "testpass123"})
    owner = db.get_user_by_email("owner-hive@example.com")
    overseer = db.get_user_by_email("overseer-hive@example.com")
    swarm_id = db.default_swarm_id(owner["id"])
    db.swarm_memberships.setdefault(swarm_id, {})[overseer["id"]] = {"user_id": overseer["id"], "role": "overseer"}
    db.create_device(owner["id"], "overseer-drone", "Overseer Drone", {"ip_address": "10.0.0.10"}, raw_token="drone-token", swarm_id=swarm_id)
    overseer_token = client.post("/api/auth/login", json={"email": "overseer-hive@example.com", "username": "overseer-hive-at-example.com", "password": "testpass123"}).json()["access_token"]

    hive = client.get("/api/hive", headers={"Authorization": f"Bearer {overseer_token}"})
    assert next(item for item in hive.json()["hive"] if item["swarm_id"] == swarm_id)["can_view"] is True
    devices = client.get(f"/api/devices?swarm_id={swarm_id}", headers={"Authorization": f"Bearer {overseer_token}"})
    assert devices.status_code == 200
    assert devices.json()["devices"][0]["device_id"] == "overseer-drone"
    detail = client.get("/api/devices/overseer-drone", headers={"Authorization": f"Bearer {overseer_token}"})
    assert detail.status_code == 200
    mutate = client.post("/api/devices/overseer-drone/actions", headers={"Authorization": f"Bearer {overseer_token}"}, json={"action": "restart"})
    assert mutate.status_code == 403


def test_unauthorized_user_cannot_view_private_drone_details(client):
    client.post("/api/auth/register", json={"email": "private-owner@example.com", "username": "private-owner-at-example.com", "password": "testpass123"})
    client.post("/api/auth/register", json={"email": "outsider@example.com", "username": "outsider-at-example.com", "password": "testpass123"})
    owner = db.get_user_by_email("private-owner@example.com")
    db.create_device(owner["id"], "private-drone", "Private Drone", {"ip_address": "10.0.0.11"}, raw_token="drone-token")
    outsider_token = client.post("/api/auth/login", json={"email": "outsider@example.com", "username": "outsider-at-example.com", "password": "testpass123"}).json()["access_token"]

    response = client.get("/api/devices/private-drone", headers={"Authorization": f"Bearer {outsider_token}"})
    assert response.status_code == 404


def test_download_state_and_cancel_rbac(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    swarm_id = client.get("/api/swarms", headers={"Authorization": f"Bearer {owner_token}"}).json()["swarms"][0]["id"]
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "target-a", "Target A", {"ip_address": "10.0.0.2"}, raw_token="drone-token", swarm_id=swarm_id)

    heartbeat = client.post(
        "/api/devices/target-a/heartbeat",
        headers={"Authorization": "Bearer drone-token"},
        json={
            "device_name": "Target A",
            "downloads": {
                "target_drone_id": "target-a",
                "downloads": [],
                "active": [{
                    "job_id": "job-1",
                    "target_drone_id": "target-a",
                    "source_drone_id": "source-a",
                    "file_path": "snes/Game.zip",
                    "file_size": 100,
                    "downloaded_bytes": 25,
                    "percentage": 25,
                    "status": "downloading",
                }],
                "queued": [{
                    "job_id": "job-2",
                    "target_drone_id": "target-a",
                    "source_drone_id": "source-b",
                    "file_path": "snes/Next.zip",
                    "queue_position": 1,
                    "status": "queued",
                }],
            },
        },
    )
    assert heartbeat.status_code == 200

    downloads = client.get("/api/downloads", headers={"Authorization": f"Bearer {owner_token}"})
    assert downloads.status_code == 200
    target = downloads.json()["targets"][0]
    assert target["concurrency"]["scope"] == "target_drone"
    assert target["active"][0]["source_drone_id"] == "source-a"
    assert target["queued"][0]["queue_position"] == 1

    live_update = client.post(
        "/api/devices/target-a/downloads",
        headers={"Authorization": "Bearer drone-token"},
        json={
            "target_drone_id": "target-a",
            "active": [{
                "job_id": "job-1",
                "asset_type": "artwork",
                "target_drone_id": "target-a",
                "source_drone_id": "source-a",
                "file_path": "Game.zip:image",
                "rom_path": "Game.zip",
                "artwork_type": "image",
                "file_size": 100,
                "downloaded_bytes": 75,
                "percentage": 75,
                "status": "downloading",
            }],
            "queued": [],
        },
    )
    assert live_update.status_code == 200
    assert live_update.json() == {"status": "accepted"}
    downloads = client.get("/api/downloads", headers={"Authorization": f"Bearer {owner_token}"})
    assert downloads.json()["targets"][0]["active"][0]["downloaded_bytes"] == 75
    assert downloads.json()["targets"][0]["active"][0]["asset_type"] == "artwork"
    assert downloads.json()["targets"][0]["active"][0]["artwork_type"] == "image"

    cancel = client.post(
        "/api/devices/target-a/downloads/job-2/cancel",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["action"]["action"] == "cancel_download"
    assert cancel.json()["action"]["payload"]["job_id"] == "job-2"

    client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "viewer@example.com", "role": "overseer"},
    )
    client.post("/api/auth/register", json={"email": "viewer@example.com", "username": "viewer-at-example.com", "password": "testpass123"})
    viewer_token = client.post("/api/auth/login", json={"email": "viewer@example.com", "username": "viewer-at-example.com", "password": "testpass123"}).json()["access_token"]

    viewer_downloads = client.get("/api/downloads", headers={"Authorization": f"Bearer {viewer_token}"})
    assert viewer_downloads.status_code == 200
    denied = client.post(
        "/api/devices/target-a/downloads/job-1/cancel",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert denied.status_code == 403


def _device_registration_payload(**overrides):
    payload = {
        "email": "test@example.com",
        "device_id": "device-123",
        "device_name": "Test Device",
        "batocera_info": {
            "model": "Test Model",
            "system": "Linux",
            "architecture": "x86_64",
            "cpu_model": "Test CPU",
            "cpu_cores": 4,
            "cpu_threads": 8,
            "cpu_max_frequency": "3.0 GHz",
            "memory_available": "8 GiB",
            "memory_total": "16 GiB",
            "ip_address": "192.168.1.1",
        },
    }
    payload.update(overrides)
    return payload


def test_register_device_requires_authorization_token(client):
    """Unauthorized Drone registration is rejected without storing a pending connection."""
    client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "username": "test-at-example.com",
            "password": "testpass123",
        }
    )

    response = client.post(
        "/api/devices/register",
        json=_device_registration_payload()
    )
    assert response.status_code == 401
    assert db.pending_drone_connections == {}
    assert db.devices == {}


def test_register_device_with_valid_token_requires_approval_then_alive_works(client):
    """A valid integration token creates a pending Drone until the Overlord approves it."""
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    )
    token = login_response.json()["access_token"]
    token_response = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "Test token"},
    )
    auth_token = token_response.json()["token"]["authorization_token"]

    register_response = client.post(
        "/api/devices/register",
        headers={"Authorization": f"Bearer {auth_token}"},
        json=_device_registration_payload(
            authorization_token=auth_token,
            device_id="drone-123",
            device_name="Test Drone",
            api_port=443,
            scheme="https",
            reachable_url="https://bff-drone-a:443",
            batocera_info={
                "model": "Test Model",
                "system": "Linux",
                "architecture": "x86_64",
                "cpu_model": "Test CPU",
                "cpu_cores": 4,
                "cpu_threads": 8,
                "cpu_max_frequency": "3.0 GHz",
                "memory_available": "8 GiB",
                "memory_total": "16 GiB",
                "ip_address": "bff-drone-a",
                "certificate": {
                    "status": "loaded",
                    "fingerprint": "abc123",
                    "public_certificate": "-----BEGIN CERTIFICATE-----\\nTEST\\n-----END CERTIFICATE-----\\n",
                    "private_key": "must-not-store",
                },
            },
        ),
    )
    assert register_response.status_code == 200
    assert register_response.json()["status"] == "pending"

    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert pending_response.status_code == 200
    assert pending_response.json()["connections"][0]["device_id"] == "drone-123"

    accept_response = client.post("/api/drone-connections/drone-123/accept", headers={"Authorization": f"Bearer {token}"})
    assert accept_response.status_code == 200
    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert pending_response.status_code == 200
    assert pending_response.json()["connections"] == []

    claim_response = client.post(
        "/api/devices/register",
        headers={"Authorization": f"Bearer {auth_token}"},
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-123", device_name="Test Drone"),
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["status"] == "approved"
    drone_token = claim_response.json()["drone_token"]

    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices_response.status_code == 200
    device = devices_response.json()["devices"][0]
    assert device["device_id"] == "drone-123"
    assert device["reachable_url"] == "https://bff-drone-a:443"
    assert device["certificate"]["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert device["certificate"]["fingerprint"] == "abc123"
    assert "private_key" not in device["certificate"]

    heartbeat_response = client.post(
        "/api/devices/drone-123/heartbeat",
        headers={"Authorization": f"Bearer {drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}, "reachable_url": "https://bff-drone-a:443"},
    )
    assert heartbeat_response.status_code == 200

    cert_response = client.get(
        "/api/devices/drone-123/peer-certificate/drone-123",
        headers={"Authorization": f"Bearer {drone_token}"},
    )
    assert cert_response.status_code == 200
    assert cert_response.json()["certificate_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert cert_response.json()["metadata"]["fingerprint"] == "abc123"
    assert "private_key" not in cert_response.json()["metadata"]

    download_probe = client.get(
        "/api/devices/drone-123/speed/download?bytes=4096",
        headers={"Authorization": f"Bearer {drone_token}"},
    )
    assert download_probe.status_code == 200
    assert len(download_probe.content) == 4096

    upload_probe = client.post(
        "/api/devices/drone-123/speed/upload",
        headers={"Authorization": f"Bearer {drone_token}"},
        content=b"x" * 4096,
    )
    assert upload_probe.status_code == 200
    assert upload_probe.json()["bytes_received"] == 4096


def test_register_device_token_lookup_ignores_stale_drone_email_hint(client):
    """A copied onboarding token remains valid even if Drone has an old email saved."""
    client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"},
    )
    user_token = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    auth_token = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "Cabinet token"},
    ).json()["token"]["authorization_token"]

    register_response = client.post(
        "/api/devices/register",
        json=_device_registration_payload(
            email="stale-drone-config@example.com",
            authorization_token=auth_token,
            device_id="stale-email-drone",
            device_name="Stale Email Drone",
        ),
    )

    assert register_response.status_code == 200
    assert register_response.json()["status"] == "pending"
    assert db.pending_drone_connections["stale-email-drone"]["user_id"] == db.get_user_by_email("owner@example.com")["id"]


def test_register_device_refreshes_persistent_state_before_token_lookup(client, monkeypatch):
    calls = {"count": 0}

    def count_refresh():
        calls["count"] += 1

    monkeypatch.setattr(db, "refresh_persistent_state", count_refresh)
    response = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token="not-valid", device_id="refresh-before-register"),
    )

    assert response.status_code == 401
    assert calls["count"] == 1


def test_device_exists_uses_relational_store_with_postgres_url(client, monkeypatch):
    relational_device = {
        "id": "device-internal",
        "device_id": "relational-drone",
        "device_name": "Relational Drone",
        "user_id": "user-1",
        "approval_status": "approved",
    }
    monkeypatch.setattr(db_module.postgres_store, "url", "postgresql://example/overmind")
    monkeypatch.setattr(db_module.postgres_store, "available", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "get_device_by_device_id", lambda device_id: relational_device)

    assert db.device_exists("user-1", "relational-drone") is True


def test_reapproving_same_drone_updates_existing_device_instead_of_duplicating(client):
    """Repeated approval with a new authorization token keeps one visible Drone record."""
    client.post("/api/auth/register", json={"email": "dedupe@example.com", "username": "dedupe-at-example.com", "password": "testpass123"})
    login_response = client.post("/api/auth/login", json={"email": "dedupe@example.com", "username": "dedupe-at-example.com", "password": "testpass123"})
    token = login_response.json()["access_token"]

    first_token_response = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "First token"},
    )
    first_auth_token = first_token_response.json()["token"]["authorization_token"]
    client.post(
        "/api/devices/register",
        headers={"Authorization": f"Bearer {first_auth_token}"},
        json=_device_registration_payload(email="dedupe@example.com", authorization_token=first_auth_token, device_id="same-drone", device_name="Pedestal"),
    )
    assert client.post("/api/drone-connections/same-drone/accept", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    second_token_response = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "Second token"},
    )
    second_auth_token = second_token_response.json()["token"]["authorization_token"]
    reconnect_response = client.post(
        "/api/devices/register",
        headers={"Authorization": f"Bearer {second_auth_token}"},
        json=_device_registration_payload(email="dedupe@example.com", authorization_token=second_auth_token, device_id="same-drone", device_name="Pedestal Updated"),
    )
    assert reconnect_response.status_code == 200
    assert reconnect_response.json()["status"] == "approved"

    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices_response.status_code == 200
    devices = [device for device in devices_response.json()["devices"] if device["device_id"] == "same-drone"]
    assert len(devices) == 1
    assert devices[0]["device_name"] == "Pedestal Updated"
    assert len([device for device in db.devices.values() if device["device_id"] == "same-drone"]) == 1


def test_existing_duplicate_drone_records_are_collapsed(client):
    """Persisted duplicate records for one physical Drone are collapsed on read."""
    client.post("/api/auth/register", json={"email": "collapse@example.com", "username": "collapse-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("collapse@example.com")
    internal_id = db.create_device(user["id"], "duplicate-drone", "Original", {"ip_address": "10.0.0.2"}, raw_token="token")
    db.devices["manual-duplicate"] = {
        **db.devices[internal_id],
        "id": "manual-duplicate",
        "device_name": "Manual Duplicate",
    }
    db.user_devices[user["id"]].append("manual-duplicate")

    login_response = client.post("/api/auth/login", json={"email": "collapse@example.com", "username": "collapse-at-example.com", "password": "testpass123"})
    token = login_response.json()["access_token"]
    response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    devices = [device for device in response.json()["devices"] if device["device_id"] == "duplicate-drone"]
    assert len(devices) == 1
    assert len([device for device in db.devices.values() if device["device_id"] == "duplicate-drone"]) == 1


def test_register_device_resolves_owner_from_token_without_email(client):
    """Drone registration no longer needs the Overmind email when the token is valid."""
    client.post(
        "/api/auth/register",
        json={"email": "token-owner@example.com", "username": "token-owner-at-example.com", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "token-owner@example.com", "username": "token-owner-at-example.com", "password": "testpass123"},
    )
    token = login_response.json()["access_token"]
    token_response = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "Token-only Drone"},
    )
    auth_token = token_response.json()["token"]["authorization_token"]
    payload = _device_registration_payload(
        authorization_token=auth_token,
        device_id="token-only-drone",
        device_name="Token Only Drone",
    )
    payload.pop("email", None)

    register_response = client.post(
        "/api/devices/register",
        headers={"Authorization": f"Bearer {auth_token}"},
        json=payload,
    )

    assert register_response.status_code == 200
    assert register_response.json()["status"] == "pending"
    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert any(conn["device_id"] == "token-only-drone" for conn in pending_response.json()["connections"])


def test_integration_token_onboarding_and_approved_token_claim(client):
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    token_response = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {token}"},
        json={"label": "Test token"},
    )
    assert token_response.status_code == 200
    auth_token = token_response.json()["token"]["authorization_token"]

    register_response = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="token-drone", device_name="Token Drone"),
    )
    assert register_response.status_code == 200
    assert register_response.json()["status"] == "pending"

    accept_response = client.post("/api/drone-connections/token-drone/accept", headers={"Authorization": f"Bearer {token}"})
    assert accept_response.status_code == 200

    claim_response = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="token-drone", device_name="Token Drone"),
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["status"] == "approved"
    assert claim_response.json()["drone_token"]


def test_integration_token_cannot_register_different_drone(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    auth_token = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "single use"},
    ).json()["token"]["authorization_token"]

    first = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    )
    assert first.status_code == 200

    second = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-b"),
    )
    assert second.status_code == 401
    assert db.get_device_by_device_id("drone-b") is None


def test_integration_token_cannot_register_same_id_with_different_certificate(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    auth_token = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "cert-bound"},
    ).json()["token"]["authorization_token"]

    first = client.post(
        "/api/devices/register",
        json=_device_registration_payload(
            authorization_token=auth_token,
            device_id="drone-a",
            batocera_info={
                **_device_registration_payload()["batocera_info"],
                "certificate": {"fingerprint": "cert-a", "public_certificate": "-----BEGIN CERTIFICATE-----\\nA\\n-----END CERTIFICATE-----\\n"},
            },
        ),
    )
    assert first.status_code == 200

    second = client.post(
        "/api/devices/register",
        json=_device_registration_payload(
            authorization_token=auth_token,
            device_id="drone-a",
            batocera_info={
                **_device_registration_payload()["batocera_info"],
                "certificate": {"fingerprint": "cert-b", "public_certificate": "-----BEGIN CERTIFICATE-----\\nB\\n-----END CERTIFICATE-----\\n"},
            },
        ),
    )
    assert second.status_code == 401


def test_approval_preserves_bound_token_and_heartbeat_succeeds(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    auth_token = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "same drone"},
    ).json()["token"]["authorization_token"]

    first = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    )
    assert first.json()["status"] == "pending"
    accept = client.post("/api/drone-connections/drone-a/accept", headers={"Authorization": f"Bearer {user_token}"})
    assert accept.status_code == 200
    assert accept.json()["drone_token"] is None

    immediate_heartbeat = client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    )
    assert immediate_heartbeat.status_code == 200

    first_claim = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    )
    old_drone_token = first_claim.json()["drone_token"]
    second = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    )
    assert second.status_code == 200
    new_drone_token = second.json()["drone_token"]
    assert new_drone_token == old_drone_token == auth_token

    current = client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": f"Bearer {new_drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    )
    assert current.status_code == 200


def test_approval_updates_existing_removed_drone_to_new_bound_token(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    first_auth = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "old token"},
    ).json()["token"]["authorization_token"]
    first = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=first_auth, device_id="drone-a"),
    )
    assert first.json()["status"] == "pending"
    client.post("/api/drone-connections/drone-a/accept", headers={"Authorization": f"Bearer {user_token}"})
    assert client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": f"Bearer {first_auth}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    ).status_code == 200
    client.delete("/api/devices/drone-a", headers={"Authorization": f"Bearer {user_token}"})

    second_auth = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "new token"},
    ).json()["token"]["authorization_token"]
    pending = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=second_auth, device_id="drone-a"),
    )
    assert pending.json()["status"] == "pending"
    client.post("/api/drone-connections/drone-a/accept", headers={"Authorization": f"Bearer {user_token}"})

    assert client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": f"Bearer {second_auth}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    ).status_code == 200
    assert client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": f"Bearer {first_auth}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    ).status_code == 401


def test_revoked_integration_token_invalidates_backed_drone_token(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    token_payload = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "revocable"},
    ).json()["token"]
    auth_token = token_payload["authorization_token"]
    first = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    )
    assert first.json()["status"] == "pending"
    client.post("/api/drone-connections/drone-a/accept", headers={"Authorization": f"Bearer {user_token}"})
    drone_token = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    ).json()["drone_token"]

    revoke = client.delete(
        f"/api/integration-tokens/{token_payload['id']}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert revoke.status_code == 200
    heartbeat = client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": f"Bearer {drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    )
    assert heartbeat.status_code == 401


def test_deny_pending_drone_connection(client):
    """Unauthorized registration no longer creates a pending connection to deny."""
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    register_response = client.post(
        "/api/devices/register",
        json={
            "device_id": "rogue-drone",
            "device_name": "Rogue Drone",
            "batocera_info": {
                "model": "Test Model",
                "system": "Linux",
                "architecture": "x86_64",
                "cpu_model": "Test CPU",
                "cpu_cores": 4,
                "cpu_threads": 8,
                "cpu_max_frequency": "3.0 GHz",
                "memory_available": "8 GiB",
                "memory_total": "16 GiB",
                "ip_address": "192.168.1.1",
            },
        },
    )
    assert register_response.status_code == 401
    response = client.post(
        "/api/drone-connections/rogue-drone/deny",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    pending_response = client.get(
        "/api/drone-connections",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pending_response.json()["connections"] == []


def test_accept_pending_drone_connection_tolerates_relational_row_without_batocera_info(client):
    client.post(
        "/api/auth/register",
        json={"email": "accept-missing-info@example.com", "username": "accept-missing-info", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "accept-missing-info@example.com", "username": "accept-missing-info", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("accept-missing-info@example.com")
    db.pending_drone_connections["58:47:ca:7e:38:57"] = {
        "id": "58:47:ca:7e:38:57",
        "user_id": user["id"],
        "device_id": "58:47:ca:7e:38:57",
        "device_name": "Colon Drone",
        "status": "pending",
        "detected_at": datetime.utcnow(),
        "last_seen": datetime.utcnow(),
    }

    response = client.post(
        "/api/drone-connections/58:47:ca:7e:38:57/accept",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["device"]["device_id"] == "58:47:ca:7e:38:57"


def test_accept_pending_drone_connection_initializes_missing_user_device_bucket(client):
    client.post(
        "/api/auth/register",
        json={"email": "accept-cache@example.com", "username": "accept-cache", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "accept-cache@example.com", "username": "accept-cache", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("accept-cache@example.com")
    db.user_devices.pop(user["id"], None)
    db.pending_drone_connections["cache-drone"] = {
        "id": "cache-drone",
        "user_id": user["id"],
        "device_id": "cache-drone",
        "device_name": "Cache Drone",
        "status": "pending",
        "batocera_info": {},
        "detected_at": datetime.utcnow(),
        "last_seen": datetime.utcnow(),
    }

    response = client.post(
        "/api/drone-connections/cache-drone/accept",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["device"]["device_id"] == "cache-drone"
    assert db.user_devices[user["id"]]


def test_list_devices_uses_authorization_header(client):
    """Authenticated routes should accept Bearer token from header."""
    client.post(
        "/api/auth/register",
        json={"email": "auth-header@example.com", "username": "auth-header-at-example.com", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "auth-header@example.com", "username": "auth-header-at-example.com", "password": "testpass123"},
    )
    token = login_response.json()["access_token"]
    response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "devices" in response.json()


def test_list_devices_refreshes_persistent_state_before_read(client, monkeypatch):
    client.post(
        "/api/auth/register",
        json={"email": "fresh-devices@example.com", "username": "fresh-devices", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "fresh-devices@example.com", "username": "fresh-devices", "password": "testpass123"},
    )
    token = login_response.json()["access_token"]
    calls = {"count": 0}

    def count_refresh():
        calls["count"] += 1

    monkeypatch.setattr(db, "refresh_persistent_state", count_refresh)
    response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert calls["count"] == 1


def test_demo_seed_exposes_devices_and_systems(client):
    """Seeded demo user should have visible devices/systems data."""
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices_response.status_code == 200
    assert len(devices_response.json().get("devices", [])) > 0

    systems_response = client.get("/api/systems", headers={"Authorization": f"Bearer {token}"})
    assert systems_response.status_code == 200
    assert len(systems_response.json().get("systems", [])) > 0

    first_device = devices_response.json()["devices"][0]["device_id"]
    per_device_systems_response = client.get(
        f"/api/devices/{first_device}/systems",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert per_device_systems_response.status_code == 200
    assert len(per_device_systems_response.json().get("systems", [])) > 0

    roms_response = client.get(
        f"/api/devices/{first_device}/roms",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert roms_response.status_code == 200
    systems = roms_response.json().get("systems", {})
    assert len(systems.keys()) >= 5
    for rom_list in systems.values():
        assert len(rom_list) >= 5


def test_device_roms_support_server_side_pagination(client):
    seed_test_fleet()
    token = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    ).json()["access_token"]

    response = client.get(
        "/api/devices/arcade-cabinet-001/roms?system_name=snes&page=1&per_page=2",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert payload["page"] == 1
    assert payload["per_page"] == 2
    assert len(payload["roms"]) == 2


def test_device_systems_ui_loads_roms_by_page():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert "apiGet(`/api/devices/${selectedDeviceId}/systems`)" in js
    assert "if (currentDeviceView === 'systems') {\n                    loadDeviceSystems();\n                    loadSwarmRomAvailabilityPanel();\n                }" in js
    assert "romParams.set('page', String(page));" in js
    assert "romParams.set('per_page', String(ROMS_PER_PAGE));" in js
    assert "apiGet(`/api/devices/${selectedDeviceId}/roms?${romParams.toString()}`)" in js
    assert "apiGet(`/api/devices/${selectedDeviceId}/master-artwork?${artworkParams.toString()}`)" in js
    assert "rom-artwork-table" in js
    assert "const artworkRows = artworkRowsForRom(row, artworkLookup, row.system_name || system || '')" in js
    assert "toggleMasterRomDetail" in js
    assert "renderRomDetailPanel(row, artworkRows, sizeText, sources || preferred, statusLabel)" in js
    assert "document.querySelectorAll('.rom-master-detail-row').forEach" in js


def test_background_polling_does_not_pin_global_loading_toast():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert "const controller = new AbortController();" in js
    assert "setInterval(() => loadPendingConnections({showLoader: false}), 30000)" in js
    assert "setInterval(() => loadNotifications({showLoader: false}), 60000)" in js
    assert "loadDevices({showLoader: false, background: true})" in js
    assert "loadDeviceActions({showLoader: false})" in js
    assert "if (devicesRefreshInFlight) return;" in js
    assert "if (pendingConnectionsInFlight) return;" in js
    assert "if (notificationsInFlight) return;" in js


def test_demo_seed_exposes_pending_drone_connections(client):
    """Seeded demo user should see pending psionic Drone connection requests."""
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    pending_response = client.get(
        "/api/drone-connections",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pending_response.status_code == 200
    connections = pending_response.json()["connections"]
    assert len(connections) == 2
    assert {conn["device_id"] for conn in connections} == {
        "rogue-signal-001",
        "rogue-signal-002",
    }


def test_drone_heartbeat_updates_device_name(client):
    """Device name is controlled by the Drone heartbeat."""
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("owner@example.com")
    db.create_device(user["id"], "drone-a", "Old Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    response = client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": "Bearer drone-token"},
        json={"device_name": "Arcade Alpha"},
    )

    assert response.status_code == 200
    assert db.get_device_by_device_id("drone-a")["device_name"] == "Arcade Alpha"


def test_delete_device_removes_device_and_related_data(client):
    """Device deletion removes the device, ROMs, and gameplay logs for that user."""
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    delete_response = client.delete(
        "/api/devices/arcade-cabinet-001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_response.status_code == 200

    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices_response.status_code == 200
    assert all(
        device["device_id"] != "arcade-cabinet-001"
        for device in devices_response.json()["devices"]
    )

    detail_response = client.get(
        "/api/devices/arcade-cabinet-001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_response.status_code == 404
    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert all(conn["device_id"] != "arcade-cabinet-001" for conn in pending_response.json()["connections"])


def test_drone_self_disconnect_removes_from_swarm_without_pending_connection(client):
    client.post("/api/auth/register", json={"email": "disconnect@example.com", "username": "disconnect-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("disconnect@example.com")
    db.create_device(user["id"], "disconnect-drone", "Disconnect Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    disconnect_response = client.post(
        "/api/devices/disconnect-drone/disconnect",
        headers={"Authorization": "Bearer drone-token"},
    )
    assert disconnect_response.status_code == 200

    login_response = client.post("/api/auth/login", json={"email": "disconnect@example.com", "username": "disconnect-at-example.com", "password": "testpass123"})
    token = login_response.json()["access_token"]
    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert all(device["device_id"] != "disconnect-drone" for device in devices_response.json()["devices"])
    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert all(conn["device_id"] != "disconnect-drone" for conn in pending_response.json()["connections"])


def test_swarm_master_list_deduplicates_by_fingerprint_and_activity_search(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("test@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "drone-b", "Drone B", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.add_roms("drone-a", "snes", [{"rom_name": "Same Game.zip", "file_path": "Same Game.zip", "rom_fingerprint": "abc", "file_size": 3}])
    db.add_roms("drone-b", "snes", [{"rom_name": "Renamed Game.zip", "file_path": "Renamed Game.zip", "rom_fingerprint": "abc", "file_size": 3}])
    db.add_rom_sync_activity("drone-b", {
        "source_drone_id": "drone-a",
        "target_drone_id": "drone-b",
        "system": "snes",
        "rom_name": "Renamed Game.zip",
        "rom_fingerprint": "abc",
        "status": "completed",
        "duration_ms": 2400,
    })

    master = client.get("/api/master-roms?q=abc", headers={"Authorization": f"Bearer {token}"})
    assert master.status_code == 200
    assert master.json()["total"] == 1
    assert {device["device_id"] for device in master.json()["roms"][0]["devices"]} == {"drone-a", "drone-b"}

    activity = client.get("/api/sync-activity?q=Renamed", headers={"Authorization": f"Bearer {token}"})
    assert activity.status_code == 200
    assert activity.json()["activity"][0]["duration_ms"] == 2400


def test_metadata_inventory_endpoints_use_database_paging_when_assets_are_stored(client, monkeypatch):
    client.post("/api/auth/register", json={"email": "paging@example.com", "username": "paging-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "paging@example.com", "username": "paging-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("paging@example.com")
    source_id = db.create_device(user["id"], "page-source", "Page Source", {"ip_address": "10.0.0.2"}, raw_token="source")
    db.create_device(user["id"], "page-target", "Page Target", {"ip_address": "10.0.0.3"}, raw_token="target")
    calls = []

    def mock_page_master_assets(device_ids, asset_type, **kwargs):
        calls.append((asset_type, kwargs))
        common = {"_device_internal_id": source_id, "_master_key": f"key:{asset_type}", "_present_on_selected": False}
        if asset_type == "rom":
            row = {**common, "system_name": "snes", "rom_name": "Paged.zip", "file_path": "Paged.zip"}
        elif asset_type == "bios":
            row = {**common, "bios_name": "paged.bin", "file_path": "bios/paged.bin"}
        else:
            row = {**common, "_artwork_type": "image", "system": "snes", "rom_path": "Paged.zip"}
        return [row], 412

    monkeypatch.setattr(db, "_asset_store_enabled", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "page_master_assets", mock_page_master_assets)
    headers = {"Authorization": f"Bearer {token}"}
    responses = [
        client.get("/api/devices/page-target/master-roms?page=3&per_page=17&q=paged", headers=headers),
        client.get("/api/devices/page-target/master-bios?page=2&per_page=11", headers=headers),
        client.get("/api/devices/page-target/master-artwork?page=4&per_page=9&artwork_type=image", headers=headers),
        client.get("/api/master-roms?page=5&per_page=7", headers=headers),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["total"] == 412 for response in responses)
    assert [(asset_type, params["page"], params["per_page"]) for asset_type, params in calls] == [
        ("rom", 3, 17),
        ("bios", 2, 11),
        ("artwork", 4, 9),
        ("rom", 5, 7),
    ]
    assert calls[0][1]["query"] == "paged"
    assert calls[2][1]["artwork_type"] == "image"


def test_device_master_rom_presence_survives_grouping_when_selected_row_is_not_first(client, monkeypatch):
    client.post("/api/auth/register", json={"email": "presence@example.com", "username": "presence-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "presence@example.com", "username": "presence-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("presence@example.com")
    source_id = db.create_device(user["id"], "presence-source", "Presence Source", {"ip_address": "10.0.0.2"}, raw_token="source")
    target_id = db.create_device(user["id"], "presence-target", "Presence Target", {"ip_address": "10.0.0.3"}, raw_token="target")

    def mock_page_master_assets(device_ids, asset_type, **kwargs):
        assert asset_type == "rom"
        return [
            {
                "_device_internal_id": source_id,
                "_master_key": "fingerprint:abc123",
                "_present_on_selected": False,
                "system_name": "fbneo",
                "rom_name": "1942.zip",
                "file_path": "1942.zip",
                "rom_fingerprint": "abc123",
            },
            {
                "_device_internal_id": target_id,
                "_master_key": "fingerprint:abc123",
                "_present_on_selected": True,
                "system_name": "fbneo",
                "rom_name": "1942.zip",
                "file_path": "1942.zip",
                "rom_fingerprint": "abc123",
            },
        ], 1

    monkeypatch.setattr(db, "_asset_store_enabled", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "page_master_assets", mock_page_master_assets)

    response = client.get("/api/devices/presence-target/master-roms?q=1942", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    row = response.json()["roms"][0]
    assert row["present_on_selected"] is True
    assert {device["device_id"] for device in row["devices"]} == {"presence-source", "presence-target"}


def test_drone_sync_activity_endpoint_upserts_by_sync_id(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("test@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    queued = client.post(
        "/api/devices/drone-a/sync-activity",
        headers={"Authorization": "Bearer drone-token"},
        json={
            "sync_id": "sync-1",
            "target_drone_id": "drone-a",
            "system": "snes",
            "rom_name": "Game.zip",
            "status": "queued",
        },
    )
    assert queued.status_code == 200
    completed = client.post(
        "/api/devices/drone-a/sync-activity",
        headers={"Authorization": "Bearer drone-token"},
        json={
            "sync_id": "sync-1",
            "source_drone_id": "drone-b",
            "target_drone_id": "drone-a",
            "system": "snes",
            "rom_name": "Game.zip",
            "relative_path": "Game.zip",
            "status": "completed",
            "bytes_transferred": 8,
            "file_size": 8,
            "rom_fingerprint": "abc",
            "duration_ms": 1000,
        },
    )
    assert completed.status_code == 200

    activity = client.get("/api/devices/drone-a/sync-activity", headers={"Authorization": f"Bearer {token}"})
    assert activity.status_code == 200
    rows = activity.json()["activity"]
    assert len(rows) == 1
    assert rows[0]["id"] == "sync-1"
    assert rows[0]["status"] == "completed"
    assert rows[0]["bytes_transferred"] == 8
    assert rows[0]["rom_fingerprint"] == "abc"


def test_sync_rom_action_payload_includes_only_source_devices_with_rom(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("test@example.com")
    db.create_device(user["id"], "source-without-rom", "Source Without ROM", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-with-rom", "Source With ROM", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
    mark_source_resolvable("source-with-rom")
    db.add_roms("source-with-rom", "snes", [{"rom_name": "Game.zip", "file_path": "Game.zip", "rom_fingerprint": "abc", "file_size": 8}])

    response = client.post(
        "/api/devices/target-c/sync-rom",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "snes", "file_path": "Game.zip", "rom_fingerprint": "abc", "file_size": 8},
    )
    assert response.status_code == 200

    claim = client.post(
        "/api/devices/target-c/actions/claim",
        headers={"Authorization": "Bearer c"},
        json={},
    )
    assert claim.status_code == 200
    action = claim.json()["actions"][0]
    assert action["payload"]["devices"] == [{"device_id": "source-with-rom", "device_name": "Source With ROM"}]


def test_sync_rom_does_not_queue_associated_artwork_by_default(client):
    client.post("/api/auth/register", json={"email": "rom-artwork@example.com", "username": "rom-artwork-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "rom-artwork@example.com", "username": "rom-artwork-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("rom-artwork@example.com")
    db.create_device(user["id"], "source-with-assets", "Source Assets", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "target-without-assets", "Target Assets", {"ip_address": "10.0.0.3"}, raw_token="b")
    mark_source_resolvable("source-with-assets")
    db.add_roms("source-with-assets", "snes", [{"rom_name": "Game.zip", "file_path": "Game.zip", "rom_fingerprint": "abc"}])
    db.add_artwork("source-with-assets", [{
        "system": "snes",
        "rom_path": "/userdata/roms/snes/Game.zip",
        "rom_name": "Game.zip",
        "artwork_types": ["image", "marquee"],
    }])

    response = client.post(
        "/api/devices/target-without-assets/sync-rom",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "snes", "file_path": "Game.zip", "rom_fingerprint": "abc"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artwork_action_count"] == 0
    assert payload["artwork_actions"] == []

    claim = client.post(
        "/api/devices/target-without-assets/actions/claim",
        headers={"Authorization": "Bearer b"},
        json={},
    )
    assert claim.status_code == 200
    actions = claim.json()["actions"]
    assert [action["action"] for action in actions] == ["sync_rom"]


def test_sync_rom_rejects_source_that_is_not_publicly_resolvable(client):
    client.post("/api/auth/register", json={"email": "blocked@example.com", "username": "blocked-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "blocked@example.com", "username": "blocked-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("blocked@example.com")
    db.create_device(user["id"], "unresolved-source", "Unresolved Source", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "blocked-target", "Blocked Target", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.add_roms("unresolved-source", "snes", [{"rom_name": "Game.zip", "file_path": "Game.zip", "rom_fingerprint": "abc", "file_size": 8}])

    response = client.post(
        "/api/devices/blocked-target/sync-rom",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "snes", "file_path": "Game.zip", "rom_fingerprint": "abc"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "No resolvable source Drone has this ROM"
    assert db.get_device_actions(user["id"], "blocked-target") == []


def test_sync_folder_rom_payload_matches_by_path_without_fingerprint(client):
    client.post("/api/auth/register", json={"email": "folder@example.com", "username": "folder-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "folder@example.com", "username": "folder-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("folder@example.com")
    db.create_device(user["id"], "source-with-folder", "Source Folder", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-folder", "Target Folder", {"ip_address": "10.0.0.4"}, raw_token="c")
    mark_source_resolvable("source-with-folder")
    db.add_roms("source-with-folder", "ps3", [{"rom_name": "Game.ps3", "file_path": "Game.ps3", "entry_type": "folder", "file_size": 10}])

    response = client.post(
        "/api/devices/target-folder/sync-rom",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "ps3", "file_path": "Game.ps3", "entry_type": "folder", "file_size": 10},
    )
    assert response.status_code == 200

    claim = client.post(
        "/api/devices/target-folder/actions/claim",
        headers={"Authorization": "Bearer c"},
        json={},
    )
    assert claim.status_code == 200
    action = claim.json()["actions"][0]
    assert action["payload"]["entry_type"] == "folder"
    assert action["payload"].get("rom_fingerprint") is None
    assert action["payload"]["devices"] == [{"device_id": "source-with-folder", "device_name": "Source Folder"}]


def test_rom_metadata_upload_persists_bios_and_master_bios(client):
    client.post("/api/auth/register", json={"email": "bios@example.com", "username": "bios-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "bios@example.com", "username": "bios-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("bios@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    db.create_device(user["id"], "drone-b", "Drone B", {"ip_address": "10.0.0.3"}, raw_token="drone-token-b")

    response = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "rom_metadata",
            "roms": [],
            "systems": [],
            "bios": [{"name": "flash.bin", "path": "dc/flash.bin", "byte_count": 9, "bios_md5": "bios-fingerprint"}],
            "gamelists": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["bios_count"] == 1

    bios_response = client.get("/api/devices/drone-a/bios", headers={"Authorization": f"Bearer {token}"})
    assert bios_response.status_code == 200
    assert bios_response.json()["bios"][0]["file_path"] == "dc/flash.bin"
    assert bios_response.json()["bios"][0]["bios_md5"] == "bios-fingerprint"

    master_response = client.get("/api/devices/drone-b/master-bios", headers={"Authorization": f"Bearer {token}"})
    assert master_response.status_code == 200
    row = master_response.json()["bios"][0]
    assert row["file_path"] == "dc/flash.bin"
    assert row["present_on_selected"] is False
    assert row["devices"] == [{"device_id": "drone-a", "device_name": "Drone A"}]


def test_rom_metadata_upload_marks_offline_drone_online(client):
    client.post("/api/auth/register", json={"email": "assetseen@example.com", "username": "assetseen-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "assetseen@example.com", "username": "assetseen-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("assetseen@example.com")
    device_id = db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    device = db.devices[device_id]
    db.devices[device["id"]]["last_seen"] = datetime.utcnow() - timedelta(seconds=999)
    db.devices[device["id"]]["last_known_status"] = "offline"

    before = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert before.status_code == 200
    assert before.json()["devices"][0]["status"] == "offline"

    response = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "chunk_index": 0,
            "chunk_total": 1,
            "inventory_complete": True,
            "systems": [{"name": "snes", "rom_count": 1}],
            "roms": [{"system": "snes", "rom_name": "Game", "file_path": "Game.zip", "file_size": 3}],
            "bios": [],
            "artwork": [],
        },
    )
    assert response.status_code == 200

    after = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert after.status_code == 200
    assert after.json()["devices"][0]["status"] == "online"


def test_rom_metadata_hash_patch_enriches_existing_inventory_without_replacing_roms(client):
    client.post("/api/auth/register", json={"email": "hashes@example.com", "username": "hashes-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("hashes@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")

    inventory = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory",
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [
                {"system": "snes", "file_path": "Game One.zip", "rom_name": "Game One", "file_size": 3},
                {"system": "snes", "file_path": "Game Two.zip", "rom_name": "Game Two", "file_size": 3},
            ],
            "bios": [{"file_path": "dc/flash.bin", "bios_md5": "bios-fingerprint"}],
            "artwork": [],
        },
    )
    assert inventory.status_code == 200

    patch = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "rom_hash_patch",
            "roms": [{"system": "snes", "file_path": "Game One.zip", "rom_fingerprint": "hash-one", "fingerprint": "hash-one"}],
            "hash_progress": {"processed": 1, "total": 2, "complete": False},
        },
    )
    assert patch.status_code == 200

    stored = db.get_device_roms("drone-a")
    assert len(stored) == 2
    by_path = {row["file_path"]: row for row in stored}
    assert by_path["Game One.zip"]["rom_fingerprint"] == "hash-one"
    assert by_path["Game Two.zip"]["rom_fingerprint"] is None
    assert db.get_device_bios("drone-a")[0]["bios_md5"] == "bios-fingerprint"


def test_asset_metadata_inventory_chunks_append_without_replacing_previous_chunks(client):
    client.post("/api/auth/register", json={"email": "chunks@example.com", "username": "chunks-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("chunks@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")

    first = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "inventory_id": "inventory-1",
            "chunk_index": 0,
            "chunk_total": 2,
            "inventory_complete": False,
            "inventory_counts": {"roms": 2, "bios": 1, "artwork": 0},
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [{"system": "snes", "file_path": "Game One.zip", "rom_name": "Game One", "file_size": 3}],
            "bios": [{"file_path": "dc/flash.bin", "bios_md5": "bios-fingerprint"}],
            "artwork": [],
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "inventory_id": "inventory-1",
            "chunk_index": 1,
            "chunk_total": 2,
            "inventory_complete": True,
            "inventory_counts": {"roms": 2, "bios": 1, "artwork": 0},
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [{"system": "snes", "file_path": "Game Two.zip", "rom_name": "Game Two", "file_size": 3}],
            "bios": [],
            "artwork": [],
        },
    )
    assert second.status_code == 200

    stored = db.get_device_roms("drone-a")
    assert len(stored) == 2
    assert {row["file_path"] for row in stored} == {"Game One.zip", "Game Two.zip"}
    assert db.get_device_bios("drone-a")[0]["bios_md5"] == "bios-fingerprint"
    assert db.get_device_by_device_id("drone-a")["rom_metadata"]["inventory_complete"] is True


def test_asset_metadata_upload_accepts_drone_sync_payload_fields(client):
    client.post("/api/auth/register", json={"email": "sync-payload@example.com", "username": "sync-payload-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("sync-payload@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")

    response = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "collected_at": "2026-05-30T23:43:00+00:00",
            "roms_root": "/userdata/roms",
            "bios_root": "/userdata/bios",
            "cache": {"schema_version": 3},
            "replace_all": True,
            "inventory_id": "drone-a:2026-05-30T23:43:00+00:00:1:1:1",
            "chunk_index": 0,
            "chunk_total": 1,
            "inventory_complete": True,
            "inventory_counts": {"roms": 1, "bios": 1, "artwork": 1},
            "systems": [{"name": "snes", "system_name": "snes", "rom_count": 1, "bios_count": 0, "artwork_count": 1}],
            "gamelists": [{"system": "snes", "path": "/userdata/roms/snes/gamelist.xml"}],
            "roms": [
                {
                    "entry_type": "file",
                    "system": "snes",
                    "system_name": "snes",
                    "name": "Game One.zip",
                    "rom_name": "Game One",
                    "file_path": "Game One.zip",
                    "relative_path": "Game One.zip",
                    "unique_id": "stable-rom-id",
                    "file_size": 3,
                    "byte_count": 3,
                    "size": 3,
                    "modified_time": 1770000000,
                    "mtime": 1770000000,
                }
            ],
            "bios": [
                {
                    "entry_type": "file",
                    "name": "bios.bin",
                    "path": "snes/bios.bin",
                    "file_path": "snes/bios.bin",
                    "relative_path": "snes/bios.bin",
                    "unique_id": "stable-bios-id",
                    "file_size": 4,
                    "byte_count": 4,
                    "size": 4,
                    "modified_time": 1770000001,
                    "mtime": 1770000001,
                }
            ],
            "artwork": [
                {
                    "asset_type": "artwork",
                    "system": "snes",
                    "rom_name": "Game One",
                    "rom_path": "Game One.zip",
                    "file_path": "media/images/Game One.png",
                    "artwork_type": "image",
                    "artwork_types": ["image"],
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"rom_count": 1, "bios_count": 1, "artwork_count": 1, "saves_count": 0}
    stored = db.get_device_by_device_id("drone-a")["rom_metadata"]
    assert stored["collected_at"] == "2026-05-30T23:43:00+00:00"
    assert stored["bios_root"] == "/userdata/bios"
    assert stored["cache"] == {"schema_version": 3}


def test_asset_metadata_upload_stores_and_lists_game_saves(client):
    client.post("/api/auth/register", json={"email": "saves@example.com", "username": "saves-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("saves@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    login = client.post("/api/auth/login", json={"email": "saves@example.com", "username": "saves-at-example.com", "password": "testpass123"})
    user_token = login.json()["access_token"]

    upload = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory",
            "replace_all": True,
            "saves_files_thumbprint": "saves-thumb-1",
            "saves": [
                {"system": "snes", "save_name": "Chrono Trigger.srm", "file_path": "snes/Chrono Trigger.srm", "fingerprint": "fp-ct", "file_size": 8192, "modified_time": 1717000000},
                {"system": "gba", "save_name": "Metroid.sav", "file_path": "gba/Metroid.sav", "fingerprint": "fp-mf", "file_size": 4096, "modified_time": 1717000100},
            ],
        },
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["saves_count"] == 2

    listing = client.get("/api/devices/drone-a/saves", headers={"Authorization": f"Bearer {user_token}"})
    assert listing.status_code == 200
    saves = listing.json()["saves"]
    assert {row["file_path"] for row in saves} == {"snes/Chrono Trigger.srm", "gba/Metroid.sav"}
    assert {row["fingerprint"] for row in saves} == {"fp-ct", "fp-mf"}

    # The Drone-supplied saves thumbprint is stored verbatim and echoed in the heartbeat.
    heartbeat = client.post(
        "/api/devices/drone-a/heartbeat",
        headers={"Authorization": "Bearer drone-token-a"},
        json={"device_name": "Drone A"},
    )
    assert heartbeat.json()["saves_files_thumbprint"] == "saves-thumb-1"


def test_device_saves_endpoint_pages_and_searches(client):
    client.post("/api/auth/register", json={"email": "saves-page@example.com", "username": "saves-page-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("saves-page@example.com")
    db.create_device(user["id"], "drone-sp", "Drone SP", {"ip_address": "10.0.0.2"}, raw_token="drone-token-sp")
    token = client.post("/api/auth/login", json={"email": "saves-page@example.com", "username": "saves-page-at-example.com", "password": "testpass123"}).json()["access_token"]

    saves = [
        {"system": "snes", "save_name": f"Game {i}.srm", "file_path": f"snes/Game {i}.srm", "fingerprint": f"fp-{i}", "file_size": 1000 + i, "modified_time": 1717000000 + i}
        for i in range(7)
    ]
    saves.append({"system": "gba", "save_name": "Metroid.sav", "file_path": "gba/Metroid.sav", "fingerprint": "fp-mf", "file_size": 4096, "modified_time": 1717009999})
    upload = client.post(
        "/api/devices/drone-sp/rom-metadata",
        headers={"Authorization": "Bearer drone-token-sp"},
        json={"device_id": "drone-sp", "type": "asset_metadata", "update_mode": "inventory", "replace_all": True, "saves": saves},
    )
    assert upload.status_code == 200, upload.text

    auth = {"Authorization": f"Bearer {token}"}
    # Paging: page 1 of 3 with per_page=3, total=8.
    p1 = client.get("/api/devices/drone-sp/saves?page=1&per_page=3", headers=auth).json()
    assert p1["total"] == 8 and p1["page"] == 1 and p1["per_page"] == 3
    assert len(p1["saves"]) == 3
    p3 = client.get("/api/devices/drone-sp/saves?page=3&per_page=3", headers=auth).json()
    assert len(p3["saves"]) == 2  # last page
    ids = [r["file_path"] for r in (p1["saves"] + client.get("/api/devices/drone-sp/saves?page=2&per_page=3", headers=auth).json()["saves"] + p3["saves"])]
    assert len(set(ids)) == 8  # disjoint pages

    # Search narrows results (across system/name/path).
    search = client.get("/api/devices/drone-sp/saves?q=metroid", headers=auth).json()
    assert search["total"] == 1
    assert search["saves"][0]["file_path"] == "gba/Metroid.sav"


def test_asset_metadata_queued_full_refresh_keeps_existing_rows_visible_until_last_chunk(client):
    client.post("/api/auth/register", json={"email": "refresh@example.com", "username": "refresh-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("refresh@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    headers = {"Authorization": "Bearer drone-token-a"}

    original = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory",
            "systems": [{"name": "snes", "rom_count": 1}],
            "roms": [{"system": "snes", "file_path": "Old Game.zip", "rom_name": "Old Game"}],
            "bios": [{"file_path": "old/bios.bin", "bios_md5": "old-bios"}],
            "artwork": [],
        },
    )
    assert original.status_code == 200

    first = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "replace_all": True,
            "inventory_id": "replacement-1",
            "chunk_index": 0,
            "chunk_total": 2,
            "inventory_complete": False,
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [{"system": "snes", "file_path": "New One.zip", "rom_name": "New One"}],
            "bios": [{"file_path": "new/bios.bin", "bios_md5": "new-bios"}],
            "artwork": [],
        },
    )
    assert first.status_code == 200
    assert {row["file_path"] for row in db.get_device_roms("drone-a")} == {"Old Game.zip"}
    assert {row["file_path"] for row in db.get_device_bios("drone-a")} == {"old/bios.bin"}

    second = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "replace_all": True,
            "inventory_id": "replacement-1",
            "chunk_index": 1,
            "chunk_total": 2,
            "inventory_complete": True,
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [{"system": "snes", "file_path": "New Two.zip", "rom_name": "New Two"}],
            "bios": [],
            "artwork": [],
        },
    )
    assert second.status_code == 200
    assert {row["file_path"] for row in db.get_device_roms("drone-a")} == {"New One.zip", "New Two.zip"}
    assert {row["file_path"] for row in db.get_device_bios("drone-a")} == {"new/bios.bin"}
    device = db.get_device_by_device_id("drone-a")
    assert device["rom_inventory_fingerprint"] == db_module.compute_rom_inventory_fingerprint(db.get_device_roms("drone-a"))


def test_asset_metadata_final_chunk_stores_drone_rom_inventory_fingerprint(client):
    client.post("/api/auth/register", json={"email": "fingerprint-upload@example.com", "username": "fingerprint-upload-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("fingerprint-upload@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    headers = {"Authorization": "Bearer drone-token-a"}
    expected = db_module.compute_rom_inventory_fingerprint([
        {"system": "snes", "file_path": "A.zip", "rom_fingerprint": "aaa", "file_size": 8},
        {"system": "snes", "file_path": "B.zip", "rom_fingerprint": "bbb", "file_size": 9},
    ])

    first = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "replace_all": True,
            "inventory_id": "fingerprint-1",
            "chunk_index": 0,
            "chunk_total": 2,
            "inventory_complete": False,
            "roms": [{"system": "snes", "file_path": "A.zip", "rom_fingerprint": "aaa", "file_size": 8}],
        },
    )
    assert first.status_code == 200
    assert not db.get_device_by_device_id("drone-a").get("rom_inventory_fingerprint")

    second = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_chunk",
            "replace_all": True,
            "inventory_id": "fingerprint-1",
            "chunk_index": 1,
            "chunk_total": 2,
            "inventory_complete": True,
            "rom_inventory_fingerprint": expected,
            "rom_inventory_fingerprint_algorithm": db_module.ROM_INVENTORY_FINGERPRINT_ALGORITHM,
            "roms": [{"system": "snes", "file_path": "B.zip", "rom_fingerprint": "bbb", "file_size": 9}],
        },
    )

    assert second.status_code == 200
    device = db.get_device_by_device_id("drone-a")
    assert device["drone_rom_inventory_fingerprint"] == expected
    assert device["rom_inventory_fingerprint"] == expected


def test_heartbeat_does_not_queue_purge_when_rom_inventory_fingerprint_differs(client):
    # Resync is now Drone-driven via echoed asset thumbprints; Overmind must NOT queue a
    # server-side purge on a recomputed fingerprint mismatch (the old behavior produced an
    # endless purge -> full-refresh loop whenever the two computations drifted).
    client.post("/api/auth/register", json={"email": "fingerprint-heartbeat@example.com", "username": "fingerprint-heartbeat-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("fingerprint-heartbeat@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    headers = {"Authorization": "Bearer drone-token-a"}
    db.add_roms("drone-a", "snes", [{"rom_name": "Old.zip", "file_path": "Old.zip", "rom_fingerprint": "old", "file_size": 1}])
    db.update_device_rom_inventory_fingerprint("drone-a", compute_overmind=True)

    response = client.post(
        "/api/devices/drone-a/heartbeat",
        headers=headers,
        json={
            "device_id": "drone-a",
            "rom_inventory_fingerprint": "different",
            "rom_inventory_fingerprint_algorithm": db_module.ROM_INVENTORY_FINGERPRINT_ALGORITHM,
        },
    )

    assert response.status_code == 200
    actions = response.json()["actions"]
    assert all(action["action"] != "purge_asset_cache" for action in actions)
    # The Drone-reported fingerprint is still recorded for display.
    assert db.get_device_by_device_id("drone-a")["drone_rom_inventory_fingerprint"] == "different"


def test_heartbeat_accepts_saves_files_thumbprint_and_updates_last_seen(client):
    # Regression: the Drone sends saves_files_thumbprint in its heartbeat. The request
    # model is strict (extra="forbid"), so a missing field made every heartbeat 422 ->
    # last_seen never updated -> the Drone showed permanently offline despite heartbeats.
    client.post("/api/auth/register", json={"email": "hb-saves@example.com", "username": "hb-saves-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("hb-saves@example.com")
    db.create_device(user["id"], "drone-hb", "Drone HB", {"ip_address": "10.0.0.2"}, raw_token="drone-token-hb")
    stale_device = db.get_device_by_device_id("drone-hb")
    stale_device["last_seen"] = datetime.utcnow() - timedelta(hours=1)

    response = client.post(
        "/api/devices/drone-hb/heartbeat",
        headers={"Authorization": "Bearer drone-token-hb"},
        json={
            "device_id": "drone-hb",
            "device_name": "Drone HB",
            "romset_files_thumbprint": "r1",
            "bios_files_thumbprint": "b1",
            "saves_files_thumbprint": "s1",
        },
    )
    assert response.status_code == 200, response.text
    # last_seen refreshed -> device reads online (not the stale 1h-old timestamp).
    refreshed = db.get_device_by_device_id("drone-hb")
    assert (datetime.utcnow() - refreshed["last_seen"]).total_seconds() < 60


def test_heartbeat_accepts_download_queue_state_fields(client):
    # Regression: the Drone's download snapshot gained queue-state fields (paused,
    # queue_eta_seconds, queue_remaining_bytes, queue_estimate_*, ...). DroneDownloadsReport
    # was strict (extra="forbid"), so those 422-ed the whole heartbeat -> last_seen never
    # updated -> the Drone showed offline. The report is now extensible.
    client.post("/api/auth/register", json={"email": "hb-dl@example.com", "username": "hb-dl-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("hb-dl@example.com")
    db.create_device(user["id"], "drone-dl", "Drone DL", {"ip_address": "10.0.0.3"}, raw_token="drone-token-dl")
    stale_device = db.get_device_by_device_id("drone-dl")
    stale_device["last_seen"] = datetime.utcnow() - timedelta(hours=1)

    response = client.post(
        "/api/devices/drone-dl/heartbeat",
        headers={"Authorization": "Bearer drone-token-dl"},
        json={
            "device_id": "drone-dl",
            "device_name": "Drone DL",
            "downloads": {
                "target_drone_id": "drone-dl",
                "concurrency": {"scope": "target_drone", "active_limit": 3},
                "paused": False,
                "queue_eta_seconds": None,
                "queue_remaining_bytes": 0,
                "queue_known_remaining_bytes": 0,
                "queue_estimated_unknown_bytes": 0,
                "queue_unknown_size_count": 0,
                "queue_size_estimate_available": True,
                "queue_estimate_speed_bps": 12000000,
                "queue_estimate_speed_source": "active",
                "queue_eta_state": "ready",
                "active": [],
                "queued": [],
                "recent": [],
                "downloads": [],
            },
        },
    )
    assert response.status_code == 200, response.text
    refreshed = db.get_device_by_device_id("drone-dl")
    assert (datetime.utcnow() - refreshed["last_seen"]).total_seconds() < 60


def test_heartbeat_accepts_screen_mode_and_audio_volume_in_system_info(client):
    client.post("/api/auth/register", json={"email": "hb-vol@example.com", "username": "hb-vol-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("hb-vol@example.com")
    db.create_device(user["id"], "drone-vol", "Drone Vol", {"ip_address": "10.0.0.2"}, raw_token="drone-token-vol")

    response = client.post(
        "/api/devices/drone-vol/heartbeat",
        headers={"Authorization": "Bearer drone-token-vol"},
        json={
            "device_id": "drone-vol",
            "system_info": {"hostname": "drone-vol", "screen_mode": "kid", "audio_volume": 65},
        },
    )
    assert response.status_code == 200, response.text
    refreshed = db.get_device_by_device_id("drone-vol")
    assert refreshed["system_info"]["audio_volume"] == 65
    assert refreshed["system_info"]["screen_mode"] == "kid"


def test_heartbeat_echoes_stored_asset_thumbprints(client):
    client.post("/api/auth/register", json={"email": "thumbprint-echo@example.com", "username": "thumbprint-echo-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("thumbprint-echo@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    headers = {"Authorization": "Bearer drone-token-a"}

    # Before any asset upload, Overmind has no stored thumbprints to echo.
    first = client.post("/api/devices/drone-a/heartbeat", headers=headers, json={"device_id": "drone-a"})
    assert first.status_code == 200
    assert first.json()["romset_files_thumbprint"] is None
    assert first.json()["bios_files_thumbprint"] is None

    # A full inventory upload carrying thumbprints stores them verbatim...
    upload = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory",
            "romset_files_thumbprint": "romset-tp-123",
            "bios_files_thumbprint": "bios-tp-456",
            "roms": [{"system": "snes", "file_path": "A.zip", "rom_name": "A", "file_size": 1}],
            "bios": [{"file_path": "snes/bios.bin", "bios_md5": "deadbeef", "file_size": 2}],
        },
    )
    assert upload.status_code == 200

    # ...and the next heartbeat echoes them back so the Drone can compare.
    second = client.post("/api/devices/drone-a/heartbeat", headers=headers, json={"device_id": "drone-a"})
    assert second.status_code == 200
    assert second.json()["romset_files_thumbprint"] == "romset-tp-123"
    assert second.json()["bios_files_thumbprint"] == "bios-tp-456"
    device = db.get_device_by_device_id("drone-a")
    assert device["romset_files_thumbprint"] == "romset-tp-123"
    assert device["bios_files_thumbprint"] == "bios-tp-456"


def test_heartbeat_does_not_queue_metadata_rebuild_when_rom_inventory_fingerprint_matches(client):
    client.post("/api/auth/register", json={"email": "fingerprint-match@example.com", "username": "fingerprint-match-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("fingerprint-match@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    headers = {"Authorization": "Bearer drone-token-a"}
    db.add_roms("drone-a", "snes", [{"rom_name": "Same.zip", "file_path": "Same.zip", "rom_fingerprint": "same", "file_size": 1}])
    device = db.update_device_rom_inventory_fingerprint("drone-a", compute_overmind=True)

    response = client.post(
        "/api/devices/drone-a/heartbeat",
        headers=headers,
        json={"device_id": "drone-a", "rom_inventory_fingerprint": device["rom_inventory_fingerprint"]},
    )

    assert response.status_code == 200
    assert response.json()["actions"] == []


def test_rom_hash_patch_completion_recomputes_overmind_fingerprint(client):
    client.post("/api/auth/register", json={"email": "fingerprint-hash@example.com", "username": "fingerprint-hash-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("fingerprint-hash@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    headers = {"Authorization": "Bearer drone-token-a"}
    upload = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory",
            "roms": [{"system": "snes", "file_path": "A.zip", "rom_name": "A", "file_size": 1}],
        },
    )
    assert upload.status_code == 200
    before = db.get_device_by_device_id("drone-a")["rom_inventory_fingerprint"]

    patch = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "rom_hash_patch",
            "roms": [{"system": "snes", "file_path": "A.zip", "rom_fingerprint": "abc", "file_size": 1}],
            "hash_progress": {"processed": 1, "total": 1, "complete": True},
        },
    )

    assert patch.status_code == 200
    after = db.get_device_by_device_id("drone-a")["rom_inventory_fingerprint"]
    assert after != before
    assert after == db_module.compute_rom_inventory_fingerprint(db.get_device_roms("drone-a"))


def test_asset_metadata_delta_only_upserts_and_deletes_listed_assets(client):
    client.post("/api/auth/register", json={"email": "delta@example.com", "username": "delta-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("delta@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    headers = {"Authorization": "Bearer drone-token-a"}
    client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_delta",
            "roms": [
                {"system": "snes", "file_path": "Keep.zip", "rom_name": "Keep"},
                {"system": "snes", "file_path": "Remove.zip", "rom_name": "Remove"},
            ],
            "bios": [{"file_path": "keep/bios.bin", "bios_md5": "keep-bios"}],
            "artwork": [],
            "deleted": {"roms": [], "bios": [], "artwork": []},
        },
    )
    client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory",
            "systems": [],
            "roms": [],
            "bios": [],
            "artwork": [],
        },
    )
    assert {row["file_path"] for row in db.get_device_roms("drone-a")} == {"Keep.zip", "Remove.zip"}

    response = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers=headers,
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "update_mode": "inventory_delta",
            "roms": [{"system": "snes", "file_path": "Added.zip", "rom_name": "Added"}],
            "bios": [],
            "artwork": [],
            "deleted": {"roms": [{"system": "snes", "file_path": "Remove.zip"}], "bios": [], "artwork": []},
        },
    )
    assert response.status_code == 200
    assert {row["file_path"] for row in db.get_device_roms("drone-a")} == {"Keep.zip", "Added.zip"}
    assert {row["file_path"] for row in db.get_device_bios("drone-a")} == {"keep/bios.bin"}


def test_sync_bios_action_payload_includes_only_source_devices_with_bios(client):
    client.post("/api/auth/register", json={"email": "sync-bios@example.com", "username": "sync-bios-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "sync-bios@example.com", "username": "sync-bios-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("sync-bios@example.com")
    db.create_device(user["id"], "source-without-bios", "Source Without BIOS", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-with-bios", "Source With BIOS", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
    mark_source_resolvable("source-with-bios")
    db.add_bios("source-with-bios", [{"bios_name": "flash.bin", "file_path": "dc/flash.bin", "bios_md5": "bios-fingerprint", "file_size": 8}])

    response = client.post(
        "/api/devices/target-c/sync-bios",
        headers={"Authorization": f"Bearer {token}"},
        json={"file_path": "dc/flash.bin", "bios_md5": "bios-fingerprint", "file_size": 8},
    )
    assert response.status_code == 200

    claim = client.post(
        "/api/devices/target-c/actions/claim",
        headers={"Authorization": "Bearer c"},
        json={},
    )
    assert claim.status_code == 200
    action = claim.json()["actions"][0]
    assert action["action"] == "sync_bios"
    assert action["payload"]["devices"] == [{"device_id": "source-with-bios", "device_name": "Source With BIOS"}]


def test_asset_metadata_upload_persists_artwork_and_master_artwork(client):
    client.post("/api/auth/register", json={"email": "artwork@example.com", "username": "artwork-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "artwork@example.com", "username": "artwork-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("artwork@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    db.create_device(user["id"], "drone-b", "Drone B", {"ip_address": "10.0.0.3"}, raw_token="drone-token-b")

    response = client.post(
        "/api/devices/drone-a/rom-metadata",
        headers={"Authorization": "Bearer drone-token-a"},
        json={
            "device_id": "drone-a",
            "type": "asset_metadata",
            "roms": [],
            "systems": [],
            "bios": [],
            "artwork": [{
                "system": "snes",
                "rom_path": "Game.zip",
                "rom_name": "Game.zip",
                "title": "Game",
                "artwork_types": ["image", "marquee"],
            }],
            "gamelists": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["artwork_count"] == 1

    master_response = client.get("/api/devices/drone-b/master-artwork", headers={"Authorization": f"Bearer {token}"})
    assert master_response.status_code == 200
    rows = master_response.json()["artwork"]
    assert {(row["rom_path"], row["artwork_type"]) for row in rows} == {("Game.zip", "image"), ("Game.zip", "marquee")}
    assert all(row["present_on_selected"] is False for row in rows)
    assert all(row["devices"] == [{"device_id": "drone-a", "device_name": "Drone A"}] for row in rows)


def test_sync_artwork_action_payload_includes_only_source_devices_with_artwork(client):
    client.post("/api/auth/register", json={"email": "sync-artwork@example.com", "username": "sync-artwork-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "sync-artwork@example.com", "username": "sync-artwork-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("sync-artwork@example.com")
    db.create_device(user["id"], "source-without-artwork", "Source Without Artwork", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-with-artwork", "Source With Artwork", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
    mark_source_resolvable("source-with-artwork")
    db.add_artwork("source-with-artwork", [{
        "system": "snes",
        "rom_path": "Game.zip",
        "rom_name": "Game.zip",
        "artwork_types": ["image"],
    }])

    response = client.post(
        "/api/devices/target-c/sync-artwork",
        headers={"Authorization": f"Bearer {token}"},
        json={"system": "snes", "rom_path": "Game.zip", "artwork_type": "image"},
    )
    assert response.status_code == 200

    claim = client.post(
        "/api/devices/target-c/actions/claim",
        headers={"Authorization": "Bearer c"},
        json={},
    )
    assert claim.status_code == 200
    action = claim.json()["actions"][0]
    assert action["action"] == "sync_artwork"
    assert action["payload"]["devices"] == [{"device_id": "source-with-artwork", "device_name": "Source With Artwork"}]
    assert action["payload"]["artwork_type"] == "image"


def test_bulk_sync_artwork_filters_sources_and_systems(client):
    client.post("/api/auth/register", json={"email": "bulk-artwork@example.com", "username": "bulk-artwork-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "bulk-artwork@example.com", "username": "bulk-artwork-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("bulk-artwork@example.com")
    db.create_device(user["id"], "source-a", "Source A", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-b", "Source B", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
    mark_source_resolvable("source-a")
    db.add_artwork("source-a", [{
        "system": "snes",
        "rom_path": "Game.zip",
        "rom_name": "Game.zip",
        "artwork_types": ["image"],
    }])
    db.add_artwork("source-b", [{
        "system": "gba",
        "rom_path": "Other.gba",
        "rom_name": "Other.gba",
        "artwork_types": ["image"],
    }])

    response = client.post(
        "/api/devices/target-c/sync-artwork-bulk",
        headers={"Authorization": f"Bearer {token}"},
        json={"systems": ["snes"], "devices": ["source-a"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["action_count"] == 1
    assert payload["queued_artwork_count"] == 1

    claim = client.post(
        "/api/devices/target-c/actions/claim",
        headers={"Authorization": "Bearer c"},
        json={},
    )
    assert claim.status_code == 200
    action = claim.json()["actions"][0]
    assert action["action"] == "sync_artwork"
    assert action["payload"]["system_name"] == "snes"
    assert action["payload"]["rom_path"] == "Game.zip"
    assert action["payload"]["devices"] == [{"device_id": "source-a", "device_name": "Source A"}]


def test_bulk_sync_queues_missing_roms_between_selected_drones_only(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "username": "test-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("test@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "drone-b", "Drone B", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "drone-c", "Drone C", {"ip_address": "10.0.0.4"}, raw_token="c")
    mark_source_resolvable("drone-a", "8.8.8.8")
    mark_source_resolvable("drone-b", "1.1.1.1")
    db.add_roms("drone-a", "snes", [{"rom_name": "A.zip", "file_path": "A.zip", "rom_fingerprint": "aaa", "file_size": 8}])
    db.add_roms("drone-b", "snes", [{"rom_name": "B.zip", "file_path": "B.zip", "rom_fingerprint": "bbb", "file_size": 9}])
    db.add_roms("drone-c", "snes", [{"rom_name": "C.zip", "file_path": "C.zip", "rom_fingerprint": "ccc", "file_size": 10}])
    db.add_artwork("drone-b", [{
        "system": "snes",
        "rom_path": "B.zip",
        "rom_name": "B.zip",
        "artwork_types": ["image"],
    }])

    response = client.post(
        "/api/bulk-sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_ids": ["drone-a", "drone-b"], "systems": ["snes"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["action_count"] == 2
    assert payload["queued_rom_count"] == 2
    assert payload["artwork_action_count"] == 0
    assert payload["artwork_actions"] == []

    claim_a = client.post("/api/devices/drone-a/actions/claim", headers={"Authorization": "Bearer a"}, json={})
    claim_b = client.post("/api/devices/drone-b/actions/claim", headers={"Authorization": "Bearer b"}, json={})
    assert claim_a.status_code == 200
    assert claim_b.status_code == 200
    claim_a_actions = claim_a.json()["actions"]
    assert [action["action"] for action in claim_a_actions] == ["sync_system"]
    assert claim_a_actions[0]["payload"]["roms"][0]["file_path"] == "B.zip"
    assert claim_a_actions[0]["payload"]["roms"][0]["devices"] == [{"device_id": "drone-b", "device_name": "Drone B"}]
    assert claim_a_actions[0]["payload"]["roms"][0]["sync_id"]
    assert claim_b.json()["actions"][0]["payload"]["roms"][0]["file_path"] == "A.zip"
    assert claim_b.json()["actions"][0]["payload"]["roms"][0]["devices"] == [{"device_id": "drone-a", "device_name": "Drone A"}]
    activity_a = client.get("/api/devices/drone-a/sync-activity", headers={"Authorization": f"Bearer {token}"}).json()["activity"]
    target_activity = next(row for row in activity_a if row["target_drone_id"] == "drone-a")
    assert target_activity["rom_name"] == "B.zip"
    assert target_activity["source_drone_id"] == "drone-b"
    assert target_activity["status"] == "pending"
    assert target_activity["id"] == claim_a_actions[0]["payload"]["roms"][0]["sync_id"]
    sync_notifications = [
        row for row in client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"}).json()["notifications"]
        if row["event_type"] == "sync_triggered"
    ]
    assert len(sync_notifications) == 1
    assert "syncing 2 ROM(s) for snes" in sync_notifications[0]["message"]


def test_sync_system_queues_only_roms_from_resolvable_sources(client):
    client.post("/api/auth/register", json={"email": "system-sync@example.com", "username": "system-sync-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "system-sync@example.com", "username": "system-sync-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("system-sync@example.com")
    db.create_device(user["id"], "good-source", "Good Source", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "blocked-source", "Blocked Source", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-system", "Target System", {"ip_address": "10.0.0.4"}, raw_token="c")
    mark_source_resolvable("good-source")
    db.add_roms("good-source", "snes", [{"rom_name": "Good.zip", "file_path": "Good.zip", "rom_fingerprint": "good"}])
    db.add_roms("blocked-source", "snes", [{"rom_name": "Blocked.zip", "file_path": "Blocked.zip", "rom_fingerprint": "blocked"}])

    response = client.post(
        "/api/devices/target-system/sync-system",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "snes"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artwork_action_count"] == 0
    assert payload["artwork_actions"] == []
    roms = payload["action"]["payload"]["roms"]
    assert [row["file_path"] for row in roms] == ["Good.zip"]
    assert roms[0]["devices"] == [{"device_id": "good-source", "device_name": "Good Source"}]


def test_sync_system_uses_peer_check_resolvability_for_sources(client):
    client.post("/api/auth/register", json={"email": "probe-sync@example.com", "username": "probe-sync-at-example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "probe-sync@example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("probe-sync@example.com")
    db.create_device(user["id"], "fresh-source", "Fresh Source", {"network": {"public_ip": "8.8.8.8"}, "api_port": 443, "scheme": "https"}, raw_token="a")
    db.create_device(user["id"], "fresh-target", "Fresh Target", {"ip_address": "10.0.0.4"}, raw_token="b")
    db.add_roms("fresh-source", "fbneo", [{"rom_name": "Game.zip", "file_path": "Game.zip", "rom_fingerprint": "good"}])

    # Without a peer check, the source should not be offered
    response_before = client.post(
        "/api/devices/fresh-target/sync-system",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "fbneo"},
    )
    assert response_before.status_code == 400

    # After a passing peer check, the source becomes available
    mark_source_resolvable("fresh-source", "8.8.8.8")
    response_after = client.post(
        "/api/devices/fresh-target/sync-system",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "fbneo"},
    )
    assert response_after.status_code == 200
    roms = response_after.json()["action"]["payload"]["roms"]
    assert roms[0]["devices"] == [{"device_id": "fresh-source", "device_name": "Fresh Source"}]
    assert db.is_drone_peer_resolvable("fresh-source") is True


def test_set_volume_action_accepts_level_payload(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    create_response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "set_volume", "payload": {"level": 50}},
    )
    assert create_response.status_code == 200
    assert create_response.json()["action"]["action"] == "set_volume"

    claim_response = client.post(
        "/api/devices/arcade-cabinet-001/actions/claim",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={},
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["action"]["payload"]["level"] == 50


def test_unsupported_device_action_is_rejected(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "do_something_unsupported"},
    )
    assert response.status_code == 400


def test_get_device_actions_recency_window(client):
    seed_test_fleet()
    device = db.get_device_by_device_id("arcade-cabinet-001")
    user_id = device["user_id"]

    action = db.create_device_action(user_id, "arcade-cabinet-001", "restart", {})
    db.complete_device_action("arcade-cabinet-001", action["id"], "completed", message="done")

    # Recently completed actions appear only when include_recent is requested.
    recent = db.get_device_actions(user_id, "arcade-cabinet-001", include_recent=True)
    assert any(a["id"] == action["id"] for a in recent)
    active_only = db.get_device_actions(user_id, "arcade-cabinet-001")
    assert all(a["id"] != action["id"] for a in active_only)

    # Once it ages past the window it drops off even with include_recent.
    stored = next(a for a in db.device_actions[device["id"]] if a["id"] == action["id"])
    stored["completed_at"] = datetime.utcnow() - timedelta(hours=2)
    aged = db.get_device_actions(user_id, "arcade-cabinet-001", include_recent=True)
    assert all(a["id"] != action["id"] for a in aged)


def test_expire_stale_device_actions_marks_in_progress_as_failed(client):
    seed_test_fleet()
    device = db.get_device_by_device_id("arcade-cabinet-001")
    user_id = device["user_id"]

    stale = db.create_device_action(user_id, "arcade-cabinet-001", "set_screen_mode", {"mode": "kiosk"})
    fresh = db.create_device_action(user_id, "arcade-cabinet-001", "restart", {})
    db.claim_pending_device_actions("arcade-cabinet-001")  # both -> in_progress
    stored = {a["id"]: a for a in db.device_actions[device["id"]]}
    stored[stale["id"]]["claimed_at"] = datetime.utcnow() - timedelta(minutes=20)

    expired = db.expire_stale_device_actions(600)
    assert expired == 1
    assert stored[stale["id"]]["status"] == "failed"
    assert "timed out" in (stored[stale["id"]]["message"] or "").lower()
    # The recently claimed action is left running.
    assert stored[fresh["id"]]["status"] == "in_progress"

    # The timed-out action remains visible (as failed) in the recent-actions listing.
    listed = db.get_device_actions(user_id, "arcade-cabinet-001", include_recent=True)
    failed = [a for a in listed if a["id"] == stale["id"]]
    assert failed and failed[0]["status"] == "failed"


def test_poll_device_status_job_expires_stale_actions(client):
    from overmind import main

    seed_test_fleet()
    device = db.get_device_by_device_id("arcade-cabinet-001")
    user_id = device["user_id"]
    action = db.create_device_action(user_id, "arcade-cabinet-001", "set_screen_mode", {"mode": "kiosk"})
    db.claim_pending_device_actions("arcade-cabinet-001")
    stored = next(a for a in db.device_actions[device["id"]] if a["id"] == action["id"])
    stored["claimed_at"] = datetime.utcnow() - timedelta(hours=1)

    main.poll_device_status_notifications_once()

    assert stored["status"] == "failed"


def test_device_action_lifecycle(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    create_response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "restart"},
    )
    assert create_response.status_code == 200
    action_id = create_response.json()["action"]["id"]

    claim_response = client.post(
        "/api/devices/arcade-cabinet-001/actions/claim",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={},
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["action"]["id"] == action_id
    assert claim_response.json()["action"]["status"] == "in_progress"

    complete_response = client.post(
        f"/api/devices/arcade-cabinet-001/actions/{action_id}/complete",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "status": "completed",
            "message": "Restart scheduled",
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.json() == {"status": "accepted"}
    actions_response = client.get(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Recently completed actions remain visible (last hour) so an operator can confirm
    # the queued action was picked up and finished, instead of it silently vanishing.
    completed = [action for action in actions_response.json()["actions"] if action["id"] == action_id]
    assert len(completed) == 1
    assert completed[0]["status"] == "completed"
    action_notifications = [
        row
        for row in client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"}).json()["notifications"]
        if row["event_type"] == "device_action_completed" and row["details"].get("action_id") == action_id
    ]
    assert len(action_notifications) == 1
    assert "Remote Restart completed" in action_notifications[0]["message"]

    retry_response = client.post(
        f"/api/devices/arcade-cabinet-001/actions/{action_id}/complete",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={"status": "completed", "message": "Restart scheduled"},
    )
    assert retry_response.status_code == 200
    action_notifications = [
        row
        for row in client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"}).json()["notifications"]
        if row["event_type"] == "device_action_completed" and row["details"].get("action_id") == action_id
    ]
    assert len(action_notifications) == 1


def test_device_action_completion_uses_postgres_store_when_available(client, monkeypatch):
    device = {
        "id": "internal-drone",
        "device_id": "pg-drone",
        "device_name": "PG Drone",
        "user_id": "owner",
        "swarm_id": "swarm-1",
        "approval_status": "approved",
    }
    calls = {}

    def complete(device_id, action_id, status, message, result):
        calls["args"] = (device_id, action_id, status, message, result)
        return {
            "id": action_id,
            "device_id": device_id,
            "action": "restart",
            "status": status,
            "message": message,
            "result": result,
        }

    monkeypatch.setattr(db_module.postgres_store, "available", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "get_device_by_device_id", lambda device_id: device if device_id == "pg-drone" else None)
    monkeypatch.setattr(db_module.postgres_store, "complete_device_action", complete)

    action = db.complete_device_action("pg-drone", "action-1", "completed", "Reboot command issued.", {"type": "restart"})

    assert action["id"] == "action-1"
    assert calls["args"] == ("pg-drone", "action-1", "completed", "Reboot command issued.", {"type": "restart"})
    notifications = db.notifications["swarm-1"]
    assert notifications[-1]["event_type"] == "device_action_completed"
    assert notifications[-1]["details"]["action_id"] == "action-1"


def test_sync_action_completion_does_not_create_per_artwork_notifications(client):
    user_id = db.create_user("sync-actions@example.com", "hash")
    internal_id = db.create_device(user_id, "sync-drone", "Sync Drone", {"ip_address": "10.0.0.2"}, raw_token="token")
    device = db.devices[internal_id]
    before = len(db.notifications.get(device["swarm_id"], []))
    actions = [
        db.create_device_action(user_id, "sync-drone", "sync_rom", {"system_name": "fbneo", "file_path": "1943.zip"}),
        db.create_device_action(user_id, "sync-drone", "sync_artwork", {"system_name": "fbneo", "rom_path": "1943.zip", "artwork_type": "image"}),
        db.create_device_action(user_id, "sync-drone", "sync_artwork", {"system_name": "fbneo", "rom_path": "1943.zip", "artwork_type": "marquee"}),
    ]

    for action in actions:
        db.complete_device_action("sync-drone", action["id"], "completed", "queued", {"type": action["action"]})

    notifications = db.notifications.get(device["swarm_id"], [])
    assert len(notifications) == before
    assert not any(
        row["event_type"] == "device_action_completed" and row["details"].get("action") in {"sync_rom", "sync_artwork"}
        for row in notifications
    )


def test_action_results_store_raw_logs_for_selected_drone_view(client):
    user_id = db.create_user("logs@example.com", "hash")
    internal_id = db.create_device(user_id, "log-drone", "Log Drone", {"ip_address": "10.0.0.2"}, raw_token="token")
    device = db.get_device(internal_id)

    db.store_action_result(device, {
        "type": "log_sources",
            "logs": [{"source": "drone_stderr", "files": [{"path": "/tmp/drone.err", "content": "line-1\nline-2"}]}],
        })
    db.store_action_result(device, {
        "type": "log_sources",
        "logs": [{"source": "drone_stderr", "files": [{"path": "/tmp/drone.err", "content": "line-3"}]}],
    })

    logs = db.get_device_log_sources("log-drone", line_limit=2)
    assert logs["logs"][0]["source"] == "drone_stderr"
    assert logs["logs"][0]["files"][0]["content"] == "line-2\nline-3"


def test_game_log_result_does_not_store_raw_logs(client):
    user_id = db.create_user("es-logs@example.com", "hash")
    internal_id = db.create_device(user_id, "es-log-drone", "ES Log Drone", {"ip_address": "10.0.0.2"}, raw_token="token")
    device = db.get_device(internal_id)

    db.store_action_result(device, {
        "type": "game_logs",
        "sessions": [{"system_name": "snes", "game_name": "Game.sfc"}],
        "logs": [{"source": "drone_stdout", "files": [{"path": "/tmp/drone.log", "content": "raw\n"}]}],
    })

    assert not db.get_device_log_sources("es-log-drone")["logs"]
    assert device["game_logs"]["sessions"][0]["game_name"] == "Game.sfc"


def test_action_results_merge_configs_and_exclude_bak_files(client):
    user_id = db.create_user("configs@example.com", "hash")
    internal_id = db.create_device(user_id, "config-drone", "Config Drone", {"ip_address": "10.0.0.2"}, raw_token="token")
    device = db.get_device(internal_id)

    db.store_action_result(device, {
        "type": "emulator_configs",
        "configs": [
            {"root": "/configs", "relative_path": "retroarch/retroarch.cfg", "content": "video_driver = gl"},
            {"root": "/configs", "relative_path": "retroarch/retroarch.cfg.bak", "content": "old"},
            {"root": "/configs", "relative_path": "retroarch/log/runtime.cfg", "content": "runtime log"},
            {"root": "/configs", "relative_path": "retroarch/logs/trace.cfg", "content": "runtime logs"},
        ],
    })
    db.store_action_result(device, {
        "type": "emulator_configs",
        "configs": [{"root": "/configs", "relative_path": "dolphin.ini", "content": "backend = vulkan"}],
    })
    for index in range(12):
        db.store_action_result(device, {
            "type": "emulator_configs",
            "configs": [{"root": "/configs", "relative_path": "retroarch/retroarch.cfg", "content": f"video_driver = driver-{index}"}],
        })
    db.store_action_result(device, {"type": "emulator_configs", "configs": [], "incremental": True})

    configs = {row["relative_path"]: row["content"] for row in device["emulator_configs"]["configs"]}
    assert configs == {
        "dolphin.ini": "backend = vulkan",
        "retroarch/retroarch.cfg": "video_driver = driver-11",
    }
    retroarch = next(row for row in device["emulator_configs"]["configs"] if row["relative_path"] == "retroarch/retroarch.cfg")
    assert len(retroarch["versions"]) == 10
    assert retroarch["versions"][0]["content"] == "video_driver = driver-11"
    assert retroarch["versions"][-1]["content"] == "video_driver = driver-2"
    assert all(".bak" not in row["relative_path"] for row in device["emulator_configs"]["configs"])
    assert all("/log/" not in f"/{row['relative_path'].lower()}/" for row in device["emulator_configs"]["configs"])
    assert all("/logs/" not in f"/{row['relative_path'].lower()}/" for row in device["emulator_configs"]["configs"])


def test_postgres_state_encoding_strips_nul_bytes():
    encoded = _encode_state({"bad\x00key": [{"content": "\x00binary\x00text"}]})
    assert "\x00" not in json.dumps(encoded)
    assert encoded["badkey"][0]["content"] == "binarytext"


def test_game_log_upload_stores_sessions_for_game_log_list(client):
    user_id = db.create_user("game-log@example.com", "hash")
    db.create_device(user_id, "game-drone", "Game Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    response = client.post(
        "/api/devices/game-drone/game-logs",
        headers={"Authorization": "Bearer drone-token"},
        json={
            "type": "game_logs",
            "sessions": [{
                "played_at": "2026-05-26T10:15:00+00:00",
                "system_name": "snes",
                "game_name": "Game.sfc",
                "rom_path": "/userdata/roms/snes/Game.sfc",
                "rom_fingerprint": "abc123",
            }],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    logs = db.get_device_gamelogs("game-drone")
    assert len(logs) == 1
    assert logs[0]["system_name"] == "snes"
    assert logs[0]["game_name"] == "Game.sfc"
    assert logs[0]["rom_fingerprint"] == "abc123"
    assert logs[0]["played_at"] == "2026-05-26T10:15:00+00:00"


def test_game_log_retry_updates_session_without_creating_duplicate(client):
    user_id = db.create_user("game-log-retry@example.com", "hash")
    db.create_device(user_id, "game-retry-drone", "Game Retry Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")
    session = {
        "played_at": "2026-05-26T10:15:00+00:00",
        "system_name": "snes",
        "game_name": "Game.sfc",
        "rom_path": "/userdata/roms/snes/Game.sfc",
    }

    for payload in [
        session,
        {**session, "rom_fingerprint": "abc123", "duration_seconds": 90},
    ]:
        response = client.post(
            "/api/devices/game-retry-drone/game-logs",
            headers={"Authorization": "Bearer drone-token"},
            json={"type": "game_logs", "sessions": [payload]},
        )
        assert response.status_code == 200

    logs = db.get_device_gamelogs("game-retry-drone")
    assert len(logs) == 1
    assert logs[0]["rom_fingerprint"] == "abc123"
    assert logs[0]["duration_seconds"] == 90
    snapshot = db.get_device_by_device_id("game-retry-drone")["game_logs"]["sessions"]
    assert len(snapshot) == 1
    assert snapshot[0]["duration_seconds"] == 90


def test_gameplay_history_is_a_table_log_source():
    source = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert "label: 'Gameplay History'" in source
    assert "type: 'gameplay'" in source
    assert "overmindGameplayViewer" in source
    assert "gameplayViewer.innerHTML = renderGameplayTable(source.gamelogs)" in source


def test_log_source_upload_persists_and_streams_while_view_requested(client):
    client.post("/api/auth/register", json={"email": "log-upload@example.com", "username": "log-upload-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "log-upload@example.com", "username": "log-upload-at-example.com", "password": "testpass123"}).json()["access_token"]
    user_id = db.get_user_by_email("log-upload@example.com")["id"]
    db.create_device(user_id, "log-upload-drone", "Log Upload Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    inactive = client.post(
        "/api/devices/log-upload-drone/log-sources",
        headers={"Authorization": "Bearer drone-token"},
        json={"logs": [{"source": "drone_stdout", "files": [{"path": "/tmp/drone.log", "content": "ignored\n"}]}]},
    )
    assert inactive.status_code == 200
    stored = db.get_device_log_sources("log-upload-drone")
    assert stored["logs"][0]["files"][0]["content"] == "ignored"

    view = client.post("/api/devices/log-upload-drone/log-stream/view", headers={"Authorization": f"Bearer {token}"})
    assert view.status_code == 200
    heartbeat = client.post(
        "/api/devices/log-upload-drone/heartbeat",
        headers={"Authorization": "Bearer drone-token"},
        json={"device_name": "Log Upload Drone"},
    )
    assert heartbeat.json()["log_stream_requested"] is True
    active = client.post(
        "/api/devices/log-upload-drone/log-sources",
        headers={"Authorization": "Bearer drone-token"},
        json={"logs": [{"source": "drone_stdout", "files": [{"path": "/tmp/drone.log", "content": "line-1\n"}]}]},
    )
    assert active.status_code == 200
    device_response = client.get("/api/devices/log-upload-drone", headers={"Authorization": f"Bearer {token}"})
    assert device_response.status_code == 200
    assert device_response.json()["log_stream_active"] is True
    assert device_response.json()["log_sources"]["logs"][0]["files"][0]["content"] == "line-1\n"
    persisted = db.get_device_log_sources("log-upload-drone", line_limit=10)
    assert persisted["logs"][0]["files"][0]["content"] == "ignored\nline-1"


def test_log_source_upload_accepts_drone_incremental_fields_and_marks_online(client):
    user_id = db.create_user("log-upload-online@example.com", "hash")
    internal_id = db.create_device(user_id, "log-online-drone", "Log Online Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")
    db.devices[internal_id]["last_seen"] = datetime.utcnow() - timedelta(seconds=999)
    db.devices[internal_id]["last_known_status"] = "offline"

    response = client.post(
        "/api/devices/log-online-drone/log-sources",
        headers={"Authorization": "Bearer drone-token"},
        json={
            "type": "log_sources",
            "collected_at": "2026-05-31T02:31:51+00:00",
            "append": True,
            "logs": [{"source": "drone_stdout", "files": [{"path": "/tmp/drone.log", "content": "line-1\n"}]}],
        },
    )
    assert response.status_code == 200
    device = db.get_device_by_device_id("log-online-drone")
    assert device["last_known_status"] == "online"
    assert device["last_seen"] >= datetime.utcnow() - timedelta(seconds=5)
    assert db.get_device_log_sources("log-online-drone")["logs"][0]["source"] == "drone_stdout"


def test_emulator_config_upload_stores_changed_configs(client):
    user_id = db.create_user("config-upload@example.com", "hash")
    db.create_device(user_id, "config-upload-drone", "Config Upload Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    response = client.post(
        "/api/devices/config-upload-drone/emulator-configs",
        headers={"Authorization": "Bearer drone-token"},
        json={
            "type": "emulator_configs",
            "incremental": True,
            "configs": [{"root": "/configs", "relative_path": "retroarch.cfg", "content": "video_driver = vulkan", "md5": "abc123", "fingerprint": "abc123"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    device = db.get_device_by_device_id("config-upload-drone")
    assert device["emulator_configs"]["configs"][0]["relative_path"] == "retroarch.cfg"
    assert device["emulator_configs"]["configs"][0]["md5"] == "abc123"
    assert device["emulator_configs"]["configs"][0]["versions"][0]["content"] == "video_driver = vulkan"


def test_emulator_config_upload_writes_relational_store_when_available(client, monkeypatch):
    device = {
        "id": "internal-config-drone",
        "device_id": "config-pg-drone",
        "device_name": "Config PG Drone",
        "user_id": "owner",
        "swarm_id": "swarm-1",
        "approval_status": "approved",
    }
    calls = []

    monkeypatch.setattr(db_module.postgres_store, "available", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "get_device_by_device_id", lambda device_id: device if device_id == "config-pg-drone" else None)
    monkeypatch.setattr(db_module.postgres_store, "store_device_emulator_configs", lambda internal_id, payload: calls.append((internal_id, payload)))

    db.store_action_result(
        device,
        {
            "type": "emulator_configs",
            "incremental": True,
            "configs": [{"root": "/configs", "relative_path": "retroarch.cfg", "content": "video_driver = gl", "md5": "hash-1"}],
        },
    )

    assert calls == [
        (
            "internal-config-drone",
            {
                "type": "emulator_configs",
                "incremental": True,
                "configs": [{"root": "/configs", "relative_path": "retroarch.cfg", "content": "video_driver = gl", "md5": "hash-1"}],
            },
        )
    ]


def test_selected_drone_config_view_reads_relational_store(client, monkeypatch):
    user_id = db.create_user("config-view@example.com", auth_utils.hash_password("testpass123"), verified=True, username="config-view-at-example.com")
    db.create_device(user_id, "config-view-drone", "Config View Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")
    token = client.post(
        "/api/auth/login",
        json={"email": "config-view@example.com", "username": "config-view-at-example.com", "password": "testpass123"},
    ).json()["access_token"]
    relational_payload = {
        "type": "emulator_configs",
        "configs": [
            {
                "root": "/userdata/system/configs",
                "relative_path": "retroarch/retroarch.cfg",
                "content": "video_driver = vulkan",
                "fingerprint": "hash-2",
                "fingerprint": "hash-2",
                "versions": [{"content": "video_driver = vulkan", "fingerprint": "hash-2"}],
            },
            {
                "root": "/userdata/system/configs",
                "relative_path": "retroarch/log/runtime.cfg",
                "content": "runtime log",
                "fingerprint": "hash-log",
                "fingerprint": "hash-log",
            },
            {
                "root": "/userdata/system/configs",
                "relative_path": "retroarch/logs/trace.cfg",
                "content": "runtime logs",
                "fingerprint": "hash-logs",
                "fingerprint": "hash-logs",
            }
        ],
    }

    monkeypatch.setattr(db_module.postgres_store, "available", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "get_device_emulator_configs", lambda internal_id: relational_payload)

    response = client.get("/api/devices/config-view-drone", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    configs = response.json()["emulator_configs"]["configs"]
    rel_paths = [row["relative_path"] for row in configs]
    # The uploaded config is present with its content; log/logs paths are excluded.
    uploaded = next(row for row in configs if row["relative_path"] == "retroarch/retroarch.cfg")
    assert uploaded["content"] == "video_driver = vulkan"
    assert uploaded["present"] is True
    assert "retroarch/log/runtime.cfg" not in rel_paths
    assert "retroarch/logs/trace.cfg" not in rel_paths
    # The curated managed-config registry is always shown, including absent ones.
    assert "batocera.conf" in rel_paths
    dolphin = next(row for row in configs if row["relative_path"] == "dolphin-emu/Dolphin.ini")
    assert dolphin["present"] is False and dolphin["version_count"] == 0


def test_selected_drone_empty_metadata_states_explain_waiting_for_drone():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    css = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/css/overmind.css").read_text(encoding="utf-8")
    assert "Waiting for Drone to upload" in js
    assert "Waiting for Drone to upload artwork metadata" in js
    assert js.count("renderDroneMetadataWaitingState('System & Roms metadata')") >= 2
    assert "renderDroneMetadataWaitingState('BIOS metadata')" in js
    assert "renderDroneMetadataWaitingState('artwork metadata')" in js
    assert "Request System & Rom Data" in js
    assert "queueDeviceAction(\\'rebuild_asset_metadata\\')" in js
    assert "Auto-sync ROM metadata from this Drone" not in js
    assert "overmindConfigVersion" in js
    assert "downloadSelectedOvermindConfigVersion" in js
    assert "overmindConfigFilter" in js
    assert "filterOvermindConfigs" in js
    assert "config-source-scroll" in js
    assert ".config-source-scroll" in css
    assert "max-height: 520px" in css
    assert "update automatically every 30 seconds" in js
    assert "Collect Configs" not in js


def test_action_claim_returns_all_pending_actions_in_order(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]
    first = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "collect_game_logs"},
    ).json()["action"]
    second = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "collect_emulator_configs"},
    ).json()["action"]

    claim_response = client.post(
        "/api/devices/arcade-cabinet-001/actions/claim",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={},
    )

    assert claim_response.status_code == 200
    actions = claim_response.json()["actions"]
    assert [action["id"] for action in actions] == [first["id"], second["id"]]
    assert all(action["status"] == "in_progress" for action in actions)
    assert claim_response.json()["action"]["id"] == first["id"]


def test_shutdown_action_is_rejected_by_api(client):
    seed_test_fleet()
    token = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    ).json()["access_token"]

    response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "shutdown"},
    )

    assert response.status_code == 400


def test_screen_mode_action_is_supported_and_validated(client):
    seed_test_fleet()
    token = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for mode in ("full", "kiosk", "kid"):
        response = client.post(
            "/api/devices/arcade-cabinet-001/actions",
            headers=headers,
            json={"action": "set_screen_mode", "payload": {"mode": mode}},
        )
        assert response.status_code == 200
        assert response.json()["action"]["action"] == "set_screen_mode"
        assert response.json()["action"]["payload"]["mode"] == mode

    invalid_mode_response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers=headers,
        json={"action": "set_screen_mode", "payload": {"mode": "arcade"}},
    )
    assert invalid_mode_response.status_code == 400

    update_response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers=headers,
        json={"action": "update"},
    )
    assert update_response.status_code == 400


def test_delete_actions_clears_device_queue(client):
    seed_test_fleet()
    token = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers=headers,
        json={"action": "refresh_emulator_list"},
    ).status_code == 200
    assert client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers=headers,
        json={"action": "set_screen_mode", "payload": {"mode": "full"}},
    ).status_code == 200

    response = client.delete("/api/devices/arcade-cabinet-001/actions", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "deleted_count": 2}
    assert client.get("/api/devices/arcade-cabinet-001/actions", headers=headers).json()["actions"] == []


def test_rebuild_asset_metadata_action_is_supported(client):
    seed_test_fleet()
    token = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    ).json()["access_token"]
    response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "rebuild_asset_metadata"},
    )
    assert response.status_code == 200
    assert response.json()["action"]["action"] == "rebuild_asset_metadata"


def test_rebuild_asset_metadata_action_clears_existing_device_assets(client):
    client.post(
        "/api/auth/register",
        json={"email": "asset-clear@example.com", "username": "asset-clear", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "asset-clear@example.com", "username": "asset-clear", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("asset-clear@example.com")
    db.create_device(user["id"], "clear-drone", "Clear Drone", {})
    db.add_roms("clear-drone", "snes", [{"rom_name": "Game.zip", "file_path": "Game.zip", "file_size": 3}])
    db.add_bios("clear-drone", [{"bios_name": "bios.bin", "file_path": "bios.bin"}])
    db.add_artwork("clear-drone", [{"system": "snes", "rom_path": "Game.zip", "artwork_types": ["image"]}])
    device = db.get_device_by_device_id("clear-drone")
    device["rom_metadata"] = {"systems": [{"name": "snes", "rom_count": 1}]}
    device["rom_systems"] = [{"name": "snes", "rom_count": 1}]

    response = client.post(
        "/api/devices/clear-drone/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "rebuild_asset_metadata"},
    )

    assert response.status_code == 200
    assert db.get_device_roms("clear-drone") == []
    assert db.get_device_bios("clear-drone") == []
    assert db._asset_rows_for_device_internal(device["id"], "artwork") == []
    assert device["rom_metadata"] == {}
    assert db.get_user_systems_summary(user["id"]) == []


def test_asset_metadata_delta_upserts_existing_rows_without_duplicate_system_counts(client):
    client.post(
        "/api/auth/register",
        json={"email": "asset-upsert@example.com", "username": "asset-upsert", "password": "testpass123"},
    )
    user = db.get_user_by_email("asset-upsert@example.com")
    db.create_device(user["id"], "upsert-drone", "Upsert Drone", {})

    db.store_rom_metadata("upsert-drone", {
        "type": "asset_metadata",
        "update_mode": "inventory_delta",
        "systems": [{"name": "snes", "rom_count": 1}],
        "roms": [{"system": "snes", "rom_name": "Game", "file_path": "Game.zip", "file_size": 3}],
    })
    db.store_rom_metadata("upsert-drone", {
        "type": "asset_metadata",
        "update_mode": "inventory_delta",
        "systems": [{"name": "snes", "rom_count": 1}],
        "roms": [{"system": "snes", "rom_name": "Game Updated", "file_path": "Game.zip", "file_size": 4}],
    })

    roms = db.get_device_roms("upsert-drone")
    assert len(roms) == 1
    assert roms[0]["rom_name"] == "Game Updated"
    assert db.get_user_systems_summary(user["id"]) == [{"system_name": "snes", "rom_count": 1, "device_count": 1}]


def test_selected_drone_contextual_actions_ui_omits_shutdown_and_collect_data_buttons():
    html = Path(__file__).resolve().parents[1].joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert "queueDeviceAction('shutdown')" not in html
    assert ">Shutdown<" not in html
    assert "queueDeviceAction('update')" not in html
    assert ">Update<" not in html
    assert "data-device-view=\"actions\"" not in html
    assert "device-actions-panel" not in html
    assert "queueDeviceAction('restart'" in js
    assert "rebuild_asset_metadata" in html
    assert "refresh_emulator_list" in js
    assert "Rebuild Asset Metadata" in html
    # Screen mode is an explicit three-value action in the Admin tab.
    assert "deviceKioskToggle" not in js
    assert "queueKioskMode(this.checked)" not in js
    assert "data-device-view=\"admin\"" in html
    assert "function renderDeviceAdminPanel()" in js
    assert "queueDeviceScreenMode('${item.mode}')" in js
    assert "queueDeviceAction('set_screen_mode'" in js
    assert "queueDeviceAction('enable_kiosk'" not in js
    assert "queueDeviceAction('disable_kiosk'" not in js
    assert "queueDeviceVolume(" in js
    assert '<table class="table table-sm align-middle">' in js
    assert "deleteDeviceActions()" not in html
    assert "onclick=\"queueDeviceAction('collect_game_logs')\"" not in html
    assert "onclick=\"queueDeviceAction('collect_emulator_configs')\"" not in html
    assert "onclick=\"queueDeviceAction('collect_log_sources')\"" not in html
    assert "requestDeviceDataSnapshot" not in js
    assert "drone-auto-sync-panel" not in html
    assert "Auto-sync ROM metadata from this Drone" not in js
    assert "loadGameLogs({queue:" not in js
    assert "loadDeviceConfigs({queue:" not in js
    assert "if (currentDeviceView === 'gamelogs') loadGameLogs({showLoader: false});" in js
    assert "if (currentDeviceView === 'configs') loadDeviceConfigs({showLoader: false});" in js


def test_selected_drone_logs_auto_refresh_updates_existing_view_in_place():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "function renderCombinedLogsShell()" in js
    assert "const shellExists = Boolean(document.getElementById('overmindLogContent'));" in js
    assert "if (!shellExists) {" in js
    assert "selectOvermindLogSource(selectedIndex, shellExists);" in js
    assert "[10, 20, 50, 100]" in js
    assert "log_limit=${encodeURIComponent(logLimit)}" in js
    assert "if (content.textContent !== nextContent) {" in js
    assert "newestLogLinesFirst(source.content, getOvermindLogLineLimit())" in js
    assert "async function loadGameLogs(options = {})" in js
    assert "apiGet(`/api/devices/${deviceId}/gamelogs`, { showLoader: options.showLoader !== false })" in js
    assert "file.content || file.path" not in js
    assert "No log output reported yet." in js
    assert "join('\\\\n\\\\n')" not in js


def test_background_device_refresh_updates_ui_without_rebuilding_current_view():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "const background = options.background === true;" in js
    assert "if (background) {" in js
    assert "updateDevicesListInPlace();" in js
    assert "updateSelectedDeviceHeader();" in js
    assert "updateDeviceAdminStatusInPlace();" in js
    assert "function updateDeviceTileBadge(" in js
    assert "function updateDevicesListInPlace()" in js
    assert "function updateDeviceAdminStatusInPlace()" in js
    assert "data-device-field=\"last-seen\"" in js
    assert 'data-device-admin-field="screen-mode"' in js
    assert 'data-device-admin-field="volume"' in js


def test_navigation_renders_once_through_hash_route():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    select_start = js.index("function selectDevice(deviceId)")
    select_source = js[select_start:js.index("async function loadGameLogs", select_start)]
    assert "setRoute('devices', deviceId, 'systems');" in select_source
    assert "updateSelectedDeviceWorkspace();" not in select_source
    assert "switchDeviceView('systems'" not in select_source

    route_start = js.index("function applyRouteFromHash()")
    route_source = js[route_start:js.index("async function loadDeviceActions", route_start)]
    assert "switchTab(route.tab, null, false);" in route_source
    assert "updateSelectedDeviceWorkspace();" not in route_source

    assert "if (updateUrl) {\n                    setRoute('devices', selectedDeviceId, currentDeviceView);\n                    return;" in js
    assert "if (updateUrl) {\n                    setRoute(tabName);\n                    return;" in js


def test_admin_action_refresh_skips_unchanged_data_and_preserves_open_results():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "async function loadDeviceActions(options = {})" in js
    assert "const signature = JSON.stringify(actions);" in js
    assert "renderedDeviceActionsSignature === signature && container.innerHTML.trim()" in js
    assert "details[data-action-result-id][open]" in js
    assert 'data-action-result-id="${cssSafeId(action.id)}"' in js
    assert "if (openResultIds.has(details.dataset.actionResultId)) details.open = true;" in js


def test_periodic_download_and_config_refreshes_preserve_rendered_ui():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "apiGet('/api/downloads', { showLoader: !options.quiet })" in js
    assert "function updateSwarmDownloadsInPlace(rows)" in js
    assert "if (options.quiet && updateSwarmDownloadsInPlace(rows)) {" in js
    assert 'data-download-field="progress-bar"' in js
    assert "async function loadDeviceConfigs(options = {})" in js
    assert "if (container.dataset.configSignature === signature) return;" in js
    assert "loadDeviceConfigs({showLoader: false})" in js


def test_metadata_panels_submit_searches_explicitly_and_show_loading_toast():
    root = Path(__file__).resolve().parents[1]
    html = root.joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    css = root.joinpath("src/overmind/static/css/overmind.css").read_text(encoding="utf-8")
    js = root.joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert 'id="ui-loading-popout"' not in html
    assert ".ui-loading-popout.show" not in css
    assert ".toast-alert.alert-loading" in css
    assert "function beginUiLoading(message = 'Loading...')" in js
    assert "function endUiLoading()" in js
    assert "function showToast(" in js
    assert "function showLoadingToast(" in js
    assert "function loadingMessageForPath(path)" in js
    assert "Loading devices..." in js
    assert "Loading ROMs..." in js
    assert "Loading notifications..." in js
    assert 'onclick="submitDeviceRomSearch()"' in html
    assert 'oninput="handleDeviceRomSearch(event)"' not in html
    assert "function submitBiosSearch()" in js
    assert "function submitArtworkSearch()" in js
    assert 'oninput="handleBiosSearch(event)"' not in js
    assert 'oninput="handleArtworkSearch(event)"' not in js


def test_swarm_drone_tile_shows_batocera_version_instead_of_drone_id_label():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    start = js.index("function displayDevices()")
    tile_renderer = js[start:js.index("function showSwarmHome", start)]

    assert "Drone ID" not in tile_renderer
    assert "Batocera: ${escapeHtml((device.system_info || {}).batocera_version || 'n/a')}" in tile_renderer
    assert "ROM Files: ${Number(device.rom_count || 0).toLocaleString()}" in tile_renderer
    assert "Games: ${Number(device.game_count" in tile_renderer
    assert "'Resolvable'" in tile_renderer
    assert "'Not Resolvable'" in tile_renderer
    # Swarm tile Resolvable/Not Resolvable badge is driven by Overmind's own public
    # reachability probe, not peer-to-peer checks.
    assert "device.public_reachability && device.public_reachability.resolvable" in tile_renderer


def test_profile_swarm_access_exposes_remove_overseer_action():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "m.role === 'overseer'" in js
    assert "Remove Overseer" in js
    assert "Remove this Overseer from the swarm?" in js


def test_profile_swarm_access_exposes_pending_invite_resend_action():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "i.status === 'pending'" in js
    assert "Resend Invite" in js
    assert "function resendOverseerInvite(invitationId)" in js
    assert "/invitations/${encodeURIComponent(invitationId)}/resend" in js
    assert "previous invitation link will no longer work" in js


def test_profile_swarm_access_exposes_pending_invite_remove_action():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "Remove Invite" in js
    assert "function removePendingOverseerInvite(invitationId)" in js
    assert "/invitations/${encodeURIComponent(invitationId)}`" in js
    assert "invitation link will no longer work" in js


def test_drone_metadata_shows_resolvable_public_ip_state():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "const publicIpStatus = device.peer_resolvable ? ' (peer-resolvable)' : '';" in js
    assert "Public IP: ${escapeHtml(publicIp)}${publicIpStatus}" in js
    metadata_start = js.index("function renderDroneMetadataPanel()")
    metadata_end = js.index("async function loadSwarmRomAvailabilityPanel()", metadata_start)
    metadata_source = js[metadata_start:metadata_end]
    assert "Performance Metrics" not in metadata_source
    assert "renderMetricsGrid(info.performance || {})" in js
    assert "async function refreshSelectedDroneDetails()" in js
    assert "<strong>Asset Cache</strong>" not in js


def test_super_admin_runtime_metrics_ui_refreshes_when_viewed():
    root = Path(__file__).resolve().parents[1]
    html = root.joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    js = root.joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert 'id="super-admin-metrics"' in html
    assert 'id="super-admin-logs"' in html
    assert "Pending Drone Connections" in js
    assert "pending_connections" in js
    assert "assignPendingDroneToSwarm" in js
    assert "/api/admin/drone-connections/" in js
    assert "apiGet('/api/admin/runtime-metrics'" in js
    assert "apiGet('/api/admin/runtime-logs'" in js
    assert "Overmind Runtime Logs" in js
    assert "newestLogLinesFirst(logs.stdout" in js
    assert "newestLogLinesFirst(logs.stderr" in js
    assert "setInterval(() =>" in js
    assert "5000" in js


def test_signup_form_requires_username_and_posts_it():
    root = Path(__file__).resolve().parents[1]
    html = root.joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    js = root.joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert 'id="register-username" maxlength="80" required' in html
    assert "const username = document.getElementById('register-username').value.trim();" in js
    assert "JSON.stringify({ email, username, password, invitation_token: pendingInvitationToken })" in js


def test_invite_registration_ui_clears_pending_token_before_login():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert "pendingInvitationToken = null;" in js
    assert "sessionStorage.removeItem('pending_invitation_token');" in js
    assert "Registration complete. Sign in to view the swarm." in js


def test_shared_swarm_navigation_state_is_reflected_in_ui_routes():
    html = Path(__file__).resolve().parents[1].joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert "document.addEventListener('DOMContentLoaded', async () =>" in js
    assert "routeSwarmId = parseRoute().swarmId || null;" in js
    assert "await loadSwarms();" in js
    assert "goToMySwarm()" in js
    assert "shared-swarm-nav-btn" in html
    assert "openSelectedSharedSwarm()" in html
    assert "`#/devices${swarmPath}/swarm/${swarmView}`" in js
    assert "`#/devices${swarmPath}/device/${encodeURIComponent(deviceId)}/${deviceView || 'systems'}`" in js
    assert "parts[3] === 'swarm'" in js
    assert "parts[3] === 'device'" in js
    assert "row.can_view && !row.current" in js
    assert "Use My Swarm to view your own swarm." in js


def test_master_list_refreshes_devices_without_reapplying_route():
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")

    assert "async function loadDevices(options = {})" in js
    assert "const applyRoute = options.applyRoute !== false;" in js
    assert "if (applyRoute) {" in js
    assert "applyRouteFromHash();" in js
    assert "if (!currentDevices.length) await loadDevices({applyRoute: false});" in js


def test_drone_alive_claims_data_action_and_stores_result(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    create_response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "collect_rom_metadata"},
    )
    assert create_response.status_code == 200
    action_id = create_response.json()["action"]["id"]

    heartbeat_response = client.post(
        "/api/devices/arcade-cabinet-001/heartbeat",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "network": {"ipv4": ["192.168.1.50"], "ipv6": ["::1"]},
            "rom_systems": [{"name": "snes"}],
        },
    )
    assert heartbeat_response.status_code == 200
    assert heartbeat_response.json()["actions"][0]["id"] == action_id
    assert heartbeat_response.json()["actions"][0]["status"] == "in_progress"

    result = {
        "type": "rom_metadata",
        "systems": [{"name": "snes"}],
        "roms": [{"system": "snes", "rom_name": "Super Metroid"}],
        "gamelists": [{"system": "snes", "content": "<gameList />"}],
    }
    complete_response = client.post(
        f"/api/devices/arcade-cabinet-001/actions/{action_id}/complete",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "status": "completed",
            "message": "Collected 1 system.",
            "result": result,
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.json() == {"status": "accepted"}
    action = next(
        row
        for row in db.device_actions[db.get_device_by_device_id("arcade-cabinet-001")["id"]]
        if row["id"] == action_id
    )
    assert action["result"] == result
    assert action["result_received_at"] is not None


def test_alive_stores_system_info_and_peer_detail_is_latest(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    heartbeat_response = client.post(
        "/api/devices/arcade-cabinet-001/heartbeat",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "network": {"ipv4": ["192.168.1.50"]},
            "rom_systems": [{"name": "snes"}],
            "system_info": {
                "hostname": "arcade-alpha",
                "architecture": "x86_64",
                "container": True,
                "performance": {"cpu": {"host_percent": 12.5}},
            },
        },
    )
    assert heartbeat_response.status_code == 200

    first_peer = client.post(
        "/api/devices/arcade-cabinet-001/peer-checks",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "results": [
                {
                    "source_drone_id": "arcade-cabinet-001",
                    "target_drone_id": "raspberry-pi-001",
                    "target_address": "https://old.example",
                    "status": "fail",
                    "failure_reason": "timeout",
                    "checked_at": "2026-05-18T10:00:00Z",
                },
                {
                    "source_drone_id": "arcade-cabinet-001",
                    "target_drone_id": "raspberry-pi-001",
                    "target_address": "https://new.example",
                    "status": "pass",
                    "latency_ms": 12,
                    "checked_at": "2026-05-18T10:01:00Z",
                },
            ]
        },
    )
    assert first_peer.status_code == 200

    detail_response = client.get(
        "/api/devices/arcade-cabinet-001",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["system_info"]["hostname"] == "arcade-alpha"
    assert detail["system_info"]["container"] is True
    assert detail["system_info"]["performance"]["cpu"]["host_percent"] == 12.5
    assert len(detail["peer_checks"]) == 1
    assert detail["peer_checks"][0]["status"] == "pass"
    assert detail["peer_checks"][0]["target_address"] == "https://new.example"
    assert detail["peer_checks"][0]["target_name"]


def test_swarm_response_handles_postgres_timezone_aware_last_seen(client):
    client.post("/api/auth/register", json={"email": "aware@example.com", "username": "aware-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("aware@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    db.get_device_by_device_id("drone-a")["last_seen"] = datetime.now(timezone.utc)

    swarm = db.get_swarm_for_device("drone-a")

    assert swarm[0]["drone_id"] == "drone-a"
    assert swarm[0]["online"] is True


def test_device_list_marks_postgres_timezone_aware_last_seen_online(client):
    client.post("/api/auth/register", json={"email": "aware-list@example.com", "username": "aware-list-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "aware-list@example.com", "password": "testpass123"}).json()["access_token"]
    user = db.get_user_by_email("aware-list@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="drone-token-a")
    db.get_device_by_device_id("drone-a")["last_seen"] = datetime.now(timezone.utc)

    response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    device = response.json()["devices"][0]
    assert device["online"] is True
    assert device["status"] == "online"


def test_peer_check_upload_marks_drone_resolvable(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "testpass123"}).json()["access_token"]
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "drone-a", "Drone A", {"network": {"public_ip": "8.8.8.8"}}, raw_token="token-a")
    db.create_device(owner["id"], "drone-b", "Drone B", {"network": {"public_ip": "8.8.4.4"}}, raw_token="token-b")

    # drone-a reports it can reach drone-b
    resp = client.post(
        "/api/devices/drone-a/peer-checks",
        json={"results": [{"target_drone_id": "drone-b", "status": "pass", "target_address": "https://8.8.4.4", "latency_ms": 42}]},
        headers={"Authorization": "Bearer token-a"},
    )
    assert resp.status_code == 200

    response = client.get("/api/devices/drone-b", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["public_resolvable"] is True
    assert data["peer_resolvable"] is True
    assert any(r["source_drone_id"] == "drone-a" for r in data["peer_resolved_by"])


def test_peer_check_upload_does_not_mark_drone_resolvable_on_fail(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "testpass123"}).json()["access_token"]
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "drone-a", "Drone A", {"network": {"public_ip": "8.8.8.8"}}, raw_token="token-a")
    db.create_device(owner["id"], "drone-b", "Drone B", {"network": {"public_ip": "8.8.4.4"}}, raw_token="token-b")

    client.post(
        "/api/devices/drone-a/peer-checks",
        json={"results": [{"target_drone_id": "drone-b", "status": "fail", "failure_reason": "connection refused"}]},
        headers={"Authorization": "Bearer token-a"},
    )

    response = client.get("/api/devices/drone-b", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["public_resolvable"] is False
    assert data["peer_resolvable"] is False
    assert data["peer_resolved_by"] == []


def test_peer_check_resolvability_reflected_in_swarm_list(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "drone-a", "Drone A", {"network": {"public_ip": "8.8.8.8"}}, raw_token="token-a")
    db.create_device(owner["id"], "drone-b", "Drone B", {"network": {"public_ip": "8.8.4.4"}}, raw_token="token-b")

    client.post(
        "/api/devices/drone-a/peer-checks",
        json={"results": [{"target_drone_id": "drone-b", "status": "pass", "target_address": "https://8.8.4.4"}]},
        headers={"Authorization": "Bearer token-a"},
    )

    swarm = db.get_swarm_for_device("drone-a")
    drone_b_peer = next(p for p in swarm if p["drone_id"] == "drone-b")
    assert drone_b_peer["public_resolvable"] is True
    assert drone_b_peer["public_reachable_url"] == "https://8.8.4.4"


def test_drone_without_peer_checks_not_resolvable(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "testpass123"}).json()["access_token"]
    owner = db.get_user_by_email("owner@example.com")
    db.create_device(owner["id"], "lone-drone", "Lone Drone", {"network": {"public_ip": "8.8.8.8"}}, raw_token="token-lone")

    response = client.get("/api/devices/lone-drone", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["public_resolvable"] is False
    assert data["peer_resolvable"] is False
    assert data["peer_resolved_by"] == []


def test_heartbeat_ignores_rom_metadata_and_rom_metadata_endpoint_persists(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    heartbeat_response = client.post(
        "/api/devices/arcade-cabinet-001/heartbeat",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "network": {"ipv4": ["192.168.1.50"], "ipv6": ["fd00::50"], "public_ip": "198.51.100.50", "hostname_override": "bff-drone-a"},
            "reachable_url": "https://bff-drone-a:443",
            "rom_metadata": {
                "type": "rom_metadata",
                "roms_root": "/userdata/roms",
                "systems": [{"name": "snes", "rom_count": 2}],
                "roms": [
                    {"system": "snes", "name": "Super Metroid", "rom_file": "Super Metroid (USA).zip", "byte_count": 32},
                    {"system": "snes", "name": "Chrono Trigger", "rom_file": "Chrono Trigger (USA).zip", "byte_count": 32},
                ],
                "gamelists": [],
            },
        },
    )
    assert heartbeat_response.status_code == 200
    assert set(heartbeat_response.json()) == {"actions", "swarm", "log_stream_requested", "romset_files_thumbprint", "bios_files_thumbprint", "saves_files_thumbprint"}
    assert "device" not in heartbeat_response.json()
    swarm_peer = next(row for row in heartbeat_response.json()["swarm"] if row["drone_id"] == "arcade-cabinet-001")
    assert swarm_peer["reachable_url"] == "https://bff-drone-a:443"
    assert swarm_peer["public_ip"] == "198.51.100.50"
    assert swarm_peer["public_resolvable"] is False
    assert swarm_peer["public_reachable_url"] is None
    assert "peer_checks" not in swarm_peer
    assert "network" not in swarm_peer

    roms_response = client.get(
        "/api/devices/arcade-cabinet-001/roms",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert roms_response.status_code == 200
    before_snes_count = len(roms_response.json()["systems"].get("snes", []))

    metadata_response = client.post(
        "/api/devices/arcade-cabinet-001/rom-metadata",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "type": "rom_metadata",
            "replace_all": True,
            "roms_root": "/userdata/roms",
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [
                {"system": "snes", "name": "Super Metroid", "rom_file": "Super Metroid (USA).zip", "byte_count": 32, "rom_fingerprint": "aaa"},
                {"system": "snes", "name": "Chrono Trigger", "rom_file": "Chrono Trigger (USA).zip", "byte_count": 32, "rom_fingerprint": "bbb"},
            ],
            "gamelists": [],
        },
    )
    assert metadata_response.status_code == 200
    assert metadata_response.json()["rom_count"] == 2
    repeat_response = client.post(
        "/api/devices/arcade-cabinet-001/rom-metadata",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "type": "rom_metadata",
            "replace_all": True,
            "roms_root": "/userdata/roms",
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [
                {"system": "snes", "name": "Super Metroid", "rom_file": "Super Metroid (USA).zip", "byte_count": 32, "rom_fingerprint": "aaa"},
                {"system": "snes", "name": "Chrono Trigger", "rom_file": "Chrono Trigger (USA).zip", "byte_count": 32, "rom_fingerprint": "bbb"},
            ],
            "gamelists": [],
        },
    )
    assert repeat_response.status_code == 200

    roms_response = client.get(
        "/api/devices/arcade-cabinet-001/roms",
        headers={"Authorization": f"Bearer {token}"},
    )
    snes_roms = roms_response.json()["systems"]["snes"]
    assert len(snes_roms) == 2
    assert {rom["rom_fingerprint"] for rom in snes_roms} == {"aaa", "bbb"}
    assert {rom["file_path"] for rom in snes_roms} == {"Super Metroid (USA).zip", "Chrono Trigger (USA).zip"}
    assert len(roms_response.json()["systems"]["snes"]) != before_snes_count

    empty_response = client.post(
        "/api/devices/arcade-cabinet-001/rom-metadata",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "type": "rom_metadata",
            "replace_all": True,
            "roms_root": "/userdata/roms",
            "systems": [{"name": "snes", "rom_count": 0}],
            "roms": [],
            "gamelists": [],
        },
    )
    assert empty_response.status_code == 200
    roms_response = client.get(
        "/api/devices/arcade-cabinet-001/roms",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert roms_response.json()["systems"].get("snes", []) == []


def test_heartbeat_survives_noncritical_state_update_failure(client, monkeypatch):
    client.post("/api/auth/register", json={"email": "heartbeat-resilient@example.com", "username": "heartbeat-resilient-at-example.com", "password": "testpass123"})
    user = db.get_user_by_email("heartbeat-resilient@example.com")
    db.create_device(user["id"], "resilient-drone", "Resilient Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    def fail_update(*args, **kwargs):
        raise RuntimeError("simulated heartbeat side-effect failure")

    monkeypatch.setattr(db, "update_device_last_seen", fail_update)
    response = client.post(
        "/api/devices/resilient-drone/heartbeat",
        headers={"Authorization": "Bearer drone-token"},
        json={"device_name": "Resilient Drone"},
    )
    assert response.status_code == 200
    assert set(response.json()) == {"actions", "swarm", "log_stream_requested", "romset_files_thumbprint", "bios_files_thumbprint", "saves_files_thumbprint"}


def test_invitation_register_auto_verifies_and_rejects_mismatched_email(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    swarm_id = db.default_swarm_id(db.get_user_by_email("owner@example.com")["id"])

    invite_response = client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "new-user@example.com"},
    )
    assert invite_response.status_code == 200
    invite = next(row for row in db.invitations.values() if row["email"] == "new-user@example.com")
    # The raw email token is only sent by email, so use a test-created invitation for token checks.
    import secrets
    raw_token = secrets.token_urlsafe(16)
    invite["token_hash"] = auth_utils.hash_password(f"{TOKEN_HASH_SECRET}:{raw_token}")

    mismatch = client.post(
        "/api/auth/register",
        json={"email": "someone-else@example.com", "username": "someone-else-at-example.com", "password": "testpass123", "invitation_token": raw_token},
    )
    assert mismatch.status_code == 403

    registered = client.post(
        "/api/auth/register",
        json={"email": "new-user@example.com", "username": "new-user-at-example.com", "password": "testpass123", "invitation_token": raw_token},
    )
    assert registered.status_code == 200
    new_user = db.get_user_by_email("new-user@example.com")
    assert new_user["email_verified"] is True
    assert db.get_swarm_member(swarm_id, new_user["id"])["role"] == "overseer"


def test_invitation_accept_is_idempotent_after_invite_registration(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "username": "owner-at-example.com", "password": "testpass123"}).json()["access_token"]
    swarm_id = db.default_swarm_id(db.get_user_by_email("owner@example.com")["id"])

    client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "new-overseer@example.com"},
    )
    invite = next(row for row in db.invitations.values() if row["email"] == "new-overseer@example.com")
    import secrets
    raw_token = secrets.token_urlsafe(16)
    invite["token_hash"] = auth_utils.hash_password(f"{TOKEN_HASH_SECRET}:{raw_token}")

    registered = client.post(
        "/api/auth/register",
        json={"email": "new-overseer@example.com", "username": "new-overseer-at-example.com", "password": "testpass123", "invitation_token": raw_token},
    )
    assert registered.status_code == 200

    login = client.post(
        "/api/auth/login",
        json={"email": "new-overseer@example.com", "username": "new-overseer-at-example.com", "password": "testpass123"},
    )
    assert login.status_code == 200
    overseer_token = login.json()["access_token"]

    status_response = client.get(f"/api/invitations/status?token={raw_token}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "accepted"

    accept_again = client.post(
        "/api/invitations/accept",
        headers={"Authorization": f"Bearer {overseer_token}"},
        json={"token": raw_token},
    )
    assert accept_again.status_code == 200
    assert accept_again.json()["status"] == "accepted"


def test_overmind_manufactures_and_reuses_certificate(tmp_path, monkeypatch):
    monkeypatch.setenv("TLS_SELF_SIGNED_DIR", str(tmp_path))
    key_file, cert_file = ensure_self_signed_cert()
    assert key_file.exists()
    assert cert_file.exists()
    first_key_mtime = key_file.stat().st_mtime_ns
    first_cert_mtime = cert_file.stat().st_mtime_ns

    second_key, second_cert = ensure_self_signed_cert()
    assert second_key == key_file
    assert second_cert == cert_file
    assert key_file.stat().st_mtime_ns == first_key_mtime
    assert cert_file.stat().st_mtime_ns == first_cert_mtime


def test_profile_and_settings_update(client):
    seed_test_fleet()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "username": "demo-at-example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    profile_before = client.get(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile_before.status_code == 200
    assert "fleet_settings" in profile_before.json()
    assert "notification_settings" in profile_before.json()

    update_response = client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "full_name": "Demo Updated",
            "username": "demo-hive",
            "avatar_data_url": "data:image/png;base64,AAA",
            "fleet_settings": {"auto_sync_roms": False},
            "notification_settings": {
                "notify_slack": True,
                "slack_webhook": "https://hooks.slack.com/services/T/B/X",
                "types": {"gamelist_update": False},
            },
        },
    )
    assert update_response.status_code == 200
    data = update_response.json()
    assert data["full_name"] == "Demo Updated"
    assert data["username"] == "demo-hive"
    assert data["avatar_data_url"].startswith("data:image/png;base64")
    assert data["fleet_settings"]["auto_sync_roms"] is False
    assert data["notification_settings"]["notify_slack"] is True
    assert data["notification_settings"]["types"]["gamelist_update"] is False

    swarm_id = client.get("/api/swarms", headers={"Authorization": f"Bearer {token}"}).json()["swarms"][0]["id"]
    swarm_update = client.patch(
        f"/api/swarms/{swarm_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Demo Swarm"},
    )
    assert swarm_update.status_code == 200
    assert swarm_update.json()["swarm"]["name"] == "Demo Swarm"


def test_profile_username_change_cannot_take_existing_username(client):
    client.post(
        "/api/auth/register",
        json={"email": "alpha@example.com", "username": "ArcadeAlpha", "password": "testpass123"},
    )
    client.post(
        "/api/auth/register",
        json={"email": "beta@example.com", "username": "ArcadeBeta", "password": "testpass123"},
    )
    beta_token = client.post(
        "/api/auth/login",
        json={"email": "beta@example.com", "password": "testpass123"},
    ).json()["access_token"]

    duplicate = client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {beta_token}"},
        json={"username": "arcadealpha"},
    )

    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "Username already registered"
    assert db.get_user_by_email("beta@example.com")["username"] == "ArcadeBeta"


def test_core_user_reads_prefer_direct_relational_store(monkeypatch):
    user = {
        "id": "rel-user",
        "email": "rel@example.com",
        "password": "hash",
        "email_verified": True,
        "is_active": True,
        "auth_provider": "google",
        "username": "rel-user",
        "full_name": "Rel User",
        "avatar_data_url": "data:image/png;base64,REL",
        "fleet_settings": {"auto_sync_roms": True},
        "notification_settings": {"notify_email": True, "types": {}},
        "created_at": datetime.utcnow(),
    }
    monkeypatch.setattr(db_module.postgres_store, "get_user_by_email", lambda email: user if email == "rel@example.com" else None)
    monkeypatch.setattr(db_module.postgres_store, "get_user", lambda user_id: user if user_id == "rel-user" else None)

    assert db.get_user_by_email("rel@example.com")["avatar_data_url"] == "data:image/png;base64,REL"
    assert db.get_user("rel-user")["username"] == "rel-user"
    assert db.users["rel-user"]["email"] == "rel@example.com"


def test_profile_update_writes_through_direct_relational_store(monkeypatch):
    updated = {
        "id": "profile-user",
        "email": "profile@example.com",
        "username": "new-name",
        "full_name": "Profile User",
        "avatar_data_url": "data:image/png;base64,NEW",
        "fleet_settings": {},
        "notification_settings": {"types": {}},
    }
    calls = []

    def update_user_profile(user_id, username, full_name, avatar_data_url):
        calls.append((user_id, username, full_name, avatar_data_url))
        return updated

    monkeypatch.setattr(db_module.postgres_store, "update_user_profile", update_user_profile)

    result = db.update_user_profile("profile-user", username="new-name", avatar_data_url="data:image/png;base64,NEW")

    assert result == updated
    assert calls == [("profile-user", "new-name", None, "data:image/png;base64,NEW")]
    assert db.users["profile-user"]["username"] == "new-name"


def test_swarm_name_update_writes_through_direct_relational_store(monkeypatch):
    calls = []

    def update_swarm_name(swarm_id, name):
        calls.append((swarm_id, name))
        return {"id": swarm_id, "owner_id": "user-1", "name": name, "is_public": False, "created_at": datetime.utcnow()}

    monkeypatch.setattr(db_module.postgres_store, "available", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "store_app_state", lambda state: None)
    monkeypatch.setattr(db_module.postgres_store, "update_swarm_name", update_swarm_name)

    result = db.update_swarm_name("swarm-1", "  Arcade Fleet  ")

    assert result["name"] == "Arcade Fleet"
    assert calls == [("swarm-1", "Arcade Fleet")]
    assert db.swarms["swarm-1"]["name"] == "Arcade Fleet"


def test_device_authorization_update_writes_hash_through_direct_relational_store(monkeypatch):
    existing = {
        "id": "device-internal-1",
        "device_id": "drone-a",
        "device_name": "Drone A",
        "user_id": "user-1",
        "authorization_token_id": "old-token",
        "drone_token_hash": "old-hash",
    }
    calls = []

    def update_device_authorization(user_id, device_id, *, authorization_token_id, drone_token_hash=None, device_name=None):
        calls.append((user_id, device_id, authorization_token_id, drone_token_hash, device_name))
        return {
            **existing,
            "authorization_token_id": authorization_token_id,
            "drone_token_hash": drone_token_hash,
            "device_name": device_name,
        }

    monkeypatch.setattr(db_module.postgres_store, "available", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "store_app_state", lambda state: None)
    monkeypatch.setattr(db_module.postgres_store, "get_device_by_device_id", lambda device_id: existing)
    monkeypatch.setattr(db_module.postgres_store, "update_device_authorization", update_device_authorization)
    monkeypatch.setattr(db_module.postgres_store, "revoke_integration_token", lambda user_id, token_id: True)

    assert db.set_device_authorization_token(
        "user-1",
        "drone-a",
        "new-token",
        token_hash="new-hash",
        device_name="Renamed Drone",
    ) is True

    assert calls == [("user-1", "drone-a", "new-token", "new-hash", "Renamed Drone")]
    assert db.devices["device-internal-1"]["drone_token_hash"] == "new-hash"


def test_integration_token_claim_prefers_direct_relational_store(monkeypatch):
    user = {
        "id": "token-user",
        "email": "token@example.com",
        "username": "token-user",
        "full_name": "Token User",
        "fleet_settings": {},
        "notification_settings": {"types": {}},
    }
    token = {"id": "tok-1", "label": "Drone", "token_hash": "hash", "bound_device_id": "drone-a"}
    monkeypatch.setattr(
        db_module.postgres_store,
        "claim_integration_token",
        lambda email, raw, device_id, device_fingerprint=None: {"user": user, "token": token},
    )

    claimed = db.claim_integration_token("stale@example.com", "raw-token", "drone-a")

    assert claimed["user"]["id"] == "token-user"
    assert db.integration_tokens["token-user"][0]["id"] == "tok-1"


def test_pending_drone_connections_prefer_direct_relational_store(monkeypatch):
    pending_rows = {}

    def upsert(
        device_id,
        device_name,
        batocera_info,
        *,
        user_id,
        swarm_id=None,
        authorization_token_id=None,
        drone_token_hash=None,
        recovery_reason=None,
    ):
        row = {
            "id": device_id,
            "user_id": user_id,
            "swarm_id": swarm_id or "swarm-1",
            "device_id": device_id,
            "device_name": device_name,
            "batocera_info": batocera_info,
            "authorization_token_id": authorization_token_id,
            "drone_token_hash": drone_token_hash,
            "recovery_reason": recovery_reason,
            "detected_at": datetime.utcnow(),
            "last_seen": datetime.utcnow(),
            "status": "pending",
        }
        pending_rows[device_id] = row
        return row

    monkeypatch.setattr(db_module.postgres_store, "available", lambda: True)
    monkeypatch.setattr(db_module.postgres_store, "store_app_state", lambda state: None)
    monkeypatch.setattr(db_module.postgres_store, "upsert_pending_drone_connection", upsert)
    monkeypatch.setattr(db_module.postgres_store, "get_pending_drone_connections", lambda user_id: list(pending_rows.values()))
    monkeypatch.setattr(db_module.postgres_store, "delete_pending_drone_connection", lambda user_id, device_id, status=None: pending_rows.pop(device_id, None) is not None)

    created = db.create_pending_drone_connection(
        "relational-drone",
        "Relational Drone",
        {"network": {"ipv4": ["10.0.0.12"]}},
        user_id="user-1",
        authorization_token_id="token-1",
    )
    assert created["device_id"] == "relational-drone"

    db.pending_drone_connections.clear()
    listed = db.get_pending_drone_connections("user-1")
    assert [row["device_id"] for row in listed] == ["relational-drone"]

    db.pending_drone_connections.clear()
    assert db.deny_pending_drone_connection("user-1", "relational-drone") is True
    assert pending_rows == {}


def test_postgres_pending_drone_queries_exclude_approved_devices():
    store_source = Path(__file__).resolve().parents[1].joinpath("src/overmind/postgres_store.py").read_text(encoding="utf-8")

    assert "FROM drones d" in store_source
    assert "d.device_id = p.device_id" in store_source
    assert "d.approval_status = 'approved'" in store_source
    assert "DELETE FROM pending_drone_connections WHERE device_id = %s" in store_source


def test_relational_schema_declares_domain_tables():
    migrations_dir = Path(__file__).resolve().parents[1].joinpath("src/overmind/migrations")
    migration_sql = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(migrations_dir.glob("*.sql"))
    )
    store_source = Path(__file__).resolve().parents[1].joinpath("src/overmind/postgres_store.py").read_text(encoding="utf-8")

    for table_name in [
        "user_profiles",
        "user_auth_identities",
        "swarms",
        "swarm_memberships",
        "drones",
        "drone_network_state",
        "drone_system_info",
        "drone_certificates",
        "drone_roms",
        "drone_bios",
        "drone_artwork",
        "gameplay_sessions",
        "drone_log_sources",
        "drone_emulator_configs",
        "download_items",
        "sync_activity",
        "notifications",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in migration_sql, f"Missing table: {table_name}"
    assert "REFERENCES users(id) ON DELETE CASCADE" in migration_sql
    assert "REFERENCES drones(id) ON DELETE CASCADE" in migration_sql
    assert "CREATE INDEX IF NOT EXISTS idx_roms_drone_system" in migration_sql
    assert "CREATE INDEX IF NOT EXISTS idx_actions_drone_status" in migration_sql
    assert "CREATE INDEX IF NOT EXISTS idx_notifications_pending_delivery" in migration_sql
    assert "CREATE INDEX IF NOT EXISTS idx_speed_samples_drone_received" in migration_sql
    assert "OVERMIND_RESET_RELATIONAL_SCHEMA" in store_source
    assert "yoyo" in store_source
    assert "CREATE INDEX IF NOT EXISTS idx_events_drone_received" in migration_sql
    assert "CREATE INDEX IF NOT EXISTS idx_peer_checks_source_received" in migration_sql
    assert "class _TimedCursor" in store_source
    assert "PostgreSQL query operation=%s duration_ms=%.2f" in store_source
    assert "OVERMIND_POSTGRES_QUERY_LOG_PARAMS" in store_source
    assert "ALTER TABLE drones ADD COLUMN IF NOT EXISTS swarm_connected" in migration_sql
    assert "ALTER TABLE drones ADD COLUMN IF NOT EXISTS drone_token_hash" in migration_sql
    assert "ALTER TABLE pending_drone_connections ADD COLUMN IF NOT EXISTS drone_token_hash" in migration_sql
    assert "ALTER TABLE pending_drone_connections ADD COLUMN IF NOT EXISTS recovery_reason" in migration_sql
    assert "ALTER TABLE pending_drone_connections ALTER COLUMN user_id DROP NOT NULL" in migration_sql
    assert "ALTER TABLE drone_network_state ADD COLUMN IF NOT EXISTS public_resolvable" in migration_sql
    assert "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS batocera_version" in migration_sql
    assert "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS screen_mode" in migration_sql
    assert "ALTER TABLE drone_system_info ADD COLUMN IF NOT EXISTS audio_volume" in migration_sql
    assert "ALTER TABLE drone_emulator_configs ADD COLUMN IF NOT EXISTS fingerprint" in migration_sql
    assert "def store_device_emulator_configs" in store_source
    assert "def get_device_emulator_configs" in store_source
    assert "lower(relative_path) NOT LIKE '%%/log/%%'" in store_source
    assert "lower(relative_path) NOT LIKE '%%/logs/%%'" in store_source
    assert "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_pending" in migration_sql
    assert "if not _persist_json_app_state_enabled():\n            return None" in store_source
    assert "relational = self._load_relational_state(cur)" not in store_source
    assert "def list_user_notifications" in store_source
    assert "def list_user_devices" in store_source
    assert "WHERE g.drone_id = ANY(%s)" in store_source
    assert "WHERE n.swarm_id = ANY(%s)" in store_source
    assert "WHERE user_id = ANY(%s) OR swarm_id = ANY(%s)" in store_source


def test_postgres_store_materializes_state_and_assets_into_relational_tables():
    from overmind.postgres_store import PostgresMetadataStore

    class RecordingCursor:
        def __init__(self):
            self.statements = []
            self._next_id = 1

        def execute(self, sql, params=None):
            self.statements.append((sql, params))

        def executemany(self, sql, params):
            self.statements.append((sql, list(params)))

        def fetchone(self):
            value = self._next_id
            self._next_id += 1
            return [value]

        def fetchall(self):
            return []

    store = PostgresMetadataStore()
    cur = RecordingCursor()
    store._mirror_app_state_to_relational(cur, {
        "users": {"u1": {"id": "u1", "email": "u@example.com", "password": "hash", "email_verified": True, "is_active": True}},
        "swarms": {"s1": {"id": "s1", "owner_id": "u1", "name": "Main"}},
        "swarm_memberships": {"s1": {"u1": {"user_id": "u1", "role": "overlord"}}},
        "devices": {"d1": {"id": "d1", "device_id": "drone-a", "device_name": "Drone A", "user_id": "u1", "swarm_id": "s1"}},
        "device_actions": {"d1": [{"id": "a1", "device_id": "drone-a", "action": "restart", "status": "pending", "payload": {"reason": "test"}}]},
        "gamelogs": {"d1": [{"id": "g1", "game_name": "Game", "system_name": "snes"}]},
        "download_states": {"d1": {"active": [{"job_id": "j1", "status": "downloading"}], "queued": [], "recent": []}},
    })
    store._upsert_domain_assets(cur, "d1", "rom", [{"system_name": "snes", "file_path": "Game.zip", "rom_fingerprint": "abc"}])
    store._upsert_domain_assets(cur, "d1", "bios", [{"file_path": "bios.bin", "bios_md5": "def"}])
    store._upsert_domain_assets(cur, "d1", "artwork", [{"system_name": "snes", "rom_path": "Game.zip", "artwork_types": ["image"]}])

    sql = "\n".join(statement for statement, _ in cur.statements)
    for table_name in [
        "INSERT INTO users",
        "INSERT INTO swarms",
        "INSERT INTO swarm_memberships",
        "INSERT INTO drones",
        "INSERT INTO drone_actions",
        "INSERT INTO gameplay_sessions",
        "INSERT INTO download_snapshots",
        "INSERT INTO download_items",
        "INSERT INTO drone_roms",
        "INSERT INTO drone_bios",
        "INSERT INTO drone_artwork",
    ]:
        assert table_name in sql


def test_postgres_store_batches_artwork_asset_deletes(monkeypatch):
    from overmind.postgres_store import PostgresMetadataStore

    class RecordingCursor:
        def __init__(self):
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class RecordingConnection:
        def __init__(self):
            self.cursor_obj = RecordingCursor()

        def cursor(self):
            return self.cursor_obj

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    store = PostgresMetadataStore()
    conn = RecordingConnection()
    monkeypatch.setattr(store, "assets_enabled", lambda: True)
    monkeypatch.setattr(store, "_connect", lambda: conn)

    store.delete_device_asset_rows("d1", "artwork", [
        {"system_name": "snes", "rom_path": "Game One.zip", "artwork_types": ["image", "marquee"]},
        {"system_name": "snes", "rom_path": "Game Two.zip", "artwork_type": "thumbnail"},
    ])

    assert len(conn.cursor_obj.statements) == 2
    sql = "\n".join(statement for statement, _ in conn.cursor_obj.statements)
    assert "FROM unnest(%s::text[], %s::text[])" in sql
    assert "FROM unnest(%s::text[], %s::text[], %s::text[])" in sql
    assert len(conn.cursor_obj.statements[1][1][1]) == 3


def test_postgres_store_rehydrates_queued_actions_from_relational_tables():
    from overmind.postgres_store import PostgresMetadataStore

    created_at = datetime(2026, 5, 31, 4, 30, tzinfo=timezone.utc)

    class RecordingCursor:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            if "FROM users u" in self.sql:
                return [(
                    "u1", "owner@example.com", "hash", True, True, "password", created_at,
                    "owner", None, None,
                    True,
                    False, False, True, "", "", "owner@example.com",
                )]
            if "FROM user_notification_type_settings" in self.sql:
                return []
            if "FROM swarms" in self.sql:
                return [("s1", "u1", "Main", created_at)]
            if "FROM swarm_memberships" in self.sql:
                return [("s1", "u1", "overlord", created_at)]
            if "FROM integration_tokens" in self.sql:
                return []
            if "FROM drones d" in self.sql and "LEFT JOIN drone_network_state" in self.sql:
                    return [(
                        "d1", "drone-a", "Drone A", "u1", "s1", "approved", True,
                        None, "token-hash", created_at, created_at,
                        None, None, None, None, None,
                        None, None,
                        443, "https", "https://drone-a:443", False, None, None,
                        None, None, None, None, None, None, None,
                    None, None, None, None, "kid", 65, None,
                )]
            if "FROM device_admin_claims" in self.sql:
                return []
            if "FROM drone_action_parameters" in self.sql:
                return [("action-1", "options", json.dumps(_encode_state({"force": True})))]
            if "FROM drone_actions a" in self.sql:
                return [("action-1", "d1", "drone-a", "restart", "pending", created_at, None, None, None)]
            if "FROM pending_drone_connections" in self.sql:
                return []
            return []

    state = PostgresMetadataStore()._load_relational_state(RecordingCursor())

    action = state["device_actions"]["d1"][0]
    assert state["devices"]["d1"]["system_info"]["screen_mode"] == "kid"
    assert state["devices"]["d1"]["system_info"]["audio_volume"] == 65
    assert action["device_id"] == "drone-a"
    assert action["action"] == "restart"
    assert action["status"] == "pending"
    assert action["payload"] == {"options": {"force": True}}


def test_postgres_store_rehydrates_telemetry_from_relational_tables():
    from overmind.postgres_store import PostgresMetadataStore

    received_at = datetime(2026, 5, 31, 5, 0, tzinfo=timezone.utc)

    class RecordingCursor:
        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchall(self):
            if "FROM users u" in self.sql:
                return [(
                    "u1", "owner@example.com", "hash", True, True, "password", received_at,
                    "owner", None, None,
                    True,
                    False, False, True, "", "", "owner@example.com",
                )]
            if "FROM user_notification_type_settings" in self.sql:
                return []
            if "FROM swarms" in self.sql:
                return [("s1", "u1", "Main", received_at)]
            if "FROM swarm_memberships" in self.sql:
                return [("s1", "u1", "overlord", received_at)]
            if "FROM integration_tokens" in self.sql:
                return []
            if "FROM drones d" in self.sql and "LEFT JOIN drone_network_state" in self.sql:
                    return [(
                        "d1", "drone-a", "Drone A", "u1", "s1", "approved", True,
                        None, "token-hash", received_at, received_at,
                        None, None, None, None, None,
                        None, None,
                        443, "https", "https://drone-a:443", False, None, None,
                        None, None, None, None, None, None, None,
                    None, None, None, None, None, None, None,
                )]
            if "FROM gameplay_sessions" in self.sql:
                return [("game-1", "d1", "snes", "Game", "Game.zip", "abc", received_at, 60, received_at)]
            if "FROM drone_speed_samples" in self.sql:
                return [("d1", 4.5, 10.25, 12.0, received_at, received_at)]
            if "FROM drone_events e" in self.sql:
                return [(9, "d1", "rom_sync", "info", "Synced", received_at, received_at)]
            if "FROM drone_event_fields" in self.sql:
                return [(9, "job_id", json.dumps("job-1"))]
            if "FROM notifications" in self.sql:
                return [("n1", "s1", "sync_triggered", "Sync", "Sync queued", "u1", received_at, True, None)]
            if "FROM notification_fields" in self.sql:
                return [("n1", "sync_type", json.dumps("ROM"))]
            if "FROM notification_recipients" in self.sql:
                return [("n1", "u1", received_at, None)]
            return []

    state = PostgresMetadataStore()._load_relational_state(RecordingCursor())

    assert state["gamelogs"]["d1"][0]["game_name"] == "Game"
    assert state["speed_samples"]["d1"][0]["download_mbps"] == 10.25
    assert state["device_events"]["d1"][0]["metadata"] == {"job_id": "job-1"}
    notification = state["notifications"]["s1"][0]
    assert notification["delivery_pending"] is True
    assert notification["details"] == {"sync_type": "ROM"}
    assert notification["read_by"] == {"u1": received_at}


def test_postgres_store_rehydrates_peer_transfer_reporting_from_relational_tables():
    from overmind.postgres_store import PostgresMetadataStore

    reported_at = datetime(2026, 5, 31, 5, 0, tzinfo=timezone.utc)

    class RecordingCursor:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            if "FROM users u" in self.sql:
                return [(
                    "u1", "owner@example.com", "hash", True, True, "password", reported_at,
                    "owner", None, None,
                    True,
                    False, False, True, "", "", "owner@example.com",
                )]
            if "FROM user_notification_type_settings" in self.sql:
                return []
            if "FROM swarms" in self.sql:
                return [("s1", "u1", "Main", reported_at)]
            if "FROM swarm_memberships" in self.sql:
                return [("s1", "u1", "overlord", reported_at)]
            if "FROM integration_tokens" in self.sql:
                return []
            if "FROM drones d" in self.sql and "LEFT JOIN drone_network_state" in self.sql:
                    return [(
                        "d1", "drone-a", "Drone A", "u1", "s1", "approved", True,
                        None, "target-hash", reported_at, reported_at,
                        None, None, None, None, None, None, None,
                        None, None,
                        443, "https", "https://drone-a:443", False, None, None,
                        None, None, None, None, None, None, None,
                        None, None, None, None, None,
                    ), (
                        "d2", "drone-b", "Drone B", "u1", "s1", "approved", True,
                        None, "source-hash", reported_at, reported_at,
                        None, None, None, None, None,
                        None, None,
                        443, "https", "https://drone-b:443", True, "198.51.100.2", reported_at,
                        None, None, None, None, None, None, None,
                    None, None, None, None, None, None, None,
                )]
            if "FROM drone_certificates" in self.sql:
                return [("d2", "loaded", "fp", "sha", "-----BEGIN CERTIFICATE-----\\npeer\\n-----END CERTIFICATE-----", "subject", "issuer", None, None, "1", None, reported_at)]
            if "FROM drone_certificate_sans" in self.sql:
                return [("d2", "DNS:drone-b")]
            if "FROM device_admin_claims" in self.sql:
                return []
            if "FROM drone_actions a" in self.sql:
                return []
            if "FROM drone_action_parameters" in self.sql:
                return []
            if "FROM drone_peer_checks" in self.sql:
                return [(7, "d1", "drone-a", "drone-b", "https://drone-b:443", "pass", 12.5, reported_at, None, reported_at)]
            if "FROM download_snapshots" in self.sql:
                return [("d1", 11, reported_at, "target_drone", 1)]
            if "FROM download_items" in self.sql:
                return [(11, "job-1", "active", "rom", "downloading", "drone-b", "snes", "Game.zip", "Game.zip", None, None, 100, 25, 25.0, 1024.0, 1, None)]
            if "FROM sync_activity" in self.sql:
                return [("sync-1", "d1", "drone-a", "drone-b", "rom", "download", "completed", "snes", "Game.zip", "abc", None, None, 100, 100, reported_at, reported_at, None, reported_at)]
            if "FROM pending_drone_connections" in self.sql:
                return []
            return []

    state = PostgresMetadataStore()._load_relational_state(RecordingCursor())

    assert state["devices"]["d2"]["certificate"]["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert state["devices"]["d2"]["certificate"]["san"] == ["DNS:drone-b"]
    assert state["peer_checks"]["d1"][0]["target_drone_id"] == "drone-b"
    assert state["peer_checks"]["d1"][0]["status"] == "pass"
    active = state["download_states"]["d1"]["active"][0]
    assert active["job_id"] == "job-1"
    assert active["downloaded_bytes"] == 25
    sync = state["rom_sync_activity"]["d1"][0]
    assert sync["source_drone_id"] == "drone-b"
    assert sync["status"] == "completed"


def test_overmind_database_requires_postgres_without_legacy_seed_gate():
    source = Path(__file__).resolve().parents[1].joinpath("src/overmind/db.py").read_text(encoding="utf-8")
    main_source = Path(__file__).resolve().parents[1].joinpath("src/overmind/main.py").read_text(encoding="utf-8")

    assert "class OvermindDatabase" in source
    assert "db = OvermindDatabase()" in source
    assert ("USE_" + "FAKE_DATA") not in main_source


def test_drone_api_uses_explicit_contract_models():
    source = Path(__file__).resolve().parents[1].joinpath("src/overmind/main.py").read_text(encoding="utf-8")
    models = Path(__file__).resolve().parents[1].joinpath("src/overmind/models.py").read_text(encoding="utf-8")

    assert "async def drone_heartbeat(device_id: str, payload: DroneHeartbeatRequest" in source
    assert "async def upload_drone_rom_metadata(device_id: str, payload: DroneAssetMetadataUpload" in source
    assert "async def update_device_downloads(device_id: str, payload: DroneDownloadsReport" in source
    assert "async def complete_device_action(device_id: str, action_id: str, payload: DroneActionCompleteRequest" in source
    assert "async def upload_device_emulator_configs(device_id: str, payload: DroneEmulatorConfigsUpload" in source
    assert "class StrictContractModel(BaseModel):" in models
    assert 'ConfigDict(extra="forbid")' in models


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
