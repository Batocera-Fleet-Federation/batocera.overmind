"""Data models for Batocera Overmind."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


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
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    """User login request."""
    email: EmailStr
    password: str


class EmailVerificationRequest(BaseModel):
    email: EmailStr
    code: str


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
    full_name: Optional[str] = None
    created_at: datetime


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
    duration_seconds: Optional[int] = None


# Backward-compatible alias for older typo usage.
BatocerraInfo = BatoceraInfo
