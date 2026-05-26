"""Tests for the Batocera Overmind API."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from overmind.main import app, ensure_self_signed_cert, TOKEN_HASH_SECRET
from overmind.db import db
from overmind import auth as auth_utils


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


def test_auth_refresh_requires_and_returns_valid_token(client):
    client.post("/api/auth/register", json={"email": "refresh@example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "refresh@example.com", "password": "testpass123"}).json()["access_token"]

    denied = client.post("/api/auth/refresh")
    assert denied.status_code == 401

    refreshed = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {token}"})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    assert refreshed.json()["user"]["email"] == "refresh@example.com"


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


def test_social_auth_activates_existing_unverified_user(client, monkeypatch):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-secret")
    client.post("/api/auth/register", json={"email": "social-existing@example.com", "password": "testpass123"})
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


def test_email_registration_requires_verification(client, monkeypatch):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    response = client.post(
        "/api/auth/register",
        json={"email": "verify@example.com", "password": "testpass123"},
    )
    assert response.status_code == 200
    assert db.get_user_by_email("verify@example.com")["is_active"] is False
    assert client.post("/api/auth/login", json={"email": "verify@example.com", "password": "testpass123"}).status_code == 403

    code = db.email_verifications[db.get_user_by_email("verify@example.com")["id"]]["code"]
    verify = client.post("/api/auth/verify-email", json={"email": "verify@example.com", "code": code})
    assert verify.status_code == 200
    assert client.post("/api/auth/login", json={"email": "verify@example.com", "password": "testpass123"}).status_code == 200


def test_fake_data_does_not_log_registration_verification_code(client, monkeypatch, capsys):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    monkeypatch.setenv("USE_FAKE_DATA", "true")
    response = client.post("/api/auth/register", json={"email": "fake-code@example.com", "password": "testpass123"})
    assert response.status_code == 200
    captured = capsys.readouterr()
    assert "registration verification code for fake-code@example.com" not in captured.out


def test_normal_mode_does_not_log_registration_verification_code(client, monkeypatch, capsys):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    monkeypatch.delenv("USE_FAKE_DATA", raising=False)
    response = client.post("/api/auth/register", json={"email": "real-code@example.com", "password": "testpass123"})
    assert response.status_code == 200
    captured = capsys.readouterr()
    assert "registration verification code for real-code@example.com" not in captured.out


def test_expired_verification_code_fails(client, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    client.post("/api/auth/register", json={"email": "expired@example.com", "password": "testpass123"})
    user = db.get_user_by_email("expired@example.com")
    db.email_verifications[user["id"]]["expires_at"] = datetime.utcnow() - timedelta(seconds=1)
    code = db.email_verifications[user["id"]]["code"]
    response = client.post("/api/auth/verify-email", json={"email": "expired@example.com", "code": code})
    assert response.status_code == 400


def test_resend_verification_replaces_old_code(client, monkeypatch):
    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    sent = []
    monkeypatch.setattr("overmind.main.send_verification_email", lambda user, code, token: sent.append((user["email"], code, token)))

    client.post("/api/auth/register", json={"email": "resend@example.com", "password": "testpass123"})
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
    client.post("/api/auth/register", json={"email": "verified@example.com", "password": "testpass123"})
    user = db.get_user_by_email("verified@example.com")
    assert user["email_verified"] is True

    response = client.post("/api/auth/resend-verification", json={"email": "verified@example.com"})
    assert response.status_code == 200
    assert user["id"] not in db.email_verifications


def test_forgot_password_token_resets_password(client, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.delenv("OVERMIND_AUTO_VERIFY_REGISTRATION", raising=False)
    client.post("/api/auth/register", json={"email": "reset@example.com", "password": "oldpass123"})
    user = db.get_user_by_email("reset@example.com")
    db.set_user_verified(user["id"])

    response = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    assert response.status_code == 200
    raw_token = "manual-reset-token"
    db.create_password_reset(user["id"], auth_utils.hash_password(f"{TOKEN_HASH_SECRET}:{raw_token}"), datetime.utcnow() + timedelta(minutes=30))
    reset = client.post("/api/auth/reset-password", json={"token": raw_token, "password": "newpass123"})
    assert reset.status_code == 200
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "password": "newpass123"}).status_code == 200


def test_swarm_roles_gate_invites_and_mutations(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "testpass123"}).json()["access_token"]
    swarm_id = client.get("/api/swarms", headers={"Authorization": f"Bearer {owner_token}"}).json()["swarms"][0]["id"]

    invite = client.post(
        f"/api/swarms/{swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "viewer@example.com", "role": "overlord"},
    )
    assert invite.status_code == 200
    assert invite.json()["invitation"]["role"] == "overseer"

    client.post("/api/auth/register", json={"email": "viewer@example.com", "password": "testpass123"})
    viewer_token = client.post("/api/auth/login", json={"email": "viewer@example.com", "password": "testpass123"}).json()["access_token"]
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


def test_swarms_marks_users_home_swarm(client):
    client.post("/api/auth/register", json={"email": "owner-home@example.com", "password": "testpass123"})
    token = client.post("/api/auth/login", json={"email": "owner-home@example.com", "password": "testpass123"}).json()["access_token"]

    response = client.get("/api/swarms", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    swarms = response.json()["swarms"]
    assert swarms
    assert sum(1 for swarm in swarms if swarm.get("current")) == 1
    assert swarms[0]["current"] is True


def test_invited_overseer_home_swarm_is_their_owned_swarm(client):
    client.post("/api/auth/register", json={"email": "owner-home@example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner-home@example.com", "password": "testpass123"}).json()["access_token"]
    owner_swarm_id = db.default_swarm_id(db.get_user_by_email("owner-home@example.com")["id"])

    invite = client.post(
        f"/api/swarms/{owner_swarm_id}/invitations",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"email": "overseer-home@example.com"},
    )
    assert invite.status_code == 200

    client.post("/api/auth/register", json={"email": "overseer-home@example.com", "password": "testpass123"})
    overseer_token = client.post("/api/auth/login", json={"email": "overseer-home@example.com", "password": "testpass123"}).json()["access_token"]

    response = client.get("/api/swarms", headers={"Authorization": f"Bearer {overseer_token}"})

    assert response.status_code == 200
    swarms = response.json()["swarms"]
    current_swarms = [swarm for swarm in swarms if swarm.get("current")]
    assert len(current_swarms) == 1
    assert current_swarms[0]["owner_id"] == db.get_user_by_email("overseer-home@example.com")["id"]
    assert current_swarms[0]["id"] != owner_swarm_id


def test_drone_ownership_claim_success_and_owner_actions(client, capsys):
    client.post("/api/auth/register", json={"email": "claim-owner@example.com", "password": "claimpass123"})
    token = client.post("/api/auth/login", json={"email": "claim-owner@example.com", "password": "claimpass123"}).json()["access_token"]

    response = client.post(
        "/api/drones/claim-ownership",
        json={
            "device_id": "claim-drone",
            "device_name": "Claimed Drone",
            "email": "claim-owner@example.com",
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
    client.post("/api/auth/register", json={"email": "first-owner@example.com", "password": "claimpass123"})
    client.post("/api/auth/register", json={"email": "second-owner@example.com", "password": "otherpass123"})
    first_token = client.post(
        "/api/auth/login",
        json={"email": "first-owner@example.com", "password": "claimpass123"},
    ).json()["access_token"]
    second_token = client.post(
        "/api/auth/login",
        json={"email": "second-owner@example.com", "password": "otherpass123"},
    ).json()["access_token"]

    bad = client.post(
        "/api/drones/claim-ownership",
        json={"device_id": "owned-drone", "email": "first-owner@example.com", "password": "wrongpass123"},
    )
    assert bad.status_code == 401
    captured = capsys.readouterr()
    assert "wrongpass123" not in captured.out
    assert "wrongpass123" not in captured.err

    claimed = client.post(
        "/api/drones/claim-ownership",
        json={"device_id": "owned-drone", "email": "first-owner@example.com", "password": "claimpass123"},
    )
    assert claimed.status_code == 200

    second_claim = client.post(
        "/api/drones/claim-ownership",
        json={"device_id": "owned-drone", "email": "second-owner@example.com", "password": "otherpass123"},
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
    client.post("/api/auth/register", json={"email": "hive-owner@example.com", "password": "testpass123", "full_name": "Hive Owner"})
    owner_token = client.post("/api/auth/login", json={"email": "hive-owner@example.com", "password": "testpass123"}).json()["access_token"]
    client.patch(
        "/api/profile",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"username": "hive-owner", "avatar_data_url": "data:image/png;base64,AAA"},
    )
    owner = db.get_user_by_email("hive-owner@example.com")
    swarm_id = db.default_swarm_id(owner["id"])
    db.create_device(owner["id"], "hive-drone", "Hive Drone", {"ip_address": "10.0.0.9"}, raw_token="drone-token", swarm_id=swarm_id)

    client.post("/api/auth/register", json={"email": "visitor@example.com", "password": "testpass123"})
    visitor_token = client.post("/api/auth/login", json={"email": "visitor@example.com", "password": "testpass123"}).json()["access_token"]
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
    client.post("/api/auth/register", json={"email": "owner-hive@example.com", "password": "testpass123"})
    client.post("/api/auth/register", json={"email": "overseer-hive@example.com", "password": "testpass123"})
    owner = db.get_user_by_email("owner-hive@example.com")
    overseer = db.get_user_by_email("overseer-hive@example.com")
    swarm_id = db.default_swarm_id(owner["id"])
    db.swarm_memberships.setdefault(swarm_id, {})[overseer["id"]] = {"user_id": overseer["id"], "role": "overseer"}
    db.create_device(owner["id"], "overseer-drone", "Overseer Drone", {"ip_address": "10.0.0.10"}, raw_token="drone-token", swarm_id=swarm_id)
    overseer_token = client.post("/api/auth/login", json={"email": "overseer-hive@example.com", "password": "testpass123"}).json()["access_token"]

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
    client.post("/api/auth/register", json={"email": "private-owner@example.com", "password": "testpass123"})
    client.post("/api/auth/register", json={"email": "outsider@example.com", "password": "testpass123"})
    owner = db.get_user_by_email("private-owner@example.com")
    db.create_device(owner["id"], "private-drone", "Private Drone", {"ip_address": "10.0.0.11"}, raw_token="drone-token")
    outsider_token = client.post("/api/auth/login", json={"email": "outsider@example.com", "password": "testpass123"}).json()["access_token"]

    response = client.get("/api/devices/private-drone", headers={"Authorization": f"Bearer {outsider_token}"})
    assert response.status_code == 404


def test_download_state_and_cancel_rbac(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "testpass123"}).json()["access_token"]
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
    assert live_update.json()["active"] == 1
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
    client.post("/api/auth/register", json={"email": "viewer@example.com", "password": "testpass123"})
    viewer_token = client.post("/api/auth/login", json={"email": "viewer@example.com", "password": "testpass123"}).json()["access_token"]

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
    assert register_response.json()["status"] == "pending"

    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert pending_response.status_code == 200
    assert pending_response.json()["connections"][0]["device_id"] == "drone-123"

    accept_response = client.post("/api/drone-connections/drone-123/accept", headers={"Authorization": f"Bearer {token}"})
    assert accept_response.status_code == 200

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
    assert device["reachable_url"] == "https://bff-drone-a:8443"
    assert device["certificate"]["public_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert device["certificate"]["fingerprint"] == "abc123"
    assert "private_key" not in device["certificate"]

    heartbeat_response = client.post(
        "/api/devices/drone-123/heartbeat",
        headers={"Authorization": f"Bearer {drone_token}"},
        json={"network": {"ipv4": ["192.168.1.50"]}, "reachable_url": "https://bff-drone-a:8443"},
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


def test_reapproving_same_drone_updates_existing_device_instead_of_duplicating(client):
    """Repeated approval with a new authorization token keeps one visible Drone record."""
    client.post("/api/auth/register", json={"email": "dedupe@example.com", "password": "testpass123"})
    login_response = client.post("/api/auth/login", json={"email": "dedupe@example.com", "password": "testpass123"})
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
    client.post("/api/auth/register", json={"email": "collapse@example.com", "password": "testpass123"})
    user = db.get_user_by_email("collapse@example.com")
    internal_id = db.create_device(user["id"], "duplicate-drone", "Original", {"ip_address": "10.0.0.2"}, raw_token="token")
    db.devices["manual-duplicate"] = {
        **db.devices[internal_id],
        "id": "manual-duplicate",
        "device_name": "Manual Duplicate",
    }
    db.user_devices[user["id"]].append("manual-duplicate")

    login_response = client.post("/api/auth/login", json={"email": "collapse@example.com", "password": "testpass123"})
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
        json={"email": "token-owner@example.com", "password": "testpass123"},
    )
    login_response = client.post(
        "/api/auth/login",
        json={"email": "token-owner@example.com", "password": "testpass123"},
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


def test_integration_token_cannot_register_same_id_with_different_certificate(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    user_token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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


def test_drone_heartbeat_updates_device_name(client):
    """Device name is controlled by the Drone heartbeat."""
    client.post("/api/auth/register", json={"email": "owner@example.com", "password": "testpass123"})
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
    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert all(conn["device_id"] != "arcade-cabinet-001" for conn in pending_response.json()["connections"])


def test_drone_self_disconnect_removes_from_swarm_without_pending_connection(client):
    client.post("/api/auth/register", json={"email": "disconnect@example.com", "password": "testpass123"})
    user = db.get_user_by_email("disconnect@example.com")
    db.create_device(user["id"], "disconnect-drone", "Disconnect Drone", {"ip_address": "10.0.0.2"}, raw_token="drone-token")

    disconnect_response = client.post(
        "/api/devices/disconnect-drone/disconnect",
        headers={"Authorization": "Bearer drone-token"},
    )
    assert disconnect_response.status_code == 200

    login_response = client.post("/api/auth/login", json={"email": "disconnect@example.com", "password": "testpass123"})
    token = login_response.json()["access_token"]
    devices_response = client.get("/api/devices", headers={"Authorization": f"Bearer {token}"})
    assert all(device["device_id"] != "disconnect-drone" for device in devices_response.json()["devices"])
    pending_response = client.get("/api/drone-connections", headers={"Authorization": f"Bearer {token}"})
    assert all(conn["device_id"] != "disconnect-drone" for conn in pending_response.json()["connections"])


def test_swarm_master_list_deduplicates_by_md5_and_activity_search(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("test@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "drone-b", "Drone B", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.add_roms("drone-a", "snes", [{"rom_name": "Same Game.zip", "file_path": "Same Game.zip", "rom_md5": "abc", "file_size": 3}])
    db.add_roms("drone-b", "snes", [{"rom_name": "Renamed Game.zip", "file_path": "Renamed Game.zip", "rom_md5": "abc", "file_size": 3}])
    db.add_rom_sync_activity("drone-b", {
        "source_drone_id": "drone-a",
        "target_drone_id": "drone-b",
        "system": "snes",
        "rom_name": "Renamed Game.zip",
        "rom_md5": "abc",
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


def test_drone_sync_activity_endpoint_upserts_by_sync_id(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
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
            "rom_md5": "abc",
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
    assert rows[0]["rom_md5"] == "abc"


def test_sync_rom_action_payload_includes_only_source_devices_with_rom(client):
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("test@example.com")
    db.create_device(user["id"], "source-without-rom", "Source Without ROM", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-with-rom", "Source With ROM", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
    db.add_roms("source-with-rom", "snes", [{"rom_name": "Game.zip", "file_path": "Game.zip", "rom_md5": "abc", "file_size": 8}])

    response = client.post(
        "/api/devices/target-c/sync-rom",
        headers={"Authorization": f"Bearer {token}"},
        json={"system_name": "snes", "file_path": "Game.zip", "rom_md5": "abc", "file_size": 8},
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


def test_rom_metadata_upload_persists_bios_and_master_bios(client):
    client.post("/api/auth/register", json={"email": "bios@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "bios@example.com", "password": "testpass123"},
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
            "bios": [{"name": "flash.bin", "path": "dc/flash.bin", "byte_count": 9, "md5": "bios-md5"}],
            "gamelists": [],
        },
    )
    assert response.status_code == 200
    assert response.json()["bios_count"] == 1

    bios_response = client.get("/api/devices/drone-a/bios", headers={"Authorization": f"Bearer {token}"})
    assert bios_response.status_code == 200
    assert bios_response.json()["bios"][0]["file_path"] == "dc/flash.bin"
    assert bios_response.json()["bios"][0]["bios_md5"] == "bios-md5"

    master_response = client.get("/api/devices/drone-b/master-bios", headers={"Authorization": f"Bearer {token}"})
    assert master_response.status_code == 200
    row = master_response.json()["bios"][0]
    assert row["file_path"] == "dc/flash.bin"
    assert row["present_on_selected"] is False
    assert row["devices"] == [{"device_id": "drone-a", "device_name": "Drone A"}]


def test_sync_bios_action_payload_includes_only_source_devices_with_bios(client):
    client.post("/api/auth/register", json={"email": "sync-bios@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "sync-bios@example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("sync-bios@example.com")
    db.create_device(user["id"], "source-without-bios", "Source Without BIOS", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-with-bios", "Source With BIOS", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
    db.add_bios("source-with-bios", [{"bios_name": "flash.bin", "file_path": "dc/flash.bin", "bios_md5": "bios-md5", "file_size": 8}])

    response = client.post(
        "/api/devices/target-c/sync-bios",
        headers={"Authorization": f"Bearer {token}"},
        json={"file_path": "dc/flash.bin", "bios_md5": "bios-md5", "file_size": 8},
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
    client.post("/api/auth/register", json={"email": "artwork@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "artwork@example.com", "password": "testpass123"},
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
    client.post("/api/auth/register", json={"email": "sync-artwork@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "sync-artwork@example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("sync-artwork@example.com")
    db.create_device(user["id"], "source-without-artwork", "Source Without Artwork", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-with-artwork", "Source With Artwork", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
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
    client.post("/api/auth/register", json={"email": "bulk-artwork@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "bulk-artwork@example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("bulk-artwork@example.com")
    db.create_device(user["id"], "source-a", "Source A", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "source-b", "Source B", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "target-c", "Target C", {"ip_address": "10.0.0.4"}, raw_token="c")
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
    client.post("/api/auth/register", json={"email": "test@example.com", "password": "testpass123"})
    token = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "password": "testpass123"},
    ).json()["access_token"]
    user = db.get_user_by_email("test@example.com")
    db.create_device(user["id"], "drone-a", "Drone A", {"ip_address": "10.0.0.2"}, raw_token="a")
    db.create_device(user["id"], "drone-b", "Drone B", {"ip_address": "10.0.0.3"}, raw_token="b")
    db.create_device(user["id"], "drone-c", "Drone C", {"ip_address": "10.0.0.4"}, raw_token="c")
    db.add_roms("drone-a", "snes", [{"rom_name": "A.zip", "file_path": "A.zip", "rom_md5": "aaa", "file_size": 8}])
    db.add_roms("drone-b", "snes", [{"rom_name": "B.zip", "file_path": "B.zip", "rom_md5": "bbb", "file_size": 9}])
    db.add_roms("drone-c", "snes", [{"rom_name": "C.zip", "file_path": "C.zip", "rom_md5": "ccc", "file_size": 10}])

    response = client.post(
        "/api/bulk-sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_ids": ["drone-a", "drone-b"], "systems": ["snes"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["action_count"] == 2
    assert payload["queued_rom_count"] == 2

    claim_a = client.post("/api/devices/drone-a/actions/claim", headers={"Authorization": "Bearer a"}, json={})
    claim_b = client.post("/api/devices/drone-b/actions/claim", headers={"Authorization": "Bearer b"}, json={})
    assert claim_a.status_code == 200
    assert claim_b.status_code == 200
    assert claim_a.json()["actions"][0]["payload"]["roms"][0]["file_path"] == "B.zip"
    assert claim_a.json()["actions"][0]["payload"]["roms"][0]["devices"] == [{"device_id": "drone-b", "device_name": "Drone B"}]
    assert claim_b.json()["actions"][0]["payload"]["roms"][0]["file_path"] == "A.zip"
    assert claim_b.json()["actions"][0]["payload"]["roms"][0]["devices"] == [{"device_id": "drone-a", "device_name": "Drone A"}]


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


def test_action_claim_returns_all_pending_actions_in_order(client):
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
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
    db.populate_fake_data()
    token = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
    ).json()["access_token"]

    response = client.post(
        "/api/devices/arcade-cabinet-001/actions",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": "shutdown"},
    )

    assert response.status_code == 400


def test_selected_drone_actions_ui_omits_shutdown_and_collect_data_buttons():
    html = Path(__file__).resolve().parents[1].joinpath("src/overmind/templates/index.html").read_text(encoding="utf-8")
    js = Path(__file__).resolve().parents[1].joinpath("src/overmind/static/js/overmind.js").read_text(encoding="utf-8")
    assert "queueDeviceAction('shutdown')" not in html
    assert ">Shutdown<" not in html
    assert "onclick=\"queueDeviceAction('collect_game_logs')\"" not in html
    assert "onclick=\"queueDeviceAction('collect_emulator_configs')\"" not in html
    assert "onclick=\"queueDeviceAction('collect_log_sources')\"" not in html
    assert "loadGameLogs({queue: true})" in js
    assert "loadDeviceConfigs({queue: true})" in js


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
    assert heartbeat_response.json()["action"]["id"] == action_id
    assert heartbeat_response.json()["action"]["status"] == "in_progress"

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
    assert len(detail["peer_checks"]) == 1
    assert detail["peer_checks"][0]["status"] == "pass"
    assert detail["peer_checks"][0]["target_address"] == "https://new.example"
    assert detail["peer_checks"][0]["target_name"]


def test_heartbeat_ignores_rom_metadata_and_rom_metadata_endpoint_persists(client):
    db.populate_fake_data()
    login_response = client.post(
        "/api/auth/login",
        json={"email": "demo@example.com", "password": "DemoPass123"},
    )
    token = login_response.json()["access_token"]

    heartbeat_response = client.post(
        "/api/devices/arcade-cabinet-001/heartbeat",
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
    assert heartbeat_response.status_code == 200
    swarm_peer = next(row for row in heartbeat_response.json()["swarm"] if row["device_id"] == "arcade-cabinet-001")
    assert swarm_peer["reachable_url"] == "https://bff-drone-a:8443"

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
            "roms_root": "/userdata/roms",
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [
                {"system": "snes", "name": "Super Metroid", "rom_file": "Super Metroid (USA).zip", "byte_count": 32, "rom_md5": "aaa"},
                {"system": "snes", "name": "Chrono Trigger", "rom_file": "Chrono Trigger (USA).zip", "byte_count": 32, "rom_md5": "bbb"},
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
            "roms_root": "/userdata/roms",
            "systems": [{"name": "snes", "rom_count": 2}],
            "roms": [
                {"system": "snes", "name": "Super Metroid", "rom_file": "Super Metroid (USA).zip", "byte_count": 32, "rom_md5": "aaa"},
                {"system": "snes", "name": "Chrono Trigger", "rom_file": "Chrono Trigger (USA).zip", "byte_count": 32, "rom_md5": "bbb"},
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
    assert {rom["rom_md5"] for rom in snes_roms} == {"aaa", "bbb"}
    assert {rom["file_path"] for rom in snes_roms} == {"Super Metroid (USA).zip", "Chrono Trigger (USA).zip"}
    assert len(roms_response.json()["systems"]["snes"]) != before_snes_count

    empty_response = client.post(
        "/api/devices/arcade-cabinet-001/rom-metadata",
        headers={"Authorization": "Bearer demo-local-drone-token"},
        json={
            "device_id": "arcade-cabinet-001",
            "type": "rom_metadata",
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


def test_invitation_register_auto_verifies_and_rejects_mismatched_email(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "testpass123"}).json()["access_token"]
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
        json={"email": "someone-else@example.com", "password": "testpass123", "invitation_token": raw_token},
    )
    assert mismatch.status_code == 403

    registered = client.post(
        "/api/auth/register",
        json={"email": "new-user@example.com", "password": "testpass123", "invitation_token": raw_token},
    )
    assert registered.status_code == 200
    new_user = db.get_user_by_email("new-user@example.com")
    assert new_user["email_verified"] is True
    assert db.get_swarm_member(swarm_id, new_user["id"])["role"] == "overseer"


def test_invitation_accept_is_idempotent_after_invite_registration(client):
    client.post("/api/auth/register", json={"email": "owner@example.com", "password": "testpass123"})
    owner_token = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "testpass123"}).json()["access_token"]
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
        json={"email": "new-overseer@example.com", "password": "testpass123", "invitation_token": raw_token},
    )
    assert registered.status_code == 200

    login = client.post(
        "/api/auth/login",
        json={"email": "new-overseer@example.com", "password": "testpass123"},
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
