"""Runtime config — all env-backed so the systemd unit (or compose
file) is the single source of truth, no code edits required to change
behaviour on a deployed device."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    return os.environ.get(key, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # ── Identity (matches the Neuron firmware bundle) ────────────────
    device_dna:  str
    bundle_dir:  Path
    api_key:     str | None
    # ── Motion limits (mechanical safety envelope) ───────────────────
    max_x_mm:    float
    max_y_mm:    float
    max_z_mm:    float
    steps_per_mm:   float
    max_feed_mm_s:  float       # max linear feed-rate
    homing_feed_mm_s: float
    accel_mm_s2: float
    # ── GPIO pins ────────────────────────────────────────────────────
    estop_bcm:   int            # E-stop NC contact (active LOW = pressed)
    limit_x_bcm: int
    limit_y_bcm: int
    limit_z_bcm: int
    # ── MQTT to parent / master ──────────────────────────────────────
    broker_url:  str            # mqtts://master:8883 (mTLS) — empty = MQTT disabled
    certs_dir:   Path           # /boot/neuron/certs (device.crt + key + ca.crt)
    factory:     str
    line:        str
    machine:     str
    # ── Watchdog ─────────────────────────────────────────────────────
    parent_grace_s: int         # parent heartbeat silence before pause/abort
    # ── HTTP UI ──────────────────────────────────────────────────────
    bind_host:   str
    bind_port:   int
    upload_max_mb: int


def load() -> Settings:
    """Build a frozen Settings from the environment. Called once at
    process start; SmartPlotter never re-reads config at runtime."""
    return Settings(
        device_dna       = os.environ.get("SMARTPLOTTER_DEVICE_DNA", "DNA-LOCAL-DEV"),
        bundle_dir       = Path(os.environ.get("SMARTPLOTTER_BUNDLE_DIR", "/boot/neuron")),
        api_key          = os.environ.get("SMARTPLOTTER_API_KEY") or None,
        max_x_mm         = _env_float("SMARTPLOTTER_MAX_X_MM", 300.0),
        max_y_mm         = _env_float("SMARTPLOTTER_MAX_Y_MM", 300.0),
        max_z_mm         = _env_float("SMARTPLOTTER_MAX_Z_MM",  50.0),
        steps_per_mm     = _env_float("SMARTPLOTTER_STEPS_PER_MM", 80.0),
        max_feed_mm_s    = _env_float("SMARTPLOTTER_MAX_FEED_MM_S",   50.0),
        homing_feed_mm_s = _env_float("SMARTPLOTTER_HOMING_FEED",     10.0),
        accel_mm_s2      = _env_float("SMARTPLOTTER_ACCEL_MM_S2",    400.0),
        estop_bcm        = _env_int  ("SMARTPLOTTER_ESTOP_BCM", 26),
        limit_x_bcm      = _env_int  ("SMARTPLOTTER_LIMIT_X_BCM", 16),
        limit_y_bcm      = _env_int  ("SMARTPLOTTER_LIMIT_Y_BCM", 20),
        limit_z_bcm      = _env_int  ("SMARTPLOTTER_LIMIT_Z_BCM", 21),
        broker_url       = os.environ.get("SMARTPLOTTER_BROKER_URL", ""),
        certs_dir        = Path(os.environ.get("SMARTPLOTTER_CERTS_DIR", "/boot/neuron/certs")),
        factory          = os.environ.get("SMARTPLOTTER_FACTORY", "default"),
        line             = os.environ.get("SMARTPLOTTER_LINE",    "default"),
        machine          = os.environ.get("SMARTPLOTTER_MACHINE", "smartplotter"),
        parent_grace_s   = _env_int  ("SMARTPLOTTER_PARENT_GRACE_S", 5),
        bind_host        = os.environ.get("SMARTPLOTTER_BIND_HOST", "0.0.0.0"),
        bind_port        = _env_int  ("SMARTPLOTTER_BIND_PORT", 5000),
        upload_max_mb    = _env_int  ("SMARTPLOTTER_UPLOAD_MAX_MB", 10),
    )


# Topic helpers — same MQTT topic structure the Neuron platform uses.
def topic_telemetry(s: Settings) -> str:
    return f"factory/{s.factory}/{s.line}/{s.machine}/telemetry"

def topic_alerts(s: Settings) -> str:
    return f"factory/{s.factory}/{s.line}/{s.machine}/alerts"

def topic_emergency(s: Settings) -> str:
    return f"emergency/{s.device_dna}"

def topic_cmd(s: Settings) -> str:
    return f"cmd/{s.device_dna}"
