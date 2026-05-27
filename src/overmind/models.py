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
    performance: dict[str, Any] = Field(default_factory=dict)


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
    rom_md5: str = Field(..., description="MD5 hash of the ROM")
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
    # Each ROM object should have: rom_name, rom_md5, file_path (optional), file_size (optional)


class GamePlayLog(BaseModel):
    """Request to log game play."""
    device_id: str
    system_name: str
    game_name: str
    rom_path: Optional[str] = None
    rom_md5: Optional[str] = None
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


class DroneDownloadsReport(StrictContractModel):
    target_drone_id: Optional[str] = None
    concurrency: dict[str, Any] = Field(default_factory=lambda: {"scope": "target_drone", "active_limit": 1})
    active: list[DroneDownloadItem] = Field(default_factory=list)
    queued: list[DroneDownloadItem] = Field(default_factory=list)
    recent: list[DroneDownloadItem] = Field(default_factory=list)


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
    rom_md5: Optional[str] = None
    md5: Optional[str] = None
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
    md5: Optional[str] = None
    file_size: Optional[int] = None
    byte_count: Optional[int] = None


class DroneArtworkAsset(ExtensibleContractModel):
    system: Optional[str] = None
    system_name: Optional[str] = None
    rom_name: Optional[str] = None
    rom_path: Optional[str] = None
    file_path: Optional[str] = None
    title: Optional[str] = None
    artwork_type: Optional[str] = None
    artwork_types: list[str] = Field(default_factory=list)


class DroneAssetDeleteSet(StrictContractModel):
    roms: list[DroneRomAsset] = Field(default_factory=list)
    bios: list[DroneBiosAsset] = Field(default_factory=list)
    artwork: list[DroneArtworkAsset] = Field(default_factory=list)


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
    roms_root: Optional[str] = None
    systems: list[DroneAssetSystem] = Field(default_factory=list)
    roms: list[DroneRomAsset] = Field(default_factory=list)
    bios: list[DroneBiosAsset] = Field(default_factory=list)
    artwork: list[DroneArtworkAsset] = Field(default_factory=list)
    gamelists: list[dict[str, Any]] = Field(default_factory=list)
    deleted: Optional[DroneAssetDeleteSet] = None
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
    rom_md5: Optional[str] = None
    duration_seconds: Optional[int] = None


class DroneGameLogsUpload(StrictContractModel):
    type: Optional[Literal["game_logs"]] = "game_logs"
    sessions: list[DroneGameSession] = Field(default_factory=list)


class DroneLogFile(ExtensibleContractModel):
    path: Optional[str] = None
    content: str = ""
    modified_at: Optional[str] = None


class DroneLogSource(ExtensibleContractModel):
    source: str
    files: list[DroneLogFile] = Field(default_factory=list)


class DroneLogSourcesUpload(StrictContractModel):
    type: Optional[Literal["log_sources"]] = "log_sources"
    logs: list[DroneLogSource] = Field(default_factory=list)


class DroneEmulatorConfigFile(ExtensibleContractModel):
    root: Optional[str] = None
    relative_path: str
    content: str = ""
    modified_at: Optional[str] = None


class DroneEmulatorConfigsUpload(StrictContractModel):
    type: Optional[Literal["emulator_configs"]] = "emulator_configs"
    incremental: bool = True
    configs: list[DroneEmulatorConfigFile] = Field(default_factory=list)
