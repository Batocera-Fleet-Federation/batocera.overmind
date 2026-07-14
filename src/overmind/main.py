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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    DroneActionCompleteRequest, DroneAssetMetadataUpload, DroneDownloadsReport,
    DroneGameLogsUpload, DroneHeartbeatRequest, DronePeerChecksUpload, DroneSpeedSampleUpload,
    # Phase 1 request models
    NotificationIdsRequest, InvitationAcceptRequest, SwarmMemberUpdateRequest, SwarmRenameRequest,
    ProfileUpdateRequest,
    # Phase 1 response models
    StatusResponse, MessageResponse, LoginResponse, AuthProvidersResponse,
    SwarmListResponse, SwarmEnvelope, SwarmAccessResponse,
    InvitationEnvelope, ResendInvitationResponse, RemoveInvitationResponse,
    InvitationStatusResponse, AcceptInvitationResponse, SwarmMemberUpdateResponse,
    MarkNotificationsResponse, DismissNotificationsResponse, NotificationListResponse,
    ProfileResponse, HiveResponse,
    # Phase 2-4 request models
    AdminAssignRequest, DroneClaimRequest, IntegrationTokenCreateRequest, SignCsrRequest,
    AutoSyncUpdateRequest, DeviceActionRequest, SyncRomRequest, SyncBiosRequest,
    SyncSystemRequest, BulkSyncRequest,
    # Phase 2-4 response models
    DeviceModel, GenericObjectResponse, HealthResponse,
    AdminOverviewResponse, AdminSyncActionsResponse, AdminAuditLogResponse,
    AdminLandingVisitsResponse, MetricsResponse, RuntimeLogsResponse, AdminAssignResponse,
    DevicesListResponse, DeviceTokenResponse, PeerCertificateResponse,
    DeviceRegisterResponse, AcceptDroneConnectionResponse,
    IntegrationTokensResponse, IntegrationTokenEnvelope, DroneConnectionsResponse,
    AutoSyncPolicyResponse, ActionsResponse, DeleteActionsResponse, DownloadsResponse,
    ActionEnvelope, ActionQueuedResponse, ClaimActionsResponse, SyncRomResponse,
    BulkSyncResponse, HeartbeatResponse, AssetMetadataAck,
    SpeedUploadResponse, SpeedSamplesResponse,
    DeviceRomsResponse, MasterRomsResponse, BiosListResponse, MasterBiosResponse,
    RomUpdateResponse, SyncActivityResponse,
    SystemsResponse, GameplayLogResponse, GamelogsResponse,
    TransferCreateRequest, TransferResponse,
)
from overmind.db import db, ADMIN_ALERT_SWARM_ID
from overmind import auth
from overmind import cache
from overmind import emailer
from overmind import networking
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
from overmind.transfer_tokens import mint_transfer_token
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
    "set_screen_mode",
    "set_volume",
    "set_music_volume",
    "get_es_collections_state",
    "set_es_collections",
    "set_idle_volume_automation",
    "set_idle_game_exit_automation",
    "set_wifi_recovery_automation",
    "collect_rom_metadata",
    "rebuild_asset_metadata",
    "purge_asset_cache",
    "collect_game_logs",
    "collect_emulator_configs",
    "collect_log_sources",
    "refresh_emulator_list",
    "run_pixn_update",
    "run_pixen_update",
    "sync_rom",
    "sync_system",
    "sync_bios",
    "sync_artwork",
    "cancel_download",
    "pause_download",
    "resume_download",
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
# Safety net: an action a Drone claimed but never reported completion for (e.g. the
# Drone died mid-action, or its completion POST failed) is marked failed after this
# many seconds so it stops showing as a perpetual "in progress" in the actions UI.
DEVICE_ACTION_TIMEOUT_SECONDS = max(60, int(os.getenv("DEVICE_ACTION_TIMEOUT_SECONDS", "600")))
# Public-reachability probe: bounded so a single 60s run can never back up.
# Per-probe TCP timeout, worker fan-out, and a hard wall-clock budget that keeps
# every run well under the EventBridge 60s cadence (which has retries disabled).
PUBLIC_REACHABILITY_PROBE_TIMEOUT_SECONDS = max(0.1, float(os.getenv("PUBLIC_REACHABILITY_PROBE_TIMEOUT_SECONDS", "3")))
PUBLIC_REACHABILITY_MAX_WORKERS = max(1, int(os.getenv("PUBLIC_REACHABILITY_MAX_WORKERS", "20")))
PUBLIC_REACHABILITY_RUN_BUDGET_SECONDS = max(1.0, float(os.getenv("PUBLIC_REACHABILITY_RUN_BUDGET_SECONDS", "45")))
# 0 = probe every approved Drone each run; a positive value caps work per run and
# relies on oldest-checked-first ordering to round-robin a very large fleet.
PUBLIC_REACHABILITY_MAX_DEVICES_PER_RUN = max(0, int(os.getenv("PUBLIC_REACHABILITY_MAX_DEVICES_PER_RUN", "0")))
def _resolve_public_reachability_enabled(
    *, edge_enabled: Optional[str], override: Optional[str]
) -> bool:
    """Whether to run the inbound public-reachability probe.

    The probe is the cross-network fallback for *direct* WAN transfers when there
    is no Edge. Default it conditional on the Edge so a deployment can't silently
    lose cross-network sync: ON without an Edge, OFF with one. An explicit
    OVERMIND_PUBLIC_REACHABILITY_ENABLED always wins.
    """
    truthy = {"1", "true", "yes", "on"}
    override_value = (override or "").strip().lower()
    if override_value in truthy:
        return True
    if override_value in {"0", "false", "no", "off"}:
        return False
    return (edge_enabled or "").strip().lower() not in truthy


# OVERMIND_EDGE_ENABLED is the control plane's awareness that an Edge is deployed
# (set by Terraform when enable_edge=true); it flips the probe's default.
PUBLIC_REACHABILITY_ENABLED = _resolve_public_reachability_enabled(
    edge_enabled=os.getenv("OVERMIND_EDGE_ENABLED"),
    override=os.getenv("OVERMIND_PUBLIC_REACHABILITY_ENABLED"),
)
TOKEN_HASH_SECRET = os.getenv("TOKEN_HASH_SECRET", auth.SECRET_KEY)
# Lifetime of a relayed-transfer authorization token (and its session offer).
TRANSFER_TOKEN_TTL_SECONDS = max(30, int(os.getenv("TRANSFER_TOKEN_TTL_SECONDS", "300")))
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


_LOG_TAIL_REENTRY = threading.local()


class CapturedLoggingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if _STREAM_LOG_CAPTURE is None:
            return
        try:
            message = self.format(record)
            # Match conventional stream semantics in the admin UI: routine
            # informational logs go to stdout, while warnings/errors stay on
            # stderr. Per-query PostgreSQL timing noise still reaches the real
            # stream but is excluded from the bounded tail shown in the UI.
            is_error = record.levelno >= logging.WARNING
            captured = not _is_db_query_log_record(record)
            target = _STREAM_LOG_CAPTURE.stderr if is_error else _STREAM_LOG_CAPTURE.stdout
            target.write(message + "\n", capture=captured)
            # Mirror warnings/errors to a shared buffer so the admin UI shows a
            # consistent error history regardless of which Lambda instance logged
            # it. Guarded against reentrancy (the Redis client may log on failure).
            if is_error and captured and not getattr(_LOG_TAIL_REENTRY, "active", False):
                _LOG_TAIL_REENTRY.active = True
                try:
                    cache.append_log_tail("stderr", [message])
                finally:
                    _LOG_TAIL_REENTRY.active = False
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
    # stdout. Drop pre-existing handlers so application logs flow only through
    # CapturedLoggingHandler, which preserves stdout/stderr semantics while
    # forwarding to the real streams for CloudWatch.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = CapturedLoggingHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)


def stream_log_snapshot() -> dict:
    snapshot = _STREAM_LOG_CAPTURE.snapshot() if _STREAM_LOG_CAPTURE else {
        "stdout": "",
        "stderr": "",
        "max_lines": OVERMIND_LOG_CAPTURE_LINES,
        "captured_at": datetime.utcnow().isoformat() + "Z",
        "capture_active": False,
    }
    # Prefer the shared cross-instance error tail so the admin stderr view stays
    # stable instead of flickering as different Lambda instances answer each poll.
    shared_stderr = cache.read_log_tail("stderr", OVERMIND_LOG_CAPTURE_LINES)
    if shared_stderr is not None:
        snapshot["stderr"] = "\n".join(shared_stderr)
    return snapshot


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
_PUBLIC_REACHABILITY_THREAD = None
_DEVICE_STATUS_THREAD = None
_RUNTIME_INITIALIZED = False

# Local/container cadence for the maintenance jobs that AWS drives via EventBridge.
# Lambda never starts these in-process pollers (start_pollers is False there), so AWS
# behavior is unchanged; this only makes drone reachability/status work in local runs.
PUBLIC_REACHABILITY_POLL_INTERVAL_SECONDS = int(os.getenv("PUBLIC_REACHABILITY_POLL_INTERVAL_SECONDS", "60"))
DEVICE_STATUS_POLL_INTERVAL_SECONDS = int(os.getenv("DEVICE_STATUS_POLL_INTERVAL_SECONDS", "60"))


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
    # Fail any action a Drone claimed but never reported completion for, so it stops
    # hanging as "in progress" forever.
    try:
        expired = db.expire_stale_device_actions(DEVICE_ACTION_TIMEOUT_SECONDS)
        if expired:
            print(f"Expired {expired} stale device action(s) past {DEVICE_ACTION_TIMEOUT_SECONDS}s timeout")
    except Exception as error:
        logger.warning("Failed to expire stale device actions: %s", error)
    # Sweep transfer sessions (relay/P2P handoffs) that were authorized but never
    # ran to completion, so they stop showing as pending past their token expiry.
    try:
        expired_transfers = postgres_store.expire_transfer_sessions()
        if expired_transfers:
            print(f"Expired {expired_transfers} stale transfer session(s) past their token expiry")
    except Exception as error:
        logger.warning("Failed to expire stale transfer sessions: %s", error)


def _emit_reachability_notification(device: dict, resolvable: bool, answered_by: Optional[str] = None) -> None:
    """Record a swarm notification when a Drone flips Resolvable <-> Not Resolvable.

    Persisted directly to Postgres (delivery_pending) so the digest job emails users
    who enabled the ``drone_reachability`` notification type; the in-app notification
    shows immediately. Best-effort: a failure here must not break the probe loop.
    """
    swarm_id = str(device.get("swarm_id") or "")
    if not swarm_id:
        return
    label = str(device.get("device_name") or device.get("device_id") or "A Drone")
    status = "Resolvable" if resolvable else "Not Resolvable"
    event_type = "drone_resolvable" if resolvable else "drone_unresolvable"
    title = "Drone became resolvable" if resolvable else "Drone became unresolvable"
    message = f"{label} is now {status} from Overmind."
    if not resolvable and answered_by:
        message = (
            f"{label} is now Not Resolvable from Overmind: another Drone ({answered_by}) answers at its "
            "public IP and port. If both Drones share one public IP, forward a different external port to this one."
        )
    details = {
        "device": {"device_id": device.get("device_id"), "device_name": label},
        "status": status,
        "nature": status,
    }
    if answered_by:
        details["answered_by"] = answered_by
    try:
        postgres_store.insert_swarm_notification(swarm_id, event_type, title, message, details)
    except Exception as error:
        logger.warning("Failed to record reachability notification for %s: %s", label, error)


def poll_public_reachability_once() -> dict:
    """TCP-probe each approved Drone's public IP:port and persist status changes.

    Runs entirely off the lean ``postgres_store`` path (no full app-state refresh),
    fans out probes across a bounded thread pool with a short per-probe timeout, and
    stops issuing work once a hard wall-clock budget is hit so a single run can never
    exceed the 60s EventBridge cadence and back up. Only Drones whose Resolvable /
    Not Resolvable status actually changed are written back, keeping RDS writes minimal.

    Default is conditional on the Edge: OFF when the Edge is deployed
    (OVERMIND_EDGE_ENABLED, outbound-only model), ON when it is not (so
    cross-network Drones keep a direct WAN path). OVERMIND_PUBLIC_REACHABILITY_ENABLED
    overrides either way.
    """
    if not PUBLIC_REACHABILITY_ENABLED:
        return {"job": "public-reachability", "status": "disabled", "checked": 0, "changed": 0}
    limit = PUBLIC_REACHABILITY_MAX_DEVICES_PER_RUN
    devices = postgres_store.list_all_approved_devices(limit=limit, oldest_checked_first=True) or []
    if not devices:
        return {"job": "public-reachability", "status": "ok", "checked": 0, "changed": 0}

    deadline = time.monotonic() + PUBLIC_REACHABILITY_RUN_BUDGET_SECONDS
    checked = 0
    changed = 0

    def _probe(device: dict) -> tuple[dict, Optional[str], bool, Optional[str]]:
        network = device.get("network") if isinstance(device.get("network"), dict) else {}
        reachability = device.get("public_reachability") if isinstance(device.get("public_reachability"), dict) else {}
        public_ip = network.get("public_ip") or network.get("public") or reachability.get("public_ip")
        port = int(device.get("api_port") or 443)
        expected_id = str(device.get("device_id") or "").strip()
        # Identity check, not just a TCP connect: two Drones can share one public IP
        # (same NAT) with 443 forwarded to only one of them, so a bare connect would
        # mark BOTH reachable. We confirm the responder's /health drone_id is *this*
        # Drone; if another Drone answers (port-forward lands elsewhere) it is Not
        # Resolvable, and we record who actually answered for diagnostics.
        observed_id = (
            networking.probe_drone_identity(str(public_ip), port, PUBLIC_REACHABILITY_PROBE_TIMEOUT_SECONDS)
            if (public_ip and expected_id)
            else None
        )
        resolvable = bool(public_ip) and bool(expected_id) and observed_id is not None and observed_id == expected_id
        return device, (str(public_ip) if public_ip else None), resolvable, observed_id

    pool = ThreadPoolExecutor(max_workers=PUBLIC_REACHABILITY_MAX_WORKERS)
    try:
        futures = {pool.submit(_probe, device): device for device in devices}
        for future in as_completed(futures):
            if time.monotonic() >= deadline:
                logger.warning("Public reachability run budget reached; deferring remaining Drones to next run")
                break
            try:
                device, public_ip, resolvable, observed_id = future.result()
            except Exception as error:
                logger.warning("Public reachability probe failed: %s", error)
                continue
            checked += 1
            # A different Drone answering at this public IP:port (shared NAT /
            # port-forward) is the actionable failure mode -- log who answered.
            expected_id = str(device.get("device_id") or "").strip()
            identity_mismatch = bool(public_ip and observed_id and expected_id and observed_id != expected_id)
            if identity_mismatch:
                logger.info(
                    "Public reachability: %s not resolvable -- public IP %s:%s answered by a different Drone (%s)",
                    expected_id, public_ip, int(device.get("api_port") or 443), observed_id,
                )
            previous = bool((device.get("public_reachability") or {}).get("resolvable"))
            if resolvable == previous:
                continue
            result = {
                "resolvable": resolvable,
                "public_ip": public_ip,
                "api_port": int(device.get("api_port") or 443) if resolvable else None,
                "checked_at": datetime.utcnow(),
                "answered_by": observed_id if identity_mismatch else None,
                "identity_mismatch": identity_mismatch,
            }
            if postgres_store.update_device_reachability(str(device.get("id") or ""), result):
                changed += 1
                _emit_reachability_notification(device, resolvable, answered_by=result["answered_by"])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return {"job": "public-reachability", "status": "ok", "checked": checked, "changed": changed}


def run_scheduled_job(job_name: str) -> dict:
    """Run a single background job by name for EventBridge or local scripts."""
    job = str(job_name or "").strip().lower().replace("_", "-")
    if job in {"notification-delivery", "notifications"}:
        delivered = poll_notification_delivery_once()
        return {"job": job, "status": "ok", "delivered": delivered}
    if job in {"device-status", "offline-status", "status-notifications"}:
        poll_device_status_notifications_once()
        return {"job": job, "status": "ok"}
    if job in {"public-reachability", "reachability"}:
        return poll_public_reachability_once()
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


def start_public_reachability_poller() -> None:
    """Run the public-reachability TCP probe in-process (container/local runtime).

    On AWS this job is driven by an EventBridge schedule; the container runtime has no
    scheduler, so without this in-process loop Drones are never probed and always show as
    Not Resolvable locally. Lambda does not call this (start_pollers is False there).
    """
    global _PUBLIC_REACHABILITY_THREAD
    if not PUBLIC_REACHABILITY_ENABLED:
        return  # outbound-only default; no inbound probing
    interval_seconds = max(0, int(os.getenv("PUBLIC_REACHABILITY_POLL_INTERVAL_SECONDS", str(PUBLIC_REACHABILITY_POLL_INTERVAL_SECONDS))))
    if interval_seconds == 0 or (_PUBLIC_REACHABILITY_THREAD and _PUBLIC_REACHABILITY_THREAD.is_alive()):
        return

    def loop() -> None:
        while True:
            time.sleep(max(5, interval_seconds))
            try:
                poll_public_reachability_once()
            except Exception as error:
                logger.warning("Public reachability poll failed: %s", error)

    _PUBLIC_REACHABILITY_THREAD = threading.Thread(target=loop, name="public-reachability-poller", daemon=True)
    _PUBLIC_REACHABILITY_THREAD.start()


def start_device_status_poller() -> None:
    """Run offline/online status detection in-process (container/local runtime).

    Same rationale as the reachability poller: AWS uses EventBridge, the container does not.
    """
    global _DEVICE_STATUS_THREAD
    interval_seconds = max(0, int(os.getenv("DEVICE_STATUS_POLL_INTERVAL_SECONDS", str(DEVICE_STATUS_POLL_INTERVAL_SECONDS))))
    if interval_seconds == 0 or (_DEVICE_STATUS_THREAD and _DEVICE_STATUS_THREAD.is_alive()):
        return

    def loop() -> None:
        while True:
            time.sleep(max(5, interval_seconds))
            try:
                poll_device_status_notifications_once()
            except Exception as error:
                logger.warning("Device status poll failed: %s", error)

    _DEVICE_STATUS_THREAD = threading.Thread(target=loop, name="device-status-poller", daemon=True)
    _DEVICE_STATUS_THREAD.start()


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
        # AWS runs these via EventBridge; the container has no scheduler, so drive them
        # in-process or Drones never get reachability/status updates outside Lambda.
        start_public_reachability_poller()
        start_device_status_poller()

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


def _client_ip(request: Request) -> str:
    """Best-effort real client IP, honoring the proxy/API-Gateway forwarded header."""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else ""


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


def _super_admin_users() -> list[dict]:
    """All users treated as superadmins (currently the configured email; future-proofed)."""
    users: list[dict] = []
    seen: set[str] = set()
    candidate = db.get_user_by_email(SUPER_ADMIN_EMAIL)
    if candidate and candidate.get("id") and candidate["id"] not in seen:
        users.append(candidate)
        seen.add(candidate["id"])
    return users


def _super_admin_ids(exclude_user_id: Optional[str] = None) -> list[str]:
    return [str(u["id"]) for u in _super_admin_users() if u.get("id") and str(u["id"]) != str(exclude_user_id or "")]


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


def resolvable_asset_sources(sources: list, target_device_id: Optional[str] = None, require_resolvable: bool = True) -> list:
    """Return known sources for an asset (each an existing, accessible Drone).

    With require_resolvable (the default), further filters to sources with a
    passing peer-check right now -- used where an immediately-usable list is
    needed. Callers that queue a sync and let the Drone hold it 'pending' until
    a source becomes reachable (see actions.py's enqueue_pending_rom/bios) pass
    require_resolvable=False to keep every known-but-currently-unreachable
    source, so the Drone has candidates to keep retrying against.
    """
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
        if require_resolvable and not db.is_drone_peer_resolvable(source_id):
            continue
        eligible.append({
            "device_id": source_id,
            "device_name": source.get("device_name") or source_device.get("device_name") or source_id,
        })
    return eligible


# NOTE: ROM sync does not separately queue artwork. Overmind no longer stores an
# artwork inventory (gamelist-source-of-truth refactor) -- the receiving Drone pulls
# artwork itself from the source peer's gamelist right after the ROM lands.


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
    try:
        admin_ids = _super_admin_ids(exclude_user_id=user_id)
        if admin_ids:
            db.add_admin_notification(
                admin_ids,
                "admin_user_registered",
                "New user registered",
                f"{user.get('username') or user.get('email')} just created an Overmind account.",
                {"user": {"id": user_id, "email": user.get("email"), "username": user.get("username")}},
            )
        db.record_audit_event(
            "user_registered",
            f"New user registered: {user.get('email')}",
            actor=user,
            target_type="user",
            target_id=user_id,
            target_label=user.get("email"),
        )
    except Exception as error:
        logger.warning("Failed to record user-registration admin alert: %s", error)
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


@app.post("/api/auth/login", response_model=LoginResponse)
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


@app.post("/api/auth/refresh", response_model=LoginResponse)
async def refresh_auth_token(authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    return build_login_response(user)


@app.post("/api/auth/verify-email", response_model=StatusResponse)
async def verify_email_code(payload: EmailVerificationRequest):
    if not db.verify_email_code(str(payload.email), payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification code")
    return {"status": "verified"}


@app.post("/api/auth/resend-verification", response_model=StatusResponse)
async def resend_verification_email(payload: EmailVerificationResendRequest):
    user = db.get_user_by_email(str(payload.email))
    if user and not user.get("email_verified"):
        create_and_send_verification(user)
    return {"status": "sent"}


@app.get("/api/auth/verify-email", response_class=RedirectResponse)
async def verify_email_link(token: str):
    user = db.verify_email_token(token, verify_secret_token)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link")
    return RedirectResponse(f"{emailer.base_url()}/#verified=1")


@app.post("/api/auth/forgot-password", response_model=MessageResponse)
async def forgot_password(payload: ForgotPasswordRequest):
    user = db.get_user_by_email(str(payload.email))
    if user and user.get("auth_provider") in (None, "password") and user.get("password"):
        raw_token = secrets.token_urlsafe(32)
        db.create_password_reset(user["id"], hash_secret_token(raw_token), datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES))
        sent = send_password_reset_email(user, raw_token)
        logger.info("Password reset email send result user_id=%s sent=%s", user.get("id"), sent)
    return {"message": "If an eligible account exists, a password reset email has been sent."}


@app.post("/api/auth/reset-password", response_model=StatusResponse)
async def reset_password(payload: ResetPasswordRequest):
    if not db.consume_password_reset(payload.token, auth.hash_password(payload.password), verify_secret_token):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset token")
    return {"status": "password_updated"}


@app.get("/api/auth/providers", response_model=AuthProvidersResponse)
async def auth_providers():
    """Return social auth providers enabled by ENV VARs."""
    return {
        "providers": {
            provider: oauth_provider_enabled(provider)
            for provider in OAUTH_PROVIDERS.keys()
        }
    }


@app.post("/api/auth/{provider}", response_model=LoginResponse)
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


@app.get("/api/auth/{provider}/start", response_class=RedirectResponse)
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


@app.get("/api/auth/{provider}/callback", response_class=RedirectResponse)
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

@app.get("/api/swarms", response_model=SwarmListResponse)
async def list_swarms(authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    default_swarm_id = db.default_swarm_id(user["id"])
    return {"swarms": [{**swarm, "current": swarm.get("id") == default_swarm_id} for swarm in db.get_user_swarms(user["id"])]}


@app.get("/api/notifications", response_model=NotificationListResponse)
async def list_notifications(limit: int = 50, offset: int = 0, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    rows = db.get_user_notifications(user["id"], limit=limit, offset=offset)
    # Counts come from a dedicated COUNT query so the unread badge and page count stay
    # accurate without fetching every notification (and its detail fields) each load.
    counts = db.count_user_notifications(user["id"])
    return {
        "notifications": rows,
        "unread_count": int(counts.get("unread") or 0),
        "total_count": int(counts.get("total") or 0),
        "limit": limit,
        "offset": offset,
    }


@app.post("/api/notifications/read", response_model=MarkNotificationsResponse)
async def mark_notifications_read(payload: Optional[NotificationIdsRequest] = None, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    ids = payload.ids if payload else None
    count = db.mark_notifications_read(user["id"], ids)
    return {"status": "ok", "marked_read": count}


@app.post("/api/notifications/dismiss", response_model=DismissNotificationsResponse)
async def dismiss_notifications(payload: Optional[NotificationIdsRequest] = None, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    ids = payload.ids if payload else None
    count = db.dismiss_notifications(user["id"], ids)
    return {"status": "ok", "dismissed": count}


@app.post("/api/landing-visit", response_model=StatusResponse)
async def record_landing_visit(request: Request):
    """Public: log an anonymous landing-page visit by client IP (fire-and-forget)."""
    try:
        db.record_landing_visit(_client_ip(request), request.headers.get("user-agent"))
    except Exception as error:
        logger.warning("Failed to record landing visit: %s", error)
    return {"status": "ok"}


@app.get("/api/admin/overview", response_model=AdminOverviewResponse)
async def admin_overview(authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    db.refresh_admin_overview_state()
    db._dedupe_all_device_records()
    users = sorted((admin_user_row(user) for user in db.users.values()), key=lambda row: str(row.get("email") or "").lower())
    swarms = sorted(
        (admin_swarm_row(swarm) for swarm in db.swarms.values() if swarm.get("id") != ADMIN_ALERT_SWARM_ID),
        key=lambda row: str(row.get("name") or "").lower(),
    )
    drones = sorted((admin_drone_row(device) for device in db.devices.values()), key=lambda row: str(row.get("device_name") or row.get("device_id") or "").lower())
    pending_connections = sorted(
        (admin_pending_drone_connection_row(connection) for connection in db.get_all_pending_drone_connections()),
        key=lambda row: str(row.get("last_seen") or row.get("detected_at") or ""),
        reverse=True,
    )
    return {"users": users, "swarms": swarms, "drones": drones, "pending_connections": pending_connections}


@app.post("/api/admin/drone-connections/{device_id}/assign", response_model=AdminAssignResponse)
async def admin_assign_drone_connection(device_id: str, body: AdminAssignRequest, authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    payload = body.model_dump(exclude_none=True)
    swarm_id = str((payload or {}).get("swarm_id") or "").strip()
    if not swarm_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="swarm_id is required")
    device = db.admin_assign_pending_drone_connection(device_id, swarm_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending Drone connection or swarm not found")
    return {"status": "assigned", "device": device_response(device)}


@app.get("/api/admin/sync-actions", response_model=AdminSyncActionsResponse)
async def admin_sync_actions(
    q: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
):
    """List sync actions (any status) across all users and drones (super admin only)."""
    require_super_admin(authorization)
    db.refresh_admin_overview_state()
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    page = db.list_all_sync_actions(search=q, limit=limit, offset=offset)
    return {"sync_actions": page["actions"], "total": page["total"], "limit": limit, "offset": offset}


@app.get("/api/admin/sync-actions/summary", response_model=GenericObjectResponse)
async def admin_sync_actions_summary(authorization: Optional[str] = Header(default=None)):
    """Status counts across all sync actions (super admin only)."""
    require_super_admin(authorization)
    db.refresh_admin_overview_state()
    return db.summarize_sync_actions()


@app.get("/api/admin/audit-log", response_model=AdminAuditLogResponse)
async def admin_audit_log(
    q: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
):
    """List Super Admin audit-log events, newest first (super admin only)."""
    require_super_admin(authorization)
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    page = db.list_audit_events(search=q, limit=limit, offset=offset)
    return {"audit_events": page["events"], "total": page["total"], "limit": limit, "offset": offset}


@app.get("/api/admin/transfers", response_model=GenericObjectResponse)
async def admin_transfers(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
):
    """Recent peer transfer sessions (relay/P2P), newest first (super admin only).

    Surfaces which transport served each transfer and its lifecycle status so the
    outbound-only data plane is observable without router/log access.
    """
    require_super_admin(authorization)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    page = db.list_transfer_sessions(status=status, limit=limit, offset=offset)
    return {
        "transfers": page["transfers"],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/admin/landing-visits", response_model=AdminLandingVisitsResponse)
async def admin_landing_visits(
    limit: int = 20,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
):
    """Unique-visitor count and recent landing-page visitors (super admin only)."""
    require_super_admin(authorization)
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    stats = db.landing_visit_stats()
    page = db.list_landing_visits(limit=limit, offset=offset)
    return {
        "unique": stats["unique"],
        "total": stats["total"],
        "visits": page["visits"],
        "total_rows": page["total"],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/admin/runtime-metrics", response_model=MetricsResponse)
async def admin_runtime_metrics(authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    return {"metrics": collect_runtime_metrics(Path.cwd())}


@app.get("/api/admin/runtime-logs", response_model=RuntimeLogsResponse)
async def admin_runtime_logs(authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    return {"logs": stream_log_snapshot()}


@app.post("/api/admin/run-job", response_model=GenericObjectResponse)
async def admin_run_scheduled_job(job: str, authorization: Optional[str] = Header(default=None)):
    """Trigger a scheduled maintenance job on demand (super admin only)."""
    require_super_admin(authorization)
    try:
        result = run_scheduled_job(job)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return result


@app.delete("/api/admin/users/{user_id}", response_model=StatusResponse)
async def admin_delete_user(user_id: str, authorization: Optional[str] = Header(default=None)):
    user = require_super_admin(authorization)
    if user_id == user["id"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Super admin cannot delete their own account")
    if not db.admin_delete_user(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"status": "deleted", "user_id": user_id}


@app.delete("/api/admin/drones/{device_id}", response_model=StatusResponse)
async def admin_delete_drone(device_id: str, authorization: Optional[str] = Header(default=None)):
    require_super_admin(authorization)
    if not db.admin_delete_device(device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone not found")
    return {"status": "deleted", "device_id": device_id}


@app.post("/api/swarms", response_model=SwarmEnvelope)
async def create_swarm(payload: SwarmCreateRequest, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    swarm = db.create_swarm(user["id"], payload.name)
    return {"swarm": {**swarm, "role": OWNER_ROLE}}


@app.get("/api/swarms/{swarm_id}/access", response_model=SwarmAccessResponse)
async def get_swarm_access(swarm_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    access = db.list_swarm_access(swarm_id)
    member = db.get_swarm_member(swarm_id, user["id"])
    return {"access": access, "role": member.get("role") if member else None}


@app.post("/api/swarms/{swarm_id}/invitations", response_model=InvitationEnvelope)
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


@app.post("/api/swarms/{swarm_id}/invitations/{invitation_id}/resend", response_model=ResendInvitationResponse)
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


@app.delete("/api/swarms/{swarm_id}/invitations/{invitation_id}", response_model=RemoveInvitationResponse)
async def remove_swarm_invitation(swarm_id: str, invitation_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    invite = db.remove_pending_invitation(swarm_id, invitation_id)
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending invitation not found")
    print(f"Pending invitation removed for {invite.get('email')}: swarm_id={swarm_id}")
    return {"status": "removed", "invitation_id": invitation_id}


@app.get("/api/invitations/status", response_model=InvitationStatusResponse)
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


@app.post("/api/invitations/accept", response_model=AcceptInvitationResponse)
async def accept_invitation(payload: InvitationAcceptRequest, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    token = str(payload.token or "")
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


@app.delete("/api/swarms/{swarm_id}/members/{target_user_id}", response_model=StatusResponse)
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


@app.patch("/api/swarms/{swarm_id}/members/{target_user_id}", response_model=SwarmMemberUpdateResponse)
async def update_swarm_member(swarm_id: str, target_user_id: str, payload: SwarmMemberUpdateRequest, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    role = str(payload.role or "").lower()
    if role != READONLY_ROLE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role must be overseer")
    if not db.update_swarm_member_role(swarm_id, target_user_id, role):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Swarm member not found")
    return {"status": "updated", "role": role}


@app.patch("/api/swarms/{swarm_id}", response_model=SwarmEnvelope)
async def update_swarm(swarm_id: str, payload: SwarmRenameRequest, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    selected_swarm_id(user, swarm_id)
    require_swarm_role(user, swarm_id, {OWNER_ROLE})
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    if len(name) > 80:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name must be 80 characters or fewer")
    swarm = db.update_swarm_name(swarm_id, name)
    if not swarm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Swarm not found")
    return {"swarm": {**swarm, "role": (db.get_swarm_member(swarm_id, user["id"]) or {}).get("role")}}


# ==================== Device Management ====================

@app.post("/api/devices/register", response_model=DeviceRegisterResponse)
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
        # Re-resolve the device: set_device_authorization_token reconciles it to the
        # authoritative Postgres drones.id, which can differ from the id captured above.
        # Using the stale id makes the heartbeat child writes (drone_system_info) violate
        # the drone_id foreign key and 500 the whole registration.
        device = db.get_device_by_device_id(device_data.device_id) or device
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

    pending = db.create_pending_drone_connection(
        device_data.device_id,
        device_data.device_name,
        batocera_info,
        user_id=user["id"],
        authorization_token_id=integration_token.get("id"),
    )
    # Alert superadmins the first time a Drone they don't own registers (not on retries).
    if pending.get("_created") and str(user.get("email") or "").strip().lower() != SUPER_ADMIN_EMAIL:
        try:
            owner_label = user.get("username") or user.get("email")
            admin_ids = _super_admin_ids(exclude_user_id=user["id"])
            if admin_ids:
                db.add_admin_notification(
                    admin_ids,
                    "admin_drone_registered",
                    "New Drone registered",
                    f"{device_data.device_name} registered under {owner_label} (awaiting approval).",
                    {
                        "device": {"device_id": device_data.device_id, "device_name": device_data.device_name},
                        "owner": {"id": user["id"], "email": user.get("email")},
                    },
                )
            db.record_audit_event(
                "drone_registered",
                f"New Drone registered: {device_data.device_name} (owner {owner_label})",
                actor=user,
                target_type="drone",
                target_id=device_data.device_id,
                target_label=device_data.device_name,
            )
        except Exception as error:
            logger.warning("Failed to record drone-registration admin alert: %s", error)
    return {
        "message": "Psionic connection detected. Awaiting Overlord approval.",
        "status": "pending",
        "device_id": device_data.device_id,
    }


@app.post("/api/drones/claim-ownership", response_model=DeviceRegisterResponse)
async def claim_drone_ownership(body: DroneClaimRequest):
    """Claim a Drone directly with Overmind account credentials."""
    payload = body.model_dump(exclude_none=True)
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


@app.get("/api/integration-tokens", response_model=IntegrationTokensResponse)
async def list_integration_tokens(authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    return {"tokens": db.get_integration_tokens(user["id"])}


@app.post("/api/integration-tokens", response_model=IntegrationTokenEnvelope)
async def create_integration_token(body: IntegrationTokenCreateRequest, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    payload = body.model_dump(exclude_none=True)
    token = db.create_integration_token(user["id"], str(payload.get("label") or "Drone onboarding"))
    public = {k: v for k, v in token.items() if k != "token_hash"}
    public["authorization_token"] = public.pop("raw_token_once")
    return {"token": public}


@app.delete("/api/integration-tokens/{token_id}", response_model=StatusResponse)
async def revoke_integration_token(token_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    if not db.revoke_integration_token(user["id"], token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration token not found")
    return {"status": "revoked", "id": token_id}


@app.get("/api/drone-connections", response_model=DroneConnectionsResponse)
async def list_drone_connections(swarm_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    """List pending drone connection attempts for the Overlord."""
    user = get_current_user(authorization)
    db.refresh_persistent_state()
    sid = selected_swarm_id(user, swarm_id)
    require_swarm_role(user, sid, {"overlord"})
    return {"connections": db.get_pending_drone_connections(user["id"])}


@app.post("/api/drone-connections/{device_id}/accept", response_model=AcceptDroneConnectionResponse)
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


@app.post("/api/drone-connections/{device_id}/deny", response_model=MessageResponse)
async def deny_drone_connection(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Deny a pending drone connection."""
    user = get_current_user(authorization)
    db.refresh_persistent_state()
    sid = selected_swarm_id(user)
    require_swarm_role(user, sid, {"overlord"})
    if not db.deny_pending_drone_connection(user["id"], device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drone connection not found")
    return {"message": "Drone connection denied.", "device_id": device_id}


@app.get("/api/devices", response_model=DevicesListResponse)
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
            device_response(d, include_inventory_counts=False, include_emulator_configs=False)
            for d in devices
        ]
    }


@app.get("/api/devices/{device_id}", response_model=DeviceModel)
async def get_device(
    device_id: str,
    include_inventory: bool = True,
    include_configs: bool = True,
    authorization: Optional[str] = Header(default=None),
):
    """Get device details."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Device not found"
        )
    return device_response(
        device,
        include_inventory_counts=include_inventory,
        include_emulator_configs=include_configs,
    )


@app.get("/api/devices/{device_id}/peer-certificate/{peer_id}", response_model=PeerCertificateResponse)
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


@app.post("/api/devices/{device_id}/certificate/sign", response_model=GenericObjectResponse)
async def sign_device_certificate(device_id: str, body: SignCsrRequest, authorization: Optional[str] = Header(default=None)):
    """Sign a CSR for an already approved Drone."""
    device = get_current_drone(device_id, authorization)
    payload = body.model_dump(exclude_none=True)
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


@app.post("/api/devices/{device_id}/disconnect", response_model=StatusResponse)
async def disconnect_device_from_drone(device_id: str, authorization: Optional[str] = Header(default=None)):
    """Allow an approved Drone to disconnect itself from its swarm."""
    device = get_current_drone(device_id, authorization)
    if not db.delete_device(device["user_id"], device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "disconnected", "device_id": device_id}


@app.post("/api/devices/{device_id}/token/rotate", response_model=DeviceTokenResponse)
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


@app.patch("/api/devices/{device_id}/auto-sync", response_model=AutoSyncPolicyResponse)
async def update_device_auto_sync(
    device_id: str,
    body: AutoSyncUpdateRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Update per-Drone ROM metadata sync policy."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    payload = body.model_dump(exclude_none=True)
    systems = payload.get("systems") if isinstance(payload.get("systems"), list) else []
    policy = db.update_device_auto_sync_policy(device["user_id"], device_id, bool(payload.get("enabled")), systems)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"auto_sync_policy": policy}


@app.delete("/api/devices/{device_id}", response_model=MessageResponse)
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


@app.get("/api/devices/{device_id}/actions", response_model=ActionsResponse)
async def list_device_actions(device_id: str, authorization: Optional[str] = Header(default=None)):
    """List actions for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    actions = db.get_device_actions(device["user_id"], device_id, include_recent=True)
    if actions is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"actions": actions}


@app.delete("/api/devices/{device_id}/actions", response_model=DeleteActionsResponse)
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


@app.get("/api/downloads", response_model=DownloadsResponse)
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


@app.post("/api/devices/{device_id}/downloads/{job_id}/cancel", response_model=ActionQueuedResponse)
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


@app.post("/api/devices/{device_id}/downloads/{job_id}/pause", response_model=ActionQueuedResponse)
async def pause_device_download(device_id: str, job_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    action = db.create_device_action(device["user_id"], device_id, "pause_download", {"job_id": job_id})
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "queued", "action": action}


@app.post("/api/devices/{device_id}/downloads/{job_id}/resume", response_model=ActionQueuedResponse)
async def resume_device_download(device_id: str, job_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    action = db.create_device_action(device["user_id"], device_id, "resume_download", {"job_id": job_id})
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "queued", "action": action}


@app.post("/api/devices/{device_id}/downloads", response_model=StatusResponse)
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


@app.post("/api/devices/{device_id}/actions", response_model=ActionEnvelope)
async def create_device_action(
    device_id: str,
    body: DeviceActionRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Queue a remote action for a device."""
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    payload = body.model_dump(exclude_none=True)
    action_type = str(payload.get("action") or "").strip().lower()
    if action_type == "reboot":
        action_type = "restart"
    if action_type not in SUPPORTED_DEVICE_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action")
    action_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    if action_type == "set_screen_mode":
        mode = str(action_payload.get("mode") or "").strip().lower()
        if mode not in {"full", "kiosk", "kid"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Screen mode must be one of: full, kiosk, kid",
            )
        action_payload = {**action_payload, "mode": mode}
    if action_type == "set_music_volume":
        try:
            level = max(0, min(100, int(action_payload.get("level"))))
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="level must be a number from 0 to 100")
        action_payload = {"level": level}
    if action_type == "set_es_collections":
        normalized_collections: dict = {}
        if action_payload.get("music_volume") is not None:
            try:
                normalized_collections["music_volume"] = max(0, min(100, int(action_payload["music_volume"])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="music_volume must be a number")
        if action_payload.get("screensaver_minutes") is not None:
            try:
                normalized_collections["screensaver_minutes"] = max(0, min(120, int(action_payload["screensaver_minutes"])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="screensaver_minutes must be a number")
        for list_key in ("hidden_systems", "ungrouped_systems", "auto_collections", "custom_collections"):
            if list_key in action_payload:
                raw_value = action_payload.get(list_key)
                if not isinstance(raw_value, list):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{list_key} must be a list of names")
                normalized_collections[list_key] = [str(item).strip() for item in raw_value if str(item).strip()]
        if not normalized_collections:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide at least one of: music_volume, screensaver_minutes, hidden_systems, ungrouped_systems, auto_collections, custom_collections",
            )
        action_payload = normalized_collections
    if action_type == "set_idle_volume_automation":
        normalized: dict = {}
        if "enabled" in action_payload:
            normalized["enabled"] = bool(action_payload.get("enabled"))
        if action_payload.get("idle_minutes") is not None:
            try:
                normalized["idle_minutes"] = max(1, min(1440, int(action_payload["idle_minutes"])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="idle_minutes must be a number")
        if action_payload.get("target_volume") is not None:
            try:
                normalized["target_volume"] = max(0, min(100, int(action_payload["target_volume"])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target_volume must be a number")
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide at least one of: enabled, idle_minutes, target_volume",
            )
        action_payload = normalized
    if action_type == "set_idle_game_exit_automation":
        normalized: dict = {}
        if "enabled" in action_payload:
            normalized["enabled"] = bool(action_payload.get("enabled"))
        if action_payload.get("idle_minutes") is not None:
            try:
                normalized["idle_minutes"] = max(1, min(1440, int(action_payload["idle_minutes"])))
            except (TypeError, ValueError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="idle_minutes must be a number")
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide at least one of: enabled, idle_minutes",
            )
        action_payload = normalized
    if action_type == "set_wifi_recovery_automation":
        if "enabled" not in action_payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide enabled",
            )
        action_payload = {"enabled": bool(action_payload.get("enabled"))}
    if action_type in {"rebuild_asset_metadata", "purge_asset_cache"}:
        db.clear_device_asset_metadata(device["user_id"], device_id)
    action = db.create_device_action(device["user_id"], device_id, action_type, action_payload)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"action": action}


@app.post("/api/devices/{device_id}/actions/claim", response_model=ClaimActionsResponse)
async def claim_device_action(device_id: str, payload: Optional[dict] = None, authorization: Optional[str] = Header(default=None)):
    """Claim all currently pending actions for a polling drone."""
    get_current_drone(device_id, authorization)
    actions = db.claim_pending_device_actions(device_id)
    return {"actions": actions, "action": actions[0] if actions else None}


@app.post("/api/devices/{device_id}/transfers", response_model=TransferResponse)
async def create_transfer(
    device_id: str,
    body: TransferCreateRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Authorize a relayed peer transfer: pull ``asset`` from ``source_device_id``
    into ``device_id`` (the receiver).

    Mints a short-lived, signed transfer token plus a shared relay ``session_id``
    that both Drones use to rendezvous, and records the session for monitoring /
    resume. The control plane never carries ROM bytes -- the transfer happens
    Drone-to-Drone (relay or direct).
    """
    source_device_id = str(body.source_device_id or "").strip()
    if not source_device_id or source_device_id == device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_device_id must differ from the target device",
        )

    # Either the receiver Drone authenticates as itself (drone-initiated pull,
    # restricted to a same-swarm source) or a user with admin on the receiver
    # (UI-initiated). Drone auth is tried first since the token shape differs.
    receiver = db.verify_device_token(device_id, get_bearer_token(authorization))
    if receiver:
        swarm_id = receiver.get("swarm_id")
        source = db.get_device_by_device_id(source_device_id)
        if not swarm_id or not source or source.get("swarm_id") != swarm_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Source device not found"
            )
    else:
        user = get_current_user(authorization)
        receiver = db.user_can_access_device(user["id"], device_id)
        if not receiver:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
        require_device_admin(user, receiver)
        source = db.user_can_access_device(user["id"], source_device_id)
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Source device not found"
            )

    asset = body.asset.model_dump(exclude_none=True)
    if not str(asset.get("relative_path") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="asset.relative_path is required"
        )

    session_id = uuid.uuid4().hex
    expires_at = int(time.time()) + TRANSFER_TOKEN_TTL_SECONDS
    token = mint_transfer_token(
        auth.SECRET_KEY,
        session_id=session_id,
        from_device=source_device_id,
        to_device=device_id,
        asset=asset,
        ttl_seconds=TRANSFER_TOKEN_TTL_SECONDS,
    )
    try:
        postgres_store.create_transfer_session(
            session_id=session_id,
            from_device=source_device_id,
            to_device=device_id,
            asset=asset,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at_epoch=expires_at,
            swarm_id=receiver.get("swarm_id"),
        )
    except Exception as error:  # noqa: BLE001 -- recording is best-effort
        logger.warning("Failed to record transfer session %s: %s", session_id, error)

    return {
        "session_id": session_id,
        "token": token,
        "source_device_id": source_device_id,
        "target_device_id": device_id,
        "asset": asset,
        "expires_at": expires_at,
    }


@app.post("/api/devices/{device_id}/heartbeat", response_model=HeartbeatResponse)
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
    # Record the Drone-reported ROM inventory fingerprint for display only. We deliberately
    # do NOT queue a purge/resync from a server-side fingerprint comparison here: Overmind
    # used to recompute its own fingerprint from stored rows and queue purge_asset_cache on
    # any mismatch, which produced an endless purge -> 72MB full-refresh loop whenever the
    # two computations drifted. Resync is now Drone-driven via the asset thumbprints echoed
    # in this response (see below).
    rom_fingerprint = str(heartbeat.get("rom_inventory_fingerprint") or "").strip()
    if rom_fingerprint:
        try:
            db.update_device_rom_inventory_fingerprint(device_id, drone_fingerprint=rom_fingerprint)
        except Exception:
            logger.exception("Heartbeat ROM fingerprint record failed device_id=%s", device_id)
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
        # Echo the asset thumbprints Overmind last stored (verbatim from the Drone's last
        # upload). The Drone compares these against what it holds on disk and pushes a
        # resync only when they differ.
        "romset_files_thumbprint": str((updated or {}).get("romset_files_thumbprint") or "") or None,
        "bios_files_thumbprint": str((updated or {}).get("bios_files_thumbprint") or "") or None,
    }


@app.post("/api/devices/{device_id}/rom-metadata", response_model=AssetMetadataAck)
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
    dropped_artwork = metadata.get("artwork") if isinstance(metadata.get("artwork"), list) else []
    dropped_saves = metadata.get("saves") if isinstance(metadata.get("saves"), list) else []
    metadata["artwork"] = []
    metadata["saves"] = []
    metadata.pop("saves_files_thumbprint", None)
    metadata.pop("saves_root", None)
    deleted = metadata.get("deleted") if isinstance(metadata.get("deleted"), dict) else None
    if deleted is not None:
        deleted["artwork"] = []
        deleted["saves"] = []
    db.store_rom_metadata(device_id, metadata)
    db.update_device_last_seen(device["id"])
    print(
        f"Asset metadata upload accepted for {device_id}: rom_count={len(roms)} bios_count={len(bios)} "
        f"artwork_count=0 saves_count=0 dropped_artwork={len(dropped_artwork)} dropped_saves={len(dropped_saves)}"
    )
    return {"rom_count": len(roms), "bios_count": len(bios), "artwork_count": 0, "saves_count": 0}


@app.post("/api/drones/rom-metadata", response_model=AssetMetadataAck)
async def upload_drone_rom_metadata_by_payload(payload: DroneAssetMetadataUpload, authorization: Optional[str] = Header(default=None)):
    device_id = str(payload.device_id or "").strip()
    if not device_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device_id is required")
    return await upload_drone_rom_metadata(device_id, payload, authorization)


@app.post("/api/devices/{device_id}/events", response_model=StatusResponse)
async def add_drone_event(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    """Persist Drone telemetry events using the existing Drone bearer token."""
    get_current_drone(device_id, authorization)
    event = db.add_device_event(device_id, payload)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/peer-checks", response_model=StatusResponse)
async def add_peer_checks(device_id: str, payload: DronePeerChecksUpload, authorization: Optional[str] = Header(default=None)):
    """Persist peer-to-peer health results reported by a Drone."""
    get_current_drone(device_id, authorization)
    results = payload.model_dump(exclude_none=True).get("results") or []
    stored = db.add_peer_checks(device_id, results)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "accepted"}


@app.post("/api/devices/{device_id}/actions/{action_id}/complete", response_model=StatusResponse)
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


@app.post("/api/devices/{device_id}/speed", response_model=StatusResponse)
async def add_speed_sample(device_id: str, payload: DroneSpeedSampleUpload, authorization: Optional[str] = Header(default=None)):
    """Store a Drone upload/download speed sample."""
    get_current_drone(device_id, authorization)
    sample = db.add_speed_sample(device_id, payload.model_dump(exclude_none=True))
    if not sample:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    print(f"Speed sample accepted for {device_id}: up={sample.get('upload_mbps')} down={sample.get('download_mbps')}")
    return {"status": "accepted"}


@app.get("/api/devices/{device_id}/speed/download", response_class=Response)
async def download_speed_probe(device_id: str, bytes: int = 262144, authorization: Optional[str] = Header(default=None)):
    """Return bounded bytes for a Drone to measure Overmind download throughput."""
    get_current_drone(device_id, authorization)
    size = max(1024, min(int(bytes), 2 * 1024 * 1024))
    return Response(content=b"0" * size, media_type="application/octet-stream")


@app.post("/api/devices/{device_id}/speed/upload", response_model=SpeedUploadResponse)
async def upload_speed_probe(device_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """Accept bounded bytes for a Drone to measure Overmind upload throughput."""
    get_current_drone(device_id, authorization)
    body = await request.body()
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Speed probe payload too large")
    return {"bytes_received": len(body)}


@app.get("/api/devices/{device_id}/speed", response_model=SpeedSamplesResponse)
async def get_speed_samples(device_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    samples = db.get_speed_samples(user["id"], device_id)
    if samples is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"samples": samples}


@app.get("/api/profile", response_model=ProfileResponse)
async def get_profile(authorization: Optional[str] = Header(default=None)):
    """Get profile and user settings."""
    user = get_current_user(authorization)
    return profile_response(user)


@app.patch("/api/profile", response_model=ProfileResponse)
async def update_profile(payload: ProfileUpdateRequest, authorization: Optional[str] = Header(default=None)):
    """Update profile and user settings."""
    user = get_current_user(authorization)
    user_id = user["id"]
    # Only fields the client actually sent are applied (PATCH semantics); exclude_unset
    # preserves the original "key present?" checks below.
    fields = payload.model_dump(exclude_unset=True)

    if "username" in fields:
        username = str(fields.get("username") or "").strip()
        if not username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")
        if db.username_exists(username, exclude_user_id=user_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")
        fields["username"] = username

    if "username" in fields or "full_name" in fields or "avatar_data_url" in fields:
        db.update_user_profile(
            user_id,
            username=fields.get("username") if "username" in fields else None,
            full_name=fields.get("full_name") if "full_name" in fields else None,
            avatar_data_url=fields.get("avatar_data_url") if "avatar_data_url" in fields else None,
        )

    if "fleet_settings" in fields and isinstance(fields["fleet_settings"], dict):
        db.update_user_fleet_settings(user_id, fields["fleet_settings"])

    if "notification_settings" in fields and isinstance(fields["notification_settings"], dict):
        db.update_user_notification_settings(user_id, fields["notification_settings"])

    return profile_response(db.get_user(user_id))


@app.get("/api/hive", response_model=HiveResponse)
async def get_hive(authorization: Optional[str] = Header(default=None)):
    """Return a privacy-safe public swarm directory."""
    user = get_current_user(authorization)
    print(f"Hive page/list requested: user_id={user['id']}")
    return hive_response(user, data_store=db)


# ==================== ROM Management ====================

@app.post("/api/devices/{device_id}/roms", response_model=RomUpdateResponse)
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


@app.get("/api/devices/{device_id}/roms", response_model=DeviceRomsResponse, response_model_exclude_none=True)
async def get_device_roms(
    device_id: str,
    system_name: Optional[str] = None,
    q: Optional[str] = None,
    page: Optional[int] = None,
    per_page: int = 100,
    offset: Optional[int] = None,
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

    if page is not None or offset is not None:
        result = db.get_device_roms_page(
            device_id,
            system_name=system_name,
            query=q,
            page=page or 1,
            per_page=per_page,
            offset=offset,
        )
        return {
            "roms": result["rows"],
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
            "offset": result["offset"],
        }

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


@app.get("/api/devices/{device_id}/master-roms", response_model=MasterRomsResponse)
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


@app.get("/api/master-roms", response_model=MasterRomsResponse)
async def get_swarm_master_roms(
    q: Optional[str] = None,
    system: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    """Return a swarm-wide approved-Drone ROM master list deduplicated by fingerprint when available."""
    user = get_current_user(authorization)
    result = db.get_swarm_master_roms_page(user["id"], query=q, system_name=system, page=page, per_page=per_page)
    return {"roms": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


@app.get("/api/devices/{device_id}/bios", response_model=BiosListResponse)
async def get_device_bios(device_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"bios": db.get_device_bios(device_id)}


@app.get("/api/devices/{device_id}/master-bios", response_model=MasterBiosResponse)
async def get_device_master_bios(
    device_id: str,
    q: Optional[str] = None,
    status: Optional[str] = None,
    system_name: Optional[str] = None,
    unassigned: bool = False,
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
        system_name=system_name,
        bios_unassigned=unassigned,
        page=page,
        per_page=per_page,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"bios": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


@app.get("/api/master-bios", response_model=MasterBiosResponse)
async def get_swarm_master_bios(
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    user = get_current_user(authorization)
    result = db.get_swarm_master_bios_page(user["id"], query=q, page=page, per_page=per_page)
    return {"bios": result["rows"], "total": result["total"], "page": result["page"], "per_page": result["per_page"]}


@app.post("/api/devices/{device_id}/sync-rom", response_model=SyncRomResponse)
async def sync_device_rom(device_id: str, body: SyncRomRequest, authorization: Optional[str] = Header(default=None)):
    payload = body.model_dump(exclude_none=True)
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    require_device_admin(user, device)
    system_name = str(payload.get("system_name") or payload.get("system") or "").strip()
    gamelist_id = str(payload.get("gamelist_id") or "").strip()
    # gamelist_id is the ROM identity (the sender resolves it -> <path> in its
    # own gamelist). rom_name is retained only for display / sync-activity.
    rom_name = str(payload.get("rom_name") or payload.get("file_path") or "").strip()
    if not system_name or not gamelist_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="system_name and gamelist_id are required")
    source_devices = payload.get("devices") if isinstance(payload.get("devices"), list) else []
    if not source_devices:
        requested_fingerprint = str(payload.get("rom_fingerprint") or payload.get("fingerprint") or "").strip().lower()
        for row in db.get_master_roms_for_device(device["user_id"], device_id) or []:
            row_system = str(row.get("system_name") or "").strip().lower()
            row_gid = str(row.get("gamelist_id") or "").strip()
            row_fingerprint = str(row.get("rom_fingerprint") or "").strip().lower()
            if row_system != system_name.lower():
                continue
            if requested_fingerprint and row_fingerprint == requested_fingerprint:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                if not rom_name:
                    rom_name = row.get("rom_name") or ""
                break
            if not requested_fingerprint and row_gid == gamelist_id:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                if not rom_name:
                    rom_name = row.get("rom_name") or ""
                break
    # require_resolvable=False: keep every known source even if none is
    # reachable right now -- the Drone holds the sync 'pending' and retries
    # rather than us failing the request outright. Only truly no known source
    # (empty here) is a hard error.
    source_devices = resolvable_asset_sources(source_devices, device_id, require_resolvable=False)
    if not source_devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Drone has this ROM")
    sync_id = str(uuid.uuid4())
    action = db.create_device_action(device["user_id"], device_id, "sync_rom", {
        "sync_id": sync_id,
        "system_name": system_name,
        "gamelist_id": gamelist_id,
        "rom_name": rom_name or gamelist_id,
        "rom_fingerprint": payload.get("rom_fingerprint"),
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
        "gamelist_id": gamelist_id,
        "rom_name": rom_name or gamelist_id,
        "action": "download",
        "status": "pending",
        "file_size": payload.get("file_size"),
        "rom_fingerprint": payload.get("rom_fingerprint"),
        "entry_type": payload.get("entry_type") or "file",
    })
    notify_sync_triggered(user, device, "ROM", f"ROM sync for {system_name}/{rom_name or gamelist_id}", [device], source_devices, action)
    return {"action": action, "artwork_actions": [], "artwork_action_count": 0}


@app.post("/api/devices/{device_id}/sync-bios", response_model=ActionEnvelope)
async def sync_device_bios(device_id: str, body: SyncBiosRequest, authorization: Optional[str] = Header(default=None)):
    payload = body.model_dump(exclude_none=True)
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
        requested_fingerprint = str(payload.get("bios_md5") or payload.get("md5") or "").strip().lower()
        requested_path = bios_path.replace("\\", "/").strip().lstrip("./").lower()
        for row in db.get_master_bios_for_device(device["user_id"], device_id) or []:
            row_path = str(row.get("file_path") or row.get("bios_name") or "").replace("\\", "/").strip().lstrip("./").lower()
            row_fingerprint = str(row.get("bios_md5") or row.get("md5") or "").strip().lower()
            if requested_fingerprint and row_fingerprint == requested_fingerprint:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                break
            if not requested_fingerprint and row_path == requested_path:
                source_devices = row.get("devices") if isinstance(row.get("devices"), list) else []
                break
    source_devices = resolvable_asset_sources(source_devices, device_id, require_resolvable=False)
    if not source_devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Drone has this BIOS")
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


@app.post("/api/devices/{device_id}/sync-system", response_model=SyncRomResponse)
async def sync_device_system(device_id: str, body: SyncSystemRequest, authorization: Optional[str] = Header(default=None)):
    payload = body.model_dump(exclude_none=True)
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
        {**row, "devices": resolvable_asset_sources(row.get("devices") or [], device_id, require_resolvable=False)}
        for row in master_rows
        if str(row.get("system_name") or "").lower() == system_name.lower() and not row.get("present_on_selected")
    ]
    missing = [row for row in missing if row["devices"]]
    if not missing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No Drone has missing ROMs for this system")
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
            "gamelist_id": row.get("gamelist_id"),
            "rom_name": row.get("rom_name") or row.get("gamelist_id"),
            "entry_type": row.get("entry_type") or "file",
            "action": "download",
            "status": "pending",
            "file_size": row.get("file_size"),
            "rom_fingerprint": row.get("rom_fingerprint"),
        })
    notify_sync_triggered(user, device, "System", f"syncing {len(missing)} ROM(s) for {system_name}", [device], [source for row in missing for source in (row.get("devices") or [])], action)
    return {"action": action, "artwork_actions": [], "artwork_action_count": 0}


@app.post("/api/bulk-sync", response_model=BulkSyncResponse)
async def bulk_sync_drones(body: BulkSyncRequest, authorization: Optional[str] = Header(default=None)):
    """Queue sync actions so selected Drones converge for the selected systems."""
    payload = body.model_dump(exclude_none=True)
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
                "gamelist_id": rom.get("gamelist_id") or rom.get("gamelist_game_id"),
                "rom_name": rom.get("rom_name") or rom.get("name"),
                "rom_fingerprint": rom.get("rom_fingerprint"),
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
            if not row.get("rom_fingerprint") and rom.get("rom_fingerprint"):
                row["rom_fingerprint"] = rom.get("rom_fingerprint")
            if not row.get("file_size") and rom.get("file_size"):
                row["file_size"] = rom.get("file_size")

    actions = []
    queued_roms = 0
    notification_batches: dict[str, dict] = {}
    for target_id, target_roms in selected_roms_by_device.items():
        target_keys = {db._rom_key(rom) for rom in target_roms}
        missing_by_system: dict[str, list] = {}
        for key, row in union.items():
            if key in target_keys:
                continue
            # Same relaxed-fail-fast as sync_device_system: keep known-but-currently-
            # unreachable sources too, so the Drone can hold the sync 'pending'
            # rather than this ROM silently never being offered to this target.
            source_devices = resolvable_asset_sources(row.get("devices", []), target_id, require_resolvable=False)
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
                        "gamelist_id": row.get("gamelist_id"),
                        "rom_name": row.get("rom_name") or row.get("gamelist_id"),
                        "entry_type": row.get("entry_type") or "file",
                        "action": "download",
                        "status": "pending",
                        "file_size": row.get("file_size"),
                        "rom_fingerprint": row.get("rom_fingerprint"),
                    })
                batch = notification_batches.setdefault(system_name, {"rom_count": 0, "targets": [], "sources": [], "action": action})
                batch["rom_count"] += len(missing)
                batch["targets"].append(devices[target_id])
                batch["sources"].extend(source for row in missing for source in (row.get("devices") or []))

    for system_name, batch in sorted(notification_batches.items()):
        notify_sync_triggered(
            user,
            batch["targets"][0],
            "Bulk system",
            f"syncing {batch['rom_count']} ROM(s) for {system_name}",
            batch["targets"],
            batch["sources"],
            batch["action"],
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


@app.post("/api/devices/{device_id}/sync-activity", response_model=StatusResponse)
async def add_device_sync_activity(device_id: str, payload: dict, authorization: Optional[str] = Header(default=None)):
    get_current_drone(device_id, authorization)
    entry = db.add_rom_sync_activity(device_id, payload)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"status": "accepted"}


@app.get("/api/devices/{device_id}/sync-activity", response_model=SyncActivityResponse)
async def get_device_sync_activity(device_id: str, authorization: Optional[str] = Header(default=None)):
    user = get_current_user(authorization)
    device = db.user_can_access_device(user["id"], device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    rows = db.get_rom_sync_activity(device["user_id"], device_id)
    if rows is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"activity": rows}


@app.get("/api/sync-activity", response_model=SyncActivityResponse)
async def search_sync_activity(
    q: Optional[str] = None,
    status_filter: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    user = get_current_user(authorization)
    return {"activity": db.search_rom_sync_activity(user["id"], query=q, status=status_filter)}


@app.get("/api/devices/{device_id}/systems", response_model=SystemsResponse)
async def get_device_systems(
    device_id: str,
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 25,
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
    result = db.get_device_systems_page(device_id, query=q, page=page, per_page=per_page)
    return {
        "systems": result["rows"],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
    }


# ==================== Game Play Logging ====================

@app.post("/api/devices/{device_id}/gameplay", response_model=GameplayLogResponse)
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
        rom_fingerprint=gameplay_data.rom_fingerprint,
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


@app.post("/api/devices/{device_id}/game-logs", response_model=StatusResponse)
async def upload_device_game_logs(device_id: str, payload: DroneGameLogsUpload, authorization: Optional[str] = Header(default=None)):
    """Accept newly detected game launches from a Drone."""
    device = get_current_drone(device_id, authorization)
    result = payload.model_dump(exclude_none=True)
    result["type"] = "game_logs"
    db.store_action_result(device, result)
    return {"status": "accepted"}


@app.get("/api/devices/{device_id}/gamelogs", response_model=GamelogsResponse)
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


@app.get("/api/gameplay", response_model=GamelogsResponse)
async def get_swarm_gameplay(
    limit: int = 200,
    authorization: Optional[str] = Header(default=None),
):
    """Fleet-wide play history for the Drone-Swarm overview (all accessible Drones)."""
    user = get_current_user(authorization)
    limit = max(1, min(int(limit or 200), 500))
    return {"gamelogs": db.get_swarm_gamelogs(user["id"], limit=limit)}


@app.get("/api/systems", response_model=SystemsResponse)
async def list_systems(q: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    """List systems with ROM counts across all user devices."""
    user = get_current_user(authorization)
    return {"systems": db.get_user_systems_summary(user["id"], query=q)}


# ==================== UI ====================

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serve the web UI."""
    return get_ui_html()


@app.get("/favicon.ico", response_class=Response)
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

@app.get("/health", response_model=HealthResponse)
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
