"""Runtime performance metrics for Overmind diagnostics."""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LAST_SAMPLE: Optional[dict] = None


def _read_meminfo() -> dict:
    parsed = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            value = raw.strip().split()[0]
            parsed[key] = int(value) * 1024
    except Exception:
        return {}
    total = int(parsed.get("MemTotal") or 0)
    available = int(parsed.get("MemAvailable") or 0)
    used = max(0, total - available) if total else 0
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "used_percent": round((used / total) * 100, 2) if total else None,
    }


def _read_process_memory() -> dict:
    try:
        values = {}
        for line in Path("/proc/self/status").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(("VmRSS:", "VmSize:")):
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0]) * 1024
        return {"rss_bytes": values.get("VmRSS"), "vms_bytes": values.get("VmSize")}
    except Exception:
        return {"rss_bytes": None, "vms_bytes": None}


def _read_cpu_times() -> dict:
    process_ticks = os.times()
    process_seconds = float(process_ticks.user + process_ticks.system)
    total_jiffies = None
    idle_jiffies = None
    try:
        parts = Path("/proc/stat").read_text(encoding="utf-8", errors="ignore").splitlines()[0].split()[1:]
        values = [int(part) for part in parts]
        total_jiffies = sum(values)
        idle_jiffies = values[3] + (values[4] if len(values) > 4 else 0)
    except Exception:
        pass
    return {"process_seconds": process_seconds, "total_jiffies": total_jiffies, "idle_jiffies": idle_jiffies}


def _read_diskstats() -> dict:
    totals = {"read_bytes": 0, "write_bytes": 0, "io_ms": 0, "weighted_io_ms": 0}
    try:
        for line in Path("/proc/diskstats").read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if name.startswith(("loop", "ram", "fd")):
                continue
            sectors_read = int(parts[5])
            sectors_written = int(parts[9])
            totals["read_bytes"] += sectors_read * 512
            totals["write_bytes"] += sectors_written * 512
            totals["io_ms"] += int(parts[12])
            totals["weighted_io_ms"] += int(parts[13])
    except Exception:
        return {}
    return totals


def collect_runtime_metrics(root: Optional[Path] = None) -> dict:
    global _LAST_SAMPLE
    now = time.monotonic()
    wall = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cpu = _read_cpu_times()
    diskstats = _read_diskstats()
    previous = _LAST_SAMPLE
    elapsed = max(0.001, now - float(previous.get("monotonic") or now)) if previous else None

    process_cpu_percent = None
    host_cpu_percent = None
    disk_rates = {}
    if previous and elapsed:
        cpu_count = max(1, os.cpu_count() or 1)
        process_delta = cpu["process_seconds"] - float(previous["cpu"].get("process_seconds") or 0)
        process_cpu_percent = round(max(0.0, process_delta / elapsed * 100 / cpu_count), 2)
        if cpu.get("total_jiffies") is not None and previous["cpu"].get("total_jiffies") is not None:
            total_delta = int(cpu["total_jiffies"]) - int(previous["cpu"]["total_jiffies"])
            idle_delta = int(cpu["idle_jiffies"]) - int(previous["cpu"]["idle_jiffies"])
            if total_delta > 0:
                host_cpu_percent = round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 2)
        if diskstats and previous.get("diskstats"):
            prev_disk = previous["diskstats"]
            read_delta = max(0, diskstats.get("read_bytes", 0) - prev_disk.get("read_bytes", 0))
            write_delta = max(0, diskstats.get("write_bytes", 0) - prev_disk.get("write_bytes", 0))
            weighted_delta = max(0, diskstats.get("weighted_io_ms", 0) - prev_disk.get("weighted_io_ms", 0))
            disk_rates = {
                "read_bytes_per_second": round(read_delta / elapsed, 2),
                "write_bytes_per_second": round(write_delta / elapsed, 2),
                "contention_percent": round(max(0.0, min(100.0, weighted_delta / (elapsed * 1000) * 100)), 2),
            }

    usage_root = Path(root or os.getcwd())
    disk = {}
    try:
        usage = shutil.disk_usage(usage_root)
        disk = {
            "path": str(usage_root),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": round((usage.used / usage.total) * 100, 2) if usage.total else None,
            **disk_rates,
        }
    except Exception:
        disk = dict(disk_rates)

    sample = {
        "collected_at": wall,
        "cpu": {
            "process_percent": process_cpu_percent,
            "host_percent": host_cpu_percent,
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "cpu_count": os.cpu_count(),
        },
        "memory": _read_meminfo(),
        "process": _read_process_memory(),
        "disk": disk,
        "diskstats": diskstats,
        "monotonic": now,
    }
    _LAST_SAMPLE = sample
    return {key: value for key, value in sample.items() if key not in {"monotonic", "diskstats"}}
