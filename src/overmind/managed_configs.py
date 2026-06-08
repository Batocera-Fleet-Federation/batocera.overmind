"""Curated registry of expected Batocera configs surfaced in the Overmind UI.

The drone uploads config files (and their version history) as they change; this
module defines the canonical set Overmind always displays so a config that does not
exist on the drone still shows up (as unavailable) rather than silently missing.
"""
from __future__ import annotations

from typing import Any

# Each entry maps a display name to the ``relative_path`` the drone reports for it.
MANAGED_CONFIG_REGISTRY = (
    {"name": "Batocera (batocera.conf)", "relative_path": "batocera.conf"},
    {"name": "EmulationStation Settings", "relative_path": "emulationstation/es_settings.cfg"},
    {"name": "EmulationStation Systems", "relative_path": "emulationstation/es_systems.cfg"},
    {"name": "EmulationStation Input", "relative_path": "emulationstation/es_input.cfg"},
    {"name": "RetroArch (custom)", "relative_path": "retroarch/retroarchcustom.cfg"},
    {"name": "RetroArch Core Options", "relative_path": "retroarch/cores/retroarch-core-options.cfg"},
    {"name": "Dolphin", "relative_path": "dolphin-emu/Dolphin.ini"},
    {"name": "DuckStation", "relative_path": "duckstation/settings.ini"},
    {"name": "PCSX2", "relative_path": "PCSX2/inis/PCSX2.ini"},
    {"name": "PPSSPP", "relative_path": "ppsspp/PSP/SYSTEM/ppsspp.ini"},
    {"name": "RPCS3", "relative_path": "rpcs3/config.yml"},
    {"name": "Flycast", "relative_path": "flycast/emu.cfg"},
    {"name": "DOSBox", "relative_path": "dosbox/dosbox.conf"},
    {"name": "MAME", "relative_path": "mame/mame.ini"},
    {"name": "Cemu", "relative_path": "cemu/settings.xml"},
    {"name": "Citra / Azahar", "relative_path": "citra-emu/qt-config.ini"},
    {"name": "Vita3K", "relative_path": "vita3k/config.yml"},
    {"name": "Xemu", "relative_path": "xemu/xemu.toml"},
    {"name": "ScummVM", "relative_path": "scummvm/scummvm.ini"},
)


def _version_count(config: dict) -> int:
    versions = config.get("versions")
    if isinstance(versions, list) and versions:
        return len(versions)
    # A stored config with content but no explicit version list still counts as one.
    return 1 if config.get("content") not in (None, "") else 0


def merge_managed_configs(payload: Any) -> dict:
    """Annotate the emulator-config payload with availability + version counts and
    ensure every expected (registry) config appears, even when absent on the drone."""
    if not isinstance(payload, dict):
        payload = {"type": "emulator_configs", "configs": []}
    configs = [dict(config) for config in (payload.get("configs") or []) if isinstance(config, dict)]
    by_relpath: dict = {}
    for config in configs:
        rel = str(config.get("relative_path") or "").strip().lower()
        config["present"] = True
        config["version_count"] = _version_count(config)
        config.setdefault("managed", False)
        if rel and rel not in by_relpath:
            by_relpath[rel] = config

    for entry in MANAGED_CONFIG_REGISTRY:
        match = by_relpath.get(entry["relative_path"].lower())
        if match is not None:
            match["managed"] = True
            match["name"] = entry["name"]
        else:
            configs.append({
                "name": entry["name"],
                "relative_path": entry["relative_path"],
                "root": "",
                "present": False,
                "managed": True,
                "version_count": 0,
                "versions": [],
                "content": "",
            })

    # Managed entries first (present before absent, alphabetical), then extra uploads.
    def sort_key(config: dict):
        return (
            0 if config.get("managed") else 1,
            0 if config.get("present") else 1,
            str(config.get("name") or config.get("relative_path") or "").lower(),
        )

    configs.sort(key=sort_key)
    result = dict(payload)
    result["type"] = payload.get("type") or "emulator_configs"
    result["configs"] = configs
    result["managed_total"] = len(MANAGED_CONFIG_REGISTRY)
    result["present_total"] = sum(1 for config in configs if config.get("present"))
    return result
