"""Pin-map resolution — three sources, in priority order:

    1. /boot/neuron/brain.json's `pinmap` array (shipped by the
       Neuron Platform's firmware bundle — the wizard's step 5 puts
       it there). Match by `signal_name` normalised to the keys we
       want (x_step, x_dir, y_step, y_dir, y2_step, y2_dir, z_step,
       z_dir, enable, limit_x, limit_y, limit_z, estop).

    2. Env-var overrides: SMARTPLOTTER_PIN_X_STEP=17 etc. Lets the
       operator tweak a single pin from /opt/smartplotter/env
       without rebuilding the bundle.

    3. Hard-coded defaults — the CNC Shield V3 / Pi wiring we ship
       with. Used when neither (1) nor (2) covers a pin.

The resolved map is logged at startup + surfaced on /api/pinmap so
the operator can verify what's actually wired. Re-uses the GpioMapping
rows the wizard already captures — no duplicate data entry."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path


log = logging.getLogger("smartplotter.pins")


# Default BCM pin map — CNC Shield V3 / Pi convention. Override any
# of these via brain.json (preferred) or env (fallback).
DEFAULT_PIN_MAP: dict[str, int] = {
    "x_step":  17, "x_dir":  27,
    "y_step":  22, "y_dir":  23,
    "y2_step": 24, "y2_dir": 25,
    "z_step":   5, "z_dir":   6,
    "enable":  12,
    # Limit + estop (consumed by safety.py, not motion.py)
    "limit_x": 16, "limit_y": 20, "limit_z": 21,
    "estop":   26,
}


# Map well-known signal_name spellings → our logical pin keys. Case-
# insensitive, hyphen/underscore-insensitive comparison.
_SIGNAL_TO_LOGICAL: dict[str, str] = {
    "x-step": "x_step",  "xstep":  "x_step", "x_step":  "x_step",
    "x-dir":  "x_dir",   "xdir":   "x_dir",  "x_dir":   "x_dir",
    "y-step": "y_step",  "ystep":  "y_step", "y_step":  "y_step",
    "y-dir":  "y_dir",   "ydir":   "y_dir",  "y_dir":   "y_dir",
    "y2-step":"y2_step", "y2step": "y2_step","y2_step": "y2_step",
    "y2-dir": "y2_dir",  "y2dir":  "y2_dir", "y2_dir":  "y2_dir",
    "z-step": "z_step",  "zstep":  "z_step", "z_step":  "z_step",
    "z-dir":  "z_dir",   "zdir":   "z_dir",  "z_dir":   "z_dir",
    "en":     "enable",  "enable": "enable", "driver-en": "enable",
    "x+":     "limit_x", "x-min":  "limit_x", "limit-x": "limit_x",
    "y+":     "limit_y", "y-min":  "limit_y", "limit-y": "limit_y",
    "z+":     "limit_z", "z-min":  "limit_z", "limit-z": "limit_z",
    "estop":  "estop",   "e-stop": "estop",   "emergency-stop": "estop",
}


def _normalise(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _bcm_from_compute_pin(label: str | None) -> int | None:
    """The Neuron Brain stores compute pins as their human label —
    'GPIO17', 'GP4', 'GPIO12_PWM0', etc. Extract the integer."""
    if not label:
        return None
    digits = "".join(ch for ch in label if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _load_from_brain_json(bundle_dir: Path) -> dict[str, int]:
    """Pull GpioMapping rows from brain.json → {logical_pin_name: bcm}.
    Returns {} when brain.json is missing or empty (e.g. dev-host
    workstation)."""
    p = bundle_dir / "brain.json"
    if not p.is_file():
        log.info("pins: no brain.json at %s — falling back to env/defaults", p)
        return {}
    try:
        doc = json.loads(p.read_text())
    except Exception as e:
        log.warning("pins: brain.json unreadable (%s) — falling back", e)
        return {}
    out: dict[str, int] = {}
    for row in doc.get("pinmap") or []:
        sig = (row.get("signal_name") or row.get("board_pin") or "").strip()
        if not sig:
            continue
        key = _SIGNAL_TO_LOGICAL.get(_normalise(sig))
        if key is None:
            continue
        bcm = _bcm_from_compute_pin(row.get("compute_pin"))
        if bcm is not None:
            out[key] = bcm
    if out:
        log.info("pins: loaded %d entries from %s: %s", len(out), p, out)
    return out


def _load_from_env() -> dict[str, int]:
    out: dict[str, int] = {}
    for key in DEFAULT_PIN_MAP:
        env_key = f"SMARTPLOTTER_PIN_{key.upper()}"
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        try:
            out[key] = int(raw)
        except ValueError:
            log.warning("pins: env %s=%r not an int — ignored", env_key, raw)
    if out:
        log.info("pins: env overrides: %s", out)
    return out


def resolve(bundle_dir: Path | str = "/boot/neuron") -> dict[str, int]:
    """Build the final BCM map: defaults ← brain.json ← env. Logs the
    full resolved map so the operator can see exactly what's wired."""
    bundle_dir = Path(bundle_dir)
    out = dict(DEFAULT_PIN_MAP)
    out.update(_load_from_brain_json(bundle_dir))
    out.update(_load_from_env())
    log.info("pins: resolved map (%d pins): %s",
             len(out), {k: v for k, v in sorted(out.items())})
    return out
