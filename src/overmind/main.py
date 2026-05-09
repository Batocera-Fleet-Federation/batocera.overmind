"""Main FastAPI application."""

import os
import subprocess
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from datetime import timedelta
from typing import Optional

from overmind.models import (
    UserRegister, UserLogin, User, DeviceRegister,
    RomListUpdate, GamePlayLog
)
from overmind.db import db
from overmind import auth

app = FastAPI(
    title="Batocera Overmind API",
    description="API for Batocera system management and game tracking",
    version="0.1.0",
)


def ensure_self_signed_cert() -> tuple[Path | None, Path | None]:
    """Create a self-signed TLS certificate if one does not already exist."""
    cert_dir = Path(os.getenv("TLS_SELF_SIGNED_DIR", "./local-data/certs"))
    cert_dir.mkdir(parents=True, exist_ok=True)

    key_file = cert_dir / "server.key"
    cert_file = cert_dir / "server.crt"
    if key_file.exists() and cert_file.exists():
        return key_file, cert_file

    cmd = [
        "openssl",
        "req",
        "-x509",
        "-nodes",
        "-days",
        "3650",
        "-newkey",
        "rsa:2048",
        "-keyout",
        str(key_file),
        "-out",
        str(cert_file),
        "-subj",
        "/CN=localhost",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return key_file, cert_file
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"⚠️  Unable to create self-signed certificate: {exc}")
        return None, None

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Authentication ====================

@app.post("/api/auth/register", response_model=User)
async def register(user_data: UserRegister):
    """Register a new user."""
    if db.user_exists(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    hashed_password = auth.hash_password(user_data.password)
    user_id = db.create_user(user_data.email, hashed_password, user_data.full_name)
    user = db.get_user(user_id)
    
    return User(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        created_at=user["created_at"]
    )


@app.post("/api/auth/login")
async def login(credentials: UserLogin):
    """Login user and return access token."""
    user = db.get_user_by_email(credentials.email)
    
    if not user or not auth.verify_password(credentials.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    access_token = auth.create_access_token(
        data={"sub": user["id"], "email": user["email"]},
        expires_delta=timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"]
        }
    }


async def verify_token(token: str) -> dict:
    """Verify JWT token and return decoded data."""
    payload = auth.decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    return payload


def get_current_user(authorization: Optional[str]) -> dict:
    """Dependency to get current user from Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header"
        )
    
    token = parts[1]
    payload = auth.decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    user_id = payload.get("sub")
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    return user


# ==================== Device Management ====================

@app.post("/api/devices/register")
async def register_device(device_data: DeviceRegister):
    """Register a Batocera device. Called by batocera.drone app."""
    # Verify user
    user = db.get_user_by_email(device_data.email)
    if not user or not auth.verify_password(device_data.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Check if device already registered
    if db.device_exists(user["id"], device_data.device_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device already registered for this user"
        )
    
    # Register device
    internal_id = db.create_device(
        user["id"],
        device_data.device_id,
        device_data.device_name,
        device_data.batocera_info.model_dump()
    )
    
    device = db.get_device(internal_id)
    return {
        "message": "Device registered successfully",
        "device": {
            "id": device["id"],
            "device_id": device["device_id"],
            "device_name": device["device_name"],
            "registered_at": device["registered_at"]
        }
    }


@app.get("/api/devices")
async def list_devices(authorization: Optional[str] = Header(default=None)):
    """List all devices for the authenticated user."""
    user = get_current_user(authorization)
    devices = db.get_user_devices(user["id"])
    
    return {
        "devices": [
            {
                "id": d["id"],
                "device_id": d["device_id"],
                "device_name": d["device_name"],
                "batocera_info": d["batocera_info"],
                "registered_at": d["registered_at"],
                "last_seen": d["last_seen"]
            }
            for d in devices
        ]
    }


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Get device details."""
    user = get_current_user(authorization)
    
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    
    return {
        "id": device["id"],
        "device_id": device["device_id"],
        "device_name": device["device_name"],
        "batocera_info": device["batocera_info"],
        "registered_at": device["registered_at"],
        "last_seen": device["last_seen"]
    }


@app.patch("/api/devices/{device_id}/name")
async def rename_device(
    device_id: str,
    payload: dict,
    authorization: Optional[str] = Header(default=None),
):
    """Rename a device from the UI."""
    user = get_current_user(authorization)
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    new_name = (payload.get("device_name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_name is required")

    db.update_device_name(device_id, new_name)
    updated = db.get_device_by_device_id(device_id)
    return {"device_id": updated["device_id"], "device_name": updated["device_name"]}


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Delete a device and its associated ROM/gameplay data."""
    user = get_current_user(authorization)
    if not db.delete_device(user["id"], device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"message": "Device deleted successfully", "device_id": device_id}


@app.get("/api/devices/{device_id}/actions")
async def list_device_actions(device_id: str, authorization: Optional[str] = Header(default=None)):
    """List actions for a device."""
    user = get_current_user(authorization)
    actions = db.get_device_actions(user["id"], device_id)
    if actions is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"actions": actions}


@app.post("/api/devices/{device_id}/actions")
async def create_device_action(
    device_id: str,
    payload: dict,
    authorization: Optional[str] = Header(default=None),
):
    """Queue a remote action for a device."""
    user = get_current_user(authorization)
    action_type = str(payload.get("action") or "").strip().lower()
    if action_type == "reboot":
        action_type = "restart"
    if action_type not in {"shutdown", "restart", "update"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action")
    action = db.create_device_action(user["id"], device_id, action_type)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"action": action}


def verify_device_credentials(payload: dict) -> dict:
    """Verify drone-supplied Overmind credentials."""
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    user = db.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return user


@app.post("/api/devices/{device_id}/actions/claim")
async def claim_device_action(device_id: str, payload: dict):
    """Claim the next pending action for a polling drone."""
    user = verify_device_credentials(payload)
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    action = db.claim_next_device_action(device_id)
    return {"action": action}


@app.post("/api/devices/{device_id}/actions/{action_id}/complete")
async def complete_device_action(device_id: str, action_id: str, payload: dict):
    """Mark a claimed device action completed or failed."""
    user = verify_device_credentials(payload)
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    result_status = str(payload.get("status") or "").strip().lower()
    if result_status not in {"completed", "failed"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status must be completed or failed")
    action = db.complete_device_action(device_id, action_id, result_status, payload.get("message"))
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    return {"action": action}


@app.get("/api/profile")
async def get_profile(authorization: Optional[str] = Header(default=None)):
    """Get profile and user settings."""
    user = get_current_user(authorization)
    return {
        "id": user["id"],
        "email": user["email"],
        "full_name": user.get("full_name"),
        "avatar_data_url": user.get("avatar_data_url"),
        "fleet_settings": user.get("fleet_settings", {}),
        "notification_settings": user.get("notification_settings", {}),
    }


@app.patch("/api/profile")
async def update_profile(payload: dict, authorization: Optional[str] = Header(default=None)):
    """Update profile and user settings."""
    user = get_current_user(authorization)
    user_id = user["id"]

    if "full_name" in payload or "avatar_data_url" in payload:
        db.update_user_profile(
            user_id,
            full_name=payload.get("full_name") if "full_name" in payload else None,
            avatar_data_url=payload.get("avatar_data_url") if "avatar_data_url" in payload else None,
        )

    if "fleet_settings" in payload and isinstance(payload["fleet_settings"], dict):
        db.update_user_fleet_settings(user_id, payload["fleet_settings"])

    if "notification_settings" in payload and isinstance(payload["notification_settings"], dict):
        db.update_user_notification_settings(user_id, payload["notification_settings"])

    updated = db.get_user(user_id)
    return {
        "id": updated["id"],
        "email": updated["email"],
        "full_name": updated.get("full_name"),
        "avatar_data_url": updated.get("avatar_data_url"),
        "fleet_settings": updated.get("fleet_settings", {}),
        "notification_settings": updated.get("notification_settings", {}),
    }


# ==================== ROM Management ====================

@app.post("/api/devices/{device_id}/roms")
async def update_device_roms(
    device_id: str,
    rom_data: RomListUpdate,
    authorization: Optional[str] = Header(default=None),
):
    """Update ROM list for a device."""
    user = get_current_user(authorization)
    
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    
    rom_ids = db.add_roms(device_id, rom_data.system_name, rom_data.roms)
    
    return {
        "message": "ROMs updated successfully",
        "system_name": rom_data.system_name,
        "rom_count": len(rom_ids),
        "rom_ids": rom_ids
    }


@app.get("/api/devices/{device_id}/roms")
async def get_device_roms(
    device_id: str,
    system_name: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """Get ROMs for a device."""
    user = get_current_user(authorization)
    
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    
    if system_name:
        roms = db.get_device_roms_by_system(device_id, system_name)
    else:
        roms = db.get_device_roms(device_id)
    
    # Group by system if not filtered
    if not system_name:
        grouped = {}
        for rom in roms:
            sys = rom.get("system_name")
            if sys not in grouped:
                grouped[sys] = []
            grouped[sys].append(rom)
        return {"systems": grouped}
    
    return {"roms": roms}


@app.get("/api/devices/{device_id}/systems")
async def get_device_systems(
    device_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """Get systems for a selected device."""
    user = get_current_user(authorization)
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found",
        )
    return {"systems": db.get_device_systems_summary(device_id)}


# ==================== Game Play Logging ====================

@app.post("/api/devices/{device_id}/gameplay")
async def log_gameplay(
    device_id: str,
    gameplay_data: GamePlayLog,
    authorization: Optional[str] = Header(default=None),
):
    """Log a game play session."""
    user = get_current_user(authorization)
    
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    
    gamelog_id = db.log_gameplay(
        device_id,
        gameplay_data.system_name,
        gameplay_data.game_name,
        gameplay_data.duration_seconds
    )
    
    if not gamelog_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to log gameplay"
        )
    
    return {
        "message": "Gameplay logged successfully",
        "gamelog_id": gamelog_id
    }


@app.get("/api/devices/{device_id}/gamelogs")
async def get_device_gamelogs(
    device_id: str,
    system_name: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """Get game play logs for a device."""
    user = get_current_user(authorization)
    
    device = db.get_device_by_device_id(device_id)
    if not device or device["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    
    if system_name:
        gamelogs = db.get_device_gamelogs_by_system(device_id, system_name)
    else:
        gamelogs = db.get_device_gamelogs(device_id)
    
    return {"gamelogs": gamelogs}


@app.get("/api/systems")
async def list_systems(authorization: Optional[str] = Header(default=None)):
    """List systems with ROM counts across all user devices."""
    user = get_current_user(authorization)
    return {"systems": db.get_user_systems_summary(user["id"])}


# ==================== UI ====================

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serve the web UI."""
    return get_ui_html()


@app.get("/favicon.ico")
async def favicon() -> Response:
    """Return an empty favicon response to avoid browser 404 noise."""
    return Response(status_code=204)


def get_ui_html() -> str:
    """Get the HTML for the web UI."""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Batocera Overmind</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }
            
            body {
                font-family: "Nunito", -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                background: #f8f9fc;
                min-height: 100vh;
            }
            
            .container {
                width: 100%;
                max-width: 100%;
                min-height: 100vh;
                background: #f8f9fc;
            }
            
            header {
                background: #4e73df;
                color: white;
                padding: 0;
            }
            
            header h1 {
                font-size: 1.2rem;
                margin: 0;
            }
            
            header p {
                font-size: 1.1em;
                opacity: 0.9;
            }
            
            main {
                padding: 1rem;
            }
            
            .nav-tabs {
                display: flex;
                gap: 10px;
                margin-bottom: 12px;
                border-bottom: none;
                flex-wrap: wrap;
            }
            
            .nav-tabs button {
                background: none;
                border: none;
                padding: 10px 20px;
                cursor: pointer;
                font-size: 1em;
                color: #666;
                border-bottom: 3px solid transparent;
                transition: all 0.3s ease;
            }
            
            .nav-tabs button.active {
                color: #fff;
                background: #4e73df;
                border-bottom-color: transparent;
            }

            .sub-nav-btn {
                background: #f8f9fa;
                color: #1f2937;
                border: 1px solid #dee2e6;
                border-radius: 6px;
                margin-bottom: 8px;
                width: 100%;
                text-align: left;
            }

            .sub-nav-btn.active {
                background: #1e3a8a;
                color: #ffffff;
                border-color: #1e3a8a;
            }

            .app-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 16px;
                margin-bottom: 12px;
                padding: .75rem 1rem;
                background: #4e73df;
                border: 1px solid #4e73df;
                border-radius: .5rem;
                box-shadow: 0 .15rem 1rem 0 rgba(58,59,69,.05);
            }

            .top-nav {
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
            }

            .top-actions {
                display: flex;
                align-items: center;
                gap: 10px;
            }

            .top-nav .nav-btn {
                color: #ffffff !important;
                border-color: rgba(255,255,255,.55) !important;
                background: transparent !important;
            }

            .top-nav .nav-btn:hover {
                color: #ffffff !important;
                border-color: #ffffff !important;
                background: rgba(255,255,255,.12) !important;
            }

            .top-nav .nav-btn.active {
                color: #4e73df !important;
                background: #ffffff !important;
                border-color: #ffffff !important;
            }

            .profile-chip {
                background: transparent;
                border: none;
                padding: 0;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 42px;
                height: 42px;
                border-radius: 9999px;
                overflow: hidden;
                cursor: pointer;
            }

            .avatar-top-right {
                width: 42px;
                height: 42px;
                object-fit: cover;
                display: none;
            }

            .avatar-fallback {
                width: 42px;
                height: 42px;
                border-radius: 9999px;
                background: #e5e7eb;
                color: #111827;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-size: 20px;
            }
            
            .nav-tabs button:hover {
                color: #667eea;
            }
            
            .content-section {
                display: none;
            }
            
            .content-section.active {
                display: block;
            }
            
            .form-group {
                margin-bottom: 20px;
            }
            
            label {
                display: block;
                margin-bottom: 8px;
                font-weight: 500;
                color: #333;
            }
            
            input[type="text"],
            input[type="email"],
            input[type="password"],
            textarea {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 1em;
                font-family: inherit;
            }
            
            input[type="text"]:focus,
            input[type="email"]:focus,
            input[type="password"]:focus,
            textarea:focus {
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            }
            
            button {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 12px 24px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 1em;
                font-weight: 500;
                transition: transform 0.2s ease, box-shadow 0.2s ease;
            }
            
            button:hover {
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
            }
            
            button:active {
                transform: translateY(0);
            }
            
            .message {
                padding: 15px;
                border-radius: 5px;
                margin-bottom: 20px;
                display: none;
            }
            
            .message.success {
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
                display: block;
            }
            
            .message.error {
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
                display: block;
            }
            
            .device-card {
                background: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 15px;
            }
            
            .device-card h3 {
                color: #667eea;
                margin-bottom: 10px;
            }
            
            .device-info {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin: 15px 0;
            }
            
            .info-item {
                background: white;
                padding: 10px;
                border-radius: 5px;
                border-left: 3px solid #667eea;
            }
            
            .info-label {
                font-weight: 600;
                color: #666;
                font-size: 0.9em;
            }
            
            .info-value {
                color: #333;
                word-break: break-all;
            }
            
            .logout-btn {
                background: #dc3545;
                margin-top: 20px;
            }
            
            .logout-btn:hover {
                box-shadow: 0 5px 15px rgba(220, 53, 69, 0.4);
            }
            
            .form-row {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
            }
            
            .auth-form {
                max-width: 400px;
                margin: 0 auto;
            }
            
            .auth-form h2 {
                margin-bottom: 20px;
                color: #333;
            }
            
            .toggle-form {
                text-align: center;
                margin-top: 20px;
                color: #666;
            }
            
            .toggle-form button {
                background: none;
                color: #667eea;
                padding: 0;
                text-decoration: underline;
            }
            
            .toggle-form button:hover {
                transform: none;
                box-shadow: none;
            }
            
            .loading {
                display: inline-block;
                width: 20px;
                height: 20px;
                border: 3px solid rgba(255,255,255,.3);
                border-radius: 50%;
                border-top-color: white;
                animation: spin 1s ease-in-out infinite;
            }
            
            @keyframes spin {
                to { transform: rotate(360deg); }
            }
            
            .roms-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 15px;
                margin-top: 20px;
            }
            
            .rom-item {
                background: white;
                border: 1px solid #dee2e6;
                border-radius: 5px;
                padding: 15px;
            }
            
            .rom-item strong {
                color: #667eea;
            }
            
            .empty-state {
                text-align: center;
                padding: 40px;
                color: #999;
            }
            
            .empty-state svg {
                width: 64px;
                height: 64px;
                margin-bottom: 20px;
                opacity: 0.5;
            }

            .tree-view details {
                background: #fff;
                border: 1px solid #e3e6f0;
                border-radius: 6px;
                padding: 10px 12px;
                margin-bottom: 10px;
                box-shadow: 0 .15rem .5rem 0 rgba(58,59,69,.08);
            }

            .tree-view summary {
                cursor: pointer;
                font-weight: 600;
                color: #4e73df;
            }

            .tree-view ul {
                margin: 10px 0 0 20px;
                padding: 0;
            }

            .dashboard-layout {
                display: grid;
                grid-template-columns: 280px 1fr;
                gap: 24px;
                align-items: start;
            }

            .dashboard-sidebar {
                position: sticky;
                top: 10px;
            }

            .dashboard-content {
                min-height: 320px;
                background: #fff;
                border: 1px solid #e3e6f0;
                border-radius: .5rem;
                box-shadow: 0 .15rem 1rem 0 rgba(58,59,69,.05);
                padding: 1rem;
            }

            @media (max-width: 900px) {
                .dashboard-layout {
                    grid-template-columns: 1fr;
                }
            }

            :root {
                --admin-bg: #f2f5fa;
                --admin-surface: #ffffff;
                --admin-surface-muted: #f8f9fc;
                --admin-border: #e3e6f0;
                --admin-sidebar: #24406c;
                --admin-sidebar-accent: #4e73df;
                --admin-text: #1f2937;
                --admin-muted: #6b7280;
            }

            body {
                background: radial-gradient(circle at top right, #dbe6ff 0%, #f2f5fa 45%, #eef2f8 100%);
                color: var(--admin-text);
                font-family: "Nunito", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }

            .layout-shell { min-height: 100vh; }

            .sidebar {
                background: linear-gradient(180deg, #224abe 0%, var(--admin-sidebar) 100%);
                color: #fff;
                min-height: 100vh;
                border-right: 1px solid rgba(255, 255, 255, 0.14);
                box-shadow: 0 0.15rem 1rem rgba(0, 0, 0, 0.14);
            }

            .brand-block { border-bottom: 1px solid rgba(255, 255, 255, 0.18); }

            .brand-mark {
                width: 2rem;
                height: 2rem;
                border-radius: 50%;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                background: rgba(255, 255, 255, 0.14);
                border: 1px solid rgba(255, 255, 255, 0.24);
            }

            .menu-label {
                font-size: 0.72rem;
                letter-spacing: 0.07em;
                text-transform: uppercase;
                color: rgba(255, 255, 255, 0.64);
                font-weight: 700;
            }

            .sidebar .btn {
                border: 0;
                text-align: left;
                color: rgba(255, 255, 255, 0.92) !important;
                background: transparent !important;
                padding: 0.62rem 0.8rem;
                border-radius: 0.45rem;
                font-weight: 700;
                font-size: 0.92rem;
                transform: none !important;
                box-shadow: none !important;
            }

            .sidebar .btn:hover,
            .sidebar .btn:focus,
            .sidebar .btn.active {
                background: rgba(255, 255, 255, 0.14) !important;
                color: #fff !important;
            }

            .topbar {
                background: var(--admin-surface);
                border: 1px solid var(--admin-border);
                border-radius: 0.75rem;
                box-shadow: 0 0.15rem 1rem rgba(58, 59, 69, 0.08);
                min-height: 4.1rem;
            }

            .app-shell {
                background: var(--admin-surface);
                border: 1px solid var(--admin-border);
                border-radius: 0.75rem;
                box-shadow: 0 0.15rem 1rem rgba(58, 59, 69, 0.08);
            }

            .app-container {
                width: 100%;
                max-width: 100%;
                min-height: auto;
                background: transparent;
            }

            .app-main { padding: 0; }
            header, .app-header { display: none !important; }
            .dashboard-content {
                min-height: 320px;
                background: transparent;
                border: 0;
                border-radius: 0;
                box-shadow: none;
                padding: 0;
            }

            .content-section.active { display: block; }
            .requires-auth { display: none; }
            body.is-authenticated .requires-auth { display: flex; }
            body.is-authenticated .sidebar .requires-auth { display: inline-flex; }
            .sidebar .requires-auth { display: none; }
            .device-view-btn.active {
                color: #fff !important;
                background: var(--admin-sidebar-accent) !important;
                border-color: var(--admin-sidebar-accent) !important;
            }
            .profile-nav-btn {
                align-items: center;
                justify-content: flex-start;
            }
            .profile-nav-btn .profile-chip {
                flex: 0 0 auto;
                border-color: rgba(255,255,255,.35);
            }
            .device-grid {
                display: grid;
                grid-template-columns: repeat(3, minmax(0, 1fr));
                gap: 1rem;
                margin-bottom: 1rem;
            }
            .device-tile {
                height: 100%;
                cursor: pointer;
                padding: 0;
                color: var(--admin-text);
                background: var(--admin-surface) !important;
                border: 1px solid var(--admin-border) !important;
                border-radius: 0.5rem;
                transition: border-color 0.15s ease, box-shadow 0.15s ease;
            }
            .device-tile:hover,
            .device-tile.active {
                border-color: #b4c7fb !important;
                box-shadow: 0 0.2rem 1rem rgba(78, 115, 223, 0.14) !important;
                transform: none !important;
            }
            .device-tile.active {
                background: #f7f9ff !important;
            }
            .device-detail-view {
                margin-bottom: 0;
            }

            label { color: var(--admin-text); }
            .text-muted { color: var(--admin-muted) !important; }
            .card-title, .fw-semibold, h1, h2, h3, h4, h5 { color: var(--admin-text); }
            .card, .device-card, .info-item, .tree-view details, input[type="text"], input[type="email"], input[type="password"], textarea {
                background: var(--admin-surface);
                border-color: var(--admin-border);
            }
            input[type="text"]:focus,
            input[type="email"]:focus,
            input[type="password"]:focus,
            textarea:focus {
                border-color: #9bb3f5;
                box-shadow: 0 0 0 0.2rem rgba(78, 115, 223, 0.2);
            }

            .btn {
                transform: none !important;
                box-shadow: none !important;
            }
            .btn:hover {
                transform: none !important;
                box-shadow: none !important;
            }
            .btn-primary,
            button.btn-primary {
                background: var(--admin-sidebar-accent) !important;
                border-color: var(--admin-sidebar-accent) !important;
                color: #fff !important;
            }
            .btn-primary:hover,
            button.btn-primary:hover {
                background: #2e59d9 !important;
                border-color: #2e59d9 !important;
            }
            .btn-outline-secondary, .btn-outline-primary {
                color: #4b5563;
                border-color: var(--admin-border);
                background: var(--admin-surface) !important;
            }
            .btn-outline-danger {
                color: #b42318 !important;
                border-color: #f1b7b2 !important;
                background: var(--admin-surface) !important;
            }
            .btn-outline-danger:hover {
                color: #fff !important;
                background: #b42318 !important;
                border-color: #b42318 !important;
            }
            button:not(.btn):not(.profile-chip) {
                background: var(--admin-sidebar-accent);
                color: #fff;
            }

            .auth-form {
                max-width: 440px;
                margin: 1rem auto;
            }
            .auth-form h2 {
                color: var(--admin-text);
                font-weight: 800;
            }
            .toggle-form button {
                background: transparent !important;
                color: var(--admin-sidebar-accent) !important;
            }
            .message.success, .message.error {
                display: block;
            }
            .device-card {
                border: 1px solid var(--admin-border);
                border-radius: 0.5rem;
            }
            .device-card h3, .tree-view summary, .rom-item strong {
                color: var(--admin-sidebar-accent);
            }
            .profile-chip {
                background: transparent !important;
                width: 2.25rem;
                height: 2.25rem;
                padding: 0;
                border: 1px solid var(--admin-border);
                border-radius: 50%;
                color: #9aa3b2;
                transform: none !important;
                box-shadow: none !important;
            }
            .profile-chip:hover {
                transform: none !important;
                box-shadow: none !important;
            }
            .avatar-top-right, .avatar-fallback {
                width: 2.25rem;
                height: 2.25rem;
            }
            .avatar-fallback {
                background: transparent;
                color: #9aa3b2;
                font-size: 1rem;
            }
            .empty-state {
                background: var(--admin-surface-muted);
                border: 1px dashed var(--admin-border);
                border-radius: 0.5rem;
                color: var(--admin-muted);
            }
            footer {
                background: var(--admin-surface);
                border-color: var(--admin-border);
            }

            @media (max-width: 991.98px) {
                .sidebar { min-height: auto; }
                .sidebar .btn { width: 100%; }
                .device-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            }
            @media (max-width: 575.98px) {
                .device-grid { grid-template-columns: 1fr; }
            }
        </style>
    </head>
    <body>
        <div class="container-fluid layout-shell py-3 py-lg-4">
            <div class="row g-3">
                <aside class="col-12 col-lg-3 col-xl-2">
                    <div class="sidebar rounded-3 h-100 p-3">
                        <div class="brand-block pb-3 mb-3">
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <span class="brand-mark"><i class="bi bi-controller"></i></span>
                                <div class="h5 mb-0 text-white">Batocera Admin</div>
                            </div>
                            <div class="small text-white-50">Systems and ROMs</div>
                        </div>
                        <div class="menu-label mb-2">Navigation</div>
                        <div class="d-grid gap-1 nav-actions">
                            <a href="#/devices" role="button" class="btn nav-btn active requires-auth" data-tab="devices" onclick="event.preventDefault(); switchTab('devices', this)"><i class="bi bi-hdd-network me-2"></i>Devices</a>
                            <a href="#/fleet" role="button" class="btn nav-btn requires-auth" data-tab="fleet" onclick="event.preventDefault(); switchTab('fleet', this)"><i class="bi bi-sliders me-2"></i>Fleet Settings</a>
                            <a href="#/notifications" role="button" class="btn nav-btn requires-auth" data-tab="notifications" onclick="event.preventDefault(); switchTab('notifications', this)"><i class="bi bi-bell me-2"></i>Notifications</a>
                            <a href="https://github.com/Batocera-Fleet-Federation/batocera.overmind" target="_blank" rel="noopener noreferrer" role="button" class="btn"><i class="bi bi-github me-2"></i>GitHub</a>
                            <a href="/docs" target="_blank" rel="noopener noreferrer" role="button" class="btn"><i class="bi bi-braces me-2"></i>API Docs</a>
                            <a href="#/profile" role="button" class="btn nav-btn requires-auth" data-tab="profile" onclick="event.preventDefault(); switchTab('profile', this)"><i class="bi bi-person me-2"></i>Profile</a>
                            <a href="#" role="button" class="btn requires-auth" onclick="event.preventDefault(); logout()"><i class="bi bi-box-arrow-right me-2"></i>Logout</a>
                        </div>
                    </div>
                </aside>
                <main id="app" class="col-12 col-lg-9 col-xl-10 app-main">
                    <div class="app-shell p-3 p-md-4">
                <!-- Auth Section -->
                <div id="auth-section" class="content-section active">
                    <div class="auth-form">
                        <div id="auth-message" class="message"></div>
                        
                        <!-- Login Form -->
                        <div id="login-form">
                            <h2>Login</h2>
                            <form onsubmit="handleLogin(event)">
                                <div class="form-group">
                                    <label>Email</label>
                                    <input type="email" id="login-email" required>
                                </div>
                                <div class="form-group">
                                    <label>Password</label>
                                    <input type="password" id="login-password" required>
                                </div>
                                <button class="btn btn-primary" type="submit">Login</button>
                            </form>
                            <div class="toggle-form">
                                Don't have an account?
                                <button onclick="toggleAuthForm()">Register</button>
                            </div>
                        </div>
                        
                        <!-- Register Form -->
                        <div id="register-form" style="display: none;">
                            <h2>Register</h2>
                            <form onsubmit="handleRegister(event)">
                                <div class="form-group">
                                    <label>Email</label>
                                    <input type="email" id="register-email" required>
                                </div>
                                <div class="form-group">
                                    <label>Full Name (optional)</label>
                                    <input type="text" id="register-name">
                                </div>
                                <div class="form-group">
                                    <label>Password (min. 8 characters)</label>
                                    <input type="password" id="register-password" minlength="8" required>
                                </div>
                                <button class="btn btn-primary" type="submit">Register</button>
                            </form>
                            <div class="toggle-form">
                                Already have an account?
                                <button onclick="toggleAuthForm()">Login</button>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Dashboard Section -->
                <div id="dashboard-section" class="content-section" style="display: none;">
                    <div id="selected-device-summary" class="mb-3 text-muted"></div>
                    <section class="dashboard-content">
                        <div id="devices-tab" class="content-section dashboard-tab active">
                            <div id="device-list-view">
                                <h3>Your Devices</h3>
                                <div id="devices-list"></div>
                            </div>
                            <div id="selected-device-workspace" class="device-card device-detail-view" style="display: none;">
                                <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                                    <div>
                                        <h4 id="selected-device-title" class="h5 mb-1">Selected Device</h4>
                                        <div id="selected-device-id" class="small text-muted"></div>
                                    </div>
                                    <div class="d-flex flex-wrap align-items-center gap-2">
                                        <button class="btn btn-outline-secondary btn-sm" onclick="backToDevices()">
                                            <i class="bi bi-arrow-left me-1"></i>Devices
                                        </button>
                                        <button class="btn btn-outline-secondary btn-sm" onclick="renameDevicePrompt(selectedDeviceId)">
                                            <i class="bi bi-pencil me-1"></i>Rename
                                        </button>
                                        <button class="btn btn-outline-danger btn-sm" onclick="deleteSelectedDevice()">
                                            <i class="bi bi-trash me-1"></i>Delete
                                        </button>
                                        <div class="btn-group" role="group" aria-label="Device views">
                                            <button class="btn btn-outline-primary btn-sm device-view-btn active" data-device-view="systems" onclick="switchDeviceView('systems', this)">
                                                <i class="bi bi-grid me-1"></i>Systems
                                            </button>
                                            <button class="btn btn-outline-primary btn-sm device-view-btn" data-device-view="gamelogs" onclick="switchDeviceView('gamelogs', this)">
                                                <i class="bi bi-clock-history me-1"></i>Game Logs
                                            </button>
                                            <button class="btn btn-outline-primary btn-sm device-view-btn" data-device-view="actions" onclick="switchDeviceView('actions', this)">
                                                <i class="bi bi-lightning-charge me-1"></i>Actions
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                <div id="device-systems-panel" class="device-subpanel">
                                    <div class="mb-3">
                                        <label class="form-label" for="device-rom-search">Search systems and ROMs</label>
                                        <input id="device-rom-search" class="form-control" type="search" placeholder="Type to filter systems and ROMs" oninput="handleDeviceRomSearch(event)">
                                    </div>
                                    <div id="systems-list"></div>
                                </div>
                                <div id="device-gamelogs-panel" class="device-subpanel" style="display:none;">
                                    <div id="gamelogs-list"></div>
                                </div>
                                <div id="device-actions-panel" class="device-subpanel" style="display:none;">
                                    <div class="d-flex flex-wrap gap-2 mb-3">
                                        <button class="btn btn-outline-danger btn-sm" onclick="queueDeviceAction('shutdown')"><i class="bi bi-power me-1"></i>Shutdown</button>
                                        <button class="btn btn-outline-danger btn-sm" onclick="queueDeviceAction('restart')"><i class="bi bi-arrow-clockwise me-1"></i>Restart</button>
                                        <button class="btn btn-outline-primary btn-sm" onclick="queueDeviceAction('update')"><i class="bi bi-download me-1"></i>Update</button>
                                        <button class="btn btn-outline-secondary btn-sm" onclick="loadDeviceActions()"><i class="bi bi-arrow-repeat me-1"></i>Refresh</button>
                                    </div>
                                    <div id="actions-list"></div>
                                </div>
                            </div>
                        </div>

                        <div id="profile-tab" class="content-section dashboard-tab" style="display: none;">
                            <h3>Profile</h3>
                            <div class="device-card">
                                <div class="form-group">
                                    <label>Avatar</label>
                                    <input type="file" id="profile-avatar-input" accept="image/*" onchange="handleAvatarSelected(event)">
                                </div>
                                <div class="form-group">
                                    <label>Name</label>
                                    <input type="text" id="profile-name-input" placeholder="Your display name">
                                </div>
                                <button class="btn btn-primary" onclick="saveProfile()">Save Profile</button>
                            </div>
                        </div>

                        <div id="fleet-tab" class="content-section dashboard-tab" style="display: none;">
                            <h3>Fleet Settings</h3>
                            <div class="device-card">
                                <label style="display:flex; gap:10px; align-items:center;">
                                    <input type="checkbox" id="fleet-auto-sync-roms">
                                    Auto-sync ROMs
                                </label>
                                <div style="margin-top: 14px;">
                                    <button class="btn btn-primary" onclick="saveFleetSettings()">Save Fleet Settings</button>
                                </div>
                            </div>
                        </div>

                        <div id="notifications-tab" class="content-section dashboard-tab" style="display: none;">
                            <h3>Notification Settings</h3>
                            <div class="device-card">
                                <label style="display:flex; gap:10px; align-items:center;">
                                    <input type="checkbox" id="notify-slack" onchange="toggleNotificationInputs()"> Notify Slack
                                </label>
                                <div class="form-group">
                                    <label>Slack Webhook</label>
                                    <input type="text" id="notify-slack-webhook" placeholder="https://hooks.slack.com/...">
                                </div>

                                <label style="display:flex; gap:10px; align-items:center;">
                                    <input type="checkbox" id="notify-discord" onchange="toggleNotificationInputs()"> Notify Discord
                                </label>
                                <div class="form-group">
                                    <label>Discord Webhook</label>
                                    <input type="text" id="notify-discord-webhook" placeholder="https://discord.com/api/webhooks/...">
                                </div>

                                <label style="display:flex; gap:10px; align-items:center;">
                                    <input type="checkbox" id="notify-email" onchange="toggleNotificationInputs()"> Notify Email
                                </label>
                                <div class="form-group">
                                    <label>Email Address</label>
                                    <input type="email" id="notify-email-address" placeholder="name@example.com" disabled readonly>
                                    <div class="small text-muted mt-1">Uses the logged-in account email.</div>
                                </div>

                                <div class="form-group">
                                    <label>Notify on</label>
                                    <label style="display:flex; gap:10px; align-items:center;">
                                        <input type="checkbox" id="notify-type-gamelist-update"> Gamelist update
                                    </label>
                                    <label style="display:flex; gap:10px; align-items:center;">
                                        <input type="checkbox" id="notify-type-device-offline"> Device offline
                                    </label>
                                    <label style="display:flex; gap:10px; align-items:center;">
                                        <input type="checkbox" id="notify-type-sync-failure"> Sync failure
                                    </label>
                                </div>

                                <button class="btn btn-primary" onclick="saveNotificationSettings()">Save Notification Settings</button>
                            </div>
                        </div>
                    </section>
                </div>
                        <footer class="mt-4 pt-3 border-top text-muted small">
                            Batocera Overmind centralized API console
                        </footer>
                    </div>
                </main>
            </div>
        </div>
        
        <script>
            let currentUser = null;
            let currentProfile = null;
            let currentDevices = [];
            let selectedDeviceId = null;
            let currentTab = 'devices';
            let currentDeviceView = 'systems';
            let currentDeviceSystems = {};
            let deviceRomSearchQuery = '';
            let systemPageState = {};
            const ROMS_PER_PAGE = 20;
            const pageMeta = {
                auth: ['Login', 'Access your fleet'],
                devices: ['Devices', 'Systems and ROMs'],
                profile: ['Profile', 'Account settings'],
                fleet: ['Fleet Settings', 'Sync preferences'],
                notifications: ['Notifications', 'Delivery preferences'],
            };

            document.addEventListener('DOMContentLoaded', () => {
                const token = localStorage.getItem('auth_token');
                if (token) {
                    authToken = token;
                    showDashboard();
                    loadProfile();
                    loadDevices();
                } else {
                    setPageChrome('auth');
                }
            });

            window.addEventListener('hashchange', () => {
                if (!authToken) return;
                applyRouteFromHash();
            });

            let authToken = localStorage.getItem('auth_token') || null;

            async function apiGet(path) {
                const response = await fetch(path, {
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (response.status === 401) {
                    logout();
                    showMessage('Session expired. Please log in again.', 'error');
                    throw new Error('Unauthorized');
                }
                return response;
            }

            async function apiPatch(path, payload) {
                const response = await fetch(path, {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${authToken}`
                    },
                    body: JSON.stringify(payload)
                });
                if (response.status === 401) {
                    logout();
                    showMessage('Session expired. Please log in again.', 'error');
                    throw new Error('Unauthorized');
                }
                return response;
            }

            async function apiDelete(path) {
                const response = await fetch(path, {
                    method: 'DELETE',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (response.status === 401) {
                    logout();
                    showMessage('Session expired. Please log in again.', 'error');
                    throw new Error('Unauthorized');
                }
                return response;
            }

            async function handleLogin(e) {
                e.preventDefault();
                const email = document.getElementById('login-email').value;
                const password = document.getElementById('login-password').value;
                const btn = e.target.querySelector('button');
                btn.disabled = true;
                try {
                    const response = await fetch('/api/auth/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, password })
                    });
                    if (!response.ok) throw new Error('Login failed');
                    const data = await response.json();
                    authToken = data.access_token;
                    currentUser = data.user;
                    localStorage.setItem('auth_token', authToken);
                    showDashboard();
                    await loadProfile();
                    await loadDevices();
                    showMessage('Login successful!', 'success');
                } catch (error) {
                    showMessage('Login failed: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }

            async function handleRegister(e) {
                e.preventDefault();
                const email = document.getElementById('register-email').value;
                const full_name = document.getElementById('register-name').value || null;
                const password = document.getElementById('register-password').value;
                const btn = e.target.querySelector('button');
                btn.disabled = true;
                try {
                    const response = await fetch('/api/auth/register', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, password, full_name })
                    });
                    if (!response.ok) {
                        const error = await response.json();
                        throw new Error(error.detail || 'Registration failed');
                    }
                    showMessage('Registration successful! Please login.', 'success');
                    setTimeout(() => toggleAuthForm(), 500);
                } catch (error) {
                    showMessage('Registration failed: ' + error.message, 'error');
                } finally {
                    btn.disabled = false;
                }
            }

            function logout() {
                authToken = null;
                currentUser = null;
                currentProfile = null;
                selectedDeviceId = null;
                currentDeviceView = 'systems';
                localStorage.removeItem('auth_token');
                document.body.classList.remove('is-authenticated');
                document.getElementById('auth-section').classList.add('active');
                document.getElementById('dashboard-section').classList.remove('active');
                document.getElementById('auth-section').style.display = 'block';
                document.getElementById('dashboard-section').style.display = 'none';
                document.getElementById('login-form').style.display = 'block';
                document.getElementById('register-form').style.display = 'none';
                setPageChrome('auth');
                window.location.hash = '#/devices';
            }

            function showDashboard() {
                document.body.classList.add('is-authenticated');
                document.getElementById('auth-section').classList.remove('active');
                document.getElementById('dashboard-section').classList.add('active');
                document.getElementById('auth-section').style.display = 'none';
                document.getElementById('dashboard-section').style.display = 'block';
                setPageChrome(currentTab);
            }

            function toggleAuthForm() {
                document.getElementById('login-form').style.display =
                    document.getElementById('login-form').style.display === 'none' ? 'block' : 'none';
                document.getElementById('register-form').style.display =
                    document.getElementById('register-form').style.display === 'none' ? 'block' : 'none';
            }

            async function loadProfile() {
                try {
                    const response = await apiGet('/api/profile');
                    if (!response.ok) throw new Error('Failed to load profile');
                    currentProfile = await response.json();
                    renderProfileUI();
                } catch (error) {
                    console.error('Error loading profile:', error);
                }
            }

            function renderProfileUI() {
                if (!currentProfile) return;
                document.getElementById('profile-name-input').value = currentProfile.full_name || '';

                document.getElementById('fleet-auto-sync-roms').checked = !!(currentProfile.fleet_settings || {}).auto_sync_roms;
                const ns = currentProfile.notification_settings || {};
                document.getElementById('notify-slack').checked = !!ns.notify_slack;
                document.getElementById('notify-discord').checked = !!ns.notify_discord;
                document.getElementById('notify-email').checked = !!ns.notify_email;
                document.getElementById('notify-slack-webhook').value = ns.slack_webhook || '';
                document.getElementById('notify-discord-webhook').value = ns.discord_webhook || '';
                document.getElementById('notify-email-address').value = currentProfile.email || '';
                const types = ns.types || {};
                document.getElementById('notify-type-gamelist-update').checked = !!types.gamelist_update;
                document.getElementById('notify-type-device-offline').checked = !!types.device_offline;
                document.getElementById('notify-type-sync-failure').checked = !!types.sync_failure;
                toggleNotificationInputs();
            }

            function toggleNotificationInputs() {
                const slackEnabled = document.getElementById('notify-slack').checked;
                const discordEnabled = document.getElementById('notify-discord').checked;
                const emailEnabled = document.getElementById('notify-email').checked;
                document.getElementById('notify-slack-webhook').disabled = !slackEnabled;
                document.getElementById('notify-discord-webhook').disabled = !discordEnabled;
                document.getElementById('notify-email-address').disabled = true;
            }

            async function handleAvatarSelected(event) {
                const file = event.target.files && event.target.files[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = async () => {
                    await saveProfile(reader.result);
                };
                reader.readAsDataURL(file);
            }

            async function saveProfile(avatarDataUrlOverride = null) {
                try {
                    const payload = {
                        full_name: document.getElementById('profile-name-input').value.trim() || null,
                    };
                    if (avatarDataUrlOverride !== null) payload.avatar_data_url = avatarDataUrlOverride;
                    const response = await apiPatch('/api/profile', payload);
                    if (!response.ok) throw new Error('Failed to save profile');
                    currentProfile = await response.json();
                    renderProfileUI();
                    showMessage('Profile updated.', 'success');
                } catch (error) {
                    console.error('Error saving profile:', error);
                }
            }

            async function saveFleetSettings() {
                try {
                    const response = await apiPatch('/api/profile', {
                        fleet_settings: {
                            auto_sync_roms: document.getElementById('fleet-auto-sync-roms').checked
                        }
                    });
                    if (!response.ok) throw new Error('Failed to save fleet settings');
                    currentProfile = await response.json();
                    showMessage('Fleet settings saved.', 'success');
                } catch (error) {
                    console.error('Error saving fleet settings:', error);
                }
            }

            async function saveNotificationSettings() {
                try {
                    const response = await apiPatch('/api/profile', {
                        notification_settings: {
                            notify_slack: document.getElementById('notify-slack').checked,
                            notify_discord: document.getElementById('notify-discord').checked,
                            notify_email: document.getElementById('notify-email').checked,
                            slack_webhook: document.getElementById('notify-slack-webhook').value.trim(),
                            discord_webhook: document.getElementById('notify-discord-webhook').value.trim(),
                            email_address: currentProfile.email || '',
                            types: {
                                gamelist_update: document.getElementById('notify-type-gamelist-update').checked,
                                device_offline: document.getElementById('notify-type-device-offline').checked,
                                sync_failure: document.getElementById('notify-type-sync-failure').checked
                            }
                        }
                    });
                    if (!response.ok) throw new Error('Failed to save notification settings');
                    currentProfile = await response.json();
                    renderProfileUI();
                    showMessage('Notification settings saved.', 'success');
                } catch (error) {
                    console.error('Error saving notification settings:', error);
                }
            }

            async function loadDevices() {
                try {
                    const response = await apiGet('/api/devices');
                    if (!response.ok) throw new Error('Failed to load devices');
                    const data = await response.json();
                    currentDevices = data.devices;
                    if (selectedDeviceId && !currentDevices.some(d => d.device_id === selectedDeviceId)) selectedDeviceId = null;
                    displayDevices();
                    updateSelectedDeviceSummary();
                    updateSelectedDeviceWorkspace();
                    applyRouteFromHash();
                } catch (error) {
                    console.error('Error loading devices:', error);
                }
            }

            function updateSelectedDeviceSummary() {
                const summary = document.getElementById('selected-device-summary');
                if (!summary) return;
                summary.style.display = selectedDeviceId ? 'none' : 'block';
                if (!selectedDeviceId) {
                    summary.textContent = 'Select a device to view systems, ROMs, and game logs.';
                    return;
                }
                const device = currentDevices.find(d => d.device_id === selectedDeviceId);
                summary.textContent = device ? `Selected device: ${device.device_name} (${device.device_id})` : `Selected device: ${selectedDeviceId}`;
            }

            function displayDevices() {
                const container = document.getElementById('devices-list');
                if (currentDevices.length === 0) {
                    container.innerHTML = '<div class="empty-state">No devices registered yet</div>';
                    return;
                }
                container.innerHTML = `
                    <div class="device-grid">
                        ${currentDevices.map(device => `
                            <button type="button" class="card device-tile text-start border shadow-sm ${device.device_id === selectedDeviceId ? 'active' : ''}" onclick="selectDevice('${device.device_id}')">
                                <div class="card-body">
                                    <div class="d-flex align-items-start justify-content-between gap-2 mb-2">
                                        <h5 class="card-title mb-0">${device.device_name}</h5>
                                        <i class="bi bi-hdd-network text-muted"></i>
                                    </div>
                                    <div class="small text-muted mb-3">Device ID</div>
                                    <code class="small d-block text-break">${device.device_id}</code>
                                    <div class="small text-muted mt-3">${device.last_seen ? `Last seen: ${new Date(device.last_seen).toLocaleString()}` : 'Last seen unavailable'}</div>
                                </div>
                            </button>
                        `).join('')}
                    </div>
                `;
            }

            function selectDevice(deviceId) {
                selectedDeviceId = deviceId;
                currentDeviceView = 'systems';
                currentDeviceSystems = {};
                systemPageState = {};
                deviceRomSearchQuery = '';
                displayDevices();
                updateSelectedDeviceSummary();
                updateSelectedDeviceWorkspace();
                switchTab('devices', null, false);
                switchDeviceView('systems', null, false);
                setRoute('devices', deviceId, 'systems');
            }

            async function loadGameLogs() {
                if (!selectedDeviceId) {
                    document.getElementById('gamelogs-list').innerHTML = '<div class="empty-state">Select a device in Devices to view game logs.</div>';
                    return;
                }
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/gamelogs`);
                    if (!response.ok) throw new Error('Failed to load game logs');
                    const data = await response.json();
                    displayGameLogs(data.gamelogs || []);
                } catch (error) {
                    console.error('Error loading game logs:', error);
                }
            }

            function displayGameLogs(logs) {
                const container = document.getElementById('gamelogs-list');
                if (!logs.length) {
                    container.innerHTML = '<div class="empty-state">No games played yet</div>';
                    return;
                }
                logs.sort((a, b) => new Date(b.played_at) - new Date(a.played_at));
                container.innerHTML = logs.map(log => `
                    <div class="card mb-2 border-left-info shadow-sm">
                        <div class="card-body py-2">
                            <strong>${log.game_name}</strong>
                            <div class="small text-muted mt-1">System: ${log.system_name}</div>
                            <div class="small text-secondary">${new Date(log.played_at).toLocaleString()}${log.duration_seconds ? ` • ${(log.duration_seconds / 60).toFixed(1)} minutes` : ''}</div>
                        </div>
                    </div>
                `).join('');
            }

            async function loadDeviceSystems() {
                if (!selectedDeviceId) {
                    document.getElementById('systems-list').innerHTML = '<div class="empty-state">Select a device to view systems.</div>';
                    return;
                }
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/roms`);
                    if (!response.ok) throw new Error('Failed to load device systems');
                    const data = await response.json();
                    currentDeviceSystems = data.systems || {};
                    displaySystemsTree();
                } catch (error) {
                    console.error('Error loading systems:', error);
                }
            }

            function handleDeviceRomSearch(event) {
                deviceRomSearchQuery = (event.target.value || '').trim().toLowerCase();
                systemPageState = {};
                displaySystemsTree();
            }

            function setSystemPage(systemName, page) {
                systemPageState[systemName] = Math.max(1, page);
                displaySystemsTree();
            }

            function filteredSystemEntries() {
                const query = deviceRomSearchQuery;
                return Object.entries(currentDeviceSystems).reduce((entries, [systemName, roms]) => {
                    const systemMatches = systemName.toLowerCase().includes(query);
                    const filteredRoms = !query || systemMatches
                        ? roms
                        : roms.filter(rom => String(rom.rom_name || '').toLowerCase().includes(query));
                    if (!query || systemMatches || filteredRoms.length) entries.push([systemName, filteredRoms]);
                    return entries;
                }, []);
            }

            function displaySystemsTree() {
                const container = document.getElementById('systems-list');
                const entries = filteredSystemEntries();
                if (!entries.length) {
                    container.innerHTML = '<div class="empty-state">No systems or ROMs matched your search.</div>';
                    return;
                }
                entries.sort((a, b) => a[0].localeCompare(b[0]));
                container.innerHTML = `
                    <div class="tree-view">
                        ${entries.map(([systemName, roms]) => {
                            const totalBytes = roms.reduce((sum, rom) => sum + Number(rom.file_size || 0), 0);
                            const totalMb = (totalBytes / 1024 / 1024).toFixed(2);
                            const totalPages = Math.max(1, Math.ceil(roms.length / ROMS_PER_PAGE));
                            const currentPage = Math.min(systemPageState[systemName] || 1, totalPages);
                            const start = (currentPage - 1) * ROMS_PER_PAGE;
                            const pageRoms = roms.slice(start, start + ROMS_PER_PAGE);
                            return `
                                <details>
                                    <summary>${systemName} (${roms.length} ROMs, ${totalMb} MB)</summary>
                                    <ul class="list-unstyled ms-3 mt-2">
                                        ${pageRoms.map(rom => `<li class="py-1 border-bottom small">${rom.rom_name}${rom.file_size ? ` <span class="text-muted">(${(rom.file_size / 1024 / 1024).toFixed(2)} MB)</span>` : ''}</li>`).join('')}
                                    </ul>
                                    <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 ms-3 mt-2 small text-muted">
                                        <span>Showing ${roms.length ? start + 1 : 0}-${Math.min(start + ROMS_PER_PAGE, roms.length)} of ${roms.length}</span>
                                        <div class="btn-group btn-group-sm" role="group" aria-label="${systemName} pages">
                                            <button class="btn btn-outline-secondary" ${currentPage <= 1 ? 'disabled' : ''} onclick="setSystemPage('${systemName.replace(/'/g, "\\'")}', ${currentPage - 1})">Previous</button>
                                            <button class="btn btn-outline-secondary" disabled>Page ${currentPage} of ${totalPages}</button>
                                            <button class="btn btn-outline-secondary" ${currentPage >= totalPages ? 'disabled' : ''} onclick="setSystemPage('${systemName.replace(/'/g, "\\'")}', ${currentPage + 1})">Next</button>
                                        </div>
                                    </div>
                                </details>
                            `;
                        }).join('')}
                    </div>
                `;
            }

            function updateSelectedDeviceWorkspace() {
                const workspace = document.getElementById('selected-device-workspace');
                const listView = document.getElementById('device-list-view');
                const title = document.getElementById('selected-device-title');
                const idNode = document.getElementById('selected-device-id');
                if (!workspace) return;
                if (!selectedDeviceId) {
                    workspace.style.display = 'none';
                    if (listView) listView.style.display = 'block';
                    return;
                }
                const device = currentDevices.find(d => d.device_id === selectedDeviceId);
                if (listView) listView.style.display = 'none';
                workspace.style.display = 'block';
                if (title) title.textContent = device ? device.device_name : 'Selected Device';
                if (idNode) idNode.textContent = device ? `Device ID: ${device.device_id}` : `Device ID: ${selectedDeviceId}`;
            }

            function backToDevices() {
                selectedDeviceId = null;
                currentDeviceView = 'systems';
                setRoute('devices', null, 'systems');
            }

            function switchDeviceView(viewName, buttonEl = null, updateUrl = true) {
                if (!selectedDeviceId) return;
                currentDeviceView = ['gamelogs', 'actions'].includes(viewName) ? viewName : 'systems';
                document.querySelectorAll('.device-view-btn').forEach(btn => btn.classList.remove('active'));
                const activeBtn = buttonEl || document.querySelector(`.device-view-btn[data-device-view="${currentDeviceView}"]`);
                if (activeBtn) activeBtn.classList.add('active');

                const systemsPanel = document.getElementById('device-systems-panel');
                const gamelogsPanel = document.getElementById('device-gamelogs-panel');
                const actionsPanel = document.getElementById('device-actions-panel');
                if (systemsPanel) systemsPanel.style.display = currentDeviceView === 'systems' ? 'block' : 'none';
                if (gamelogsPanel) gamelogsPanel.style.display = currentDeviceView === 'gamelogs' ? 'block' : 'none';
                if (actionsPanel) actionsPanel.style.display = currentDeviceView === 'actions' ? 'block' : 'none';

                if (currentDeviceView === 'systems') loadDeviceSystems();
                if (currentDeviceView === 'gamelogs') loadGameLogs();
                if (currentDeviceView === 'actions') loadDeviceActions();
                if (updateUrl) setRoute('devices', selectedDeviceId, currentDeviceView);
            }

            function setRoute(tabName, deviceId = selectedDeviceId, deviceView = currentDeviceView) {
                let hash = `#/${tabName}`;
                if (tabName === 'devices' && deviceId) hash = `#/devices/${encodeURIComponent(deviceId)}/${deviceView || 'systems'}`;
                if (window.location.hash !== hash) window.location.hash = hash; else applyRouteFromHash();
            }

            function parseRoute() {
                const raw = window.location.hash || '#/devices';
                const clean = raw.replace(/^#\\/?/, '');
                const parts = clean.split('/').filter(Boolean);
                const allowed = ['devices', 'profile', 'fleet', 'notifications'];
                if ((parts[0] === 'systems' || parts[0] === 'gamelogs') && parts[1]) {
                    return { tab: 'devices', deviceId: decodeURIComponent(parts[1]), deviceView: parts[0] };
                }
                const tab = allowed.includes(parts[0]) ? parts[0] : 'devices';
                const deviceId = tab === 'devices' && parts[1] ? decodeURIComponent(parts[1]) : null;
                const deviceView = tab === 'devices' && ['gamelogs', 'actions'].includes(parts[2]) ? parts[2] : 'systems';
                return { tab, deviceId, deviceView };
            }

            function applyRouteFromHash() {
                const route = parseRoute();
                if (route.tab === 'devices' && !route.deviceId) {
                    selectedDeviceId = null;
                } else if (route.deviceId && currentDevices.some(d => d.device_id === route.deviceId)) {
                    selectedDeviceId = route.deviceId;
                    currentDeviceView = route.deviceView || 'systems';
                }
                updateSelectedDeviceSummary();
                updateSelectedDeviceWorkspace();
                switchTab(route.tab, null, false);
                if (selectedDeviceId && route.tab === 'devices') switchDeviceView(currentDeviceView, null, false);
            }

            async function loadDeviceActions() {
                const container = document.getElementById('actions-list');
                if (!selectedDeviceId || !container) return;
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/actions`);
                    if (!response.ok) throw new Error('Failed to load device actions');
                    const data = await response.json();
                    const actions = data.actions || [];
                    if (!actions.length) {
                        container.innerHTML = '<div class="empty-state">No actions queued yet.</div>';
                        return;
                    }
                    container.innerHTML = actions.map(action => `
                        <div class="card mb-2 shadow-sm">
                            <div class="card-body py-2">
                                <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                                    <strong>${action.action}</strong>
                                    <span class="badge text-bg-secondary">${action.status}</span>
                                </div>
                                <div class="small text-muted mt-1">Created: ${action.created_at ? new Date(action.created_at).toLocaleString() : 'n/a'}</div>
                                ${action.message ? `<div class="small mt-1">${action.message}</div>` : ''}
                            </div>
                        </div>
                    `).join('');
                } catch (error) {
                    console.error('Error loading actions:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load actions.</div>';
                }
            }

            async function queueDeviceAction(actionName) {
                if (!selectedDeviceId) return;
                const labels = { shutdown: 'shutdown', restart: 'restart', update: 'update' };
                if (!window.confirm(`Queue ${labels[actionName] || actionName} for this device?`)) return;
                try {
                    const response = await fetch(`/api/devices/${selectedDeviceId}/actions`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify({ action: actionName })
                    });
                    if (response.status === 401) {
                        logout();
                        showMessage('Session expired. Please log in again.', 'error');
                        throw new Error('Unauthorized');
                    }
                    if (!response.ok) throw new Error('Failed to queue action');
                    await loadDeviceActions();
                    showMessage('Action queued.', 'success');
                } catch (error) {
                    console.error('Error queuing action:', error);
                }
            }

            async function renameDevicePrompt(deviceId) {
                if (!deviceId) return;
                const current = currentDevices.find(d => d.device_id === deviceId);
                const nextName = window.prompt('Enter device name:', current ? current.device_name : '');
                if (!nextName || !nextName.trim()) return;
                try {
                    const response = await apiPatch(`/api/devices/${deviceId}/name`, { device_name: nextName.trim() });
                    if (!response.ok) throw new Error('Failed to rename device');
                    await loadDevices();
                } catch (error) {
                    console.error('Error renaming device:', error);
                }
            }

            async function deleteSelectedDevice() {
                if (!selectedDeviceId) return;
                const current = currentDevices.find(d => d.device_id === selectedDeviceId);
                const label = current ? current.device_name : selectedDeviceId;
                if (!window.confirm(`Delete ${label}? This removes the device, ROM list, and game logs from Overmind.`)) return;
                try {
                    const response = await apiDelete(`/api/devices/${selectedDeviceId}`);
                    if (!response.ok) throw new Error('Failed to delete device');
                    selectedDeviceId = null;
                    currentDeviceView = 'systems';
                    await loadDevices();
                    setRoute('devices', null, 'systems');
                    showMessage('Device deleted.', 'success');
                } catch (error) {
                    console.error('Error deleting device:', error);
                }
            }

            function setPageChrome(tabName) {
                const meta = pageMeta[tabName] || pageMeta.devices;
                const title = document.getElementById('page-title');
                const subtitle = document.getElementById('page-subtitle');
                if (title) title.textContent = meta[0];
                if (subtitle) subtitle.textContent = meta[1];
            }

            function activateNav(tabName) {
                document.querySelectorAll('.nav-btn, .sub-nav-btn').forEach(btn => btn.classList.remove('active'));
                const btn = document.querySelector(`.nav-btn[data-tab="${tabName}"], .sub-nav-btn[data-tab="${tabName}"]`);
                if (btn) btn.classList.add('active');
            }

            function switchTab(tabName, buttonEl = null, updateUrl = true) {
                activateNav(tabName);
                document.querySelectorAll('.dashboard-tab').forEach(section => { section.style.display = 'none'; });
                const tabMap = {
                    devices: 'devices-tab',
                    profile: 'profile-tab',
                    fleet: 'fleet-tab',
                    notifications: 'notifications-tab',
                };
                const tabElement = document.getElementById(tabMap[tabName]);
                if (tabElement) tabElement.style.display = 'block';
                currentTab = tabName;
                if (tabName === 'devices') updateSelectedDeviceWorkspace();
                if (tabName === 'profile' || tabName === 'fleet' || tabName === 'notifications') renderProfileUI();
                setPageChrome(tabName);
                if (updateUrl) setRoute(tabName);
            }

            function showMessage(message, type) {
                const msgElement = document.getElementById('auth-message');
                msgElement.textContent = message;
                msgElement.className = `message ${type}`;
                setTimeout(() => {
                    msgElement.classList.remove('success', 'error');
                }, 5000);
            }
        </script>
    </body>
    </html>
    """


# ==================== Health Check ====================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}


# ==================== Startup ====================

@app.on_event("startup")
async def startup_event():
    """Print startup message and load fake data if requested."""
    key_file, cert_file = ensure_self_signed_cert()
    print("🎮 Batocera Overmind API started")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("🏠 UI: http://localhost:8000/")
    if key_file and cert_file:
        print(f"🔐 Self-signed cert ready: {cert_file} / {key_file}")
    
    # Load fake data if USE_FAKE_DATA environment variable is set to true
    if os.getenv("USE_FAKE_DATA", "").lower() == "true":
        print("\n📚 Loading sample data...")
        db.populate_fake_data()
        print("✓ Sample data loaded successfully!")
        print("  • 2 demo users")
        print("  • 3 sample devices")
        print("  • 10+ sample ROMs")
        print("  • 8 sample game plays")
        print("\n  Demo Credentials:")
        print("  Email: demo@example.com")
        print("  Password: DemoPass123")
        print("\n  Or:")
        print("  Email: arcade@example.com")
        print("  Password: ArcadePass123\n")


if __name__ == "__main__":
    import uvicorn
    key_file, cert_file = ensure_self_signed_cert()
    kwargs = {"host": "0.0.0.0", "port": 8000}
    if key_file and cert_file:
        kwargs["ssl_keyfile"] = str(key_file)
        kwargs["ssl_certfile"] = str(cert_file)
    uvicorn.run(app, **kwargs)
