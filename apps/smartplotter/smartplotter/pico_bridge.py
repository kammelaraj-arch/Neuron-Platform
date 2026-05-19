"""Pi ↔ Pico 2 W UART bridge for SmartPlotter.

Talks newline-delimited JSON to the Pico's pi_uart (GP0/GP1) over the
Pi's /dev/ttyAMA0 (or /dev/serial0). Used by the Flask app to:
  - on boot, ping the Pico and ask which TMC chip is on each axis
    (so the UI can show "TMC2226 detected on X" instead of "DRV8825")
  - send live current / stealthchop / sgthresh changes from the UI
  - relay E-stop commands

Falls back to None if no Pico is wired (standalone Pi-only mode).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

try:
    import serial   # pyserial — already in requirements.txt
except ImportError:
    serial = None  # type: ignore[assignment]


_log = logging.getLogger("smartplotter.pico_bridge")

# Default Pi 5 serial path. Pi 4 used /dev/ttyAMA0; Pi 5 keeps the alias.
# Set SMARTPLOTTER_PICO_DEV to override.
import os
DEV     = os.environ.get("SMARTPLOTTER_PICO_DEV", "/dev/serial0")
BAUD    = int(os.environ.get("SMARTPLOTTER_PICO_BAUD", "115200"))
TIMEOUT = float(os.environ.get("SMARTPLOTTER_PICO_TIMEOUT", "2.0"))


class PicoBridge:
    """Thread-safe newline-delimited JSON link to the Pico."""

    def __init__(self, device: str = DEV, baud: int = BAUD,
                 timeout: float = TIMEOUT):
        self.device = device
        self.baud = baud
        self.timeout = timeout
        self._port: serial.Serial | None = None
        self._lock = threading.Lock()
        self.connected = False
        self.last_error: str | None = None
        # Cached info from the most recent info / boot event.
        self.drivers: list[dict[str, Any]] = []

    # ── lifecycle ─────────────────────────────────────────────────────
    def open(self) -> bool:
        if serial is None:
            self.last_error = "pyserial not installed"
            return False
        try:
            self._port = serial.Serial(
                self.device, self.baud, timeout=self.timeout,
                write_timeout=self.timeout,
            )
            self.connected = True
            # Drain anything in the Pico's TX queue (e.g. its boot event).
            time.sleep(0.2)
            while self._port.in_waiting:
                line = self._port.readline().decode("utf-8", "replace").strip()
                self._consume_event(line)
            return True
        except Exception as e:
            self.last_error = str(e)
            self._port = None
            self.connected = False
            return False

    def close(self) -> None:
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None
        self.connected = False

    # ── I/O helpers ───────────────────────────────────────────────────
    def _send(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.connected or self._port is None:
            return None
        line = json.dumps(payload) + "\n"
        with self._lock:
            try:
                self._port.reset_input_buffer()
                self._port.write(line.encode("utf-8"))
                reply = self._port.readline().decode("utf-8", "replace").strip()
                if not reply:
                    return None
                return json.loads(reply)
            except Exception as e:
                self.last_error = str(e)
                return None

    def _consume_event(self, line: str) -> None:
        if not line:
            return
        try:
            evt = json.loads(line)
        except Exception:
            return
        if "drivers" in evt:
            self.drivers = evt["drivers"]
        if evt.get("event") == "boot":
            _log.info("Pico booted, drivers=%s", evt.get("drivers"))

    # ── public API ────────────────────────────────────────────────────
    def ping(self) -> bool:
        return (self._send({"cmd": "ping"}) or {}).get("ack") == "pong"

    def info(self) -> list[dict[str, Any]]:
        reply = self._send({"cmd": "info"}) or {}
        if "drivers" in reply:
            self.drivers = reply["drivers"]
        return self.drivers

    def redetect(self) -> list[dict[str, Any]]:
        reply = self._send({"cmd": "redetect"}) or {}
        if "drivers" in reply:
            self.drivers = reply["drivers"]
        return self.drivers

    def set_current(self, axis: str, ma: int) -> bool:
        return bool(self._send({"cmd": "current", "axis": axis, "ma": ma}))

    def set_stealthchop(self, axis: str, on: bool) -> bool:
        return bool(self._send({"cmd": "stealthchop", "axis": axis, "on": on}))

    def set_sgthresh(self, axis: str, value: int) -> bool:
        return bool(self._send({"cmd": "sgthresh", "axis": axis, "value": value}))

    def estop(self) -> bool:
        return bool((self._send({"cmd": "estop"}) or {}).get("estopped"))

    def release(self) -> bool:
        reply = self._send({"cmd": "release"}) or {}
        return reply.get("estopped") is False


# Module-level singleton — Flask app imports + .open()s on startup.
_singleton: PicoBridge | None = None


def get_bridge() -> PicoBridge:
    global _singleton
    if _singleton is None:
        _singleton = PicoBridge()
        _singleton.open()
    return _singleton
