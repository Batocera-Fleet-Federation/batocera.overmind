"""Main FastAPI application."""

import base64
import binascii
import hashlib
import hmac
import html
import logging
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import json
from collections import deque
from functools import partial
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import Optional, Union

from overmind.models import (
    UserRegister, UserLogin, User, DeviceRegister,
    RomListUpdate, GamePlayLog, SocialAuthRequest,
    EmailVerificationRequest, EmailVerificationResendRequest, ForgotPasswordRequest, ResetPasswordRequest,
    SwarmCreateRequest, SwarmInviteRequest,
    DroneActionCompleteRequest, DroneAssetMetadataUpload, DroneDownloadsReport, DroneEmulatorConfigsUpload,
    DroneGameLogsUpload, DroneHeartbeatRequest, DroneLogSourcesUpload, DronePeerChecksUpload, DroneSpeedSampleUpload,
)
from overmind.db import db
from overmind import auth
from overmind import emailer
from overmind import notification_delivery
from overmind.access_policy import (
    ensure_active_user as _ensure_active_user,
    require_device_admin as _require_device_admin,
    require_super_admin as _require_super_admin,
    require_swarm_role as _require_swarm_role,
    selected_swarm_id as _selected_swarm_id,
)
from overmind.account_notifications import (
    send_invitation_email as _send_invitation_email,
    send_password_reset_email as _send_password_reset_email,
    send_verification_email as _send_verification_email,
)
from overmind.runtime_secrets import load_runtime_secret_once
from overmind.runtime_metrics import collect_runtime_metrics
from overmind.drone_ca import sign_drone_csr
from overmind.drone_security import generate_drone_token, hash_drone_token
from overmind.postgres_store import database_url, postgres_store
from overmind.tls_server import (
    ensure_self_signed_cert as _ensure_self_signed_cert,
    run_https_app as _run_https_app,
)
from overmind.presenters import (
    admin_drone_row as _admin_drone_row,
    admin_swarm_row as _admin_swarm_row,
    admin_user_row as _admin_user_row,
    device_response as _device_response,
    hive_response,
    profile_response,
)

SUPER_ADMIN_EMAIL = "mr_jerrodh@hotmail.com"
logger = logging.getLogger("overmind.main")
_LAMBDA_RUNTIME_ENV = (os.getenv("OVERMIND_RUNTIME") or "").strip().lower() == "lambda" or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))

SUPPORTED_DEVICE_ACTIONS = {
    "restart",
    "enable_kiosk",
    "disable_kiosk",
    "collect_rom_metadata",
    "rebuild_asset_metadata",
    "purge_asset_cache",
    "collect_game_logs",
    "collect_emulator_configs",
    "collect_log_sources",
    "refresh_emulator_list",
    "sync_rom",
    "sync_system",
    "sync_bios",
    "sync_artwork",
    "cancel_download",
}
SWARM_OFFLINE_THRESHOLD_SECONDS = int(os.getenv("SWARM_OFFLINE_THRESHOLD_SECONDS", "180"))
NOTIFICATION_DELIVERY_INTERVAL_SECONDS = int(os.getenv("NOTIFICATION_DELIVERY_INTERVAL_SECONDS", "180"))
NOTIFICATION_DELIVERY_MAX_NOTIFICATIONS_PER_RUN = max(0, int(os.getenv(
    "NOTIFICATION_DELIVERY_MAX_NOTIFICATIONS_PER_RUN",
    "25" if _LAMBDA_RUNTIME_ENV else "0",
)))
DEVICE_STATUS_MAX_DEVICES_PER_RUN = max(0, int(os.getenv(
    "DEVICE_STATUS_MAX_DEVICES_PER_RUN",
    "50" if _LAMBDA_RUNTIME_ENV else "0",
)))
TOKEN_HASH_SECRET = os.getenv("TOKEN_HASH_SECRET", auth.SECRET_KEY)
VERIFICATION_TTL_MINUTES = int(os.getenv("EMAIL_VERIFICATION_EXPIRE_MINUTES", "30"))
PASSWORD_RESET_TTL_MINUTES = int(os.getenv("PASSWORD_RESET_EXPIRE_MINUTES", "30"))
OAUTH_STATE_TTL_SECONDS = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
CONTENT_DIR = Path(__file__).resolve().parent.parent.parent / "content"
VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"
OWNER_ROLE = "overlord"
READONLY_ROLE = "overseer"
OVERMIND_LOG_CAPTURE_LINES = max(100, int(os.getenv("OVERMIND_LOG_CAPTURE_LINES", "1000")))
_STREAM_LOG_CAPTURE: Optional["StreamLogCapture"] = None
DRONE_LOG_STREAM_TTL_SECONDS = int(os.getenv("DRONE_LOG_STREAM_TTL_SECONDS", "75"))
_DRONE_LOG_STREAM_REQUESTS: dict[str, float] = {}
_DRONE_LOG_STREAM_PAYLOADS: dict[str, dict] = {}
_DRONE_LOG_STREAM_LOCK = threading.RLock()

admin_user_row = partial(_admin_user_row, data_store=db, super_admin_email=SUPER_ADMIN_EMAIL)
admin_swarm_row = partial(_admin_swarm_row, data_store=db)
admin_drone_row = partial(_admin_drone_row, data_store=db)
device_response = partial(_device_response, data_store=db, offline_threshold_seconds=SWARM_OFFLINE_THRESHOLD_SECONDS)


def admin_pending_drone_connection_row(connection: dict) -> dict:
    return {
        key: value
        for key, value in (connection or {}).items()
        if key != "drone_token_hash"
    }


class CapturedStream:
    """Mirror a stream while retaining a bounded recent tail for the UI."""

    def __init__(self, name: str, wrapped: object, max_lines: int) -> None:
        self.name = name
        self.wrapped = wrapped
        self.lines = deque(maxlen=max_lines)
        self._pending = ""
        self._lock = threading.RLock()
        self.encoding = getattr(wrapped, "encoding", "utf-8")
        self.errors = getattr(wrapped, "errors", "replace")

    def write(self, data: object, capture: bool = True) -> int:
        text = str(data)
        # ``capture=False`` still forwards to the real stream (so the line reaches
        # CloudWatch) but keeps it out of the bounded tail shown in the admin UI.
        if capture:
            with self._lock:
                self._pending += text
                while "\n" in self._pending:
                    line, self._pending = self._pending.split("\n", 1)
                    self.lines.append(line)
        return self.wrapped.write(text)

    def flush(self) -> None:
        flush = getattr(self.wrapped, "flush", None)
        if callable(flush):
            flush()

    def isatty(self) -> bool:
        isatty = getattr(self.wrapped, "isatty", None)
        return bool(isatty()) if callable(isatty) else False

    def fileno(self) -> int:
        fileno = getattr(self.wrapped, "fileno", None)
        if not callable(fileno):
            raise OSError("wrapped stream has no file descriptor")
        return int(fileno())

    def snapshot(self) -> str:
        with self._lock:
            rows = list(self.lines)
            if self._pending:
                rows.append(self._pending)
        return "\n".join(rows)


class StreamLogCapture:
    def __init__(self, max_lines: int) -> None:
        self.stdout = CapturedStream("stdout", sys.stdout, max_lines)
        self.stderr = CapturedStream("stderr", sys.stderr, max_lines)

    def install(self) -> None:
        sys.stdout = self.stdout  # type: ignore[assignment]
        sys.stderr = self.stderr  # type: ignore[assignment]

    def snapshot(self) -> dict:
        return {
            "stdout": self.stdout.snapshot(),
            "stderr": self.stderr.snapshot(),
            "max_lines": OVERMIND_LOG_CAPTURE_LINES,
            "captured_at": datetime.utcnow().isoformat() + "Z",
            "capture_active": True,
        }


def _drone_log_stream_active(device_id: str) -> bool:
    now = time.monotonic()
    with _DRONE_LOG_STREAM_LOCK:
        expires_at = _DRONE_LOG_STREAM_REQUESTS.get(device_id)
        if not expires_at or expires_at <= now:
            _DRONE_LOG_STREAM_REQUESTS.pop(device_id, None)
            _DRONE_LOG_STREAM_PAYLOADS.pop(device_id, None)
            return False
        return True


def _request_drone_log_stream(device_id: str) -> None:
    with _DRONE_LOG_STREAM_LOCK:
        _DRONE_LOG_STREAM_REQUESTS[device_id] = time.monotonic() + DRONE_LOG_STREAM_TTL_SECONDS


def _store_drone_log_stream(device_id: str, payload: dict) -> None:
    if not _drone_log_stream_active(device_id):
        return
    with _DRONE_LOG_STREAM_LOCK:
        _DRONE_LOG_STREAM_PAYLOADS[device_id] = dict(payload)


def _current_drone_log_stream(device_id: str) -> Optional[dict]:
    if not _drone_log_stream_active(device_id):
        return None
    with _DRONE_LOG_STREAM_LOCK:
        payload = _DRONE_LOG_STREAM_PAYLOADS.get(device_id)
        return dict(payload) if isinstance(payload, dict) else None


class CapturedLoggingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if _STREAM_LOG_CAPTURE is None:
            return
        try:
            message = self.format(record)
            # All log records are written to stderr (never stdout). Per-query
            # PostgreSQL timing noise still reaches CloudWatch but is excluded
            # from the bounded tail shown in the admin UI.
            _STREAM_LOG_CAPTURE.stderr.write(message + "\n", capture=not _is_db_query_log_record(record))
        except Exception:
            pass


def _is_db_query_log_record(record: logging.LogRecord) -> bool:
    """Exclude per-query PostgreSQL timing logs from the captured runtime logs."""
    if record.name == "overmind.postgres_store":
        message = str(record.msg or "")
        if message.startswith("PostgreSQL query"):
            return True
    return False


def install_stream_log_capture() -> None:
    global _STREAM_LOG_CAPTURE
    if _STREAM_LOG_CAPTURE is not None:
        return
    _STREAM_LOG_CAPTURE = StreamLogCapture(OVERMIND_LOG_CAPTURE_LINES)
    _STREAM_LOG_CAPTURE.install()
    root = logging.getLogger()
    # The AWS Lambda runtime attaches a root handler that emits log records to
    # stdout. Drop pre-existing handlers so application logs (warnings, errors,
    # and tracebacks) flow only through CapturedLoggingHandler -> stderr, leaving
    # stdout for ordinary operational output. Records still reach CloudWatch
    # because the captured stderr forwards to the real stderr file descriptor.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = CapturedLoggingHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


def stream_log_snapshot() -> dict:
    return _STREAM_LOG_CAPTURE.snapshot() if _STREAM_LOG_CAPTURE else {
        "stdout": "",
        "stderr": "",
        "max_lines": OVERMIND_LOG_CAPTURE_LINES,
        "captured_at": datetime.utcnow().isoformat() + "Z",
        "capture_active": False,
    }


def mark_device_seen_fast(device: dict) -> None:
    """Record liveness before heavier heartbeat work runs."""
    if not isinstance(device, dict):
        return
    device["last_seen"] = datetime.utcnow()
    if device.get("last_known_status") in {None, "", "offline"}:
        device["last_known_status"] = "online"
    try:
        postgres_store.touch_device_last_seen(str(device.get("id") or ""))
    except Exception:
        logger.exception("Fast last_seen update failed device_id=%s", device.get("device_id"))


send_verification_email = partial(
    _send_verification_email,
    email_client=emailer,
    ttl_minutes=VERIFICATION_TTL_MINUTES,
)
send_password_reset_email = partial(
    _send_password_reset_email,
    email_client=emailer,
    ttl_minutes=PASSWORD_RESET_TTL_MINUTES,
)
send_invitation_email = partial(_send_invitation_email, email_client=emailer)


def get_app_version() -> str:
    return os.getenv("OVERMIND_VERSION") or (VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "dev")


def get_version_badge_html() -> str:
    """Render an optional runtime deployment label in the application navbar."""
    version_label = get_app_version().strip()
    if not version_label:
        return ""
    return f'<span class="badge text-bg-secondary" id="overmind-version-badge">{html.escape(version_label)}</span>'


APP_VERSION = get_app_version()


app = FastAPI(
    title="Batocera Overmind API",
    description="API for Batocera system management and game tracking",
    version=APP_VERSION.lstrip("v"),
)
_RUNTIME_SECRET_REFRESHER = None
_NOTIFICATION_DELIVERY_THREAD = None
_RUNTIME_INITIALIZED = False


def is_lambda_runtime() -> bool:
    """Return whether Overmind is running inside AWS Lambda."""
    runtime = (os.getenv("OVERMIND_RUNTIME") or "").strip().lower()
    return runtime == "lambda" or bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def apply_runtime_config_side_effects(values: dict[str, str]) -> None:
    global TOKEN_HASH_SECRET
    postgres_host_override = os.getenv("OVERMIND_POSTGRES_HOST_OVERRIDE")
    if postgres_host_override:
        os.environ["OVERMIND_POSTGRES_HOST"] = postgres_host_override
    jwt_signing_secret = os.getenv("JWT_SIGNING_SECRET") or values.get("JWT_SIGNING_SECRET")
    if jwt_signing_secret:
        auth.JWT_SIGNING_SECRET = jwt_signing_secret
    if "SECRET_KEY" in values:
        auth.SECRET_KEY = values["SECRET_KEY"]
    if "TOKEN_HASH_SECRET" in values or "SECRET_KEY" in values:
        TOKEN_HASH_SECRET = os.getenv("TOKEN_HASH_SECRET", auth.SECRET_KEY)
    postgres_store.refresh_from_environment()


def ensure_self_signed_cert():
    return _ensure_self_signed_cert()


def run_https_app() -> None:
    _run_https_app(app, certificate_loader=ensure_self_signed_cert)



def poll_notification_delivery_once() -> int:
    """Deliver queued notification events in per-channel digests."""
    return notification_delivery.deliver_pending_notifications(
        db,
        limit=NOTIFICATION_DELIVERY_MAX_NOTIFICATIONS_PER_RUN,
    )


def poll_device_status_notifications_once() -> None:
    """Detect offline/online Drone status transitions once."""
    db.update_device_status_notifications(
        SWARM_OFFLINE_THRESHOLD_SECONDS,
        limit=DEVICE_STATUS_MAX_DEVICES_PER_RUN,
    )


def run_scheduled_job(job_name: str) -> dict:
    """Run a single background job by name for EventBridge or local scripts."""
    job = str(job_name or "").strip().lower().replace("_", "-")
    if job in {"notification-delivery", "notifications"}:
        delivered = poll_notification_delivery_once()
        return {"job": job, "status": "ok", "delivered": delivered}
    if job in {"device-status", "offline-status", "status-notifications"}:
        poll_device_status_notifications_once()
        return {"job": job, "status": "ok"}
    raise ValueError(f"Unknown Overmind scheduled job: {job_name}")


def start_notification_delivery_poller() -> None:
    """Start batched delivery for notifications that do not require real-time alerts."""
    global _NOTIFICATION_DELIVERY_THREAD
    interval_seconds = max(0, int(os.getenv("NOTIFICATION_DELIVERY_INTERVAL_SECONDS", str(NOTIFICATION_DELIVERY_INTERVAL_SECONDS))))
    if interval_seconds == 0 or (_NOTIFICATION_DELIVERY_THREAD and _NOTIFICATION_DELIVERY_THREAD.is_alive()):
        return

    def loop() -> None:
        while True:
            time.sleep(max(5, interval_seconds))
            try:
                poll_notification_delivery_once()
            except Exception as error:
                logger.warning("Notification digest delivery poll failed: %s", error)

    _NOTIFICATION_DELIVERY_THREAD = threading.Thread(target=loop, name="notification-delivery-poller", daemon=True)
    _NOTIFICATION_DELIVERY_THREAD.start()


def initialize_runtime(*, start_pollers: Optional[bool] = None, prepare_tls: Optional[bool] = None) -> None:
    """Initialize runtime services once for local, container, or Lambda execution."""
    global _RUNTIME_SECRET_REFRESHER, _RUNTIME_INITIALIZED
    if _RUNTIME_INITIALIZED:
        return
    install_stream_log_capture()

    lambda_runtime = is_lambda_runtime()
    if start_pollers is None:
        start_pollers = not lambda_runtime
    if prepare_tls is None:
        prepare_tls = not lambda_runtime

    _RUNTIME_SECRET_REFRESHER = load_runtime_secret_once(on_apply=apply_runtime_config_side_effects)
    if not lambda_runtime:
        _RUNTIME_SECRET_REFRESHER.start()

    environment = (os.getenv("OVERMIND_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "").lower()
    if not database_url():
        raise RuntimeError("OVERMIND_DATABASE_URL or PostgreSQL environment variables are required")

    if environment in {"prod", "production"}:
        selected_provider = emailer.provider()
        if selected_provider == "smtp":
            required = ["EMAIL_FROM", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"]
            missing = [name for name in required if not os.getenv(name)]
            if missing:
                raise RuntimeError(f"SMTP email requires environment variable(s) in production: {', '.join(missing)}")
        elif selected_provider == "ses":
            aws_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
            aws_ses_from = os.getenv("EMAIL_FROM") or os.getenv("AWS_SES_FROM_ADDRESS") or os.getenv("SES_FROM_EMAIL")
            if not aws_region:
                raise RuntimeError("AWS_REGION environment variable is required in production mode for AWS SES email")
            if not aws_ses_from:
                raise RuntimeError("EMAIL_FROM or AWS_SES_FROM_ADDRESS environment variable is required in production mode for AWS SES email.")

    key_file, cert_file = (None, None)
    if prepare_tls:
        key_file, cert_file = ensure_self_signed_cert()

    print("🎮 Batocera Overmind API started")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("🏠 UI: http://localhost:8000/")
    if key_file and cert_file:
        print(f"🔐 Self-signed cert ready: {cert_file} / {key_file}")

    env_provider = (os.getenv("EMAIL_PROVIDER") or "").strip().lower()
    if env_provider or environment in {"prod", "production"}:
        print(f"📧 Email provider: {emailer.provider()}")

    if not postgres_store.available():
        detail = f": {postgres_store.last_error}" if getattr(postgres_store, "last_error", None) else ""
        raise RuntimeError(f"PostgreSQL is required and must be reachable before Overmind can start{detail}")
    db.refresh_persistent_state()

    if start_pollers:
        start_notification_delivery_poller()

    _RUNTIME_INITIALIZED = True

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled request error method=%s path=%s", request.method, request.url.path)
    return Response(content='{"message":"Internal Server Error"}', media_type="application/json", status_code=500)

# Mount the content directory for static assets like main.jpeg
if CONTENT_DIR.exists():
    app.mount("/content", StaticFiles(directory=str(CONTENT_DIR)), name="content")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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


class OAuthProviderError(Exception):
    """Raised when an upstream OAuth provider call fails."""

    def __init__(self, provider: str, step: str):
        self.provider = provider
        self.step = step
        super().__init__(f"{provider} OAuth {step} failed")


def oauth_provider_enabled(provider: str) -> bool:
    """Return whether a social auth provider has the required ENV VARs."""
    config = OAUTH_PROVIDERS.get(provider)
    return bool(config and os.getenv(config["client_id"]) and os.getenv(config["client_secret"]))


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def create_oauth_state(provider: str) -> str:
    """Create a signed OAuth state value that works across Lambda instances."""
    payload = {
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "exp": int(time.time()) + OAUTH_STATE_TTL_SECONDS,
    }
    payload_b64 = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(auth.SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return f"{payload_b64}.{_urlsafe_b64encode(signature)}"


def verify_oauth_state(state_value: str, provider: str) -> bool:
    """Validate OAuth state without relying on process-local memory."""
    if not state_value:
        return False

    try:
        payload_b64, signature_b64 = state_value.split(".", 1)
        expected = hmac.new(auth.SECRET_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
        supplied = _urlsafe_b64decode(signature_b64)
        if not hmac.compare_digest(expected, supplied):
            return False
        payload = json.loads(_urlsafe_b64decode(payload_b64).decode())
    except (ValueError, json.JSONDecodeError, binascii.Error):
        return False

    if payload.get("provider") != provider:
        return False
    try:
        return int(payload.get("exp", 0)) >= int(time.time())
    except (TypeError, ValueError):
        return False


def get_public_base_url(request: Request) -> str:
    """Build redirect base URL from ENV or current request."""
    configured = os.getenv("OAUTH_REDIRECT_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def oauth_failure_redirect(provider: str, message: str) -> RedirectResponse:
    """Return browser OAuth callbacks to the UI with a readable failure."""
    encoded_message = urllib.parse.quote(message)
    encoded_provider = urllib.parse.quote(provider)
    return RedirectResponse(f"/#oauth_error={encoded_message}&provider={encoded_provider}")


def oauth_provider_label(provider: str) -> str:
    """Return a user-facing OAuth provider label."""
    return "GitHub" if provider == "github" else provider.title()


def read_oauth_json(provider: str, step: str, request: urllib.request.Request) -> Union[dict, list]:
    """Read JSON from an OAuth provider and log upstream failures without leaking tokens."""
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:500] if exc.fp else ""
        logger.warning(
            "OAuth provider request failed provider=%s step=%s status=%s reason=%s body=%s",
            provider,
            step,
            exc.code,
            exc.reason,
            body,
        )
        raise OAuthProviderError(provider, step) from exc
    except urllib.error.URLError as exc:
        logger.warning("OAuth provider request failed provider=%s step=%s reason=%s", provider, step, exc.reason)
        raise OAuthProviderError(provider, step) from exc
    except json.JSONDecodeError as exc:
        logger.warning("OAuth provider returned invalid JSON provider=%s step=%s", provider, step)
        raise OAuthProviderError(provider, step) from exc


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
            "username": user.get("username"),
            "full_name": user["full_name"]
        },
        "swarms": db.get_user_swarms(user["id"]),
    }


def _authenticate_password_user(email: str, password: str) -> Optional[dict]:
    user = db.get_user_by_email(email)
    if not user:
        if is_lambda_runtime():
            if getattr(postgres_store, "last_error", None):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"PostgreSQL is unavailable: {postgres_store.last_error}",
                )
        else:
            db.refresh_persistent_state()
            user = db.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password"]):
        return None
    ensure_active_user(user)
    return user


def hash_secret_token(token: str) -> str:
    return auth.hash_password(f"{TOKEN_HASH_SECRET}:{token}")


def verify_secret_token(token: str, stored_hash: str) -> bool:
    return auth.verify_password(f"{TOKEN_HASH_SECRET}:{token}", stored_hash)


def create_and_send_verification(user: dict) -> None:
    code = f"{secrets.randbelow(1000000):06d}"
    raw_token = secrets.token_urlsafe(32)
    db.create_email_verification(
        user["id"],
        code,
        hash_secret_token(raw_token),
        datetime.utcnow() + timedelta(minutes=VERIFICATION_TTL_MINUTES),
    )
    sent = send_verification_email(user, code, raw_token)
    logger.info("Verification email send result user_id=%s sent=%s", user.get("id"), sent)


def ensure_active_user(user: dict) -> None:
    _ensure_active_user(user)


def selected_swarm_id(user: dict, swarm_id: Optional[str] = None) -> str:
    return _selected_swarm_id(user, swarm_id, data_store=db)


def require_swarm_role(user: dict, swarm_id: str, roles: set[str]) -> dict:
    return _require_swarm_role(user, swarm_id, roles, data_store=db, owner_role=OWNER_ROLE)


def require_device_admin(user: dict, device: dict) -> dict:
    return _require_device_admin(user, device, data_store=db, role_checker=require_swarm_role)


def require_super_admin(authorization: Optional[str]) -> dict:
    return _require_super_admin(authorization, current_user=get_current_user, super_admin_email=SUPER_ADMIN_EMAIL)


def _user_label(user: dict) -> str:
    return str(user.get("full_name") or user.get("email") or user.get("id") or "Unknown user")


def notify_sync_triggered(user: dict, device: dict, sync_type: str, nature: str, targets: list, sources: list, action: Optional[dict] = None) -> None:
    swarm_id = device.get("swarm_id")
    if not swarm_id:
        return
    target_devices = db._notification_devices(targets)
    source_devices = db._notification_devices(sources)
    target_names = ", ".join(row.get("device_name") or row.get("device_id") for row in target_devices) or db._device_label(device, device.get("device_id"))
    source_names = ", ".join(row.get("device_name") or row.get("device_id") for row in source_devices) or "any available source"
    db.add_swarm_notification(
        swarm_id,
        "sync_triggered",
        f"{sync_type} sync triggered",
        f"{_user_label(user)} triggered {nature} for {target_names} from {source_names}.",
        {
            "sync_type": sync_type,
            "nature": nature,
            "targets": target_devices or [{"device_id": device.get("device_id"), "device_name": db._device_label(device, device.get("device_id"))}],
            "sources": source_devices,
            "action_id": action.get("id") if isinstance(action, dict) else None,
        },
        actor_user_id=user.get("id"),
    )


def resolvable_asset_sources(sources: list, target_device_id: Optional[str] = None) -> list:
    """Return sources that have been peer-resolved by at least one drone in the swarm."""
    eligible = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("device_id") or source.get("drone_id") or "").strip()
        if not source_id or source_id == str(target_device_id or ""):
            continue
        source_device = db.get_device_by_device_id(source_id)
        if not source_device:
            continue
        if not db.is_drone_peer_resolvable(source_id):
            continue
        eligible.append({
            "device_id": source_id,
            "device_name": source.get("device_name") or source_device.get("device_name") or source_id,
        })
    return eligible


def _normalized_rom_path_variants(value: object, system_name: object = "") -> set[str]:
    raw = str(value or "").replace("\\", "/").strip().lstrip("./")
    if not raw:
        return set()
    lowered = raw.lower().lstrip("/")
    system = str(system_name or "").strip().lower().strip("/")
    variants = {lowered}
    userdata_prefix = "/userdata/roms/"
    if lowered.startswith(userdata_prefix.lstrip("/")):
        variants.add(lowered[len(userdata_prefix.lstrip("/")) :])
    if lowered.startswith(userdata_prefix):
        variants.add(lowered[len(userdata_prefix) :])
    if system:
        for candidate in list(variants):
            if candidate.startswith(f"{system}/"):
                variants.add(candidate[len(system) + 1 :])
            else:
                variants.add(f"{system}/{candidate}")
    for candidate in list(variants):
        parts = [part for part in candidate.split("/") if part]
        if parts:
            variants.add(parts[-1])
    return {item for item in variants if item}


def _rom_artwork_matches(system_name: str, rom_path: str, artwork_row: dict) -> bool:
    row_system = str(artwork_row.get("system_name") or artwork_row.get("system") or "").strip().lower()
    if row_system and row_system != str(system_name or "").strip().lower():
        return False
    requested = _normalized_rom_path_variants(rom_path, system_name)
    available = _normalized_rom_path_variants(
        artwork_row.get("rom_path") or artwork_row.get("file_path") or artwork_row.get("relative_path") or artwork_row.get("rom_name"),
        system_name,
    )
    return bool(requested & available)


def queue_associated_artwork_syncs(user: dict, device: dict, system_name: str, rom_path: str, rom_name: Optional[str] = None) -> list[dict]:
    """Queue artwork downloads that correspond to a ROM sync."""
    actions: list[dict] = []
    artwork_rows = db.get_master_artwork_for_device(device["user_id"], device["device_id"]) or []
    for row in artwork_rows:
        if row.get("present_on_selected"):
            continue
        artwork_type = str(row.get("artwork_type") or "").strip()
        row_path = str(row.get("rom_path") or row.get("file_path") or row.get("relative_path") or row.get("rom_name") or rom_path).strip()
        target_rom_path = str(rom_path or row_path).replace("\\", "/").strip()
        if target_rom_path.startswith("/userdata/roms/"):
            prefix = f"/userdata/roms/{str(system_name or '').strip()}/"
            if target_rom_path.lower().startswith(prefix.lower()):
                target_rom_path = target_rom_path[len(prefix):]
        target_rom_path = target_rom_path.lstrip("./")
        if not artwork_type or not target_rom_path or not _rom_artwork_matches(system_name, rom_path, row):
            continue
        source_devices = resolvable_asset_sources(row.get("devices") if isinstance(row.get("devices"), list) else [], device.get("device_id"))
        if not source_devices:
            continue
        action = db.create_device_action(device["user_id"], device["device_id"], "sync_artwork", {
            "asset_type": "artwork",
            "system_name": system_name,
            "system": system_name,
            "rom_name": rom_name or row.get("rom_name") or target_rom_path,
            "rom_path": target_rom_path,
            "file_path": target_rom_path,
            "artwork_type": artwork_type,
            "devices": source_devices,
            "triggered_by": "sync_rom",
        })
        if not action:
            continue
        actions.append(action)
        db.add_rom_sync_activity(device["device_id"], {
            "sync_id": action["id"],
            "asset_type": "artwork",
            "target_drone_id": device["device_id"],
            "system": system_name,
            "rom_name": rom_name or row.get("rom_name") or target_rom_path,
            "rom_path": target_rom_path,
            "relative_path": target_rom_path,
            "artwork_type": artwork_type,
            "action": "download",
            "status": "pending",
        })
    return actions


# ==================== Authentication ====================

@app.post("/api/auth/register", response_model=User)
async def register(user_data: UserRegister):
    """Register a new user."""
    db.refresh_persistent_state()
    username = user_data.username.strip()
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")
    if db.username_exists(username):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")
    if db.user_exists(user_data.email):
        print(f"Register failed for {user_data.email}: email_already_registered")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    invitation = None
    if user_data.invitation_token:
        invitation = db.find_invitation_by_token(user_data.invitation_token, verify_secret_token)
        if not invitation or invitation.get("status") != "pending" or datetime.utcnow() > invitation.get("expires_at"):
            print(f"Invitation registration rejected for {user_data.email}: invalid_or_expired")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation expired or invalid")
        if invitation.get("email") != str(user_data.email).lower():
            print(f"Invitation registration rejected for {user_data.email}: email_mismatch")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invitation email mismatch")
    
    hashed_password = auth.hash_password(user_data.password)
    auto_verify = os.getenv("OVERMIND_AUTO_VERIFY_REGISTRATION", "").strip().lower() in {"1", "true", "yes", "on"}
    if invitation:
        auto_verify = True
    user_id = db.create_user(user_data.email, hashed_password, user_data.full_name, verified=auto_verify, auth_provider="password", username=username)
    user = db.get_user(user_id)
    if invitation:
        db.accept_invitation_for_user(invitation, user_id)
        print(f"Invitation registration flow completed for {user_data.email}: swarm_id={invitation.get('swarm_id')}")
    if not auto_verify:
        create_and_send_verification(user)
    
    return User(
        id=user["id"],
        email=user["email"],
        username=user["username"],
        full_name=user["full_name"],
        created_at=user["created_at"]
    )


@app.post("/api/auth/login")
async def login(credentials: UserLogin):
    """Login user and return access token."""
    user = _authenticate_password_user(str(credentials.email), credentials.password)
    if not user:
        print(f"Login failed for {credentials.email}: invalid_credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    return build_login_response(user)


@app.post("/api/auth/refresh")
async def refresh_auth_token(authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    return build_login_response(user)


@app.post("/api/auth/verify-email")
async def verify_email_code(payload: EmailVerificationRequest):
    if not db.verify_email_code(str(payload.email), payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification code")
    return {"status": "verified"}


@app.post("/api/auth/resend-verification")
async def resend_verification_email(payload: EmailVerificationResendRequest):
    user = db.get_user_by_email(str(payload.email))
    if user and not user.get("email_verified"):
        create_and_send_verification(user)
    return {"status": "sent"}


@app.get("/api/auth/verify-email")
async def verify_email_link(token: str):
    user = db.verify_email_token(token, verify_secret_token)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link")
    return RedirectResponse(f"{emailer.base_url()}/#verified=1")


@app.post("/api/auth/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    user = db.get_user_by_email(str(payload.email))
    if user and user.get("auth_provider") in (None, "password") and user.get("password"):
        raw_token = secrets.token_urlsafe(32)
        db.create_password_reset(user["id"], hash_secret_token(raw_token), datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES))
        sent = send_password_reset_email(user, raw_token)
        logger.info("Password reset email send result user_id=%s sent=%s", user.get("id"), sent)
    return {"message": "If an eligible account exists, a password reset email has been sent."}


@app.post("/api/auth/reset-password")
async def reset_password(payload: ResetPasswordRequest):
    if not db.consume_password_reset(payload.token, auth.hash_password(payload.password), verify_secret_token):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset token")
    return {"status": "password_updated"}


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
    db.refresh_persistent_state()
    user = db.get_or_create_social_user(payload.email, payload.full_name, provider)
    return build_login_response(user)


@app.get("/api/auth/{provider}/start")
async def social_auth_start(provider: str, request: Request):
    """Redirect to a configured Google or GitHub OAuth screen."""
    config = OAUTH_PROVIDERS.get(provider)
    if not config or not oauth_provider_enabled(provider):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not configured")

    state = create_oauth_state(provider)
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
    if not verify_oauth_state(state_value or "", provider) and oauth_states.pop(state_value or "", None) != provider:
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
    try:
        token_data = read_oauth_json(provider, "token", token_request)
    except OAuthProviderError:
        return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login failed while requesting an access token.")
    access_token = token_data.get("access_token") if isinstance(token_data, dict) else None
    if not access_token:
        response_keys = list(token_data.keys()) if isinstance(token_data, dict) else []
        logger.warning("OAuth provider did not return an access token provider=%s response_keys=%s", provider, response_keys)
        return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login did not return an access token.")

    user_request = urllib.request.Request(
        config["user_url"],
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": "batocera-overmind"},
    )
    try:
        provider_user = read_oauth_json(provider, "user", user_request)
    except OAuthProviderError:
        return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login failed while loading account details.")

    if not isinstance(provider_user, dict):
        logger.warning("OAuth provider returned unexpected user payload provider=%s", provider)
        return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login returned unexpected account details.")

    email = provider_user.get("email")
    full_name = provider_user.get("name") or provider_user.get("login")
    if provider == "github" and not email:
        email_request = urllib.request.Request(
            config["email_url"],
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": "batocera-overmind"},
        )
        try:
            emails = read_oauth_json(provider, "email", email_request)
        except OAuthProviderError:
            return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login failed while loading email details.")
        if not isinstance(emails, list):
            logger.warning("OAuth provider returned unexpected email payload provider=%s", provider)
            return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login returned unexpected email details.")
        primary = next((item for item in emails if item.get("primary") and item.get("verified")), None)
        email = (primary or emails[0]).get("email") if emails else None
    if not email:
        logger.warning("OAuth provider did not return an email provider=%s", provider)
        return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login did not return a verified email.")

    try:
        user = db.get_or_create_social_user(email, full_name, provider)
    except RuntimeError as error:
        logger.warning("OAuth social login persistence failed provider=%s error=%s", provider, error)
        return oauth_failure_redirect(provider, f"{oauth_provider_label(provider)} login could not reach Overmind storage. Please try again.")
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
        db.refresh_persistent_state()
        user = db.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    ensure_active_user(user)
    
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


def _record_untrusted_drone_connection(
    device_id: str,
    device_name: str,
    batocera_info: dict,
    raw_token: Optional[str],
    reason: str,
) -> None:
    """Record a disconnected Drone for super-admin recovery without trusting ownership."""
    if not device_id or not raw_token:
        return
    try:
        db.create_pending_drone_connection(
            device_id,
            device_name or device_id,
            batocera_info if isinstance(batocera_info, dict) else {},
            user_id=None,
            authorization_token_id=None,
            drone_token_hash=hash_drone_token(raw_token),
            recovery_reason=reason,
        )
        print(f"Untrusted Drone recovery request recorded: device_id={device_id} reason={reason}")
    except Exception:
        logger.exception("Failed to record untrusted Drone recovery request device_id=%s", device_id)


def _heartbeat_batocera_info(heartbeat: dict) -> dict:
    batocera_info = {
        "network": heartbeat.get("network") if isinstance(heartbeat.get("network"), dict) else {},
        "api_port": heartbeat.get("api_port"),
        "scheme": str(heartbeat.get("scheme") or heartbeat.get("protocol") or "").strip() or None,
        "reachable_url": str(heartbeat.get("reachable_url") or "").strip() or None,
        "system_info": heartbeat.get("system_info") if isinstance(heartbeat.get("system_info"), dict) else {},
        "certificate": heartbeat.get("certificate") if isinstance(heartbeat.get("certificate"), dict) else None,
    }
    network = batocera_info.get("network") if isinstance(batocera_info.get("network"), dict) else {}
    ipv4 = network.get("ipv4") if isinstance(network.get("ipv4"), list) else []
    if ipv4:
        batocera_info["ip_address"] = ipv4[0]
    return batocera_info


# ==================== Swarms ====================

@app.get("/api/swarms")
async def list_swarms(authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    default_swarm_id = db.default_swarm_id(user["id"])
    return {"swarms": [{**swarm, "current": swarm.get("id") == default_swarm_id} for swarm in db.get_user_swarms(user["id"])]}


@app.get("/api/notifications")
async def list_notifications(limit: int = 50, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    rows = db.get_user_notifications(user["id"], limit=limit)
    unread = sum(1 for row in rows if not row.get("read"))
    return {"notifications": rows, "unread_count": unread}


@app.post("/api/notifications/read")
async def mark_notifications_read(payload: dict = None, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    ids = payload.get("ids") if isinstance(payload, dict) and isinstance(payload.get("ids"), list) else None
    count = db.mark_notifications_read(user["id"], ids)
    return {"status": "ok", "marked_read": count}


@app.post("/api/notifications/dismiss")
async def dismiss_notifications(payload: dict = None, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    ids = payload.get("ids") if isinstance(payload, dict) and isinstance(payload.get("ids"), list) else None
    count = db.dismiss_notifications(user["id"], ids)
    return {"status": "ok", "dismissed": count}


@app.get("/api/admin/overview")
async def admin_overview(authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    db.refresh_admin_overview_state()
    db._dedupe_all_device_records()
    users = sorted((admin_user_row(user) for user in db.users.values()), key=lambda row: str(row.get("email") or "").lower())
    swarms = sorted((admin_swarm_row(swarm) for swarm in db.swarms.values()), key=lambda row: str(row.get("name") or "").lower())
    drones = sorted((admin_drone_row(device) for device in db.devices.values()), key=lambda row: str(row.get("device_name") or row.get("device_id") or "").lower())
    pending_connections = sorted(
        (admin_pending_drone_connection_row(connection) for connection in db.get_all_pending_drone_connections()),
        key=lambda row: str(row.get("last_seen") or row.get("detected_at") or ""),
        reverse=True,
    )
    return {"users": users, "swarms": swarms, "drones": drones, "pending_connections": pending_connections}


@app.post("/api/admin/drone-connections/{device_id}/assign")
async def admin_assign_drone_connection(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    swarm_id = str((payload or {}).get("swarm_id") or "").strip()
    if not swarm_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="swarm_id is required")
    device = db.admin_assign_pending_drone_connection(device_id, swarm_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending Drone connection or swarm not found")
    return {"status": "assigned", "device": device_response(device)}


@app.get("/api/admin/runtime-metrics")
async def admin_runtime_metrics(authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    return {"metrics": collect_runtime_metrics(Path.cwd())}


@app.get("/api/admin/runtime-logs")
async def admin_runtime_logs(authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    return {"logs": stream_log_snapshot()}


@app.post("/api/admin/run-job")
async def admin_run_scheduled_job(job: str, authorization: Optional[str] = Header(default=None)):
    """Trigger a scheduled maintenance job on demand (super admin only)."""
    require_super_admin(authorization)
    try:
        result = run_scheduled_job(job)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return result


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, authorization: Optional[str] = Header(default=None)):
    user = require_super_admin(authorization)
    if user_id == user["id"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Super admin cannot delete their own account")
    if not db.admin_delete_user(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"status": "deleted", "user_id": user_id}


@app.delete("/api/admin/swarms/{swarm_id}")
async def admin_delete_swarm(swarm_id: str, authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    if not db.admin_delete_swarm(swarm_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Swarm not found")
    return {"status": "deleted", "swarm_id": swarm_id}


@app.delete("/api/admin/drones/{device_id}")
async def admin_delete_drone(device_id: str, authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    if not db.admin_delete_device(device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone not found")
    return {"status": "deleted", "device_id": device_id}


@app.post("/api/swarms")
async def create_swarm(payload: SwarmCreateRequest, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    swarm = db.create_swarm(user["id"], payload.name)
    return {"swarm": {**swarm, "role": OWNER_ROLE}}


@app.get("/api/swarms/{swarm_id}/access")
async def get_swarm_access(swarm_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    access = db.list_swarm_access(swarm_id)
    member = db.get_swarm_member(swarm_id, user["id"])
    return {"access": access, "role": member.get("role") if member else None}


@app.post("/api/swarms/{swarm_id}/invitations")
async def invite_swarm_member(swarm_id: str, payload: SwarmInviteRequest, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    role = READONLY_ROLE
    raw_token = secrets.token_urlsafe(32)
    invite = db.invite_to_swarm(
        swarm_id,
        str(payload.email),
        role,
        hash_secret_token(raw_token),
        datetime.utcnow() + timedelta(days=int(os.getenv("INVITATION_EXPIRE_DAYS", "7"))),
        user["id"],
    )
    invited = db.get_user_by_email(str(payload.email))
    if invited and invited.get("is_active"):
        db.accept_invitations_for_email(str(payload.email), invited["id"])
        print(f"Invitation accepted for existing user {payload.email}: swarm_id={swarm_id}")
    db.refresh_persistent_state()
    swarm = db.swarms.get(swarm_id) or {}
    sent = send_invitation_email(str(payload.email), swarm, role, raw_token)
    logger.info("Invitation email send result invitation_id=%s sent=%s", invite.get("id"), sent)
    print(f"Invitation created for {payload.email}: swarm_id={swarm_id} role={role}")
    return {"invitation": {k: v for k, v in invite.items() if k != "token_hash"}}


@app.post("/api/swarms/{swarm_id}/invitations/{invitation_id}/resend")
async def resend_swarm_invitation(swarm_id: str, invitation_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    db.refresh_persistent_state()
    invite = db.invitations.get(invitation_id)
    if not invite or invite.get("swarm_id") != swarm_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if invite.get("status") != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only pending invitations can be resent")
    raw_token = secrets.token_urlsafe(32)
    invite = db.rotate_pending_invitation(
        swarm_id,
        invitation_id,
        hash_secret_token(raw_token),
        datetime.utcnow() + timedelta(days=int(os.getenv("INVITATION_EXPIRE_DAYS", "7"))),
    )
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    db.refresh_persistent_state()
    swarm = db.swarms.get(swarm_id) or {}
    sent = send_invitation_email(str(invite.get("email") or ""), swarm, READONLY_ROLE, raw_token)
    logger.info("Invitation resend email result invitation_id=%s sent=%s", invite.get("id"), sent)
    print(f"Invitation resent for {invite.get('email')}: swarm_id={swarm_id}")
    return {"status": "sent", "invitation": {k: v for k, v in invite.items() if k != "token_hash"}}


@app.delete("/api/swarms/{swarm_id}/invitations/{invitation_id}")
async def remove_swarm_invitation(swarm_id: str, invitation_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    invite = db.remove_pending_invitation(swarm_id, invitation_id)
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending invitation not found")
    print(f"Pending invitation removed for {invite.get('email')}: swarm_id={swarm_id}")
    return {"status": "removed", "invitation_id": invitation_id}


@app.get("/api/invitations/status")
async def invitation_status(token: str):
    invite = db.find_invitation_by_token(token, verify_secret_token)
    if not invite:
        print("Invitation rejected: invalid")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation expired or invalid")
    if invite.get("status") != "pending":
        invited_user = db.get_user_by_email(str(invite.get("email") or ""))
        if invite.get("status") == "accepted" and invited_user and db.get_swarm_member(invite.get("swarm_id"), invited_user["id"]):
            return {
                "status": "accepted",
                "email": invite.get("email"),
                "swarm_id": invite.get("swarm_id"),
                "role": invite.get("role") or READONLY_ROLE,
                "registered": True,
            }
        print(f"Invitation rejected for {invite.get('email')}: already_used")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation already used")
    if datetime.utcnow() > invite.get("expires_at"):
        print(f"Invitation rejected for {invite.get('email')}: expired")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation expired or invalid")
    return {
        "status": "pending",
        "email": invite.get("email"),
        "swarm_id": invite.get("swarm_id"),
        "role": invite.get("role") or READONLY_ROLE,
        "registered": bool(db.get_user_by_email(str(invite.get("email") or ""))),
    }


@app.post("/api/invitations/accept")
async def accept_invitation(payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    token = str(payload.get("token") or "")
    invite = db.find_invitation_by_token(token, verify_secret_token)
    if not invite:
        print(f"Invitation rejected for user={user.get('email')}: invalid")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired invitation")
    if invite.get("email") != str(user.get("email") or "").lower():
        print(f"Invitation rejected for user={user.get('email')}: email_mismatch invited={invite.get('email')}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invitation email mismatch")
    if invite.get("status") == "accepted" and db.get_swarm_member(invite.get("swarm_id"), user["id"]):
        return {"status": "accepted", "swarm_id": invite["swarm_id"]}
    invite = db.accept_invitation_for_user(invite, user["id"])
    if not invite:
        print(f"Invitation rejected for user={user.get('email')}: expired_or_used")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired invitation")
    db.add_swarm_notification(
        invite["swarm_id"],
        "swarm_member_added",
        "Swarm member added",
        f"{_user_label(user)} joined the swarm.",
        {"user_id": user["id"], "email": user.get("email")},
        actor_user_id=user["id"],
    )
    print(f"Invitation accepted for {user.get('email')}: swarm_id={invite['swarm_id']}")
    return {"status": "accepted", "swarm_id": invite["swarm_id"]}


@app.delete("/api/swarms/{swarm_id}/members/{target_user_id}")
async def remove_swarm_member(swarm_id: str, target_user_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {"overlord"})
    target = db.get_user(target_user_id) or {}
    if not db.remove_swarm_member(swarm_id, target_user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Swarm member not found")
    db.add_swarm_notification(
        swarm_id,
        "swarm_member_removed",
        "Swarm member removed",
        f"{_user_label(target)} was removed from the swarm by {_user_label(user)}.",
        {"user_id": target_user_id, "email": target.get("email")},
        actor_user_id=user["id"],
    )
    return {"status": "removed"}


@app.patch("/api/swarms/{swarm_id}/members/{target_user_id}")
async def update_swarm_member(swarm_id: str, target_user_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    role = str(payload.get("role") or "").lower()
    if role != READONLY_ROLE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role must be overseer")
    if not db.update_swarm_member_role(swarm_id, target_user_id, role):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Swarm member not found")
    return {"status": "updated", "role": role}


@app.patch("/api/swarms/{swarm_id}")
async def update_swarm(swarm_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    if len(name) > 80:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name must be 80 characters or fewer")
    swarm = db.update_swarm_name(swarm_id, name)
    if not swarm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Swarm not found")
    return {"swarm": {**swarm, "role": (db.get_swarm_member(swarm_id, user["id"]) or {}).get("role")}}


# ==================== Device Management ====================

@app.post("/api/devices/register")
async def register_device(device_data: DeviceRegister, authorization: Optional[str] = Header(default=None)):
    """Register an authorized Drone and return its bearer token."""
    db.refresh_persistent_state()
    raw_auth_token = device_data.authorization_token or (get_bearer_token(authorization) if authorization else None)
    batocera_info = device_data.batocera_info.model_dump()
    certificate = batocera_info.get("certificate") if isinstance(batocera_info.get("certificate"), dict) else {}
    device_fingerprint = str(certificate.get("fingerprint") or certificate.get("sha256_fingerprint") or "").strip()
    claimed_token = db.claim_integration_token(
        str(device_data.email or ""),
        raw_auth_token,
        device_data.device_id,
        device_fingerprint,
    )
    if not claimed_token:
        _record_untrusted_drone_connection(
            device_data.device_id,
            device_data.device_name,
            batocera_info,
            raw_auth_token,
            "invalid_authorization_token",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Drone authorization token")
    user = claimed_token["user"]
    integration_token = claimed_token["token"]

    if device_data.api_port is not None:
        batocera_info["api_port"] = device_data.api_port
    if device_data.scheme:
        batocera_info["scheme"] = device_data.scheme
    if device_data.reachable_url:
        batocera_info["reachable_url"] = device_data.reachable_url

    existing_device = db.get_device_by_device_id(device_data.device_id)
    if existing_device and existing_device.get("user_id") == user["id"] and existing_device.get("approval_status", "approved") != "approved":
        db.create_pending_drone_connection(
            device_data.device_id,
            device_data.device_name,
            batocera_info,
            user_id=user["id"],
            authorization_token_id=integration_token.get("id"),
        )
        return {
            "message": "Psionic connection detected. Awaiting Overlord approval.",
            "status": "pending",
            "device_id": device_data.device_id,
        }

    if db.device_exists(user["id"], device_data.device_id):
        device = db.get_device_by_device_id(device_data.device_id)
        if not device:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        token_hash = hash_drone_token(raw_auth_token)
        db.set_device_authorization_token(
            user["id"],
            device_data.device_id,
            integration_token.get("id"),
            token_hash=token_hash,
            device_name=device_data.device_name,
        )
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
            "message": "Drone already registered. Existing bound credential accepted.",
            "status": "approved",
            "device_id": device_data.device_id,
            "drone_token": raw_auth_token,
        }

    db.create_pending_drone_connection(
        device_data.device_id,
        device_data.device_name,
        batocera_info,
        user_id=user["id"],
        authorization_token_id=integration_token.get("id"),
    )
    return {
        "message": "Psionic connection detected. Awaiting Overlord approval.",
        "status": "pending",
        "device_id": device_data.device_id,
    }


@app.post("/api/drones/claim-ownership")
async def claim_drone_ownership(payload: dict):
    """Claim a Drone directly with Overmind account credentials."""
    device_id = str(payload.get("device_id") or payload.get("drone_id") or "").strip()
    device_name = str(payload.get("device_name") or payload.get("drone_name") or device_id or "Drone").strip()
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    print(f"Drone ownership claim attempted: device_id={device_id}")
    if not device_id or not email or not password:
        print(f"Drone ownership claim failed: device_id={device_id} reason=missing_required_fields")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_id, email, and password are required")
    try:
        user = _authenticate_password_user(email, password)
    except HTTPException:
        user = None
    if not user:
        print(f"Drone ownership claim failed: device_id={device_id} reason=invalid_credentials")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    batocera_info = payload.get("batocera_info") if isinstance(payload.get("batocera_info"), dict) else {}
    if not batocera_info:
        batocera_info = {
            "ip_address": str(payload.get("ip_address") or ""),
            "network": payload.get("network") if isinstance(payload.get("network"), dict) else {},
            "api_port": payload.get("api_port"),
            "scheme": payload.get("scheme") or "https",
            "reachable_url": payload.get("reachable_url"),
            "system_info": payload.get("system_info") if isinstance(payload.get("system_info"), dict) else {},
            "certificate": payload.get("certificate") if isinstance(payload.get("certificate"), dict) else None,
        }

    db.create_device(user["id"], device_id, device_name, batocera_info, raw_token=generate_drone_token())
    db.add_device_admin_claim(user["id"], device_id)
    db.deny_pending_drone_connection(user["id"], device_id)
    print(f"Drone ownership claim succeeded: device_id={device_id} user_id={user['id']}")
    return {"status": "claimed", "device_id": device_id, "drone_token": None}


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
async def list_drone_connections(swarm_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    """List pending drone connection attempts for the Overlord."""
    user = get_current_user(authorization)
    db.refresh_persistent_state()
    sid = selected_swarm_id(user, swarm_id)
    require_swarm_role(user, sid, {"overlord"})
    return {"connections": db.get_pending_drone_connections(user["id"])}


@app.post("/api/drone-connections/{device_id}/accept")
async def accept_drone_connection(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Accept a pending drone connection."""
    user = get_current_user(authorization)
    db.refresh_persistent_state()
    sid = selected_swarm_id(user)
    require_swarm_role(user, sid, {"overlord"})
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
    db.refresh_persistent_state()
    sid = selected_swarm_id(user)
    require_swarm_role(user, sid, {"overlord"})
    if not db.deny_pending_drone_connection(user["id"], device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone connection not found")
    return {"message": "Drone connection denied.", "device_id": device_id}


@app.get("/api/devices")
async def list_devices(swarm_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    """List all devices for the authenticated user."""
    user = get_current_user(authorization)
    db.refresh_persistent_state()
    if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        db.update_device_status_notifications(SWARM_OFFLINE_THRESHOLD_SECONDS)
    sid = selected_swarm_id(user, swarm_id) if swarm_id else None
    devices = db.get_user_devices(user["id"], sid)
    
    return {
        "devices": [
            device_response(d) for d in devices
        ]
    }


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str, log_limit: int = 10, authorization: Optional[str] = Header(default=None)):
    """Get device details."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    response = device_response(device)
    stream_payload = _current_drone_log_stream(device_id)
    if stream_payload is not None:
        response["log_sources"] = stream_payload
        response["log_stream_active"] = True
    else:
        response["log_sources"] = db.get_device_log_sources(device_id, line_limit=log_limit)
        response["log_stream_active"] = False
    return response


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


@app.post("/api/devices/{device_id}/certificate/sign")
async def sign_device_certificate(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Sign a CSR for an already approved Drone."""
    device = get_current_drone(device_id, authorization)
    csr_pem = str(payload.get("csr_pem") or "")
    if "BEGIN CERTIFICATE REQUEST" not in csr_pem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="csr_pem is required")
    try:
        signed = sign_drone_csr(csr_pem, device_id, int(payload.get("days") or 365))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSR could not be signed")
    device.setdefault("certificate", {})
    device["certificate"]["overmind_signed_at"] = datetime.utcnow()
    device["certificate"]["serial_number"] = signed.get("serial_number")
    db._persist_state()
    return signed


@app.post("/api/devices/{device_id}/disconnect")
async def disconnect_device_from_drone(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Allow an approved Drone to disconnect itself from its swarm."""
    device = get_current_drone(device_id, authorization)
    if not db.delete_device(device["user_id"], device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "disconnected", "device_id": device_id}


@app.post("/api/devices/{device_id}/token/rotate")
async def rotate_device_token(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Rotate a Drone bearer token. The raw value is returned once."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    rotated = db.rotate_device_token(device["user_id"], device_id)
    if not rotated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"device": device_response(rotated["device"]), "drone_token": rotated["token"]}


@app.patch("/api/devices/{device_id}/auto-sync")
async def update_device_auto_sync(
    device_id: str,
    payload: dict,
    authorization: Optional[str] = Header(default=None),
):
    """Update per-Drone ROM metadata sync policy."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    systems = payload.get("systems") if isinstance(payload.get("systems"), list) else []
    policy = db.update_device_auto_sync_policy(device["user_id"], device_id, bool(payload.get("enabled")), systems)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"auto_sync_policy": policy}


@app.delete("/api/devices/{device_id}")
async def delete_device(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Delete a device and its associated ROM/gameplay data."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    if not db.delete_device(device["user_id"], device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"message": "Device deleted successfully", "device_id": device_id}


@app.get("/api/devices/{device_id}/actions")
async def list_device_actions(device_id: str, authorization: Optional[str] = Header(default=None)):
    """List actions for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    actions = db.get_device_actions(device["user_id"], device_id)
    if actions is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"actions": actions}


@app.delete("/api/devices/{device_id}/actions")
async def delete_device_actions(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Clear queued or in-progress remote actions for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    deleted_count = db.clear_device_actions(device["user_id"], device_id)
    if deleted_count is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "deleted", "deleted_count": deleted_count}


@app.get("/api/downloads")
async def list_downloads(device_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    if device_id:
        device = db.user_can_access_device(user["id"], device_id)
        if not device:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    states = db.get_download_states(user["id"], device_id=device_id)
    return {
        "concurrency": {"scope": "target_drone", "active_limit": 1},
        "targets": states,
    }


@app.post("/api/devices/{device_id}/downloads/{job_id}/cancel")
async def cancel_device_download(device_id: str, job_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    action = db.create_device_action(device["user_id"], device_id, "cancel_download", {"job_id": job_id})
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "queued", "action": action}


@app.post("/api/devices/{device_id}/downloads")
async def update_device_downloads(device_id: str, payload: DroneDownloadsReport, authorization: Optional[str] = Header(default=None)):
    """Persist a live download-state snapshot pushed by a Drone."""
    get_current_drone(device_id, authorization)
    report = payload.model_dump(exclude_none=True)
    state = db.store_download_state(device_id, report)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    active_count = len(state.get("active") or [])
    queued_count = len(state.get("queued") or [])
    recent_count = len(state.get("recent") or [])
    print(f"Download state accepted for {device_id}: active={active_count} queued={queued_count} recent={recent_count}")
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/actions")
async def create_device_action(
    device_id: str,
    payload: dict,
    authorization: Optional[str] = Header(default=None),
):
    """Queue a remote action for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    action_type = str(payload.get("action") or "").strip().lower()
    if action_type == "reboot":
        action_type = "restart"
    if action_type not in SUPPORTED_DEVICE_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action")
    if action_type in {"rebuild_asset_metadata", "purge_asset_cache"}:
        db.clear_device_asset_metadata(device["user_id"], device_id)
    action = db.create_device_action(device["user_id"], device_id, action_type, payload.get("payload") if isinstance(payload.get("payload"), dict) else {})
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"action": action}


@app.post("/api/devices/{device_id}/actions/claim")
async def claim_device_action(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Claim all currently pending actions for a polling drone."""
    get_current_drone(device_id, authorization)
    actions = db.claim_pending_device_actions(device_id)
    return {"actions": actions, "action": actions[0] if actions else None}


@app.post("/api/devices/{device_id}/heartbeat")
async def drone_heartbeat(device_id: str, payload: DroneHeartbeatRequest, authorization: Optional[str] = Header(default=None)):
    """Update drone last-seen and return the next pending action, if any."""
    heartbeat = payload.model_dump(exclude_none=True)
    try:
        raw_token = get_bearer_token(authorization)
    except HTTPException:
        raw_token = None
    device = db.verify_device_token(device_id, raw_token or "")
    if not device:
        _record_untrusted_drone_connection(
            device_id,
            str(heartbeat.get("device_name") or device_id),
            _heartbeat_batocera_info(heartbeat),
            raw_token,
            "invalid_drone_token",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Drone token")
    mark_device_seen_fast(device)
    try:
        db.update_device_last_seen(
            device["id"],
            network=heartbeat.get("network") if isinstance(heartbeat.get("network"), dict) else None,
            rom_systems=None,
            api_port=heartbeat.get("api_port") if heartbeat.get("api_port") is not None else None,
            scheme=str(heartbeat.get("scheme") or heartbeat.get("protocol") or "").strip() or None,
            reachable_url=str(heartbeat.get("reachable_url") or "").strip() or None,
            certificate=heartbeat.get("certificate") if isinstance(heartbeat.get("certificate"), dict) else None,
            system_info=heartbeat.get("system_info") if isinstance(heartbeat.get("system_info"), dict) else None,
        )
    except Exception:
        logger.exception("Heartbeat state update failed device_id=%s", device_id)
    drone_name = str(heartbeat.get("device_name") or "").strip()
    if drone_name:
        try:
            db.update_device_name(device_id, drone_name)
        except Exception:
            logger.exception("Heartbeat device name update failed device_id=%s", device_id)
    if isinstance(heartbeat.get("rom_metadata"), dict):
        print(f"Heartbeat ROM metadata ignored for {device_id}: use /api/devices/{device_id}/rom-metadata")
    if isinstance(heartbeat.get("rom_systems"), list) and heartbeat.get("rom_systems"):
        print(f"Heartbeat ROM systems ignored for {device_id}: use /api/devices/{device_id}/rom-metadata")
    if isinstance(heartbeat.get("downloads"), dict):
        try:
            db.store_download_state(device_id, heartbeat["downloads"])
        except Exception:
            logger.exception("Heartbeat download state update failed device_id=%s", device_id)
    rom_fingerprint = str(heartbeat.get("rom_inventory_fingerprint") or "").strip()
    if rom_fingerprint:
        try:
            db.ensure_rom_metadata_sync_action_for_fingerprint(device_id, rom_fingerprint)
        except Exception:
            logger.exception("Heartbeat ROM fingerprint comparison failed device_id=%s", device_id)
    try:
        actions = db.claim_pending_device_actions(device_id)
    except Exception:
        logger.exception("Heartbeat action claim failed device_id=%s", device_id)
        actions = []
    try:
        updated = db.get_device(device["id"]) or device
    except Exception:
        logger.exception("Heartbeat device reload failed device_id=%s", device_id)
        updated = device
    try:
        swarm = db.get_swarm_for_device(device_id, offline_seconds=SWARM_OFFLINE_THRESHOLD_SECONDS)
    except Exception:
        logger.exception("Heartbeat swarm response failed device_id=%s", device_id)
        swarm = []
    return {
        "actions": actions,
        "swarm": swarm,
        "log_stream_requested": _drone_log_stream_active(device_id),
    }


@app.post("/api/devices/{device_id}/rom-metadata")
async def upload_drone_rom_metadata(device_id: str, payload: DroneAssetMetadataUpload, authorization: Optional[str] = Header(default=None)):
    """Receive full asset metadata snapshots from a Drone outside heartbeat."""
    device = get_current_drone(device_id, authorization)
    db.update_device_last_seen(device["id"])
    metadata = payload.model_dump(exclude_none=True)
    payload_device_id = str(metadata.get("device_id") or device_id)
    if payload_device_id != device_id:
        print(f"Asset metadata upload rejected for {device_id}: payload_device_id={payload_device_id}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload device_id mismatch")
    roms = metadata.get("roms") if isinstance(metadata.get("roms"), list) else []
    bios = metadata.get("bios") if isinstance(metadata.get("bios"), list) else []
    artwork = metadata.get("artwork") if isinstance(metadata.get("artwork"), list) else []
    db.store_rom_metadata(device_id, metadata)
    db.update_device_last_seen(device["id"])
    print(f"Asset metadata upload accepted for {device_id}: rom_count={len(roms)} bios_count={len(bios)} artwork_count={len(artwork)}")
    return {"rom_count": len(roms), "bios_count": len(bios), "artwork_count": len(artwork)}


@app.post("/api/drones/rom-metadata")
async def upload_drone_rom_metadata_by_payload(payload: DroneAssetMetadataUpload, authorization: Optional[str] = Header(default=None)):
    device_id = str(payload.device_id or "").strip()
    if not device_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_id is required")
    return await upload_drone_rom_metadata(device_id, payload, authorization)


@app.post("/api/devices/{device_id}/events")
async def add_drone_event(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Persist Drone telemetry events using the existing Drone bearer token."""
    get_current_drone(device_id, authorization)
    event = db.add_device_event(device_id, payload)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/peer-checks")
async def add_peer_checks(device_id: str, payload: DronePeerChecksUpload, authorization: Optional[str] = Header(default=None)):
    """Persist peer-to-peer health results reported by a Drone."""
    get_current_drone(device_id, authorization)
    results = payload.model_dump(exclude_none=True).get("results") or []
    stored = db.add_peer_checks(device_id, results)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/actions/{action_id}/complete")
async def complete_device_action(device_id: str, action_id: str, payload: DroneActionCompleteRequest, authorization: Optional[str] = Header(default=None)):
    """Mark a claimed device action completed or failed."""
    get_current_drone(device_id, authorization)
    action_payload = payload.model_dump(exclude_none=True)
    result_status = str(action_payload.get("status") or "").strip().lower()
    if result_status not in {"completed", "failed"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status must be completed or failed")
    result = action_payload.get("result") if isinstance(action_payload.get("result"), dict) else None
    action = db.complete_device_action(device_id, action_id, result_status, action_payload.get("message"), result)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/speed")
async def add_speed_sample(device_id: str, payload: DroneSpeedSampleUpload, authorization: Optional[str] = Header(default=None)):
    """Store a Drone upload/download speed sample."""
    get_current_drone(device_id, authorization)
    sample = db.add_speed_sample(device_id, payload.model_dump(exclude_none=True))
    if not sample:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    print(f"Speed sample accepted for {device_id}: up={sample.get('upload_mbps')} down={sample.get('download_mbps')}")
    return {"status": "accepted"}


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
    return profile_response(user)


@app.patch("/api/profile")
async def update_profile(payload: dict, authorization: Optional[str] = Header(default=None)):
    """Update profile and user settings."""
    user = get_current_user(authorization)
    user_id = user["id"]

    if "username" in payload:
        username = str(payload.get("username") or "").strip()
        if not username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")
        if db.username_exists(username, exclude_user_id=user_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")
        payload["username"] = username

    if "username" in payload or "full_name" in payload or "avatar_data_url" in payload:
        db.update_user_profile(
            user_id,
            username=payload.get("username") if "username" in payload else None,
            full_name=payload.get("full_name") if "full_name" in payload else None,
            avatar_data_url=payload.get("avatar_data_url") if "avatar_data_url" in payload else None,
        )

    if "fleet_settings" in payload and isinstance(payload["fleet_settings"], dict):
        db.update_user_fleet_settings(user_id, payload["fleet_settings"])

    if "notification_settings" in payload and isinstance(payload["notification_settings"], dict):
        db.update_user_notification_settings(user_id, payload["notification_settings"])

    return profile_response(db.get_user(user_id))


@app.get("/api/hive")
async def get_hive(authorization: Optional[str] = Header(default=None)):
    """Return a privacy-safe public swarm directory."""
    user = get_current_user(authorization)
    print(f"Hive page/list requested: user_id={user['id']}")
    return hive_response(user, data_store=db)


# ==================== ROM Management ====================

@app.post("/api/devices/{device_id}/roms")
async def update_device_roms(
    device_id: str,
    rom_data: RomListUpdate,
    authorization: Optional[str] = Header(default=None),
):
    """Update ROM list for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    require_device_admin(user, device)
    
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
    page: Optional[int] = None,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    """Get ROMs for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    
    if page is not None:
        result = db.get_device_roms_page(device_id, system_name=system_name, page=page, per_page=per_page)
        return {"roms": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}

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
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    result = db.get_master_assets_page_for_device(
        device["user_id"],
        device_id,
        "rom",
        query=q,
        system_name=system,
        status=status,
        page=page,
        per_page=per_page,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "roms": result["rows"],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }


@app.get("/api/master-roms")
async def get_swarm_master_roms(
    q: Optional[str] = None,
    system: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    """Return a swarm-wide approved-Drone ROM master list deduplicated by md5 when available."""
    user = get_current_user(authorization)
    result = db.get_swarm_master_roms_page(user["id"], query=q, system_name=system, page=page, per_page=per_page)
    return {"roms": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


@app.get("/api/devices/{device_id}/bios")
async def get_device_bios(device_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"bios": db.get_device_bios(device_id)}


@app.get("/api/devices/{device_id}/master-bios")
async def get_device_master_bios(
    device_id: str,
    q: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    result = db.get_master_assets_page_for_device(
        device["user_id"],
        device_id,
        "bios",
        query=q,
        status=status,
        page=page,
        per_page=per_page,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"bios": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


@app.get("/api/master-bios")
async def get_swarm_master_bios(
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    user = get_current_user(authorization)
    result = db.get_swarm_master_bios_page(user["id"], query=q, page=page, per_page=per_page)
    return {"bios": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


@app.get("/api/devices/{device_id}/master-artwork")
async def get_device_master_artwork(
    device_id: str,
    q: Optional[str] = None,
    system: Optional[str] = None,
    artwork_type: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    result = db.get_master_assets_page_for_device(
        device["user_id"],
        device_id,
        "artwork",
        query=q,
        system_name=system,
        artwork_type=artwork_type,
        status=status,
        page=page,
        per_page=per_page,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"artwork": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


@app.post("/api/devices/{device_id}/sync-rom")
async def sync_device_rom(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    system_name = str(payload.get("system_name") or payload.get("system") or "").strip()
    rom_path = str(payload.get("file_path") or payload.get("rom_name") or "").strip()
    if not system_name or not rom_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_name and rom path are required")
    source_devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    if not source_devices:
        requested_md5 = str(payload.get("rom_md5") or payload.get("md5") or "").strip().lower()
        requested_path = rom_path.replace("\\", "/").strip().lstrip("./").lower()
        for row in db.get_master_roms_for_device(device["user_id"], device_id) or []:
            row_system = str(row.get("system_name") or "").strip().lower()
            row_path = str(row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
            row_md5 = str(row.get("rom_md5") or "").strip().lower()
            if row_system != system_name.lower():
                continue
            if requested_md5 and row_md5 == requested_md5:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                break
            if not requested_md5 and row_path == requested_path:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                break
    source_devices = resolvable_asset_sources(source_devices, device_id)
    if not source_devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No resolvable source Drone has this ROM")
    sync_id = str(uuid.uuid4())
    action = db.create_device_action(device["user_id"], device_id, "sync_rom", {
        "sync_id": sync_id,
        "system_name": system_name,
        "rom_name": payload.get("rom_name") or rom_path,
        "file_path": rom_path,
        "rom_md5": payload.get("rom_md5"),
        "file_size": payload.get("file_size"),
        "entry_type": payload.get("entry_type") or "file",
        "devices": source_devices,
    })
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    db.add_rom_sync_activity(device_id, {
        "sync_id": sync_id,
        "target_drone_id": device_id,
        "system": system_name,
        "rom_name": rom_path,
        "action": "download",
        "status": "pending",
        "file_size": payload.get("file_size"),
        "rom_md5": payload.get("rom_md5"),
        "entry_type": payload.get("entry_type") or "file",
    })
    notify_sync_triggered(user, device, "ROM", f"ROM sync for {system_name}/{rom_path}", [device], source_devices, action)
    return {"action": action, "artwork_actions": [], "artwork_action_count": 0}


@app.post("/api/devices/{device_id}/sync-bios")
async def sync_device_bios(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    bios_path = str(payload.get("file_path") or payload.get("relative_path") or payload.get("bios_name") or payload.get("name") or "").strip()
    if not bios_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="BIOS path is required")
    source_devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    if not source_devices:
        requested_md5 = str(payload.get("bios_md5") or payload.get("md5") or "").strip().lower()
        requested_path = bios_path.replace("\\", "/").strip().lstrip("./").lower()
        for row in db.get_master_bios_for_device(device["user_id"], device_id) or []:
            row_path = str(row.get("file_path") or row.get("bios_name") or "").replace("\\", "/").strip().lstrip("./").lower()
            row_md5 = str(row.get("bios_md5") or row.get("md5") or "").strip().lower()
            if requested_md5 and row_md5 == requested_md5:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                break
            if not requested_md5 and row_path == requested_path:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                break
    source_devices = resolvable_asset_sources(source_devices, device_id)
    if not source_devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No resolvable source Drone has this BIOS")
    action = db.create_device_action(device["user_id"], device_id, "sync_bios", {
        "bios_name": payload.get("bios_name") or bios_path,
        "file_path": bios_path,
        "relative_path": bios_path,
        "bios_md5": payload.get("bios_md5") or payload.get("md5"),
        "file_size": payload.get("file_size"),
        "devices": source_devices,
    })
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    db.add_rom_sync_activity(device_id, {
        "sync_id": action["id"],
        "asset_type": "bios",
        "target_drone_id": device_id,
        "system": "bios",
        "bios_name": bios_path,
        "relative_path": bios_path,
        "action": "download",
        "status": "pending",
        "file_size": payload.get("file_size"),
        "bios_md5": payload.get("bios_md5") or payload.get("md5"),
    })
    notify_sync_triggered(user, device, "BIOS", f"BIOS sync for {bios_path}", [device], source_devices, action)
    return {"action": action}


@app.post("/api/devices/{device_id}/sync-artwork")
async def sync_device_artwork(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    require_device_admin(user, device)
    system_name = str(payload.get("system_name") or payload.get("system") or "").strip()
    rom_path = str(payload.get("rom_path") or payload.get("file_path") or payload.get("rom_name") or "").strip()
    artwork_type = str(payload.get("artwork_type") or "").strip()
    if not system_name or not rom_path or not artwork_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_name, rom_path, and artwork_type are required")
    source_devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    if not source_devices:
        requested_path = rom_path.replace("\\", "/").strip().lstrip("./").lower()
        requested_type = artwork_type.strip().lower()
        for row in db.get_master_artwork_for_device(device["user_id"], device_id) or []:
            row_system = str(row.get("system_name") or "").strip().lower()
            row_path = str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").replace("\\", "/").strip().lstrip("./").lower()
            row_type = str(row.get("artwork_type") or "").strip().lower()
            if row_system == system_name.lower() and row_path == requested_path and row_type == requested_type:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                break
    source_devices = resolvable_asset_sources(source_devices, device_id)
    if not source_devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No resolvable source Drone has this artwork")
    action = db.create_device_action(device["user_id"], device_id, "sync_artwork", {
        "asset_type": "artwork",
        "system_name": system_name,
        "system": system_name,
        "rom_name": payload.get("rom_name") or rom_path,
        "rom_path": rom_path,
        "file_path": rom_path,
        "artwork_type": artwork_type,
        "devices": source_devices,
    })
    if not action:
        raise HTTPException(status_code=404, detail="Device not found")
    db.add_rom_sync_activity(device_id, {
        "sync_id": action["id"],
        "asset_type": "artwork",
        "target_drone_id": device_id,
        "system": system_name,
        "rom_name": payload.get("rom_name") or rom_path,
        "rom_path": rom_path,
        "relative_path": rom_path,
        "artwork_type": artwork_type,
        "action": "download",
        "status": "pending",
    })
    notify_sync_triggered(user, device, "Artwork", f"{artwork_type} artwork sync for {system_name}/{rom_path}", [device], source_devices, action)
    return {"action": action}


@app.post("/api/devices/{device_id}/sync-artwork-bulk")
async def sync_device_artwork_bulk(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    require_device_admin(user, device)

    raw_systems = payload.get("systems") if isinstance(payload.get("systems"), list) else []
    systems = {
        str(item or "").strip().lower()
        for item in raw_systems
        if str(item or "").strip() and str(item or "").strip().lower() != "all"
    }
    raw_devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    source_device_ids = {
        str(item.get("device_id") if isinstance(item, dict) else item or "").strip()
        for item in raw_devices
        if str(item.get("device_id") if isinstance(item, dict) else item or "").strip()
        and str(item.get("device_id") if isinstance(item, dict) else item or "").strip().lower() != "any"
    }
    artwork_type = str(payload.get("artwork_type") or "").strip().lower()

    rows = db.get_master_artwork_for_device(device["user_id"], device_id) or []
    actions = []
    queued_assets = 0
    skipped_assets = 0
    for row in rows:
        system_name = str(row.get("system_name") or row.get("system") or "").strip()
        rom_path = str(row.get("rom_path") or row.get("file_path") or row.get("rom_name") or "").strip()
        row_type = str(row.get("artwork_type") or "").strip()
        if not system_name or not rom_path or not row_type:
            skipped_assets += 1
            continue
        if systems and system_name.lower() not in systems:
            continue
        if artwork_type and row_type.lower() != artwork_type:
            continue
        if row.get("present_on_selected"):
            continue

        available_sources = row.get("devices") if isinstance(row.get("devices"), list) else []
        source_devices = resolvable_asset_sources(available_sources, device_id)
        if source_device_ids:
            source_devices = [
                source for source in source_devices
                if source.get("device_id") in source_device_ids
            ]
        if not source_devices:
            skipped_assets += 1
            continue

        action = db.create_device_action(device["user_id"], device_id, "sync_artwork", {
            "asset_type": "artwork",
            "system_name": system_name,
            "system": system_name,
            "rom_name": row.get("rom_name") or rom_path,
            "rom_path": rom_path,
            "file_path": rom_path,
            "artwork_type": row_type,
            "devices": source_devices,
        })
        if not action:
            skipped_assets += 1
            continue
        actions.append(action)
        queued_assets += 1
        db.add_rom_sync_activity(device_id, {
            "sync_id": action["id"],
            "asset_type": "artwork",
            "target_drone_id": device_id,
            "system": system_name,
            "rom_name": row.get("rom_name") or rom_path,
            "rom_path": rom_path,
            "relative_path": rom_path,
            "artwork_type": row_type,
            "action": "download",
            "status": "pending",
        })

    if actions:
        sources = []
        for action in actions:
            payload_devices = ((action.get("payload") or {}).get("devices") if isinstance(action.get("payload"), dict) else []) or []
            sources.extend(payload_devices)
        notify_sync_triggered(user, device, "Artwork", f"bulk artwork sync ({queued_assets} item(s))", [device], sources, actions[0])

    return {
        "status": "queued",
        "systems": sorted(systems) if systems else ["all"],
        "source_device_ids": sorted(source_device_ids) if source_device_ids else ["any"],
        "action_count": len(actions),
        "queued_artwork_count": queued_assets,
        "skipped_artwork_count": skipped_assets,
        "actions": actions,
    }


@app.post("/api/devices/{device_id}/sync-system")
async def sync_device_system(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    system_name = str(payload.get("system_name") or payload.get("system") or "").strip()
    if not system_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_name is required")
    master_rows = db.get_master_roms_for_device(device["user_id"], device_id) or []
    missing = [
        {**row, "devices": resolvable_asset_sources(row.get("devices") or [], device_id)}
        for row in master_rows
        if str(row.get("system_name") or "").lower() == system_name.lower() and not row.get("present_on_selected")
    ]
    missing = [row for row in missing if row["devices"]]
    if not missing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No resolvable source Drone has missing ROMs for this system")
    for row in missing:
        row["sync_id"] = str(uuid.uuid4())
    action = db.create_device_action(device["user_id"], device_id, "sync_system", {"system_name": system_name, "roms": missing})
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    for index, row in enumerate(missing, start=1):
        source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
        db.add_rom_sync_activity(device_id, {
            "sync_id": row["sync_id"],
            "source_action_id": action["id"],
            "source_drone_id": source_devices[0].get("device_id") if source_devices else None,
            "target_drone_id": device_id,
            "system": system_name,
            "rom_name": row.get("rom_name") or row.get("file_path"),
            "relative_path": row.get("file_path"),
            "entry_type": row.get("entry_type") or "file",
            "action": "download",
            "status": "pending",
            "file_size": row.get("file_size"),
            "rom_md5": row.get("rom_md5"),
        })
    notify_sync_triggered(user, device, "System", f"{system_name} system sync ({len(missing)} ROM item(s))", [device], [source for row in missing for source in (row.get("devices") or [])], action)
    return {"action": action, "artwork_actions": [], "artwork_action_count": 0}


@app.post("/api/bulk-sync")
async def bulk_sync_drones(payload: dict, authorization: Optional[str] = Header(default=None)):
    """Queue sync actions so selected Drones converge for the selected systems."""
    user = get_current_user(authorization)
    raw_device_ids = payload.get("device_ids") if isinstance(payload.get("device_ids"), list) else []
    raw_systems = payload.get("systems") if isinstance(payload.get("systems"), list) else []
    device_ids = []
    for item in raw_device_ids:
        device_id = str(item or "").strip()
        if device_id and device_id not in device_ids:
            device_ids.append(device_id)
    systems = sorted({str(item or "").strip() for item in raw_systems if str(item or "").strip()})
    if len(device_ids) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least two Drones")
    if not systems:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select at least one system")

    devices = {}
    for device_id in device_ids:
        device = db.user_can_access_device(user["id"], device_id)
        if not device:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Device not found: {device_id}")
        require_device_admin(user, device)
        devices[device_id] = device

    system_set = {system.lower() for system in systems}
    selected_roms_by_device = {
        device_id: [
            rom for rom in db.get_device_roms(device_id)
            if str(rom.get("system_name") or "").strip().lower() in system_set
        ]
        for device_id, device in devices.items()
    }
    union: dict[tuple, dict] = {}
    for source_id, roms in selected_roms_by_device.items():
        for rom in roms:
            key = db._rom_key(rom)
            if not key[0] or not key[1]:
                continue
            row = union.setdefault(key, {
                "system_name": rom.get("system_name"),
                "rom_name": rom.get("rom_name") or rom.get("file_path"),
                "file_path": rom.get("file_path") or rom.get("rom_name"),
                "rom_md5": rom.get("rom_md5"),
                "file_size": rom.get("file_size"),
                "entry_type": rom.get("entry_type") or "file",
                "devices": [],
            })
            info = devices[source_id].get("system_info") or {}
            if not any(item.get("device_id") == source_id for item in row["devices"]):
                row["devices"].append({
                    "device_id": source_id,
                    "device_name": devices[source_id].get("device_name") or info.get("hostname") or source_id,
                })
            if not row.get("rom_md5") and rom.get("rom_md5"):
                row["rom_md5"] = rom.get("rom_md5")
            if not row.get("file_size") and rom.get("file_size"):
                row["file_size"] = rom.get("file_size")

    actions = []
    queued_roms = 0
    for target_id, target_roms in selected_roms_by_device.items():
        target_keys = {db._rom_key(rom) for rom in target_roms}
        missing_by_system: dict[str, list] = {}
        for key, row in union.items():
            if key in target_keys:
                continue
            source_devices = resolvable_asset_sources(row.get("devices", []), target_id)
            if not source_devices:
                continue
            system_name = str(row.get("system_name") or "").strip()
            if not system_name:
                continue
            missing_by_system.setdefault(system_name, []).append({**row, "devices": source_devices})
        for system_name, missing in sorted(missing_by_system.items()):
            for row in missing:
                row["sync_id"] = str(uuid.uuid4())
            action = db.create_device_action(
                devices[target_id]["user_id"],
                target_id,
                "sync_system",
                {"system_name": system_name, "roms": missing},
            )
            if action:
                actions.append(action)
                queued_roms += len(missing)
                for index, row in enumerate(missing, start=1):
                    source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                    db.add_rom_sync_activity(target_id, {
                        "sync_id": row["sync_id"],
                        "source_action_id": action["id"],
                        "source_drone_id": source_devices[0].get("device_id") if source_devices else None,
                        "target_drone_id": target_id,
                        "system": system_name,
                        "rom_name": row.get("rom_name") or row.get("file_path"),
                        "relative_path": row.get("file_path"),
                        "entry_type": row.get("entry_type") or "file",
                        "action": "download",
                        "status": "pending",
                        "file_size": row.get("file_size"),
                        "rom_md5": row.get("rom_md5"),
                    })
                notify_sync_triggered(
                    user,
                    devices[target_id],
                    "Bulk system",
                    f"{system_name} convergence sync ({len(missing)} ROM item(s))",
                    [devices[target_id]],
                    [source for row in missing for source in (row.get("devices") or [])],
                    action,
                )

    return {
        "status": "queued",
        "device_count": len(device_ids),
        "systems": systems,
        "action_count": len(actions),
        "queued_rom_count": queued_roms,
        "artwork_action_count": 0,
        "actions": actions,
        "artwork_actions": [],
    }


@app.post("/api/devices/{device_id}/sync-activity")
async def add_device_sync_activity(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    get_current_drone(device_id, authorization)
    entry = db.add_rom_sync_activity(device_id, payload)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "accepted"}


@app.get("/api/devices/{device_id}/sync-activity")
async def get_device_sync_activity(device_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    rows = db.get_rom_sync_activity(device["user_id"], device_id)
    if rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"activity": rows}


@app.get("/api/sync-activity")
async def search_sync_activity(
    q: Optional[str] = None,
    status_filter: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    user = get_current_user(authorization)
    return {"activity": db.search_rom_sync_activity(user["id"], query=q, status=status_filter)}


@app.get("/api/devices/{device_id}/systems")
async def get_device_systems(
    device_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """Get systems for a selected device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
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
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    
    require_device_admin(user, device)
    gamelog_id = db.log_gameplay(
        device_id,
        gameplay_data.system_name,
        gameplay_data.game_name,
        gameplay_data.duration_seconds,
        rom_path=gameplay_data.rom_path,
        rom_md5=gameplay_data.rom_md5,
        played_at=gameplay_data.played_at,
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


@app.post("/api/devices/{device_id}/game-logs")
async def upload_device_game_logs(device_id: str, payload: DroneGameLogsUpload, authorization: Optional[str] = Header(default=None)):
    """Accept newly detected game launches from a Drone."""
    device = get_current_drone(device_id, authorization)
    result = payload.model_dump(exclude_none=True)
    result["type"] = "game_logs"
    db.store_action_result(device, result)
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/log-sources")
async def upload_device_log_sources(device_id: str, payload: DroneLogSourcesUpload, authorization: Optional[str] = Header(default=None)):
    """Accept Drone log source content and persist it for selected Drone log views."""
    device = get_current_drone(device_id, authorization)
    db.update_device_last_seen(device["id"])
    result = payload.model_dump(exclude_none=True)
    result["type"] = "log_sources"
    db.store_action_result(device, result)
    _store_drone_log_stream(device_id, result)
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/log-stream/view")
async def request_device_log_stream(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Mark a Drone logs view as active so the next heartbeat requests live log streaming."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _request_drone_log_stream(device_id)
    return {"status": "stream_requested", "ttl_seconds": DRONE_LOG_STREAM_TTL_SECONDS}


@app.post("/api/devices/{device_id}/emulator-configs")
async def upload_device_emulator_configs(device_id: str, payload: DroneEmulatorConfigsUpload, authorization: Optional[str] = Header(default=None)):
    """Accept changed emulator configs from a Drone."""
    device = get_current_drone(device_id, authorization)
    db.update_device_last_seen(device["id"])
    result = payload.model_dump(exclude_none=True)
    result["type"] = "emulator_configs"
    db.store_action_result(device, result)
    return {"status": "accepted"}


@app.get("/api/devices/{device_id}/gamelogs")
async def get_device_gamelogs(
    device_id: str,
    system_name: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """Get game play logs for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
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
    return (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8").replace(
        "__OVERMIND_VERSION_BADGE__",
        get_version_badge_html(),
    )


# ==================== Health Check ====================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": APP_VERSION}


@app.get("/{ui_path:path}", response_class=HTMLResponse)
async def serve_ui_route(ui_path: str):
    """Serve the web UI for direct browser navigation to client-side routes."""
    if ui_path.startswith(("api/", "static/", "content/")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return get_ui_html()


# ==================== Startup ====================

@app.on_event("startup")
async def startup_event():
    """Initialize runtime services."""
    initialize_runtime()


if __name__ == "__main__":
    run_https_app()
