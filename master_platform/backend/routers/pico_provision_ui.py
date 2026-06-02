"""Pico 2 W first-time provisioning page.

Walks an operator through:
  1. Flashing MicroPython on the Pico (BOOTSEL → drag .uf2)
  2. Downloading a per-group bundle (device DNA + WiFi + brain shell
     + TMC firmware files) as a single .zip
  3. Pushing the bundle onto the Pico with mpremote

The bundle is generated server-side from the group's existing config:
  - device_dna           → group.device_dna or freshly generated
  - wifi credentials     → group.primary_wifi_id (decrypted via Fernet)
  - brain shell          → group.brain_json or stub
  - mqtt parent URL      → settings.MQTT_BROKER_URL
  - safety + comms       → channels.json baked into bundle

Pico has no SSH, so unlike the Pi 5 flow there's no "push from VPS"
step — the operator's laptop is the conduit, USB-cabled to the Pico.
"""
from __future__ import annotations

import io
import json
import secrets as _secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, EdgeGroup, WifiNetwork
from ..security.secret_crypto import decrypt_secret
from ..security.ui_auth import ui_require_login


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))
_PICO_FW_DIR = _BASE.parent.parent / "apps" / "smartplotter" / "pico_firmware"

router = APIRouter(tags=["ui-pico-provision"])


# Latest MicroPython release for Pico 2 W. Pinned for reproducible
# builds; bump when a newer .uf2 is verified compatible with the
# SmartPlotter Pico firmware.
MICROPYTHON_UF2_URL = (
    "https://micropython.org/resources/firmware/"
    "RPI_PICO2_W-20250911-v1.26.1.uf2"
)
MICROPYTHON_VERSION = "v1.26.1 (2025-09-11)"


async def _load_group(session: AsyncSession, group_id: str) -> EdgeGroup:
    g = await session.get(EdgeGroup, group_id)
    if g is None:
        raise HTTPException(404, "group not found")
    return g


async def _resolve_wifi(session: AsyncSession,
                        wifi_id: str | None) -> dict | None:
    if not wifi_id:
        return None
    w = await session.get(WifiNetwork, wifi_id)
    if w is None:
        return None
    pw = decrypt_secret(w.password_encrypted) if w.password_encrypted else None
    return {
        "ssid": w.ssid,
        "security": w.security,
        "username": w.username,
        "password": pw,
        "hidden": w.hidden,
        "country_code": w.country_code or "GB",
    }


def _device_dna(group: EdgeGroup) -> str:
    """Return the group's device DNA, generating one if missing.

    Stable per-group identifier baked into the firmware so the master
    can recognise the Pico across re-flashes."""
    if group.device_dna:
        return group.device_dna
    return f"pico-{group.id[:8]}-{_secrets.token_hex(4)}"


def _build_brain_shell(group: EdgeGroup) -> dict:
    """Minimal brain.json the Pico firmware reads at boot."""
    return group.brain_json or {
        "version": "1.0.0",
        "axes": ["x", "y", "z"],
        "limits": {
            "max_x_mm": 300, "max_y_mm": 300, "max_z_mm": 50,
            "max_feed_mm_s": 50,
        },
        "safety": {
            "estop_pin": 5,
            "limit_pins": {"x": 2, "y": 3, "z": 4},
            "drv_en_pin": 12,
            "watchdog_ms": 1000,
            "disconnect_grace_s": 5,
            "default_failsafe": "stop",
        },
        "tmc_uart_pin": 4,
        "tmc_addresses": {"x": 0, "y": 1, "z": 2},
    }


def _build_channels(group: EdgeGroup, dna: str) -> dict:
    """Communication-channel manifest per CLAUDE.md (control + emergency + OTA).

    Every bundle MUST include all three by default, no operator opt-in."""
    base_topic = f"neuron/{dna}"
    return {
        "control": {
            "transport": "mtls",
            "broker_host": "neuron.shital.org.uk",
            "broker_port": 8883,
            "client_id": f"{dna}-control",
            "subscribe": [f"{base_topic}/cmd/#"],
            "publish":   [f"{base_topic}/telem/#",
                          f"{base_topic}/state/#"],
            "keepalive_s": 30,
            "qos": 1,
        },
        "emergency": {
            "transport": "mtls",
            "broker_host": "neuron.shital.org.uk",
            "broker_port": 8884,
            "client_id": f"{dna}-emerg",
            "subscribe": [f"{base_topic}/emerg/#"],
            "publish":   [f"{base_topic}/emerg/ack"],
            "allowed_commands": ["safe_stop", "safe_shutdown", "status"],
            "keepalive_s": 10,
            "qos": 2,
        },
        "ota": {
            "transport": "https",
            "endpoint": "https://neuron.shital.org.uk/api/ota/poll",
            "poll_interval_s": 3600,
            "base_version_gating": True,
        },
        "uart_local": {
            "transport": "uart",
            "device": "/dev/serial0",
            "baud": 115200,
            "role": "parent-pi-link",
            "notes": "Pi 5 controller talks to Pico over GP0/GP1.",
        },
    }


# ── pages ────────────────────────────────────────────────────────────
@router.get(
    "/ui/devices/wizard/{group_id}/pico-setup",
    response_class=HTMLResponse,
)
async def pico_setup_page(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    wifi = await _resolve_wifi(session, group.primary_wifi_id)
    dna = _device_dna(group)
    return templates.TemplateResponse(
        "pico_setup.html",
        {
            "request": request,
            "group": group,
            "wifi": wifi,
            "dna": dna,
            "micropython_url": MICROPYTHON_UF2_URL,
            "micropython_version": MICROPYTHON_VERSION,
        },
    )


@router.get("/ui/devices/wizard/{group_id}/pico-bundle.zip")
async def pico_bundle_zip(
    group_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    wifi = await _resolve_wifi(session, group.primary_wifi_id)
    dna = _device_dna(group)
    brain = _build_brain_shell(group)
    channels = _build_channels(group, dna)

    # Read the firmware source files from the repo.
    main_py = (_PICO_FW_DIR / "main.py").read_text(encoding="utf-8")
    tmc_py = (_PICO_FW_DIR / "tmc.py").read_text(encoding="utf-8")

    config = {
        "device_dna": dna,
        "group_id": group.id,
        "group_name": group.name,
        "compute": group.compute_stable_id,
        "hardware_revision": group.hardware_revision,
        "base_firmware_version": group.base_firmware_version,
        "wifi": wifi,
        "brain": brain,
        "channels": channels,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # boot.py — runs on every reset, connects WiFi, then chains main.py.
    boot_py = _BOOT_PY_TEMPLATE

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("boot.py", boot_py)
        z.writestr("main.py", main_py)
        z.writestr("tmc.py", tmc_py)
        z.writestr("config.json", json.dumps(config, indent=2))
        z.writestr("README.txt", _BUNDLE_README.format(
            dna=dna, group_name=group.name,
            wifi=wifi["ssid"] if wifi else "(none configured)",
        ))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="pico-bundle-{dna}.zip"',
        },
    )


_BOOT_PY_TEMPLATE = """# Auto-generated by Neuron Master — runs at every Pico reset.
# Loads config.json, brings up WiFi, then hands off to main.py.

import json
import time

try:
    import network
except ImportError:
    network = None

try:
    with open("config.json", "r") as f:
        _cfg = json.load(f)
except OSError:
    print("[boot] config.json missing — running in degraded local-only mode")
    _cfg = {}

print(f"[boot] DNA={_cfg.get('device_dna', '<unset>')}")

_wifi = _cfg.get("wifi") or {}
if network is not None and _wifi.get("ssid"):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if _wifi.get("country_code"):
        try:
            network.country(_wifi["country_code"])
        except Exception:
            pass
    print(f"[boot] connecting to WiFi '{_wifi['ssid']}'...")
    wlan.connect(_wifi["ssid"], _wifi.get("password") or "")
    for _ in range(20):
        if wlan.isconnected():
            print(f"[boot] WiFi up: {wlan.ifconfig()}")
            break
        time.sleep(0.5)
    else:
        print("[boot] WiFi timed out — continuing anyway (local UART still works)")
else:
    print("[boot] no WiFi configured — UART-only mode")

# main.py takes over from here.
"""

_BUNDLE_README = """Pico 2 W bundle for group: {group_name}
Device DNA: {dna}
WiFi: {wifi}

Files in this zip:
  boot.py       — connects WiFi at boot, reads config.json
  main.py       — SmartPlotter firmware entry point (UART ↔ Pi, TMC bus)
  tmc.py        — TMC2208/2209/2226 driver class
  config.json   — device DNA + WiFi + brain + comms channels

To flash (laptop USB-cabled to the Pico):
  1. Make sure MicroPython is already on the Pico
     (hold BOOTSEL, plug USB, drag the .uf2 file)
  2. pip install --user mpremote
  3. cd into this unzipped directory
  4. mpremote connect auto cp boot.py main.py tmc.py config.json :
  5. mpremote connect auto reset

After reset the Pico will:
  - bring up WiFi from config.json
  - scan the TMC bus + autodetect drivers
  - announce itself to the Pi on GP0/GP1 (UART)
  - hold motors disabled until parent releases EN

Bundle generated by Neuron Master. Regenerate any time the WiFi
password, brain, or pin map changes — the file name includes the
device DNA so multiple Picos don't get cross-pollinated.
"""
