"""Telemetry samplers — runs every `sample_interval_seconds`, gathers
the ticked channels, returns a dict the safety engine evaluates against
and the agent publishes to MQTT under
    factory/<f>/<line>/<machine>/telemetry
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable


log = logging.getLogger("neuron_agent.telemetry")


def _cpu_pct() -> float:
    """1-minute load average, expressed as percent of nproc."""
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


def _disk_pct(path: str = "/") -> float:
    try:
        s = os.statvfs(path)
    except OSError:
        return 0.0
    total = s.f_blocks * s.f_frsize
    free = s.f_bavail * s.f_frsize
    return round((1 - free / total) * 100, 1) if total else 0.0


def _temperature_c() -> float:
    """Pi CPU temp from /sys. Falls back to 0 on non-Pi."""
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


def _network_rx_bytes(iface: str = "eth0") -> int:
    p = Path(f"/sys/class/net/{iface}/statistics/rx_bytes")
    if not p.is_file():
        # Try wlan0 instead — typical on Pi 5 / Zero W where eth0 is absent.
        p = Path("/sys/class/net/wlan0/statistics/rx_bytes")
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return 0


# A channel-name → sampler-fn registry. Adding a new channel is just
# adding a row here + ticking it in the wizard.
_SAMPLERS: dict[str, Callable[[], int | float]] = {
    "cpu":          _cpu_pct,
    "memory":       _mem_pct,
    "disk":         _disk_pct,
    "temperature":  _temperature_c,
    "uptime":       _uptime_s,
    "network":      _network_rx_bytes,
    # Industrial channels we can't sample directly — populated by the
    # SafetyEngine + RecipeRunner via Telemetry.record() at runtime.
    "cycle_count":  lambda: 0,
    "fault_state":  lambda: 0,
}


class Telemetry:
    def __init__(self, channels: list[str]) -> None:
        self.channels = [c for c in channels if c in _SAMPLERS]
        self._injected: dict[str, int | float | str] = {}

    def record(self, channel: str, value) -> None:
        """Allow other components (recipe runner, safety, alarms) to
        push values into a channel — overrides the static sampler."""
        self._injected[channel] = value

    def snapshot(self) -> dict[str, int | float | str]:
        out: dict[str, int | float | str] = {}
        for c in self.channels:
            if c in self._injected:
                out[c] = self._injected[c]
            else:
                try:
                    out[c] = _SAMPLERS[c]()
                except Exception as e:
                    log.warning("sampler %s failed: %s", c, e)
                    out[c] = 0
        out["ts"] = int(time.time())
        return out
