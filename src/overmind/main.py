"""Main FastAPI application."""

import argparse
import os
import secrets
import subprocess
import urllib.parse
import urllib.request
import json
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from datetime import timedelta
from typing import Optional, Tuple

from overmind.models import (
    UserRegister, UserLogin, User, DeviceRegister,
    RomListUpdate, GamePlayLog, SocialAuthRequest
)
from overmind.db import db
from overmind import auth
from overmind.drone_security import generate_drone_token
from overmind.postgres_store import postgres_store

SUPPORTED_DEVICE_ACTIONS = {
    "shutdown",
    "restart",
    "update",
    "collect_rom_metadata",
    "collect_game_logs",
    "collect_emulator_configs",
    "collect_log_sources",
    "sync_rom",
    "sync_system",
}
SWARM_OFFLINE_THRESHOLD_SECONDS = int(os.getenv("SWARM_OFFLINE_THRESHOLD_SECONDS", "180"))

app = FastAPI(
    title="Batocera Overmind API",
    description="API for Batocera system management and game tracking",
    version="0.1.0",
)


def _tls_file_pair_usable(key_file: Path, cert_file: Path) -> bool:
    if not key_file.exists() or not cert_file.exists():
        return False
    checks = [
        ["openssl", "x509", "-in", str(cert_file), "-noout"],
        ["openssl", "pkey", "-in", str(key_file), "-noout"],
    ]
    try:
        for cmd in checks:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def ensure_self_signed_cert() -> Tuple[Optional[Path], Optional[Path]]:
    """Create a self-signed TLS certificate unless the configured pair is usable."""
    cert_dir = Path(os.getenv("TLS_SELF_SIGNED_DIR", "./local-data/certs")).expanduser()
    cert_dir.mkdir(parents=True, exist_ok=True)

    key_file = cert_dir / "server.key"
    cert_file = cert_dir / "server.crt"
    if _tls_file_pair_usable(key_file, cert_file):
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
        print(f"Unable to create self-signed certificate: {exc}")
        return None, None
    
def run_https_app() -> None:
    """Run Overmind with HTTPS, creating a self-signed cert when TLS files are not provided."""
    import uvicorn

    parser = argparse.ArgumentParser(description="Run Batocera Overmind")
    parser.add_argument("--host", default=os.getenv("OVERMIND_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OVERMIND_PORT", "8443")))
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("OVERMIND_RELOAD", "false").strip().lower() in {"1", "true", "yes", "on"},
    )
    parser.add_argument("--ssl-keyfile", default=None)
    parser.add_argument("--ssl-certfile", default=None)
    args = parser.parse_args()

    ssl_keyfile = args.ssl_keyfile
    ssl_certfile = args.ssl_certfile

    if not ssl_keyfile and not ssl_certfile:
        generated_keyfile, generated_certfile = ensure_self_signed_cert()
        ssl_keyfile = str(generated_keyfile) if generated_keyfile else None
        ssl_certfile = str(generated_certfile) if generated_certfile else None

        if ssl_keyfile and ssl_certfile:
            print(f"Using self-signed TLS certificate: {ssl_certfile}")

    elif not ssl_keyfile or not ssl_certfile:
        raise RuntimeError("Both --ssl-keyfile and --ssl-certfile must be specified together.")
    elif not _tls_file_pair_usable(Path(ssl_keyfile), Path(ssl_certfile)):
        print(f"Configured TLS certificate/key are unusable; manufacturing self-signed certificate instead: {ssl_certfile}")
        generated_keyfile, generated_certfile = ensure_self_signed_cert()
        ssl_keyfile = str(generated_keyfile) if generated_keyfile else None
        ssl_certfile = str(generated_certfile) if generated_certfile else None

    if not ssl_keyfile or not ssl_certfile:
        raise RuntimeError("HTTPS is required, but no TLS certificate/key could be loaded or generated.")

    uvicorn.run(
        "overmind.main:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OAUTH_PROVIDERS = {
    "google": {
        "client_id": "GOOGLE_CLIENT_ID",
        "client_secret": "GOOGLE_CLIENT_SECRET",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "user_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "client_id": "GITHUB_CLIENT_ID",
        "client_secret": "GITHUB_CLIENT_SECRET",
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "user_url": "https://api.github.com/user",
        "email_url": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
    },
}
oauth_states: dict[str, str] = {}


def oauth_provider_enabled(provider: str) -> bool:
    """Return whether a social auth provider has the required ENV VARs."""
    config = OAUTH_PROVIDERS.get(provider)
    return bool(config and os.getenv(config["client_id"]) and os.getenv(config["client_secret"]))


def get_public_base_url(request: Request) -> str:
    """Build redirect base URL from ENV or current request."""
    configured = os.getenv("OAUTH_REDIRECT_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def build_login_response(user: dict) -> dict:
    """Create the standard login response for a user."""
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


def device_response(device: dict) -> dict:
    """Public device shape for the Overmind UI."""
    last_seen = device.get("last_seen")
    online = False
    try:
        from datetime import datetime
        online = bool(last_seen and last_seen >= datetime.utcnow() - timedelta(seconds=SWARM_OFFLINE_THRESHOLD_SECONDS))
    except Exception:
        online = False
    cert = dict(device.get("certificate") or {})
    cert.pop("private_key", None)
    cert.pop("key", None)
    return {
        "id": device["id"],
        "device_id": device["device_id"],
        "device_name": device["device_name"],
        "batocera_info": device["batocera_info"],
        "system_info": device.get("system_info") or {},
        "registered_at": device["registered_at"],
        "last_seen": device["last_seen"],
        "network": device.get("network") or {},
        "resolved_network": device.get("resolved_network") or {"ipv4": [], "ipv6": []},
        "swarm_connected": bool(device.get("swarm_connected")),
        "rom_systems": device.get("rom_systems") or [],
        "auto_sync_policy": device.get("auto_sync_policy") or {"enabled": False, "systems": []},
        "last_speed_sample": device.get("last_speed_sample"),
        "emulator_configs": device.get("emulator_configs"),
        "log_sources": device.get("log_sources"),
        "token_rotated_at": device.get("token_rotated_at"),
        "api_port": device.get("api_port"),
        "scheme": device.get("scheme") or "https",
        "reachable_url": device.get("reachable_url"),
        "certificate": cert or None,
        "peer_checks": db.get_latest_peer_checks(device.get("device_id")) if device.get("device_id") else [],
        "online": online,
        "status": "online" if online else "offline",
    }


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
    
    return build_login_response(user)


@app.get("/api/auth/providers")
async def auth_providers():
    """Return social auth providers enabled by ENV VARs."""
    return {
        "providers": {
            provider: oauth_provider_enabled(provider)
            for provider in OAUTH_PROVIDERS.keys()
        }
    }


@app.post("/api/auth/{provider}")
async def social_auth_dev(provider: str, payload: SocialAuthRequest):
    """Sign up or log in with a configured social provider.

    This endpoint is useful for local/dev clients that already received a
    provider-verified email. Browser OAuth uses the start/callback routes below.
    """
    if not oauth_provider_enabled(provider):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{provider} auth is disabled")
    user = db.get_or_create_social_user(payload.email, payload.full_name, provider)
    return build_login_response(user)


@app.get("/api/auth/{provider}/start")
async def social_auth_start(provider: str, request: Request):
    """Redirect to a configured Google or GitHub OAuth screen."""
    config = OAUTH_PROVIDERS.get(provider)
    if not config or not oauth_provider_enabled(provider):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not configured")

    state = secrets.token_urlsafe(24)
    oauth_states[state] = provider
    redirect_uri = f"{get_public_base_url(request)}/api/auth/{provider}/callback"
    params = {
        "client_id": os.getenv(config["client_id"]),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
    }
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "select_account"
    return RedirectResponse(f"{config['auth_url']}?{urllib.parse.urlencode(params)}")


@app.get("/api/auth/{provider}/callback")
async def social_auth_callback(provider: str, request: Request):
    """Complete OAuth, create/login the Overlord, and return to the UI."""
    config = OAUTH_PROVIDERS.get(provider)
    code = request.query_params.get("code")
    state_value = request.query_params.get("state")
    if not config or not oauth_provider_enabled(provider) or not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth callback")
    if oauth_states.pop(state_value or "", None) != provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")

    redirect_uri = f"{get_public_base_url(request)}/api/auth/{provider}/callback"
    token_payload = urllib.parse.urlencode({
        "client_id": os.getenv(config["client_id"]),
        "client_secret": os.getenv(config["client_secret"]),
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    token_request = urllib.request.Request(
        config["token_url"],
        data=token_payload,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(token_request, timeout=10) as response:
        token_data = json.loads(response.read().decode())
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider did not return a token")

    user_request = urllib.request.Request(
        config["user_url"],
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(user_request, timeout=10) as response:
        provider_user = json.loads(response.read().decode())

    email = provider_user.get("email")
    full_name = provider_user.get("name") or provider_user.get("login")
    if provider == "github" and not email:
        email_request = urllib.request.Request(
            config["email_url"],
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(email_request, timeout=10) as response:
            emails = json.loads(response.read().decode())
        primary = next((item for item in emails if item.get("primary") and item.get("verified")), None)
        email = (primary or emails[0]).get("email") if emails else None
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider did not return an email")

    user = db.get_or_create_social_user(email, full_name, provider)
    login_data = build_login_response(user)
    token = urllib.parse.quote(login_data["access_token"])
    return RedirectResponse(f"/#oauth_token={token}&provider={provider}")


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


def get_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    return parts[1]


def get_current_drone(device_id: str, authorization: Optional[str]) -> dict:
    """Validate a Drone bearer token for Drone -> Overmind APIs."""
    device = db.verify_device_token(device_id, get_bearer_token(authorization))
    if not device:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Drone token")
    return device


# ==================== Device Management ====================

@app.post("/api/devices/register")
async def register_device(device_data: DeviceRegister, authorization: Optional[str] = Header(default=None)):
    """Register an authorized Drone and return its bearer token."""
    raw_auth_token = device_data.authorization_token or (get_bearer_token(authorization) if authorization else None)
    claimed_token = db.claim_integration_token(str(device_data.email or ""), raw_auth_token, device_data.device_id)
    if not claimed_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Drone authorization token")
    user = claimed_token["user"]
    integration_token = claimed_token["token"]

    batocera_info = device_data.batocera_info.model_dump()
    if device_data.api_port is not None:
        batocera_info["api_port"] = device_data.api_port
    if device_data.scheme:
        batocera_info["scheme"] = device_data.scheme
    if device_data.reachable_url:
        batocera_info["reachable_url"] = device_data.reachable_url

    if db.device_exists(user["id"], device_data.device_id):
        rotated = db.rotate_device_token(user["id"], device_data.device_id)
        if not rotated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        device = rotated["device"]
        db.set_device_authorization_token(user["id"], device_data.device_id, integration_token.get("id"))
        device["device_name"] = device_data.device_name
        db.update_device_last_seen(
            device["id"],
            network=batocera_info.get("network") if isinstance(batocera_info.get("network"), dict) else None,
            api_port=batocera_info.get("api_port"),
            scheme=str(batocera_info.get("scheme") or "").strip() or None,
            reachable_url=str(batocera_info.get("reachable_url") or "").strip() or None,
            certificate=batocera_info.get("certificate") if isinstance(batocera_info.get("certificate"), dict) else None,
            system_info=batocera_info.get("system_info") if isinstance(batocera_info.get("system_info"), dict) else None,
        )
        return {
            "message": "Drone already registered. Token rotated for this Drone.",
            "status": "approved",
            "device_id": device_data.device_id,
            "drone_token": rotated["token"],
        }

    raw_drone_token = generate_drone_token()
    internal_id = db.create_device(
        user["id"],
        device_data.device_id,
        device_data.device_name,
        batocera_info,
        raw_token=raw_drone_token,
        authorization_token_id=integration_token.get("id"),
    )
    device = db.get_device(internal_id)
    return {
        "message": "Drone registered to the Overlord.",
        "status": "approved",
        "device_id": device_data.device_id,
        "device": device_response(device),
        "drone_token": raw_drone_token,
    }


@app.get("/api/integration-tokens")
async def list_integration_tokens(authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    return {"tokens": db.get_integration_tokens(user["id"])}


@app.post("/api/integration-tokens")
async def create_integration_token(payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    token = db.create_integration_token(user["id"], str(payload.get("label") or "Drone onboarding"))
    public = {k: v for k, v in token.items() if k != "token_hash"}
    public["authorization_token"] = public.pop("raw_token_once")
    return {"token": public}


@app.delete("/api/integration-tokens/{token_id}")
async def revoke_integration_token(token_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    if not db.revoke_integration_token(user["id"], token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration token not found")
    return {"status": "revoked", "id": token_id}


@app.get("/api/drone-connections")
async def list_drone_connections(authorization: Optional[str] = Header(default=None)):
    """List pending drone connection attempts for the Overlord."""
    user = get_current_user(authorization)
    return {"connections": db.get_pending_drone_connections(user["id"])}


@app.post("/api/drone-connections/{device_id}/accept")
async def accept_drone_connection(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Accept a pending drone connection."""
    user = get_current_user(authorization)
    device = db.accept_pending_drone_connection(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone connection not found")
    return {
        "message": "Drone registered to the Overlord.",
        "device": device_response(device),
        "drone_token": device.pop("raw_token_once", None),
    }


@app.post("/api/drone-connections/{device_id}/deny")
async def deny_drone_connection(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Deny a pending drone connection."""
    user = get_current_user(authorization)
    if not db.deny_pending_drone_connection(user["id"], device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone connection not found")
    return {"message": "Drone connection denied.", "device_id": device_id}


@app.get("/api/devices")
async def list_devices(authorization: Optional[str] = Header(default=None)):
    """List all devices for the authenticated user."""
    user = get_current_user(authorization)
    devices = db.get_user_devices(user["id"])
    
    return {
        "devices": [
            device_response(d) for d in devices
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
    
    return device_response(device)


@app.get("/api/devices/{device_id}/peer-certificate/{peer_id}")
async def get_peer_certificate(device_id: str, peer_id: str, authorization: Optional[str] = Header(default=None)):
    """Return public certificate trust material for an approved peer Drone."""
    device = get_current_drone(device_id, authorization)
    peer = db.get_device_by_device_id(peer_id)
    if not peer or peer.get("user_id") != device.get("user_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer Drone not found")
    cert = peer.get("certificate") or {}
    public_cert = cert.get("public_certificate") or cert.get("certificate_pem")
    if not public_cert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer certificate not available")
    safe_meta = dict(cert)
    safe_meta.pop("private_key", None)
    safe_meta.pop("key", None)
    return {"device_id": peer_id, "certificate_pem": public_cert, "metadata": safe_meta}


@app.post("/api/devices/{device_id}/token/rotate")
async def rotate_device_token(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Rotate a Drone bearer token. The raw value is returned once."""
    user = get_current_user(authorization)
    rotated = db.rotate_device_token(user["id"], device_id)
    if not rotated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"device": device_response(rotated["device"]), "drone_token": rotated["token"]}


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


@app.patch("/api/devices/{device_id}/auto-sync")
async def update_device_auto_sync(
    device_id: str,
    payload: dict,
    authorization: Optional[str] = Header(default=None),
):
    """Update per-Drone ROM metadata sync policy."""
    user = get_current_user(authorization)
    systems = payload.get("systems") if isinstance(payload.get("systems"), list) else []
    policy = db.update_device_auto_sync_policy(user["id"], device_id, bool(payload.get("enabled")), systems)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"auto_sync_policy": policy}


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
    if action_type not in SUPPORTED_DEVICE_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action")
    action = db.create_device_action(user["id"], device_id, action_type, payload.get("payload") if isinstance(payload.get("payload"), dict) else {})
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"action": action}


@app.post("/api/devices/{device_id}/actions/claim")
async def claim_device_action(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Claim the next pending action for a polling drone."""
    get_current_drone(device_id, authorization)
    action = db.claim_next_device_action(device_id)
    return {"action": action}


@app.post("/api/devices/{device_id}/alive")
async def drone_alive(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Update drone last-seen and return the next pending action, if any."""
    device = get_current_drone(device_id, authorization)
    db.update_device_last_seen(
        device["id"],
        network=payload.get("network") if isinstance(payload.get("network"), dict) else None,
        rom_systems=payload.get("rom_systems") if isinstance(payload.get("rom_systems"), list) else None,
        api_port=payload.get("api_port") if payload.get("api_port") is not None else None,
        scheme=str(payload.get("scheme") or payload.get("protocol") or "").strip() or None,
        reachable_url=str(payload.get("reachable_url") or "").strip() or None,
        certificate=payload.get("certificate") if isinstance(payload.get("certificate"), dict) else None,
        system_info=payload.get("system_info") if isinstance(payload.get("system_info"), dict) else None,
    )
    if isinstance(payload.get("rom_metadata"), dict):
        db.store_rom_metadata(device_id, payload["rom_metadata"])
    action = db.claim_next_device_action(device_id)
    updated = db.get_device(device["id"])
    swarm = db.get_swarm_for_device(device_id, offline_seconds=SWARM_OFFLINE_THRESHOLD_SECONDS)
    return {"status": "ok", "action": action, "device": device_response(updated), "swarm": swarm}


@app.post("/api/devices/{device_id}/events")
async def add_drone_event(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Persist Drone telemetry events using the existing Drone bearer token."""
    get_current_drone(device_id, authorization)
    event = db.add_device_event(device_id, payload)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"event": event}


@app.post("/api/devices/{device_id}/peer-checks")
async def add_peer_checks(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Persist peer-to-peer health results reported by a Drone."""
    get_current_drone(device_id, authorization)
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    stored = db.add_peer_checks(device_id, results)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"results": stored}


@app.post("/api/devices/{device_id}/actions/{action_id}/complete")
async def complete_device_action(device_id: str, action_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Mark a claimed device action completed or failed."""
    get_current_drone(device_id, authorization)
    result_status = str(payload.get("status") or "").strip().lower()
    if result_status not in {"completed", "failed"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status must be completed or failed")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    action = db.complete_device_action(device_id, action_id, result_status, payload.get("message"), result)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    return {"action": action}


@app.post("/api/devices/{device_id}/speed")
async def add_speed_sample(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Store a Drone upload/download speed sample."""
    get_current_drone(device_id, authorization)
    sample = db.add_speed_sample(device_id, payload)
    if not sample:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    print(f"Speed sample accepted for {device_id}: up={sample.get('upload_mbps')} down={sample.get('download_mbps')}")
    return {"sample": sample}


@app.get("/api/devices/{device_id}/speed/download")
async def download_speed_probe(device_id: str, bytes: int = 262144, authorization: Optional[str] = Header(default=None)):
    """Return bounded bytes for a Drone to measure Overmind download throughput."""
    get_current_drone(device_id, authorization)
    size = max(1024, min(int(bytes), 2 * 1024 * 1024))
    return Response(content=b"0" * size, media_type="application/octet-stream")


@app.post("/api/devices/{device_id}/speed/upload")
async def upload_speed_probe(device_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """Accept bounded bytes for a Drone to measure Overmind upload throughput."""
    get_current_drone(device_id, authorization)
    body = await request.body()
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Speed probe payload too large")
    return {"bytes_received": len(body)}


@app.get("/api/devices/{device_id}/speed")
async def get_speed_samples(device_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    samples = db.get_speed_samples(user["id"], device_id)
    if samples is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"samples": samples}


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


@app.get("/api/devices/{device_id}/master-roms")
async def get_device_master_roms(
    device_id: str,
    q: Optional[str] = None,
    system: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    """Return master ROMs for the device, with optional server-side filters and pagination.

    Query params:
    - q: search string (system name or rom path/name)
    - system: exact system name filter
    - status: 'missing' or 'present'
    - page: page number starting at 1
    - per_page: number of rows per page
    """
    user = get_current_user(authorization)
    rows = db.get_master_roms_for_device(user["id"], device_id)
    if rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    filtered = rows
    if q:
        q_low = q.strip().lower()
        filtered = [r for r in filtered if q_low in (str(r.get('system_name') or '').lower() or '') or q_low in (str(r.get('file_path') or r.get('rom_name') or '').lower() or '')]
    if system:
        s_low = system.strip().lower()
        filtered = [r for r in filtered if (str(r.get('system_name') or '').lower() == s_low)]
    if status:
        stat = status.strip().lower()
        if stat == 'missing':
            filtered = [r for r in filtered if not r.get('present_on_selected')]
        elif stat == 'present':
            filtered = [r for r in filtered if r.get('present_on_selected')]

    if page < 1:
        page = 1
    per_page = max(1, min(per_page, 500))
    total = len(filtered)
    start = (page - 1) * per_page
    end = start + per_page
    page_rows = filtered[start:end]

    return {
        "roms": page_rows,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@app.post("/api/devices/{device_id}/sync-rom")
async def sync_device_rom(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    system_name = str(payload.get("system_name") or payload.get("system") or "").strip()
    rom_path = str(payload.get("file_path") or payload.get("rom_name") or "").strip()
    if not system_name or not rom_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_name and rom path are required")
    action = db.create_device_action(user["id"], device_id, "sync_rom", {
        "system_name": system_name,
        "rom_name": payload.get("rom_name") or rom_path,
        "file_path": rom_path,
        "rom_md5": payload.get("rom_md5"),
        "file_size": payload.get("file_size"),
    })
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    db.add_rom_sync_activity(device_id, {
        "sync_id": action["id"],
        "target_drone_id": device_id,
        "system": system_name,
        "rom_name": rom_path,
        "action": "download",
        "status": "pending",
        "file_size": payload.get("file_size"),
        "rom_md5": payload.get("rom_md5"),
    })
    return {"action": action}


@app.post("/api/devices/{device_id}/sync-system")
async def sync_device_system(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    system_name = str(payload.get("system_name") or payload.get("system") or "").strip()
    if not system_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_name is required")
    master_rows = db.get_master_roms_for_device(user["id"], device_id) or []
    missing = [
        row for row in master_rows
        if str(row.get("system_name") or "").lower() == system_name.lower() and not row.get("present_on_selected")
    ]
    action = db.create_device_action(user["id"], device_id, "sync_system", {"system_name": system_name, "roms": missing})
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    db.add_rom_sync_activity(device_id, {
        "sync_id": action["id"],
        "target_drone_id": device_id,
        "system": system_name,
        "rom_name": "*",
        "action": "download",
        "status": "pending",
    })
    return {"action": action}


@app.post("/api/devices/{device_id}/sync-activity")
async def add_device_sync_activity(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    get_current_drone(device_id, authorization)
    entry = db.add_rom_sync_activity(device_id, payload)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"activity": entry}


@app.get("/api/devices/{device_id}/sync-activity")
async def get_device_sync_activity(device_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    rows = db.get_rom_sync_activity(user["id"], device_id)
    if rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"activity": rows}


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
                --admin-bg: #101828;
                --admin-surface: #151f32;
                --admin-surface-muted: #1f2a44;
                --admin-border: #31405f;
                --admin-sidebar: #0b1020;
                --admin-sidebar-accent: #00c2ff;
                --admin-accent-hot: #ff3ea5;
                --admin-accent-coin: #ffbf3f;
                --admin-accent-green: #34d399;
                --admin-text: #ecf6ff;
                --admin-muted: #9fb0c9;
            }

            body {
                background:
                    linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px),
                    #101828;
                background-attachment: fixed;
                background-size: 42px 42px, 42px 42px, cover;
                color: var(--admin-text);
                font-family: "Nunito", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            }
            body::before {
                content: "";
                position: fixed;
                inset: 0;
                pointer-events: none;
                z-index: 0;
                background: repeating-linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.035) 1px, transparent 1px, transparent 4px);
                opacity: 0.35;
            }

            .layout-shell {
                min-height: 100vh;
                position: relative;
                z-index: 1;
            }
            .layout-shell > .row { flex-direction: column; }
            .layout-shell aside,
            .layout-shell main {
                width: 100%;
                max-width: 100%;
                flex: 0 0 auto;
            }

            .sidebar {
                background: linear-gradient(90deg, #111936 0%, var(--admin-sidebar) 100%);
                color: #fff;
                min-height: auto;
                border: 1px solid rgba(255, 255, 255, 0.14);
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
                background: rgba(0, 194, 255, 0.15);
                border: 1px solid rgba(0, 194, 255, 0.55);
                box-shadow: 0 0 18px rgba(0, 194, 255, 0.24);
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
                background: rgba(21, 31, 50, 0.86);
                border: 1px solid var(--admin-border);
                border-radius: 0.75rem;
                box-shadow: 0 0.15rem 1rem rgba(58, 59, 69, 0.08);
                min-height: 4.1rem;
                backdrop-filter: blur(10px);
            }

            .app-shell {
                background: rgba(21, 31, 50, 0.84);
                border: 1px solid var(--admin-border);
                border-radius: 0.75rem;
                box-shadow: 0 0.15rem 1rem rgba(58, 59, 69, 0.08);
                backdrop-filter: blur(10px);
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
                background: rgba(21, 31, 50, 0.9) !important;
                border: 1px solid var(--admin-border) !important;
                border-radius: 0.5rem;
                transition: border-color 0.15s ease, box-shadow 0.15s ease;
            }
            .device-tile:hover,
            .device-tile.active {
                border-color: var(--admin-sidebar-accent) !important;
                box-shadow: 0 0.2rem 1rem rgba(0, 194, 255, 0.18) !important;
                transform: none !important;
            }
            .device-tile.active {
                background: rgba(0, 194, 255, 0.12) !important;
            }
            .device-detail-view {
                margin-bottom: 0;
            }

            label { color: var(--admin-text); }
            .text-muted { color: var(--admin-muted) !important; }
            .card-title, .fw-semibold, h1, h2, h3, h4, h5 { color: var(--admin-text); }
            .card, .device-card, .info-item, .tree-view details, input[type="text"], input[type="email"], input[type="password"], textarea {
                background: rgba(21, 31, 50, 0.9);
                border-color: var(--admin-border);
                color: var(--admin-text);
            }
            input[type="text"]:focus,
            input[type="email"]:focus,
            input[type="password"]:focus,
            textarea:focus {
                background: rgba(21, 31, 50, 0.98);
                color: var(--admin-text);
                border-color: var(--admin-sidebar-accent);
                box-shadow: 0 0 0 0.18rem rgba(0, 194, 255, 0.18);
            }
            input::placeholder { color: #73849e; }

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
                color: #06111f !important;
                font-weight: 800;
            }
            .btn-primary:hover,
            button.btn-primary:hover {
                background: var(--admin-accent-hot) !important;
                border-color: var(--admin-accent-hot) !important;
                color: #fff !important;
            }
            .btn-outline-secondary, .btn-outline-primary {
                color: #c8d7ee;
                border-color: var(--admin-border);
                background: rgba(21, 31, 50, 0.9) !important;
            }
            .btn-outline-secondary:hover, .btn-outline-primary:hover {
                background: rgba(0, 194, 255, 0.12) !important;
                border-color: var(--admin-sidebar-accent) !important;
                color: #fff !important;
            }
            .btn-outline-danger {
                color: #b42318 !important;
                border-color: #f1b7b2 !important;
                background: rgba(21, 31, 50, 0.9) !important;
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
            .rom-item {
                background: rgba(31, 42, 68, 0.72);
                border-color: var(--admin-border);
                color: var(--admin-text);
            }
            .device-card h3, .tree-view summary, .rom-item strong {
                color: var(--admin-sidebar-accent);
            }
            .table {
                color: var(--admin-text);
                --bs-table-color: var(--admin-text);
                --bs-table-bg: transparent;
                --bs-table-border-color: var(--admin-border);
                --bs-table-striped-bg: rgba(255, 255, 255, 0.03);
                --bs-table-hover-bg: rgba(0, 194, 255, 0.08);
            }
            .table thead th {
                color: var(--admin-muted);
                border-color: var(--admin-border);
                font-size: 0.78rem;
                text-transform: uppercase;
            }
            .table td {
                border-color: var(--admin-border);
            }
            .tree-view details {
                margin-bottom: 0.75rem;
            }
            .tree-view li {
                border-color: rgba(255,255,255,0.08) !important;
                color: var(--admin-text);
            }
            .rom-browser-toolbar {
                background: rgba(31, 42, 68, 0.72);
                border: 1px solid var(--admin-border);
                border-radius: 0.5rem;
                padding: 0.9rem;
            }
            .profile-chip {
                background: transparent !important;
                width: 2.25rem;
                height: 2.25rem;
                padding: 0;
                border: 1px solid var(--admin-border);
                border-radius: 50%;
                color: var(--admin-muted);
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
                color: var(--admin-muted);
                font-size: 1rem;
            }
            .empty-state {
                background: rgba(31, 42, 68, 0.86);
                border: 1px dashed var(--admin-border);
                border-radius: 0.5rem;
                color: var(--admin-muted);
            }
            footer {
                background: transparent;
                border-color: var(--admin-border);
            }
            .connection-panel {
                background: rgba(0, 194, 255, 0.1);
                border: 1px solid rgba(0, 194, 255, 0.36);
                border-radius: 0.5rem;
                padding: 1rem;
                margin-bottom: 1rem;
            }
            .oauth-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 0.6rem;
                margin: 1rem 0;
            }
            .oauth-grid .btn:disabled {
                opacity: 0.42;
                cursor: not-allowed;
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
                    <div class="sidebar rounded-3 h-100 p-3 d-flex flex-column flex-lg-row align-items-lg-center gap-3">
                        <div class="brand-block pb-3 mb-3 pb-lg-0 mb-lg-0 pe-lg-3">
                            <div class="d-flex align-items-center gap-2 mb-1">
                                <span class="brand-mark"><i class="bi bi-controller"></i></span>
                                <div class="h5 mb-0 text-white">Batocera Overmind</div>
                            </div>
                            <div class="small text-white-50">Overlord command console</div>
                        </div>
                        <div class="menu-label mb-2 d-lg-none">Navigation</div>
                        <div class="d-flex flex-wrap gap-1 nav-actions ms-lg-auto">
                            <a href="#/devices" role="button" class="btn nav-btn active requires-auth" data-tab="devices" onclick="event.preventDefault(); selectedDeviceId = null; setRoute('devices', null, 'systems')"><i class="bi bi-hdd-network me-2"></i>Drones</a>
                            <a href="#/help" role="button" class="btn nav-btn requires-auth" data-tab="help" onclick="event.preventDefault(); switchTab('help', this)"><i class="bi bi-question-circle me-2"></i>Help</a>
                            <a href="#/notifications" role="button" class="btn nav-btn requires-auth" data-tab="notifications" onclick="event.preventDefault(); switchTab('notifications', this)"><i class="bi bi-bell me-2"></i>Notifications</a>
                            <a href="https://github.com/Batocera-Fleet-Federation/batocera.overmind" target="_blank" rel="noopener noreferrer" role="button" class="btn"><i class="bi bi-github me-2"></i>GitHub</a>
                            <a href="/docs" target="_blank" rel="noopener noreferrer" role="button" class="btn"><i class="bi bi-braces me-2"></i>API Docs</a>
                            <a href="#/profile" role="button" class="btn nav-btn requires-auth" data-tab="profile" onclick="event.preventDefault(); switchTab('profile', this)"><i class="bi bi-person me-2"></i>Overlord</a>
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
                            <h2>Overlord Login</h2>
                            <div class="oauth-grid">
                                <button id="google-login-btn" class="btn btn-outline-primary" type="button" onclick="startOAuth('google')" disabled><i class="bi bi-google me-1"></i>Google</button>
                                <button id="github-login-btn" class="btn btn-outline-primary" type="button" onclick="startOAuth('github')" disabled><i class="bi bi-github me-1"></i>GitHub</button>
                            </div>
                            <form onsubmit="handleLogin(event)">
                                <div class="form-group">
                                    <label>Email</label>
                                    <input type="email" id="login-email" required>
                                </div>
                                <div class="form-group">
                                    <label>Password</label>
                                    <input type="password" id="login-password" required>
                                </div>
                                <button class="btn btn-primary" type="submit">Log In</button>
                            </form>
                            <div class="toggle-form">
                                Don't have an account?
                                <button onclick="toggleAuthForm()">Sign up</button>
                            </div>
                        </div>
                        
                        <!-- Register Form -->
                        <div id="register-form" style="display: none;">
                            <h2>Overlord Sign Up</h2>
                            <div class="oauth-grid">
                                <button id="google-register-btn" class="btn btn-outline-primary" type="button" onclick="startOAuth('google')" disabled><i class="bi bi-google me-1"></i>Google</button>
                                <button id="github-register-btn" class="btn btn-outline-primary" type="button" onclick="startOAuth('github')" disabled><i class="bi bi-github me-1"></i>GitHub</button>
                            </div>
                            <form onsubmit="handleRegister(event)">
                                <div class="form-group">
                                    <label>Email</label>
                                    <input type="email" id="register-email" required>
                                </div>
                                <div class="form-group">
                                    <label>Overlord Name (optional)</label>
                                    <input type="text" id="register-name">
                                </div>
                                <div class="form-group">
                                    <label>Password (min. 8 characters)</label>
                                    <input type="password" id="register-password" minlength="8" required>
                                </div>
                                <button class="btn btn-primary" type="submit">Sign Up</button>
                            </form>
                            <div class="toggle-form">
                                Already have an account?
                                <button onclick="toggleAuthForm()">Log in</button>
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
                                <div id="pending-connections"></div>
                                <div id="integration-token-panel" class="card mb-3">
                                    <div class="card-body py-2 d-flex flex-wrap align-items-center justify-content-between gap-2">
                                        <div>
                                            <strong>Drone Authorization Token</strong>
                                            <div class="small text-muted">Generate a token, paste it into Drone admin, then approve the Psionic connection.</div>
                                        </div>
                                        <button class="btn btn-outline-primary btn-sm" onclick="generateIntegrationToken()"><i class="bi bi-key me-1"></i>Generate Token</button>
                                    </div>
                                </div>
                                <h3>Your Drones</h3>
                                <div id="devices-list"></div>
                            </div>
                            <div id="selected-device-workspace" class="device-card device-detail-view" style="display: none;">
                                <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                                    <div>
                                        <h4 id="selected-device-title" class="h5 mb-1">Selected Drone</h4>
                                        <div id="selected-device-id" class="small text-muted"></div>
                                    </div>
                                    <div class="d-flex flex-wrap align-items-center gap-2">
                                        <button class="btn btn-outline-secondary btn-sm" onclick="renameDevicePrompt(selectedDeviceId)">
                                            <i class="bi bi-pencil me-1"></i>Rename
                                        </button>
                                        <button class="btn btn-outline-danger btn-sm" onclick="deleteSelectedDevice()">
                                            <i class="bi bi-plug-x me-1"></i>Disconnect
                                        </button>
                                        <div class="btn-group" role="group" aria-label="Drone views">
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
                                    <div id="drone-network-panel" class="mb-3"></div>
                                    <div id="drone-token-panel" class="mb-3"></div>
                                    <div id="drone-speed-panel" class="mb-3"></div>
                                    <div id="drone-auto-sync-panel" class="mb-3"></div>
                                    <div id="drone-sync-activity-panel" class="mb-3"></div>
                                    <div class="mb-3 rom-browser-toolbar d-flex flex-wrap align-items-center gap-2">
                                        <div style="flex:1;min-width:220px">
                                            <label class="form-label" for="device-rom-search">Search systems and ROMs</label>
                                            <input id="device-rom-search" class="form-control" type="search" placeholder="Type to filter systems and ROMs" oninput="handleDeviceRomSearch(event)">
                                        </div>
                                        <div style="min-width:180px">
                                            <label class="form-label" for="device-rom-system-filter">System</label>
                                            <select id="device-rom-system-filter" class="form-select" onchange="handleDeviceRomFilterChange()">
                                                <option value="">All systems</option>
                                            </select>
                                        </div>
                                        <div style="min-width:160px">
                                            <label class="form-label" for="device-rom-status-filter">Status</label>
                                            <select id="device-rom-status-filter" class="form-select" onchange="handleDeviceRomFilterChange()">
                                                <option value="">All</option>
                                                <option value="missing">Missing</option>
                                                <option value="present">Present</option>
                                            </select>
                                        </div>
                                        <div id="sync-system-buttons" class="ms-auto d-flex gap-2" style="align-self:flex-end"></div>
                                    </div>
                                    <div id="swarm-rom-availability-panel" class="mb-3"></div>
                                    <div id="systems-list"></div>
                                </div>
                                <div id="device-gamelogs-panel" class="device-subpanel" style="display:none;">
                                    <div id="gamelogs-list"></div>
                                </div>
                                <div id="device-actions-panel" class="device-subpanel" style="display:none;">
                                    <div class="d-flex flex-wrap gap-2 mb-3">
                                        <button class="btn btn-outline-primary btn-sm" onclick="queueDeviceAction('collect_rom_metadata')"><i class="bi bi-list-stars me-1"></i>ROM & System Metadata</button>
                                        <button class="btn btn-outline-primary btn-sm" onclick="queueDeviceAction('collect_game_logs')"><i class="bi bi-clock-history me-1"></i>Game Logs</button>
                                        <button class="btn btn-outline-primary btn-sm" onclick="queueDeviceAction('collect_emulator_configs')"><i class="bi bi-file-earmark-code me-1"></i>Emulator Configs</button>
                                        <button class="btn btn-outline-primary btn-sm" onclick="queueDeviceAction('collect_log_sources')"><i class="bi bi-journal-text me-1"></i>Log Sources</button>
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
                            <h3>Overlord Profile</h3>
                            <div class="device-card">
                                <div class="form-group">
                                    <label>Avatar</label>
                                    <input type="file" id="profile-avatar-input" accept="image/*" onchange="handleAvatarSelected(event)">
                                </div>
                                <div class="form-group">
                                    <label>Overlord Name</label>
                                    <input type="text" id="profile-name-input" placeholder="Your Overlord name">
                                </div>
                                <button class="btn btn-primary" onclick="saveProfile()">Save Profile</button>
                            </div>
                        </div>

                        <div id="help-tab" class="content-section dashboard-tab" style="display: none;">
                            <h3>IPv4 Port Forwarding Help</h3>
                            <div class="device-card">
                                <p>Port forwarding is only needed for fleet management features that require one Drone to reach another Drone directly, such as syncing ROMs or settings across devices.</p>
                                <div class="row g-3">
                                    <div class="col-md-6"><strong>Router login IP</strong><div id="help-router-ip" class="text-muted">Usually your gateway IP, such as 192.168.1.1</div></div>
                                    <div class="col-md-6"><strong>Internal Drone IP</strong><div id="help-internal-ip" class="text-muted">Open a Drone page after it reports alive.</div></div>
                                    <div class="col-md-6"><strong>Internal port</strong><div class="text-muted">8443</div></div>
                                    <div class="col-md-6"><strong>External port</strong><div class="text-muted">8443</div></div>
                                    <div class="col-md-6"><strong>Protocol</strong><div class="text-muted">TCP</div></div>
                                    <div class="col-md-6"><strong>Test URL</strong><div id="help-test-url" class="text-muted">https://&lt;public_ip&gt;:8443/health</div></div>
                                </div>
                                <hr>
                                <p>Log in to your router, look for settings named Port Forwarding, NAT, Virtual Server, or Applications. Add a TCP rule from external port 8443 to internal port 8443 on the Drone internal IP.</p>
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
                                        <input type="checkbox" id="notify-type-device-offline"> Drone offline
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
                            Batocera Overmind centralized command console
                        </footer>
                    </div>
                </main>
            </div>
        </div>
        
        <script>
            let currentUser = null;
            let currentProfile = null;
            let currentDevices = [];
            let pendingConnections = [];
            let selectedDeviceId = null;
            let currentTab = 'devices';
            let currentDeviceView = 'systems';
            let currentDeviceSystems = {};
            let deviceRomSearchQuery = '';
            let masterRomPage = 1;
            let systemPageState = {};
            let pendingConnectionTimer = null;
            let actionRefreshTimer = null;
            const MASTER_ROM_PAGE_SIZE = 100;
            const ROMS_PER_PAGE = 20;
            const pageMeta = {
                auth: ['Overlord Login', 'Access the Overmind'],
                devices: ['Drones', 'Systems and ROMs'],
                profile: ['Overlord', 'Account settings'],
                help: ['Help', 'Port forwarding guide'],
                notifications: ['Notifications', 'Delivery preferences'],
            };

            document.addEventListener('DOMContentLoaded', () => {
                loadAuthProviders();
                handleOAuthReturn();
                const token = localStorage.getItem('auth_token');
                if (token) {
                    authToken = token;
                    showDashboard();
                    loadProfile();
                    loadDevices();
                    loadPendingConnections();
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

            async function loadAuthProviders() {
                try {
                    const response = await fetch('/api/auth/providers');
                    const data = await response.json();
                    const providers = data.providers || {};
                    ['google', 'github'].forEach(provider => {
                        ['login', 'register'].forEach(form => {
                            const btn = document.getElementById(`${provider}-${form}-btn`);
                            if (!btn) return;
                            btn.disabled = !providers[provider];
                            btn.title = providers[provider]
                                ? `Continue with ${provider}`
                                : `Set ${provider.toUpperCase()}_CLIENT_ID and ${provider.toUpperCase()}_CLIENT_SECRET to enable`;
                        });
                    });
                } catch (error) {
                    console.error('Error loading auth providers:', error);
                }
            }

            function startOAuth(provider) {
                window.location.href = `/api/auth/${provider}/start`;
            }

            function handleOAuthReturn() {
                const hash = window.location.hash || '';
                if (!hash.startsWith('#oauth_token=')) return;
                const params = new URLSearchParams(hash.slice(1));
                const token = params.get('oauth_token');
                if (!token) return;
                authToken = token;
                localStorage.setItem('auth_token', authToken);
                window.location.hash = '#/devices';
                showMessage('Overlord authenticated.', 'success');
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
                    await loadPendingConnections();
                    showMessage('Overlord authenticated.', 'success');
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
                    showMessage('Overlord created. Please log in.', 'success');
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
                pendingConnections = [];
                selectedDeviceId = null;
                currentDeviceView = 'systems';
                if (pendingConnectionTimer) clearInterval(pendingConnectionTimer);
                if (actionRefreshTimer) clearInterval(actionRefreshTimer);
                pendingConnectionTimer = null;
                actionRefreshTimer = null;
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
                startPendingConnectionPolling();
            }

            function startPendingConnectionPolling() {
                if (pendingConnectionTimer) clearInterval(pendingConnectionTimer);
                pendingConnectionTimer = setInterval(loadPendingConnections, 10000);
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

            async function loadPendingConnections() {
                if (!authToken) return;
                try {
                    const response = await apiGet('/api/drone-connections');
                    if (!response.ok) throw new Error('Failed to load drone connections');
                    const data = await response.json();
                    pendingConnections = data.connections || [];
                    displayPendingConnections();
                } catch (error) {
                    console.error('Error loading drone connections:', error);
                }
            }

            function displayPendingConnections() {
                const container = document.getElementById('pending-connections');
                if (!container) return;
                if (!pendingConnections.length) {
                    container.innerHTML = '';
                    return;
                }
                container.innerHTML = `
                    <div class="connection-panel">
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
                            <div>
                                <div class="fw-bold"><i class="bi bi-broadcast-pin me-1"></i>Psionic connection detected</div>
                                <div class="small text-muted">A Drone is requesting control from the Overmind.</div>
                            </div>
                            <button class="btn btn-outline-secondary btn-sm" onclick="loadPendingConnections()"><i class="bi bi-arrow-repeat me-1"></i>Refresh</button>
                        </div>
                        ${pendingConnections.map(conn => `
                            <div class="card mb-2">
                                <div class="card-body py-2">
                                    <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                                        <div>
                                            <strong>${conn.device_name}</strong>
                                            <div class="small text-muted">Drone ID: <code>${conn.device_id}</code></div>
                                            <div class="small text-muted">Detected: ${conn.detected_at ? new Date(conn.detected_at).toLocaleString() : 'now'}</div>
                                        </div>
                                        <div class="d-flex gap-2">
                                            <button class="btn btn-primary btn-sm" onclick="acceptDroneConnection('${conn.device_id}')"><i class="bi bi-check2-circle me-1"></i>Accept</button>
                                            <button class="btn btn-outline-danger btn-sm" onclick="denyDroneConnection('${conn.device_id}')"><i class="bi bi-x-circle me-1"></i>Deny</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            async function acceptDroneConnection(deviceId) {
                try {
                    const response = await fetch(`/api/drone-connections/${deviceId}/accept`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${authToken}` }
                    });
                    if (!response.ok) throw new Error('Failed to accept Drone connection');
                    await loadPendingConnections();
                    await loadDevices();
                    showMessage('Drone registered to the Overlord.', 'success');
                } catch (error) {
                    console.error('Error accepting Drone connection:', error);
                }
            }

            async function denyDroneConnection(deviceId) {
                if (!window.confirm('Deny this psionic connection?')) return;
                try {
                    const response = await fetch(`/api/drone-connections/${deviceId}/deny`, {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${authToken}` }
                    });
                    if (!response.ok) throw new Error('Failed to deny Drone connection');
                    await loadPendingConnections();
                    showMessage('Drone connection denied.', 'success');
                } catch (error) {
                    console.error('Error denying Drone connection:', error);
                }
            }

            async function generateIntegrationToken() {
                try {
                    const response = await fetch('/api/integration-tokens', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${authToken}`
                        },
                        body: JSON.stringify({ label: 'Local Drone onboarding' })
                    });
                    if (!response.ok) throw new Error('Failed to generate authorization token');
                    const data = await response.json();
                    showTokenModal(data.token.authorization_token);
                } catch (error) {
                    console.error('Error generating integration token:', error);
                }
            }

            function updateSelectedDeviceSummary() {
                const summary = document.getElementById('selected-device-summary');
                if (!summary) return;
                summary.style.display = selectedDeviceId ? 'none' : 'block';
                if (!selectedDeviceId) {
                    summary.textContent = 'Select a Drone to view systems, ROMs, and game logs.';
                    return;
                }
                const device = currentDevices.find(d => d.device_id === selectedDeviceId);
                summary.textContent = device ? `Selected Drone: ${device.device_name} (${device.device_id})` : `Selected Drone: ${selectedDeviceId}`;
            }

            function displayDevices() {
                const container = document.getElementById('devices-list');
                if (currentDevices.length === 0) {
                    container.innerHTML = '<div class="empty-state">No Drones registered yet</div>';
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
                                    <div class="small text-muted mb-3">Drone ID</div>
                                    <code class="small d-block text-break">${device.device_id}</code>
                                    <div class="mt-3 d-flex flex-wrap gap-1">
                                        <span class="badge ${device.online ? 'text-bg-success' : 'text-bg-danger'}">${device.online ? 'Online' : 'Offline'}</span>
                                        <span class="badge ${device.swarm_connected ? 'text-bg-success' : 'text-bg-secondary'}">${device.swarm_connected ? 'Connected to Swarm' : 'Not Connected to Swarm'}</span>
                                    </div>
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
                    document.getElementById('gamelogs-list').innerHTML = '<div class="empty-state">Select a Drone to view game logs.</div>';
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
                    document.getElementById('systems-list').innerHTML = '<div class="empty-state">Select a Drone to view systems.</div>';
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

            let deviceRomSearchDebounce = null;
            function handleDeviceRomSearch(event) {
                const val = (event.target.value || '').trim();
                deviceRomSearchQuery = val;
                masterRomPage = 1;
                // debounce server-side filtering
                if (deviceRomSearchDebounce) clearTimeout(deviceRomSearchDebounce);
                deviceRomSearchDebounce = setTimeout(() => {
                    deviceRomSearchDebounce = null;
                    loadSwarmRomAvailabilityPanel();
                }, 300);
            }

            function setMasterRomPage(page) {
                masterRomPage = Math.max(1, page);
                loadSwarmRomAvailabilityPanel();
            }

            function setSystemPage(systemName, page) {
                systemPageState[systemName] = Math.max(1, page);
                displaySystemsTree();
            }

            function handleDeviceRomFilterChange() {
                // Trigger server-side reload of the master table when filters change
                masterRomPage = 1;
                loadSwarmRomAvailabilityPanel();
            }

            async function populateSystemFilterOptions() {
                // populate systems dropdown from currentDeviceSystems or from server summary
                const select = document.getElementById('device-rom-system-filter');
                if (!select) return;
                select.innerHTML = '<option value="">All systems</option>';
                try {
                    const resp = await apiGet('/api/systems');
                    if (!resp.ok) return;
                    const data = await resp.json();
                    const systems = data.systems || [];
                    systems.forEach(s => {
                        const opt = document.createElement('option');
                        opt.value = s.system_name;
                        opt.text = `${s.system_name} (${s.rom_count})`;
                        select.appendChild(opt);
                    });
                } catch (e) {
                    // ignore
                }
            }

            async function syncSystemFromFilter(systemParam) {
                const system = systemParam || document.getElementById('device-rom-system-filter')?.value || '';
                if (!system) return alert('Select a system to sync');
                if (!selectedDeviceId) return;
                if (!confirm(`Queue sync for system ${system} on this Drone?`)) return;
                try {
                    await syncSystem(system);
                    await loadSyncActivityPanel();
                    await loadSwarmRomAvailabilityPanel();
                } catch (err) {
                    console.error('Error syncing system:', err);
                    showMessage('Failed to queue system sync.', 'error');
                }
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
                renderDroneAutoSyncPanel();
            }

            function selectedDrone() {
                return currentDevices.find(d => d.device_id === selectedDeviceId) || null;
            }

            function renderDroneNetworkPanel() {
                const container = document.getElementById('drone-network-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const resolved = device.resolved_network || {};
                const ipv4 = resolved.ipv4 || [];
                const ipv6 = resolved.ipv6 || [];
                const cert = device.certificate || {};
                const peerChecks = device.peer_checks || [];
                const info = device.system_info || {};
                const systemRows = [
                    ['Hostname', info.hostname || device.device_name],
                    ['OS', [info.os, info.os_release].filter(Boolean).join(' ')],
                    ['Batocera', info.batocera_version],
                    ['Drone App', info.drone_app_version],
                    ['Architecture', info.architecture],
                    ['CPU', info.cpu ? `${info.cpu.model || 'CPU'} ${info.cpu.count ? `(${info.cpu.count} cores)` : ''}` : ''],
                    ['Memory', info.memory ? `${info.memory.available || 'n/a'} available / ${info.memory.total || 'n/a'} total` : ''],
                    ['Storage', info.disk && info.disk.free_bytes ? `${(Number(info.disk.free_bytes) / 1024 / 1024 / 1024).toFixed(1)} GiB free` : ''],
                    ['Container', info.container === true ? 'yes' : (info.container === false ? 'no' : '')],
                    ['Updated', info.last_system_info_update || info.updated_at],
                ].filter(row => row[1]);
                const latestPeers = Object.values(peerChecks.reduce((acc, check) => {
                    const key = check.target_drone_id || check.target_address || '';
                    if (!key) return acc;
                    if (!acc[key] || String(check.checked_at || '') >= String(acc[key].checked_at || '')) acc[key] = check;
                    return acc;
                }, {}));
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2">
                        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                            <strong>Swarm Connection</strong>
                            <span class="badge ${device.swarm_connected ? 'text-bg-success' : 'text-bg-secondary'}">${device.swarm_connected ? 'Connected to Swarm' : 'Not Connected to Swarm'}</span>
                        </div>
                        <div class="small text-muted mt-2">IPv4: ${ipv4.length ? ipv4.map(escapeHtml).join(', ') : 'none resolved'}</div>
                        <div class="small text-muted">IPv6: ${ipv6.length ? ipv6.map(escapeHtml).join(', ') : 'none resolved'}</div>
                        <div class="small text-muted">API: ${escapeHtml(device.reachable_url || `${device.scheme || 'https'}://${ipv4[0] || device.device_id}:${device.api_port || 8443}`)}</div>
                        <hr>
                        <strong>Certificate</strong>
                        <div class="small text-muted">Status: ${escapeHtml(cert.status || 'unknown')}</div>
                        <div class="small text-muted">Fingerprint: ${escapeHtml(cert.fingerprint || 'n/a')}</div>
                        <div class="small text-muted">Subject: ${escapeHtml(cert.subject || 'n/a')}</div>
                        <div class="small text-muted">Issuer: ${escapeHtml(cert.issuer || 'n/a')}</div>
                        <div class="small text-muted">SAN: ${(cert.san || []).map(escapeHtml).join(', ') || 'n/a'}</div>
                        <div class="small text-muted">Valid: ${escapeHtml(cert.valid_from || 'n/a')} - ${escapeHtml(cert.valid_until || 'n/a')}</div>
                        <div class="small text-muted">Renewal: ${escapeHtml(cert.renewal_status || 'n/a')}</div>
                        <hr>
                        <strong>System Information</strong>
                        ${systemRows.length ? `<div class="row g-2 mt-1">${systemRows.map(([label, value]) => `
                            <div class="col-12 col-md-6"><div class="small text-muted">${escapeHtml(label)}</div><div class="small">${escapeHtml(String(value || ''))}</div></div>
                        `).join('')}</div>` : '<div class="small text-muted mt-1">No system information reported yet.</div>'}
                        <hr>
                        <strong>Peer-to-Peer Checks</strong>
                        ${latestPeers.length ? latestPeers.map(check => `
                            <div class="mt-2 p-2 rounded border">
                                <div class="d-flex justify-content-between gap-2">
                                    <span class="small">${escapeHtml(check.target_name || check.target_drone_id || 'Peer Drone')}</span>
                                    <span class="badge ${check.status === 'pass' ? 'text-bg-success' : 'text-bg-danger'}">${check.status === 'pass' ? 'RESOLVED' : 'FAILED'}</span>
                                </div>
                                <div class="small text-muted">${escapeHtml(check.target_address || 'n/a')} · ${escapeHtml(check.checked_at || 'n/a')} · ${check.latency_ms ?? 'n/a'} ms</div>
                                ${check.failure_reason ? `<div class="small text-danger">${escapeHtml(check.failure_reason)}</div>` : ''}
                            </div>
                        `).join('') : '<div class="small text-muted mt-1">No peer checks reported yet.</div>'}
                    </div></div>
                `;
            }

            function renderDroneTokenPanel() {
                const container = document.getElementById('drone-token-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2 d-flex flex-wrap align-items-center justify-content-between gap-2">
                        <div>
                            <strong>Drone Authorization Token</strong>
                            <div class="small text-muted">${device.token_rotated_at ? `Last rotated: ${new Date(device.token_rotated_at).toLocaleString()}` : 'Token hash stored in Overmind'}</div>
                        </div>
                        <button class="btn btn-outline-danger btn-sm" onclick="rotateDroneToken()"><i class="bi bi-arrow-clockwise me-1"></i>Rotate Token</button>
                    </div></div>
                `;
            }

            function renderDroneSpeedPanel() {
                const container = document.getElementById('drone-speed-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const sample = device.last_speed_sample;
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2">
                        <strong>Speed Sample</strong>
                        ${sample ? `<div class="small text-muted mt-1">Down ${sample.download_mbps ?? 'n/a'} Mbps / Up ${sample.upload_mbps ?? 'n/a'} Mbps / Latency ${sample.latency_ms ?? 'n/a'} ms</div>` : '<div class="small text-muted mt-1">No speed sample received yet.</div>'}
                    </div></div>
                `;
            }

            function renderDroneAutoSyncPanel() {
                const container = document.getElementById('drone-auto-sync-panel');
                const device = selectedDrone();
                if (!container || !device) return;
                const policy = device.auto_sync_policy || { enabled: false, systems: [] };
                const systems = Object.keys(currentDeviceSystems || {}).sort();
                container.innerHTML = `
                    <div class="card"><div class="card-body py-2">
                        <label class="d-flex gap-2 align-items-center mb-2">
                            <input id="drone-auto-sync-enabled" type="checkbox" ${policy.enabled ? 'checked' : ''}>
                            <strong>Auto-sync ROM metadata from this Drone</strong>
                        </label>
                        <div class="d-flex flex-wrap gap-2 mb-2">
                            ${systems.length ? systems.map(system => `
                                <label class="badge text-bg-secondary">
                                    <input class="drone-auto-sync-system me-1" type="checkbox" value="${escapeHtml(system)}" ${policy.systems.includes(system) ? 'checked' : ''}>
                                    ${escapeHtml(system)}
                                </label>
                            `).join('') : '<span class="small text-muted">Queue ROM & System Metadata to populate system checkboxes.</span>'}
                        </div>
                        <button class="btn btn-primary btn-sm" onclick="saveDroneAutoSyncPolicy()">Save Policy</button>
                    </div></div>
                `;
            }

            async function saveDroneAutoSyncPolicy() {
                if (!selectedDeviceId) return;
                const systems = Array.from(document.querySelectorAll('.drone-auto-sync-system:checked')).map(input => input.value);
                const enabled = !!document.getElementById('drone-auto-sync-enabled')?.checked;
                const response = await apiPatch(`/api/devices/${selectedDeviceId}/auto-sync`, { enabled, systems });
                if (!response.ok) throw new Error('Failed to save policy');
                await loadDevices();
                showMessage('Drone sync policy saved.', 'success');
            }

            async function loadSwarmRomAvailabilityPanel() {
                // Render a single master ROM table that shows all known ROMs across the swarm
                // and indicates whether the selected Drone already has each ROM.
                const container = document.getElementById('swarm-rom-availability-panel');
                if (!container || !selectedDeviceId) return;
                try {
                    // prepare server-side filter params
                    const params = new URLSearchParams();
                    const q = (deviceRomSearchQuery || '').trim();
                    const system = document.getElementById('device-rom-system-filter')?.value || '';
                    const status = document.getElementById('device-rom-status-filter')?.value || '';
                    if (q) params.set('q', q);
                    if (system) params.set('system', system);
                    if (status) params.set('status', status);
                    params.set('page', String(masterRomPage));
                    params.set('per_page', String(MASTER_ROM_PAGE_SIZE));
                    const url = `/api/devices/${selectedDeviceId}/master-roms` + (params.toString() ? `?${params.toString()}` : '');
                    const response = await apiGet(url);
                    if (!response.ok) throw new Error('Failed to load swarm ROM availability');
                    const payload = await response.json();
                    const filtered = payload.roms || [];
                    const total = payload.total || filtered.length;
                    const page = payload.page || masterRomPage;
                    const perPage = payload.per_page || MASTER_ROM_PAGE_SIZE;
                    const pageCount = Math.max(1, Math.ceil(total / perPage));
                    masterRomPage = page;

                    const missingCount = filtered.filter(r => !r.present_on_selected).length;
                    const renderPageButton = (pageNumber) => {
                        return `<button class="btn btn-sm ${pageNumber === page ? 'btn-primary' : 'btn-outline-secondary'}" onclick="setMasterRomPage(${pageNumber})">${pageNumber}</button>`;
                    };
                    const paginationButtons = [];
                    if (pageCount <= 7) {
                        for (let i = 1; i <= pageCount; i += 1) paginationButtons.push(renderPageButton(i));
                    } else {
                        const start = Math.max(1, page - 2);
                        const end = Math.min(pageCount, page + 2);
                        if (start > 1) paginationButtons.push(renderPageButton(1));
                        if (start > 2) paginationButtons.push('<span class="px-2">&hellip;</span>');
                        for (let i = start; i <= end; i += 1) paginationButtons.push(renderPageButton(i));
                        if (end < pageCount - 1) paginationButtons.push('<span class="px-2">&hellip;</span>');
                        if (end < pageCount) paginationButtons.push(renderPageButton(pageCount));
                    }

                    container.innerHTML = `
                        <div class="card"><div class="card-body py-2">
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <div class="d-flex gap-2 align-items-center">
                                    <strong>ROMs (Master List)</strong>
                                    <div class="small text-muted">${total} ROMs · ${missingCount} missing here</div>
                                </div>
                                <div class="d-flex gap-2">
                                    <button class="btn btn-outline-secondary btn-sm" onclick="populateSystemFilterOptions()">Refresh systems</button>
                                </div>
                            </div>
                            <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2">
                                <div class="small text-muted">Page ${page} of ${pageCount} · ${perPage} per page</div>
                                <div class="btn-group" role="group" aria-label="Master ROM pagination">
                                    <button class="btn btn-sm btn-outline-secondary" ${page <= 1 ? 'disabled' : ''} onclick="setMasterRomPage(${Math.max(1, page - 1)})">Previous</button>
                                    ${paginationButtons.join('')}
                                    <button class="btn btn-sm btn-outline-secondary" ${page >= pageCount ? 'disabled' : ''} onclick="setMasterRomPage(${Math.min(pageCount, page + 1)})">Next</button>
                                </div>
                            </div>
                            <div class="table-responsive"><table class="table table-sm align-middle"><thead><tr>
                                <th>System</th>
                                <th>ROM</th>
                                <th>Size</th>
                                <th>Source</th>
                                <th>Status</th>
                                <th></th>
                            </tr></thead><tbody>
                                ${filtered.map(row => {
                                    const present = !!row.present_on_selected;
                                    const sources = (row.devices || []).map(d => d.device_name || d.device_id).join(', ');
                                    const preferred = row.preferred_source_name || (row.devices && row.devices[0] && (row.devices[0].device_name || row.devices[0].device_id)) || '';
                                    const sizeText = row.size ? `${(Number(row.size) / 1024 / 1024).toFixed(2)} MB` : (row.file_size ? `${(Number(row.file_size) / 1024 / 1024).toFixed(2)} MB` : '');
                                    const statusLabel = present ? (row.present_label || 'Present') : (row.devices && row.devices.length ? 'Missing' : 'Unavailable');
                                    const showSync = !present && row.devices && row.devices.length;
                                    const rowData = Object.assign({}, row, { preferred_sync_source: row.preferred_source || preferred });
                                    return `
                                        <tr>
                                            <td>${escapeHtml(row.system_name || '')}</td>
                                            <td style="min-width:240px">${escapeHtml(row.file_path || row.rom_name || '')}</td>
                                            <td class="text-muted">${escapeHtml(sizeText)}</td>
                                            <td class="text-muted">${escapeHtml(sources || preferred)}</td>
                                            <td><span class="badge ${present ? 'text-bg-success' : (row.devices && row.devices.length ? 'text-bg-secondary' : 'text-bg-danger')}">${escapeHtml(statusLabel)}</span></td>
                                            <td>
                                                ${showSync ? `<button class="btn btn-primary btn-sm" onclick='syncRom(${JSON.stringify(rowData).replace(/'/g, "&apos;")})'>Sync</button>` : ''}
                                            </td>
                                        </tr>
                                    `;
                                }).join('')}
                            </tbody></table></div>
                            ${total ? '' : '<div class="small text-muted">No ROMs found for this filter.</div>'}
                        </div></div>
                    `;
                    // populate per-system Sync buttons for missing systems
                    try {
                        const btnContainer = document.getElementById('sync-system-buttons');
                        if (btnContainer) {
                            btnContainer.innerHTML = '';
                            const missingBySystem = filtered.reduce((acc, r) => {
                                if (!r.present_on_selected) {
                                    const s = r.system_name || 'Unknown';
                                    acc[s] = (acc[s] || 0) + 1;
                                }
                                return acc;
                            }, {});
                            Object.keys(missingBySystem).sort().forEach(s => {
                                const btn = document.createElement('button');
                                btn.className = 'btn btn-outline-primary btn-sm';
                                btn.textContent = `Sync ${s} (${missingBySystem[s]})`;
                                btn.onclick = () => syncSystemFromFilter(s);
                                btnContainer.appendChild(btn);
                            });
                        }
                    } catch (e) {
                        // ignore
                    }
                    // ensure system filter has options
                    populateSystemFilterOptions();
                } catch (error) {
                    console.error('Error loading master ROM table:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load ROMs.</div>';
                }
            }

            async function syncRom(row) {
                try {
                    const response = await fetch(`/api/devices/${selectedDeviceId}/sync-rom`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                        body: JSON.stringify(row)
                    });
                    if (!response.ok) throw new Error('Failed to queue ROM sync');
                    showMessage('ROM sync queued. The Drone will choose the source peer automatically.', 'success');
                    await loadSyncActivityPanel();
                    // Refresh the master ROM table so the Sync button disappears once the Drone reports the ROM
                    await loadSwarmRomAvailabilityPanel();
                } catch (error) {
                    console.error('Error queuing ROM sync:', error);
                    showMessage('Failed to queue ROM sync.', 'error');
                }
            }

            async function syncSystem(systemName) {
                const response = await fetch(`/api/devices/${selectedDeviceId}/sync-system`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${authToken}`},
                    body: JSON.stringify({ system_name: systemName })
                });
                if (!response.ok) throw new Error('Failed to queue system sync');
                showMessage('System sync queued. The Drone will choose source peers automatically.', 'success');
                await loadSyncActivityPanel();
            }

            async function loadSyncActivityPanel() {
                const container = document.getElementById('drone-sync-activity-panel');
                if (!container || !selectedDeviceId) return;
                try {
                    const response = await apiGet(`/api/devices/${selectedDeviceId}/sync-activity`);
                    if (!response.ok) throw new Error('Failed to load sync activity');
                    const rows = ((await response.json()).activity || []).slice(0, 20);
                    container.innerHTML = `<div class="card"><div class="card-body py-2"><strong>ROM Sync Activity</strong>
                        ${rows.length ? rows.map(row => `<div class="mt-2 small">
                            <span class="badge ${row.status === 'completed' ? 'text-bg-success' : row.status === 'failed' ? 'text-bg-danger' : 'text-bg-secondary'}">${escapeHtml(row.status || 'pending')}</span>
                            ${escapeHtml(row.system || '')} / ${escapeHtml(row.rom_name || '')}
                            ${row.source_drone_id ? `from ${escapeHtml(row.source_drone_id)}` : ''}
                            ${row.failure_reason ? `<div class="text-danger">${escapeHtml(row.failure_reason)}</div>` : ''}
                        </div>`).join('') : '<div class="small text-muted mt-1">No ROM sync activity yet.</div>'}
                    </div></div>`;
                } catch (error) {
                    container.innerHTML = '<div class="empty-state">Unable to load ROM sync activity.</div>';
                }
            }

            async function rotateDroneToken() {
                if (!selectedDeviceId || !window.confirm('Rotate this Drone token? The old token will stop working immediately.')) return;
                const response = await fetch(`/api/devices/${selectedDeviceId}/token/rotate`, {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${authToken}` }
                });
                if (!response.ok) throw new Error('Failed to rotate token');
                const data = await response.json();
                await loadDevices();
                showTokenModal(data.drone_token, 'New Drone Authorization Token');
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
                if (title) title.textContent = device ? device.device_name : 'Selected Drone';
                if (idNode) idNode.textContent = device ? `Drone ID: ${device.device_id}` : `Drone ID: ${selectedDeviceId}`;
                renderDroneNetworkPanel();
                renderDroneTokenPanel();
                renderDroneSpeedPanel();
                loadSwarmRomAvailabilityPanel();
                loadSyncActivityPanel();
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

                if (currentDeviceView === 'systems') loadSwarmRomAvailabilityPanel();
                if (currentDeviceView === 'gamelogs') loadGameLogs();
                if (actionRefreshTimer) clearInterval(actionRefreshTimer);
                actionRefreshTimer = null;
                if (currentDeviceView === 'actions') {
                    loadDeviceActions();
                    actionRefreshTimer = setInterval(loadDeviceActions, 5000);
                }
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
                const allowed = ['devices', 'profile', 'help', 'notifications'];
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
                    container.innerHTML = actions.map(action => {
                        const result = action.result || null;
                        const resultSummary = summarizeActionResult(result);
                        return `
                        <div class="card mb-2 shadow-sm">
                            <div class="card-body py-2">
                                <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
                                    <strong>${formatActionName(action.action)}</strong>
                                    <span class="badge text-bg-secondary">${action.status}</span>
                                </div>
                                <div class="small text-muted mt-1">Created: ${action.created_at ? new Date(action.created_at).toLocaleString() : 'n/a'}</div>
                                ${action.completed_at ? `<div class="small text-muted mt-1">Completed: ${new Date(action.completed_at).toLocaleString()}</div>` : ''}
                                ${action.message ? `<div class="small mt-1">${action.message}</div>` : ''}
                                ${result ? `
                                    <div class="small text-muted mt-2">${resultSummary}</div>
                                    <details class="mt-2">
                                        <summary class="small">View returned data</summary>
                                        <pre class="small mt-2 p-2 rounded" style="white-space:pre-wrap;background:rgba(0,0,0,0.18);max-height:360px;overflow:auto;">${escapeHtml(JSON.stringify(result, null, 2))}</pre>
                                    </details>
                                ` : ''}
                            </div>
                        </div>
                    `;
                    }).join('');
                } catch (error) {
                    console.error('Error loading actions:', error);
                    container.innerHTML = '<div class="empty-state">Unable to load actions.</div>';
                }
            }

            async function queueDeviceAction(actionName) {
                if (!selectedDeviceId) return;
                const labels = {
                    shutdown: 'shutdown',
                    restart: 'restart',
                    update: 'update',
                    collect_rom_metadata: 'collect ROM and system metadata',
                    collect_game_logs: 'collect game logs',
                    collect_emulator_configs: 'collect emulator configs',
                    collect_log_sources: 'collect log sources',
                };
                if (!window.confirm(`Queue ${labels[actionName] || actionName} for this Drone?`)) return;
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

            function formatActionName(actionName) {
                return String(actionName || 'n/a').replaceAll('_', ' ');
            }

            function summarizeActionResult(result) {
                if (!result) return '';
                if (result.type === 'rom_metadata') return `${(result.systems || []).length} systems, ${(result.roms || []).length} ROM entries, ${(result.gamelists || []).length} gamelist.xml files`;
                if (result.type === 'game_logs') return `${(result.sessions || []).length} parsed play sessions, ${(result.logs || []).length} logs`;
                if (result.type === 'emulator_configs') return `${(result.configs || []).length} config files`;
                if (result.type === 'log_sources') return `${(result.logs || []).length} log sources`;
                return 'Data returned from Drone';
            }

            function escapeHtml(value) {
                return String(value ?? '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
            }

            async function renameDevicePrompt(deviceId) {
                if (!deviceId) return;
                const current = currentDevices.find(d => d.device_id === deviceId);
                const nextName = window.prompt('Enter Drone name:', current ? current.device_name : '');
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
                if (!window.confirm(`Disconnect ${label}? This removes the Drone from this Overlord and it will no longer be controllable.`)) return;
                try {
                    const response = await apiDelete(`/api/devices/${selectedDeviceId}`);
                    if (!response.ok) throw new Error('Failed to delete device');
                    selectedDeviceId = null;
                    currentDeviceView = 'systems';
                    await loadDevices();
                    setRoute('devices', null, 'systems');
                    showMessage('Drone disconnected.', 'success');
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
                    help: 'help-tab',
                    notifications: 'notifications-tab',
                };
                const tabElement = document.getElementById(tabMap[tabName]);
                if (tabElement) tabElement.style.display = 'block';
                currentTab = tabName;
                if (tabName === 'devices') updateSelectedDeviceWorkspace();
                if (tabName === 'profile' || tabName === 'notifications') renderProfileUI();
                setPageChrome(tabName);
                if (updateUrl) setRoute(tabName);
            }

            function showTokenModal(tokenValue, title = 'Drone Authorization Token') {
                const hidden = document.getElementById('token-modal-overlay');
                if (hidden) hidden.remove();
                const overlay = document.createElement('div');
                overlay.id = 'token-modal-overlay';
                overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:99999;display:flex;align-items:center;justify-content:center;';
                overlay.innerHTML = `
                  <div style="background:var(--admin-surface,#151f32);border:1px solid var(--admin-border,#31405f);border-radius:0.75rem;max-width:600px;width:90%;padding:1.5rem;box-shadow:0 1rem 3rem rgba(0,0,0,0.45);">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
                      <h4 style="margin:0;color:var(--admin-text,#ecf6ff);">${escapeHtml(title)}</h4>
                      <button onclick="this.closest('#token-modal-overlay').remove()" style="background:transparent;border:none;color:var(--admin-muted,#9fb0c9);font-size:1.5rem;cursor:pointer;">&times;</button>
                    </div>
                    <p style="color:var(--admin-muted,#9fb0c9);margin-bottom:0.75rem;">Copy this token and paste it into the Drone admin page. It is shown only once.</p>
                    <div style="display:flex;gap:0.5rem;">
                      <input id="token-modal-value" type="text" readonly value="${escapeHtml(tokenValue)}" style="flex:1;font-family:monospace;background:rgba(0,0,0,0.3);border:1px solid var(--admin-border,#31405f);color:var(--admin-text,#ecf6ff);padding:0.65rem;border-radius:0.35rem;font-size:0.85rem;">
                      <button id="token-copy-button" type="button" aria-label="Copy token" title="Copy token" onclick="copyTokenFromModal()" style="background:var(--admin-sidebar-accent,#00c2ff);border:none;color:#06111f;font-weight:800;padding:0.65rem 1rem;border-radius:0.35rem;cursor:pointer;white-space:nowrap;"><i class="bi bi-clipboard"></i></button>
                    </div>
                    <div id="token-copy-status" class="small mt-2" role="status" aria-live="polite" style="color:var(--admin-muted,#9fb0c9);min-height:1.25rem;"></div>
                    <div style="margin-top:1rem;text-align:right;">
                      <button onclick="this.closest('#token-modal-overlay').remove()" style="background:rgba(255,255,255,0.08);border:1px solid var(--admin-border,#31405f);color:var(--admin-text,#ecf6ff);padding:0.5rem 1rem;border-radius:0.35rem;cursor:pointer;">Close</button>
                    </div>
                  </div>
                `;
                document.body.appendChild(overlay);
            }

            async function copyTextToClipboard(text) {
                if (!text) throw new Error('No token available to copy.');
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(text);
                    return;
                }
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.setAttribute('readonly', '');
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.select();
                const ok = document.execCommand('copy');
                document.body.removeChild(textarea);
                if (!ok) throw new Error('Fallback clipboard copy failed.');
            }

            async function copyTokenFromModal() {
                const input = document.getElementById('token-modal-value');
                const button = document.getElementById('token-copy-button');
                const status = document.getElementById('token-copy-status');
                const original = '<i class="bi bi-clipboard"></i>';
                try {
                    await copyTextToClipboard(input ? input.value : '');
                    if (button) {
                        button.innerHTML = '<i class="bi bi-check2"></i>';
                        button.title = 'Copied';
                    }
                    if (status) {
                        status.textContent = 'Copied';
                        status.style.color = 'var(--admin-accent-green,#34d399)';
                    }
                    setTimeout(() => {
                        if (button) {
                            button.innerHTML = original;
                            button.title = 'Copy token';
                        }
                        if (status) status.textContent = '';
                    }, 2000);
                } catch (error) {
                    console.error('Token copy failed:', error);
                    if (status) {
                        status.textContent = error.message || 'Copy failed';
                        status.style.color = '#ff9aa7';
                    }
                    showMessage(error.message || 'Copy failed', 'error');
                }
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
    postgres_store.ensure_schema()
    
    # Load fake data if USE_FAKE_DATA environment variable is set to true
    if os.getenv("USE_FAKE_DATA", "").lower() == "true":
        print("\n📚 Loading sample data...")
        db.populate_fake_data()
        print("✓ Sample data loaded successfully!")
        print("  • 2 demo users")
        print("  • 3 sample devices")
        print("  • 2 pending drone psionic connections")
        print("  • 10+ sample ROMs")
        print("  • 8 sample game plays")
        print("\n  Demo Credentials:")
        print("  Email: demo@example.com")
        print("  Password: DemoPass123")
        print("\n  Or:")
        print("  Email: arcade@example.com")
        print("  Password: ArcadePass123\n")


if __name__ == "__main__":
    run_https_app()
