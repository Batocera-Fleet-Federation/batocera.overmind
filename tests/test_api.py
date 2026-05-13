"""Tests for the Batocera Overmind API."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from overmind.main import app
from overmind.db import db


@pytest.fixture
def client():
    """Create a test client."""
    db.users.clear()
    db.user_by_email.clear()
    db.devices.clear()
    db.user_devices.clear()
    db.roms.clear()
    db.gamelogs.clear()
    db.device_actions.clear()
    db.pending_drone_connections.clear()
    return TestClient(app)


def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_register_user(client):
    """Test user registration."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpass123",
            "full_name": "Test User"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert "id" in data


def test_duplicate_registration(client):
    """Test duplicate registration fails."""
    # First registration
    client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpass123",
        }
    )
    
    # Duplicate registration
    response = client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
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
            "password": "testpass123",
        }
    )
    
    # Login
    response = client.post(
        "/api/auth/login",
        json={
            "email": "test@example.com",
            "password": "testpass123"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_invalid_login(client):
    """Test login with invalid credentials."""
    response = client.post(
        "/api/auth/login",
        json={
            "email": "test@example.com",
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


def test_register_device(client):
    """Test drone connection detection."""
    # Register user
    client.post(
        "/api/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpass123",
        }
    )
    
    # Drone attempts to connect
    response = client.post(
        "/api/devices/register",
        json={
            "email": "test@example.com",
            "password": "testpass123",
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
                "ip_address": "192.168.1.1"
            }
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Psionic connection detected. Awaiting Overlord approval."
    assert data["connection"]["device_id"] == "device-123"


def test_accept_pending_drone_connection_registers_device(client):
    """Overlord can accept a pending drone connection."""
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    token = login_response.json()["access_token"]

    client.post(
        "/api/devices/register",
        json={
            "email": "test@example.com",
            "password": "testpass123",
            "device_id": "drone-123",
            "device_name": "Test Drone",
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

    pending_response = client.get(
        "/api/drone-connections",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pending_response.status_code == 200
    assert pending_response.json()["connections"][0]["device_id"] == "drone-123"

    accept_response = client.post(
        "/api/drone-connections/drone-123/accept",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert accept_response.status_code == 200
    assert accept_response.json()["device"]["device_name"] == "Test Drone"

    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices_response.status_code == 200
    assert devices_response.json()["devices"][0]["device_id"] == "drone-123"


def test_deny_pending_drone_connection(client):
    """Overlord can deny a pending drone connection."""
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    ).json()["access_token"]
    client.post(
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
    response = client.post(
        "/api/drone-connections/rogue-drone/deny",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    pending_response = client.get(
        "/api/drone-connections",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pending_response.json()["connections"] == []


def test_list_devices_uses_authorization_header(client):
    """Authenticated routes should accept Bearer token from header."""
    client.post(
        "/api/auth/register",
        json={"email": "auth-header@example.com", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "auth-header@example.com", "password": "testpass123"},
    )
    token = login_response.json()["access_token"]
    response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "devices" in response.json()


def test_demo_seed_exposes_devices_and_systems(client):
    """Seeded demo user should have visible devices/systems data."""
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
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


def test_demo_seed_exposes_pending_drone_connections(client):
    """Seeded demo user should see pending psionic Drone connection requests."""
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
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


def test_rename_device(client):
    """Device name can be updated from UI/API."""
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]
    rename_response = client.patch(
        "/api/devices/arcade-cabinet-001/name",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_name": "Arcade Alpha"},
    )
    assert rename_response.status_code == 200
    assert rename_response.json()["device_name"] == "Arcade Alpha"


def test_delete_device_removes_device_and_related_data(client):
    """Device deletion removes the device, ROMs, and gameplay logs for that user."""
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
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


def test_device_action_lifecycle(client):
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
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
        json={"email": "demo@example.com", "password": "DemoPass123"},
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["action"]["id"] == action_id
    assert claim_response.json()["action"]["status"] == "in_progress"

    complete_response = client.post(
        f"/api/devices/arcade-cabinet-001/actions/{action_id}/complete",
        json={
            "email": "demo@example.com",
            "password": "DemoPass123",
            "status": "completed",
            "message": "Restart scheduled",
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["action"]["status"] == "completed"


def test_profile_and_settings_update(client):
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
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
    assert data["avatar_data_url"].startswith("data:image/png;base64")
    assert data["fleet_settings"]["auto_sync_roms"] is False
    assert data["notification_settings"]["notify_slack"] is True
    assert data["notification_settings"]["types"]["gamelist_update"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
