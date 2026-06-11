"""Pure transformations for device-reported snapshots and incremental updates."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Optional


def _is_excluded_emulator_config_path(value: str) -> bool:
    label = str(value or "").replace("\\", "/").strip("/")
    lowered = label.lower()
    if ".bak" in lowered:
        return True
    return bool({"log", "logs"} & {part for part in lowered.split("/") if part})


def merge_rom_metadata_hash_patch(existing: Optional[dict], incoming: dict) -> dict:
    """Apply ROM hash-only updates to an existing full inventory snapshot."""
    merged = dict(existing or {})
    existing_roms = [dict(row) for row in merged.get("roms", []) if isinstance(row, dict)]

    def key(row: dict) -> tuple:
        system = str(row.get("system") or row.get("system_name") or "").strip().lower()
        path = str(row.get("file_path") or row.get("relative_path") or row.get("rom_path") or row.get("rom_file") or "").replace("\\", "/").lstrip("./").lower()
        return system, path

    by_key = {key(row): row for row in existing_roms}
    for patch in incoming.get("roms") if isinstance(incoming.get("roms"), list) else []:
        if not isinstance(patch, dict):
            continue
        row_key = key(patch)
        current = by_key.get(row_key, {})
        by_key[row_key] = {**current, **patch}
    merged["roms"] = list(by_key.values())
    merged["assets"] = {**(merged.get("assets") or {}), "roms": merged["roms"]}
    merged["collected_at"] = incoming.get("collected_at") or merged.get("collected_at")
    merged["hash_progress"] = incoming.get("hash_progress") or merged.get("hash_progress")
    return merged


def _cap_text_lines(value: str, max_lines: int) -> str:
    lines = str(value or "").splitlines()
    return "\n".join(lines[-max(1, int(max_lines)):])


def _cap_log_payload_source_lines(payload: dict, max_lines: int) -> dict:
    """Keep a bounded recent tail for each source so one noisy log cannot hide others."""
    max_lines = max(1, int(max_lines))
    for source in payload.get("logs") if isinstance(payload.get("logs"), list) else []:
        if not isinstance(source, dict):
            continue
        remaining = max_lines
        files = source.get("files") if isinstance(source.get("files"), list) else []
        for file_info in reversed(files):
            if not isinstance(file_info, dict):
                continue
            lines = str(file_info.get("content") or "").splitlines()
            if not lines:
                continue
            if remaining <= 0:
                file_info["content"] = ""
                continue
            kept = lines[-remaining:]
            file_info["content"] = "\n".join(kept)
            remaining -= len(kept)
    return payload


def merge_log_sources(existing: Optional[dict], incoming: dict, max_lines: int = 1000) -> dict:
    """Append changed log bytes while limiting retained output."""
    merged = dict(existing or {"type": "log_sources", "logs": []})
    merged["type"] = "log_sources"
    merged["collected_at"] = incoming.get("collected_at") or merged.get("collected_at")
    by_source = {row.get("source"): dict(row) for row in merged.get("logs", []) if isinstance(row, dict)}
    for incoming_source in incoming.get("logs") if isinstance(incoming.get("logs"), list) else []:
        if not isinstance(incoming_source, dict):
            continue
        source_name = incoming_source.get("source") or "log_source"
        target_source = by_source.setdefault(source_name, {"source": source_name, "files": []})
        by_path = {
            file_info.get("path"): dict(file_info)
            for file_info in target_source.get("files", [])
            if isinstance(file_info, dict)
        }
        for incoming_file in incoming_source.get("files") if isinstance(incoming_source.get("files"), list) else []:
            if not isinstance(incoming_file, dict):
                continue
            path = incoming_file.get("path") or source_name
            target_file = by_path.setdefault(path, {"path": path, "content": ""})
            existing_content = str(target_file.get("content") or "")
            incoming_content = str(incoming_file.get("content") or "")
            if incoming_content:
                separator = "\n" if existing_content and not existing_content.endswith("\n") else ""
                target_file["content"] = _cap_text_lines(existing_content + separator + incoming_content, max_lines)
            for key in ("size", "offset", "truncated", "error", "delta"):
                if key in incoming_file:
                    target_file[key] = incoming_file[key]
            by_path[path] = target_file
        target_source["files"] = list(by_path.values())
        by_source[source_name] = target_source
    merged["logs"] = list(by_source.values())
    merged["max_lines"] = max_lines
    return _cap_log_payload_source_lines(merged, max_lines)


def _game_session_key(session: dict) -> tuple:
    return (
        str(session.get("played_at") or session.get("started_at") or ""),
        str(session.get("system_name") or session.get("system") or "").strip().lower(),
        str(session.get("game_name") or session.get("rom_name") or session.get("rom_path") or "").strip().lower(),
        str(session.get("rom_path") or "").strip().lower(),
    )


def merge_game_logs(existing: Optional[dict], incoming: dict, max_lines: int = 1000) -> dict:
    """Add newly detected game sessions and optional log-source changes."""
    merged = dict(existing or {"type": "game_logs", "sessions": []})
    merged["type"] = "game_logs"
    merged["collected_at"] = incoming.get("collected_at") or merged.get("collected_at")
    sessions = []
    by_key = {}
    for session in list(merged.get("sessions") or []) + (
        incoming.get("sessions") if isinstance(incoming.get("sessions"), list) else []
    ):
        if not isinstance(session, dict):
            continue
        key = _game_session_key(session)
        existing_index = by_key.get(key)
        if existing_index is None:
            by_key[key] = len(sessions)
            sessions.append(dict(session))
        else:
            sessions[existing_index] = {**sessions[existing_index], **session}
    merged["sessions"] = sessions[-max_lines:]
    if isinstance(incoming.get("logs"), list):
        log_payload = {"type": "log_sources", "logs": merged.get("logs") or []}
        merged["logs"] = merge_log_sources(log_payload, incoming, max_lines=max_lines).get("logs", [])
    return merged


def append_game_log_sessions(existing: list, device_id: str, sessions: list, max_rows: int = 1000) -> list:
    """Normalize session reports into persisted game-log rows."""
    bucket = []
    by_key = {}
    for row in existing or []:
        if not isinstance(row, dict):
            continue
        key = _game_session_key(row)
        existing_index = by_key.get(key)
        if existing_index is None:
            by_key[key] = len(bucket)
            bucket.append(dict(row))
        else:
            bucket[existing_index] = {**bucket[existing_index], **row}
    for session in sessions if isinstance(sessions, list) else []:
        if not isinstance(session, dict):
            continue
        system_name = str(session.get("system_name") or session.get("system") or "").strip()
        game_name = str(session.get("game_name") or session.get("rom_name") or session.get("rom_path") or "").strip()
        if not system_name or not game_name:
            continue
        played_at = session.get("played_at") or session.get("started_at") or datetime.utcnow().isoformat()
        normalized = {
            "id": str(uuid.uuid4()),
            "device_id": device_id,
            "system_name": system_name,
            "game_name": game_name,
            "rom_path": session.get("rom_path"),
            "rom_fingerprint": session.get("rom_fingerprint") or session.get("fingerprint"),
            "played_at": played_at,
            "duration_seconds": session.get("duration_seconds"),
        }
        key = _game_session_key(normalized)
        existing_index = by_key.get(key)
        if existing_index is None:
            by_key[key] = len(bucket)
            bucket.append(normalized)
        else:
            existing_row = bucket[existing_index]
            bucket[existing_index] = {
                **existing_row,
                **{field: value for field, value in normalized.items() if value is not None},
                "id": existing_row.get("id") or normalized["id"],
            }
    return bucket[-max_rows:]


def merge_emulator_configs(existing: Optional[dict], incoming: dict, max_versions: int = 10) -> dict:
    """Merge changed configuration files and maintain bounded version history."""
    merged = dict(existing or {"type": "emulator_configs", "configs": []})
    merged["type"] = "emulator_configs"
    merged["collected_at"] = incoming.get("collected_at") or merged.get("collected_at")
    by_key = {}
    for item in merged.get("configs") or []:
        if isinstance(item, dict):
            label = str(item.get("relative_path") or item.get("path") or item.get("name") or "")
            if _is_excluded_emulator_config_path(label):
                continue
            key = f"{item.get('root') or ''}:{label}"
            by_key[key] = dict(item)
    for item in incoming.get("configs") if isinstance(incoming.get("configs"), list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("relative_path") or item.get("path") or item.get("name") or "")
        if _is_excluded_emulator_config_path(label):
            continue
        key = f"{item.get('root') or ''}:{label}"
        incoming_item = dict(item)
        content = str(incoming_item.get("content") or incoming_item.get("text") or "")
        fingerprint = str(incoming_item.get("fingerprint") or hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest())
        incoming_item["fingerprint"] = fingerprint
        existing_item = by_key.get(key) if isinstance(by_key.get(key), dict) else {}
        versions = [dict(row) for row in existing_item.get("versions", []) if isinstance(row, dict)]
        if not versions and existing_item:
            existing_content = str(existing_item.get("content") or existing_item.get("text") or "")
            if existing_content:
                versions.append({
                    "version_id": str(uuid.uuid4()),
                    "collected_at": existing_item.get("collected_at") or merged.get("collected_at") or datetime.utcnow().isoformat(),
                    "fingerprint": str(existing_item.get("fingerprint") or hashlib.sha256(existing_content.encode("utf-8", errors="replace")).hexdigest()),
                    "root": existing_item.get("root"),
                    "relative_path": existing_item.get("relative_path"),
                    "path": existing_item.get("path"),
                    "name": existing_item.get("name"),
                    "content": existing_content,
                })
        known_fingerprints = {str(row.get("fingerprint") or "") for row in versions}
        if fingerprint not in known_fingerprints:
            versions.insert(0, {
                "version_id": str(uuid.uuid4()),
                "collected_at": incoming.get("collected_at") or datetime.utcnow().isoformat(),
                "fingerprint": fingerprint,
                "root": incoming_item.get("root"),
                "relative_path": incoming_item.get("relative_path"),
                "path": incoming_item.get("path"),
                "name": incoming_item.get("name"),
                "content": content,
            })
        incoming_item["versions"] = versions[:max_versions]
        by_key[key] = incoming_item
    merged["configs"] = sorted(
        by_key.values(),
        key=lambda row: str(row.get("relative_path") or row.get("path") or row.get("name") or "").lower(),
    )
    return merged
