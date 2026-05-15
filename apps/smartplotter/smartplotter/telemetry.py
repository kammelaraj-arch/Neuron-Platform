"""Cheap, dependency-free telemetry samplers — CPU / memory / temp /
disk / uptime / network. Read from /proc + /sys, fall back to zeros
elsewhere so the dev host can run the agent."""
from __future__ import annotations

import os
import time
from pathlib import Path


def sample() -> dict:
    return {
        "ts": int(time.time()),
        "cpu_pct":     _cpu_pct(),
        "mem_pct":     _mem_pct(),
        "disk_pct":    _disk_pct("/"),
        "temp_c":      _temp_c(),
        "uptime_s":    _uptime_s(),
        "load_1":      _load_1(),
    }


def _cpu_pct() -> float:
    try:
        load = os.getloadavg()[0]
        nproc = os.cpu_count() or 1
        return round(load / nproc * 100, 1)
    except (AttributeError, OSError):
        return 0.0


def _mem_pct() -> float:
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
    except OSError:
        return 0.0
    total = avail = 0
    for line in lines:
        if line.startswith("MemTotal:"):
            total = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            avail = int(line.split()[1])
    return round((1 - avail / total) * 100, 1) if total else 0.0


def _disk_pct(path: str) -> float:
    try:
        s = os.statvfs(path)
    except OSError:
        return 0.0
    total = s.f_blocks * s.f_frsize
    free  = s.f_bavail * s.f_frsize
    return round((1 - free / total) * 100, 1) if total else 0.0


def _temp_c() -> float:
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(p.read_text()) / 1000.0, 1)
    except (OSError, ValueError):
        return 0.0


def _uptime_s() -> int:
    try:
        return int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError):
        return 0


def _load_1() -> float:
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return 0.0
