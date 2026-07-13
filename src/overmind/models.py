"""Data models for Batocera Overmind."""

from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrictContractModel(BaseModel):
    """Base model for deliberate Drone/Overmind request contracts."""
    model_config = ConfigDict(extra="forbid")


class ExtensibleContractModel(BaseModel):
    """Base model for versioned records where Drones may add measured metadata."""
    model_config = ConfigDict(extra="allow")


class BatoceraInfo(BaseModel):
    """System information from Batocera device."""
    model: str = Field(..., description="Device model")
    system: str = Field(..., description="System/OS info")
    architecture: str = Field(..., description="CPU architecture")
    cpu_model: str = Field(..., description="CPU model name")
    cpu_cores: int = Field(..., description="Number of CPU cores")
    cpu_threads: int = Field(..., description="Number of CPU threads")
    cpu_max_frequency: str = Field(..., description="Max CPU frequency")
    temperature: Optional[str] = Field(None, description="System temperature")
    memory_available: str = Field(..., description="Available memory")
    memory_total: str = Field(..., description="Total memory")
    display_resolution: Optional[str] = Field(None, description="Display resolution")
    display_refresh_rate: Optional[str] = Field(None, description="Display refresh rate")
    data_partition_available: Optional[str] = Field(None, description="Available disk space")
    ip_address: str = Field(..., description="Network IP address")
    network: Optional[dict] = None
    api_port: Optional[int] = None
    scheme: Optional[str] = None
    reachable_url: Optional[str] = None
    certificate: Optional[dict] = None
    system_info: Optional[dict] = None
    battery: Optional[str] = Field(None, description="Battery status")


class UserRegister(BaseModel):
    """User registration request."""
    email: EmailStr
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")
    full_name: Optional[str] = None
    invitation_token: Optional[str] = None


class UserLogin(BaseModel):
    """User login request."""
    email: EmailStr
    password: str


class EmailVerificationRequest(BaseModel):
    email: EmailStr
    code: str


class EmailVerificationResendRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8)


class SwarmCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class SwarmInviteRequest(BaseModel):
    email: EmailStr
    role: Optional[str] = None


class User(BaseModel):
    """User model."""
    id: str
    email: EmailStr
    username: Optional[str] = None
    full_name: Optional[str] = None
    created_at: datetime


class DroneNetworkState(ExtensibleContractModel):
    ipv4: list[str] = Field(default_factory=list)
    ipv6: list[str] = Field(default_factory=list)
    hostname: Optional[str] = None
    mac_address: Optional[str] = None
    public_ip: Optional[str] = None
    local_ip: Optional[str] = None
    ssid: Optional[str] = None
    interface: Optional[str] = None


class DroneCertificate(ExtensibleContractModel):
    status: Optional[str] = None
    fingerprint: Optional[str] = None
    sha256_fingerprint: Optional[str] = None
    public_certificate: Optional[str] = None
    certificate_pem: Optional[str] = None
    subject: Optional[str] = None
    issuer: Optional[str] = None
    san: list[str] = Field(default_factory=list)
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    serial_number: Optional[str] = None
    overmind_signed_at: Optional[datetime] = None


class DroneSystemInfo(ExtensibleContractModel):
    hostname: Optional[str] = None
    model: Optional[str] = None
    system: Optional[str] = None
    architecture: Optional[str] = None
    cpu_model: Optional[str] = None
    cpu_cores: Optional[int] = None
    cpu_threads: Optional[int] = None
    cpu_max_frequency: Optional[str] = None
    memory_available: Optional[str] = None
    memory_total: Optional[str] = None
    batocera_version: Optional[str] = None
    container: Optional[bool] = None
    screen_mode: Optional[Literal["full", "kiosk", "kid"]] = None
    audio_volume: Optional[int] = None
    pixen_installed: Optional[bool] = None
    performance: dict[str, Any] = Field(default_factory=dict)
    es_collections: Optional[dict[str, Any]] = None


class DeviceRegister(BaseModel):
    """Device registration request from Batocera app."""
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    authorization_token: Optional[str] = None
    device_id: str = Field(..., description="Unique device identifier")
    device_name: str = Field(..., description="User-friendly device name")
    batocera_info: BatoceraInfo
    api_port: Optional[int] = None
    scheme: Optional[str] = None
    reachable_url: Optional[str] = None


class SocialAuthRequest(BaseModel):
    """Development-friendly social auth request for configured providers."""
    email: EmailStr
    full_name: Optional[str] = None


class Device(BaseModel):
    """Registered device model."""
    id: str  # Internal device ID
    user_id: str
    device_id: str  # Batocera device unique ID
    device_name: str
    batocera_info: BatoceraInfo
    registered_at: datetime
    last_seen: Optional[datetime] = None


class RomMetadata(BaseModel):
    """ROM metadata."""
    id: str
    device_id: str
    system_name: str
    rom_name: str
    rom_fingerprint: str = Field(..., description="Content fingerprint of the ROM")
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    added_at: datetime


class GamePlay(BaseModel):
    """Game play log entry."""
    id: str
    device_id: str
    rom_id: Optional[str] = None  # Reference to RomMetadata
    system_name: str
    game_name: str
    played_at: datetime
    duration_seconds: Optional[int] = None


class RomListUpdate(BaseModel):
    """Request to update ROM list for a device."""
    device_id: str
    system_name: str
    roms: list[dict] = Field(..., description="List of ROM metadata objects")
    # Each ROM object should have: rom_name, rom_fingerprint, file_path (optional), file_size (optional)


class GamePlayLog(BaseModel):
    """Request to log game play."""
    device_id: str
    system_name: str
    game_name: str
    rom_path: Optional[str] = None
    rom_fingerprint: Optional[str] = None
    played_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None


# Backward-compatible alias for older typo usage.
BatocerraInfo = BatoceraInfo


class DroneDownloadItem(ExtensibleContractModel):
    job_id: Optional[str] = None
    id: Optional[str] = None
    asset_type: Optional[Literal["rom", "bios", "artwork"]] = None
    status: Optional[str] = None
    target_drone_id: Optional[str] = None
    source_drone_id: Optional[str] = None
    system: Optional[str] = None
    file_path: Optional[str] = None
    relative_path: Optional[str] = None
    rom_path: Optional[str] = None
    rom_name: Optional[str] = None
    bios_name: Optional[str] = None
    artwork_type: Optional[str] = None
    file_size: Optional[int] = None
    total_bytes: Optional[int] = None
    downloaded_bytes: Optional[int] = None
    bytes_transferred: Optional[int] = None
    percentage: Optional[float] = None
    transfer_speed_bps: Optional[float] = None
    queue_position: Optional[int] = None
    failure_reason: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class DroneDownloadsReport(ExtensibleContractModel):
    # Extensible: the Drone's download summary keeps adding queue-state fields
    # (paused, queue_eta_seconds, queue_remaining_bytes, queue_estimate_*, ...).
    # These are telemetry/display state, so accept newer fields instead of 422-ing
    # the whole heartbeat — same rationale as DroneDownloadItem above.
    target_drone_id: Optional[str] = None
    concurrency: dict[str, Any] = Field(default_factory=lambda: {"scope": "target_drone", "active_limit": 1})
    active: list[DroneDownloadItem] = Field(default_factory=list)
    queued: list[DroneDownloadItem] = Field(default_factory=list)
    recent: list[DroneDownloadItem] = Field(default_factory=list)
    downloads: list[DroneDownloadItem] = Field(default_factory=list)


class DroneHeartbeatRequest(StrictContractModel):
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    network: Optional[DroneNetworkState] = None
    api_port: Optional[int] = None
    scheme: Optional[str] = None
    protocol: Optional[str] = None
    reachable_url: Optional[str] = None
    certificate: Optional[DroneCertificate] = None
    system_info: Optional[DroneSystemInfo] = None
    downloads: Optional[DroneDownloadsReport] = None
    rom_inventory_fingerprint: Optional[str] = None
    rom_inventory_fingerprint_algorithm: Optional[str] = None
    romset_files_thumbprint: Optional[str] = None
    bios_files_thumbprint: Optional[str] = None
    # Tolerance field, not read: a strict model missing this once made every heartbeat
    # from a Drone still sending it 422 (drone showed permanently offline). Accept and
    # ignore rather than re-earn that incident. See heartbeat-422-saves-thumbprint memory.
    saves_files_thumbprint: Optional[str] = None
    rom_metadata: Optional[dict[str, Any]] = None
    rom_systems: list[dict[str, Any]] = Field(default_factory=list)


class DroneAssetSystem(ExtensibleContractModel):
    name: Optional[str] = None
    system_name: Optional[str] = None
    rom_count: Optional[int] = None
    bios_count: Optional[int] = None
    artwork_count: Optional[int] = None


class DroneRomAsset(ExtensibleContractModel):
    system: Optional[str] = None
    system_name: Optional[str] = None
    name: Optional[str] = None
    rom_name: Optional[str] = None
    rom_file: Optional[str] = None
    file_path: Optional[str] = None
    relative_path: Optional[str] = None
    rom_path: Optional[str] = None
    rom_fingerprint: Optional[str] = None
    fingerprint: Optional[str] = None
    file_size: Optional[int] = None
    byte_count: Optional[int] = None
    entry_type: Optional[str] = None


class DroneBiosAsset(ExtensibleContractModel):
    name: Optional[str] = None
    bios_name: Optional[str] = None
    path: Optional[str] = None
    file_path: Optional[str] = None
    relative_path: Optional[str] = None
    bios_md5: Optional[str] = None
    fingerprint: Optional[str] = None
    file_size: Optional[int] = None
    byte_count: Optional[int] = None
    # system_name(s) this BIOS file is known to belong to, resolved by the Drone against
    # its vendored reference table. Empty for the (common) case of an unrecognized BIOS;
    # more than one entry for a BIOS legitimately shared across several systems.
    systems: list[str] = Field(default_factory=list)


class DroneArtworkAsset(ExtensibleContractModel):
    system: Optional[str] = None
    system_name: Optional[str] = None
    rom_name: Optional[str] = None
    rom_path: Optional[str] = None
    file_path: Optional[str] = None
    title: Optional[str] = None
    artwork_type: Optional[str] = None
    artwork_types: list[str] = Field(default_factory=list)


class DroneSaveAsset(ExtensibleContractModel):
    system: Optional[str] = None
    system_name: Optional[str] = None
    save_name: Optional[str] = None
    name: Optional[str] = None
    file_path: Optional[str] = None
    relative_path: Optional[str] = None
    fingerprint: Optional[str] = None
    saves_fingerprint: Optional[str] = None
    file_size: Optional[int] = None
    byte_count: Optional[int] = None
    modified_time: Optional[int] = None


class DroneAssetDeleteSet(StrictContractModel):
    roms: list[DroneRomAsset] = Field(default_factory=list)
    bios: list[DroneBiosAsset] = Field(default_factory=list)
    artwork: list[DroneArtworkAsset] = Field(default_factory=list)
    saves: list[DroneSaveAsset] = Field(default_factory=list)


class DroneAssetMetadataUpload(StrictContractModel):
    device_id: str
    type: Optional[Literal["rom_metadata", "asset_metadata"]] = "asset_metadata"
    update_mode: Optional[Literal["inventory", "inventory_chunk", "inventory_delta", "rom_hash_patch"]] = "inventory"
    replace_all: bool = False
    inventory_id: Optional[str] = None
    chunk_index: Optional[int] = None
    chunk_total: Optional[int] = None
    inventory_complete: Optional[bool] = None
    inventory_counts: dict[str, int] = Field(default_factory=dict)
    rom_inventory_fingerprint: Optional[str] = None
    rom_inventory_fingerprint_algorithm: Optional[str] = None
    romset_files_thumbprint: Optional[str] = None
    bios_files_thumbprint: Optional[str] = None
    # Accepted-but-ignored for the same reason artwork/saves rows below are: a Drone
    # mid-rollout may still send it; strict-model rejection must not 422 the upload.
    saves_files_thumbprint: Optional[str] = None
    collected_at: Optional[str] = None
    roms_root: Optional[str] = None
    bios_root: Optional[str] = None
    saves_root: Optional[str] = None
    systems: list[DroneAssetSystem] = Field(default_factory=list)
    roms: list[DroneRomAsset] = Field(default_factory=list)
    bios: list[DroneBiosAsset] = Field(default_factory=list)
    artwork: list[DroneArtworkAsset] = Field(default_factory=list)
    saves: list[DroneSaveAsset] = Field(default_factory=list)
    gamelists: list[dict[str, Any]] = Field(default_factory=list)
    deleted: Optional[DroneAssetDeleteSet] = None
    cache: dict[str, Any] = Field(default_factory=dict)
    delta_index: Optional[int] = None
    delta_total: Optional[int] = None
    hash_progress: dict[str, Any] = Field(default_factory=dict)


class DronePeerCheckResult(ExtensibleContractModel):
    source_drone_id: Optional[str] = None
    target_drone_id: str
    target_address: Optional[str] = None
    status: str
    latency_ms: Optional[float] = None
    checked_at: Optional[str] = None
    error: Optional[str] = None


class DronePeerChecksUpload(StrictContractModel):
    results: list[DronePeerCheckResult] = Field(default_factory=list)


class DroneActionCompleteRequest(StrictContractModel):
    status: Literal["completed", "failed"]
    message: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class DroneSpeedSampleUpload(ExtensibleContractModel):
    upload_mbps: Optional[float] = None
    download_mbps: Optional[float] = None
    latency_ms: Optional[float] = None
    measured_at: Optional[str] = None


class DroneGameSession(ExtensibleContractModel):
    played_at: Optional[str] = None
    system_name: Optional[str] = None
    game_name: Optional[str] = None
    rom_path: Optional[str] = None
    rom_fingerprint: Optional[str] = None
    duration_seconds: Optional[int] = None


class DroneGameLogsUpload(StrictContractModel):
    type: Optional[Literal["game_logs"]] = "game_logs"
    collected_at: Optional[str] = None
    sessions: list[DroneGameSession] = Field(default_factory=list)


# ==================== Phase 1 request models ====================
# These replace `payload: dict` handlers so Swagger documents the body. Fields are
# Optional to preserve each handler's existing validation/status-code behavior
# (handlers still raise their own 400s); PATCH handlers read model_dump(exclude_unset=True).


class NotificationIdsRequest(BaseModel):
    """Optional id list for marking/dismissing notifications (omitted body = all)."""
    ids: Optional[list[str]] = None


class InvitationAcceptRequest(BaseModel):
    token: Optional[str] = None


class SwarmMemberUpdateRequest(BaseModel):
    role: Optional[str] = None


class SwarmRenameRequest(BaseModel):
    name: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    """Partial profile update; only provided fields are applied (PATCH semantics)."""
    username: Optional[str] = None
    full_name: Optional[str] = None
    avatar_data_url: Optional[str] = None
    fleet_settings: Optional[dict] = None
    notification_settings: Optional[dict] = None


# ==================== Phase 1 response models ====================
# Permissive (extra="allow"): declared fields are documented in OpenAPI while any
# additional keys the handlers return (e.g. splatted DB records) pass through unchanged.


class StatusResponse(ExtensibleContractModel):
    status: str


class MessageResponse(ExtensibleContractModel):
    message: str


class UserSummary(ExtensibleContractModel):
    id: str
    email: str
    username: Optional[str] = None
    full_name: Optional[str] = None


class SwarmSummary(ExtensibleContractModel):
    id: Optional[str] = None
    name: Optional[str] = None
    owner_id: Optional[str] = None
    role: Optional[str] = None
    current: Optional[bool] = None
    created_at: Optional[Any] = None


class LoginResponse(ExtensibleContractModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSummary
    swarms: list[SwarmSummary] = Field(default_factory=list)


class AuthProvidersResponse(ExtensibleContractModel):
    providers: dict[str, bool] = Field(default_factory=dict)


class SwarmListResponse(ExtensibleContractModel):
    swarms: list[SwarmSummary] = Field(default_factory=list)


class SwarmEnvelope(ExtensibleContractModel):
    swarm: SwarmSummary


class SwarmAccessData(ExtensibleContractModel):
    members: list[Any] = Field(default_factory=list)
    invitations: list[Any] = Field(default_factory=list)


class SwarmAccessResponse(ExtensibleContractModel):
    access: SwarmAccessData
    role: Optional[str] = None


class InvitationSummary(ExtensibleContractModel):
    id: Optional[str] = None
    swarm_id: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[Any] = None
    expires_at: Optional[Any] = None


class InvitationEnvelope(ExtensibleContractModel):
    invitation: InvitationSummary


class ResendInvitationResponse(ExtensibleContractModel):
    status: str
    invitation: InvitationSummary


class RemoveInvitationResponse(ExtensibleContractModel):
    status: str
    invitation_id: str


class InvitationStatusResponse(ExtensibleContractModel):
    status: str
    email: Optional[str] = None
    swarm_id: Optional[str] = None
    role: Optional[str] = None
    registered: Optional[bool] = None


class AcceptInvitationResponse(ExtensibleContractModel):
    status: str
    swarm_id: str


class SwarmMemberUpdateResponse(ExtensibleContractModel):
    status: str
    role: str


class MarkNotificationsResponse(ExtensibleContractModel):
    status: str
    marked_read: int


class DismissNotificationsResponse(ExtensibleContractModel):
    status: str
    dismissed: int


class NotificationListResponse(ExtensibleContractModel):
    notifications: list[Any] = Field(default_factory=list)
    unread_count: int = 0
    total_count: int = 0
    limit: int = 0
    offset: int = 0


class ProfileResponse(ExtensibleContractModel):
    id: str
    email: str
    username: Optional[str] = None
    full_name: Optional[str] = None
    avatar_data_url: Optional[str] = None
    fleet_settings: dict = Field(default_factory=dict)
    notification_settings: dict = Field(default_factory=dict)


class HiveEntry(ExtensibleContractModel):
    swarm_id: Optional[str] = None
    swarm_name: Optional[str] = None
    owner_username: Optional[str] = None
    owner_avatar_data_url: Optional[str] = None
    drone_count: Optional[int] = None
    current: Optional[bool] = None
    viewer_role: Optional[str] = None
    can_view: Optional[bool] = None


class HiveResponse(ExtensibleContractModel):
    hive: list[HiveEntry] = Field(default_factory=list)
    swarms: list[Any] = Field(default_factory=list)


# ==================== Phase 2-4 request models ====================
# Extend ExtensibleContractModel (extra="allow") so any key the client sends still flows
# through model_dump() even when not declared here — handlers keep reading a dict, so
# their behaviour is unchanged; declared fields just document the body in Swagger.


class AdminAssignRequest(ExtensibleContractModel):
    swarm_id: Optional[str] = None


class DroneClaimRequest(ExtensibleContractModel):
    device_id: Optional[str] = None
    drone_id: Optional[str] = None
    device_name: Optional[str] = None
    drone_name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    ip_address: Optional[str] = None
    scheme: Optional[str] = None
    reachable_url: Optional[str] = None
    api_port: Optional[int] = None
    network: Optional[dict] = None
    system_info: Optional[dict] = None
    certificate: Optional[dict] = None
    batocera_info: Optional[dict] = None


class IntegrationTokenCreateRequest(ExtensibleContractModel):
    label: Optional[str] = None


class SignCsrRequest(ExtensibleContractModel):
    csr_pem: Optional[str] = None
    days: Optional[int] = None


class AutoSyncUpdateRequest(ExtensibleContractModel):
    enabled: Optional[bool] = None
    systems: Optional[list[str]] = None


class DeviceActionRequest(ExtensibleContractModel):
    action: Optional[str] = None
    payload: Optional[dict] = None


class SyncRomRequest(ExtensibleContractModel):
    system_name: Optional[str] = None
    system: Optional[str] = None
    gamelist_id: Optional[str] = None
    rom_name: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    entry_type: Optional[str] = None
    fingerprint: Optional[str] = None
    rom_fingerprint: Optional[str] = None
    devices: Optional[list[Any]] = None


class SyncBiosRequest(ExtensibleContractModel):
    bios_name: Optional[str] = None
    name: Optional[str] = None
    file_path: Optional[str] = None
    relative_path: Optional[str] = None
    file_size: Optional[int] = None
    bios_md5: Optional[str] = None
    md5: Optional[str] = None
    devices: Optional[list[Any]] = None


class SyncArtworkRequest(ExtensibleContractModel):
    artwork_type: Optional[str] = None
    system_name: Optional[str] = None
    system: Optional[str] = None
    rom_name: Optional[str] = None
    rom_path: Optional[str] = None
    file_path: Optional[str] = None
    devices: Optional[list[Any]] = None


class SyncArtworkBulkRequest(ExtensibleContractModel):
    artwork_type: Optional[str] = None
    systems: Optional[list[str]] = None
    devices: Optional[list[Any]] = None


class SyncSystemRequest(ExtensibleContractModel):
    system: Optional[str] = None
    system_name: Optional[str] = None
    devices: Optional[list[Any]] = None


class BulkSyncRequest(ExtensibleContractModel):
    device_ids: Optional[list[str]] = None
    systems: Optional[list[str]] = None


# ==================== Phase 2-4 response models ====================
# Permissive: declared fields are documented; only fields each handler always returns are
# declared so the serialized JSON keeps the same key set (extra keys pass through).


class DeviceModel(ExtensibleContractModel):
    """Core, always-present subset of presenters.device_response (the rest pass through)."""
    id: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    online: Optional[bool] = None
    status: Optional[str] = None
    swarm_connected: Optional[bool] = None
    rom_count: Optional[int] = None
    game_count: Optional[int] = None


class GenericObjectResponse(ExtensibleContractModel):
    """Open object passthrough for opaque dict payloads (job results, signed cert, summaries)."""


class HealthResponse(ExtensibleContractModel):
    status: str
    version: str


class AdminOverviewResponse(ExtensibleContractModel):
    users: list[Any] = Field(default_factory=list)
    swarms: list[Any] = Field(default_factory=list)
    drones: list[Any] = Field(default_factory=list)
    pending_connections: list[Any] = Field(default_factory=list)


class AdminSyncActionsResponse(ExtensibleContractModel):
    sync_actions: list[Any] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


class AdminAuditLogResponse(ExtensibleContractModel):
    audit_events: list[Any] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


class AdminLandingVisitsResponse(ExtensibleContractModel):
    unique: int = 0
    total: int = 0
    visits: list[Any] = Field(default_factory=list)
    total_rows: int = 0
    limit: int = 0
    offset: int = 0


class MetricsResponse(ExtensibleContractModel):
    metrics: Any = None


class RuntimeLogsResponse(ExtensibleContractModel):
    logs: Any = None


class AdminAssignResponse(ExtensibleContractModel):
    status: str
    device: DeviceModel


class DevicesListResponse(ExtensibleContractModel):
    devices: list[DeviceModel] = Field(default_factory=list)


class DeviceTokenResponse(ExtensibleContractModel):
    device: DeviceModel
    drone_token: Optional[str] = None


class PeerCertificateResponse(ExtensibleContractModel):
    device_id: str
    certificate_pem: str
    metadata: dict = Field(default_factory=dict)


class DeviceRegisterResponse(ExtensibleContractModel):
    message: Optional[str] = None
    status: str
    device_id: str
    drone_token: Optional[str] = None


class AcceptDroneConnectionResponse(ExtensibleContractModel):
    message: str
    device: DeviceModel
    drone_token: Optional[str] = None


class IntegrationTokensResponse(ExtensibleContractModel):
    tokens: list[Any] = Field(default_factory=list)


class IntegrationTokenEnvelope(ExtensibleContractModel):
    token: dict = Field(default_factory=dict)


class DroneConnectionsResponse(ExtensibleContractModel):
    connections: list[Any] = Field(default_factory=list)


class AutoSyncPolicyResponse(ExtensibleContractModel):
    auto_sync_policy: Any = None


class ActionsResponse(ExtensibleContractModel):
    actions: list[Any] = Field(default_factory=list)


class DeleteActionsResponse(ExtensibleContractModel):
    status: str
    deleted_count: int = 0


class DownloadsResponse(ExtensibleContractModel):
    concurrency: dict = Field(default_factory=dict)
    targets: list[Any] = Field(default_factory=list)


class ActionEnvelope(ExtensibleContractModel):
    action: Optional[Any] = None


class ActionQueuedResponse(ExtensibleContractModel):
    status: str
    action: Optional[Any] = None


class ClaimActionsResponse(ExtensibleContractModel):
    actions: list[Any] = Field(default_factory=list)
    action: Optional[Any] = None


class SyncRomResponse(ExtensibleContractModel):
    action: Optional[Any] = None
    artwork_actions: list[Any] = Field(default_factory=list)
    artwork_action_count: int = 0


class SyncArtworkBulkResponse(ExtensibleContractModel):
    status: str
    systems: list[Any] = Field(default_factory=list)
    source_device_ids: list[Any] = Field(default_factory=list)
    action_count: int = 0
    queued_artwork_count: int = 0
    skipped_artwork_count: int = 0
    actions: list[Any] = Field(default_factory=list)


class BulkSyncResponse(ExtensibleContractModel):
    status: str
    device_count: int = 0
    systems: list[Any] = Field(default_factory=list)
    action_count: int = 0
    queued_rom_count: int = 0
    artwork_action_count: int = 0
    actions: list[Any] = Field(default_factory=list)
    artwork_actions: list[Any] = Field(default_factory=list)


class HeartbeatResponse(ExtensibleContractModel):
    actions: list[Any] = Field(default_factory=list)
    swarm: list[Any] = Field(default_factory=list)
    log_stream_requested: bool = False
    romset_files_thumbprint: Optional[str] = None
    bios_files_thumbprint: Optional[str] = None


class AssetMetadataAck(ExtensibleContractModel):
    rom_count: int = 0
    bios_count: int = 0
    artwork_count: int = 0
    saves_count: int = 0


class SpeedUploadResponse(ExtensibleContractModel):
    bytes_received: int = 0


class SpeedSamplesResponse(ExtensibleContractModel):
    samples: list[Any] = Field(default_factory=list)


class DeviceRomsResponse(ExtensibleContractModel):
    """get_device_roms returns one of three shapes; route uses response_model_exclude_none."""
    roms: Optional[list[Any]] = None
    total: Optional[int] = None
    page: Optional[int] = None
    per_page: Optional[int] = None
    offset: Optional[int] = None
    systems: Optional[dict] = None


class MasterRomsResponse(ExtensibleContractModel):
    roms: list[Any] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 100


class BiosListResponse(ExtensibleContractModel):
    bios: list[Any] = Field(default_factory=list)


class MasterBiosResponse(ExtensibleContractModel):
    bios: list[Any] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    per_page: int = 100


class RomUpdateResponse(ExtensibleContractModel):
    message: str
    system_name: str
    rom_count: int = 0
    rom_ids: list[Any] = Field(default_factory=list)


class SyncActivityResponse(ExtensibleContractModel):
    activity: list[Any] = Field(default_factory=list)


class SystemsResponse(ExtensibleContractModel):
    systems: Any = None


class GameplayLogResponse(ExtensibleContractModel):
    message: str
    gamelog_id: Any = None


class GamelogsResponse(ExtensibleContractModel):
    gamelogs: list[Any] = Field(default_factory=list)


class TransferAssetRef(ExtensibleContractModel):
    """The asset a relayed transfer should move (mirrors the Drone AssetRef)."""
    kind: str  # "rom" | "bios" | "save" | "artwork"
    relative_path: str
    system: Optional[str] = None
    gamelist_id: Optional[str] = None
    expected_size: Optional[int] = None
    expected_hash: Optional[str] = None


class TransferCreateRequest(StrictContractModel):
    """POST /api/devices/{id}/transfers -- pull ``asset`` from ``source_device_id``
    to the path device (the receiver)."""
    source_device_id: str
    asset: TransferAssetRef


class TransferResponse(ExtensibleContractModel):
    session_id: str
    token: str
    source_device_id: str
    target_device_id: str
    asset: dict = Field(default_factory=dict)
    expires_at: Optional[int] = None
