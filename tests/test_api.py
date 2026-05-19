"""Tests for the Batocera Overmind API."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from overmind.main import app, ensure_self_signed_cert
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
    db.speed_samples.clear()
    db.device_events.clear()
    db.peer_checks.clear()
    db.integration_tokens.clear()
    db.approved_drone_tokens.clear()
    db.rom_sync_activity.clear()
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


def test_register_device_with_valid_token_returns_drone_token_and_alive_works(client):
    """A valid integration token registers the Drone and authorizes alive."""
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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
            api_port=8443,
            scheme="https",
            reachable_url="https://bff-drone-a:8443",
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
    drone_token = register_response.json()["drone_token"]
    assert register_response.json()["status"] == "approved"

    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert devices_response.status_code == 200
    device = devices_response.json()["devices"][0]
    assert device["device_id"] == "drone-123"
    assert device["reachable_url"] == "https://bff-drone-a:8443"
    assert device["certificate"]["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert device["certificate"]["fingerprint"] == "abc123"
    assert "private_key" not in device["certificate"]

    alive_response = client.post(
        "/api/devices/drone-123/alive",
        headers={"Authorization": f"Bearer {drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}, "reachable_url": "https://bff-drone-a:8443"},
    )
    assert alive_response.status_code == 200

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


def test_integration_token_onboarding_and_approved_token_claim(client):
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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
    assert register_response.json()["status"] == "approved"
    assert register_response.json()["drone_token"]

    claim_response = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="token-drone", device_name="Token Drone"),
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["status"] == "approved"
    assert claim_response.json()["drone_token"]


def test_integration_token_cannot_register_different_drone(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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


def test_re_register_same_drone_rotates_bearer_and_old_token_fails(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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
    old_drone_token = first.json()["drone_token"]
    second = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    )
    assert second.status_code == 200
    new_drone_token = second.json()["drone_token"]
    assert new_drone_token != old_drone_token

    obsolete = client.post(
        "/api/devices/drone-a/alive",
        headers={"Authorization": f"Bearer {old_drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    )
    assert obsolete.status_code == 401

    current = client.post(
        "/api/devices/drone-a/alive",
        headers={"Authorization": f"Bearer {new_drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    )
    assert current.status_code == 200


def test_revoked_integration_token_invalidates_backed_drone_token(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    ).json()["access_token"]
    token_payload = client.post(
        "/api/integration-tokens",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"label": "revocable"},
    ).json()["token"]
    auth_token = token_payload["authorization_token"]
    drone_token = client.post(
        "/api/devices/register",
        json=_device_registration_payload(authorization_token=auth_token, device_id="drone-a"),
    ).json()["drone_token"]

    revoke = client.delete(
        f"/api/integration-tokens/{token_payload['id']}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert revoke.status_code == 200
    alive = client.post(
        "/api/devices/drone-a/alive",
        headers={"Authorization": f"Bearer {drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}},
    )
    assert alive.status_code == 401


def test_deny_pending_drone_connection(client):
    """Unauthorized registration no longer creates a pending connection to deny."""
    client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "testpass123"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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
    assert complete_response.json()["action"]["status"] == "completed"


def test_drone_alive_claims_data_action_and_stores_result(client):
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    create_response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "collect_rom_metadata"},
    )
    assert create_response.status_code == 200
    action_id = create_response.json()["action"]["id"]

    alive_response = client.post(
        "/api/devices/arcade-cabinet-001/alive",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "network": {"ipv4": ["192.168.1.50"], "ipv6": ["::1"]},
            "rom_systems": [{"name": "snes"}],
        },
    )
    assert alive_response.status_code == 200
    assert alive_response.json()["action"]["id"] == action_id
    assert alive_response.json()["action"]["status"] == "in_progress"

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
    action = complete_response.json()["action"]
    assert action["result"] == result
    assert action["result_received_at"] is not None


def test_alive_stores_system_info_and_peer_detail_is_latest(client):
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    alive_response = client.post(
        "/api/devices/arcade-cabinet-001/alive",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "network": {"ipv4": ["192.168.1.50"]},
            "rom_systems": [{"name": "snes"}],
            "system_info": {
                "hostname": "arcade-alpha",
                "architecture": "x86_64",
                "container": True,
            },
        },
    )
    assert alive_response.status_code == 200

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
    assert len(detail["peer_checks"]) == 1
    assert detail["peer_checks"][0]["status"] == "pass"
    assert detail["peer_checks"][0]["target_address"] == "https://new.example"
    assert detail["peer_checks"][0]["target_name"]


def test_alive_persists_rom_metadata_and_swarm_reachable_url(client):
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    alive_response = client.post(
        "/api/devices/arcade-cabinet-001/alive",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "network": {"ipv4": ["192.168.1.50"], "ipv6": ["fd00::50"], "hostname_override": "bff-drone-a"},
            "reachable_url": "https://bff-drone-a:8443",
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
    assert alive_response.status_code == 200
    swarm_peer = next(row for row in alive_response.json()["swarm"] if row["device_id"] == "arcade-cabinet-001")
    assert swarm_peer["reachable_url"] == "https://bff-drone-a:8443"

    roms_response = client.get(
        "/api/devices/arcade-cabinet-001/roms",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert roms_response.status_code == 200
    assert len(roms_response.json()["systems"]["snes"]) == 2


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
