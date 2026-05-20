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

import hashlib
import io
import json
import socket
import ssl
import secrets as _secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, EdgeGroup, WiFiNetwork
from ..security.audit import record
from ..security.keys import issue_payload
from ..security.secret_crypto import decrypt_secret
from ..security.ui_auth import ui_require_login


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))
_PICO_FW_DIR = _BASE.parent.parent / "apps" / "smartplotter" / "pico_firmware"
_BASELINE_DIR = _BASE.parent.parent / "apps" / "neuron-pico-baseline"

router = APIRouter(tags=["ui-pico-provision"])


# Configurable via env. Default matches CLAUDE.md.
import os
NEURON_HOST = os.environ.get("NEURON_PUBLIC_HOST", "neuron.shital.org.uk")
NEURON_HTTPS_PORT = int(os.environ.get("NEURON_PUBLIC_PORT", "443"))


def _fetch_server_cert_sha256(host: str, port: int) -> str:
    """Return the SHA-256 fingerprint of the host's TLS server cert.

    Used to pin the cert into every Pico bundle so a rogue CA / MITM
    can't impersonate the master."""
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return hashlib.sha256(der).hexdigest()


async def _issue_pico_api_key(
    session: AsyncSession, group: EdgeGroup, dna: str, actor_id: str,
) -> str:
    """Mint a fresh per-Pico API key (tier=pico) and return the plaintext.

    Revokes any prior pico key bound to the same DNA so re-provisioning
    is safe (no key proliferation in the DB)."""
    # Revoke any prior key for this DNA so re-flashing rotates cleanly.
    prior = (await session.execute(
        select(APIKey).where(APIKey.owner == f"pico:{dna}",
                             APIKey.status == "active")
    )).scalars().all()
    for k in prior:
        k.status = "revoked"

    secret, kw = issue_payload(
        label=f"pico {group.name} ({dna})",
        owner=f"pico:{dna}",
        tier="pico",
        scopes=[],
        rate_per_minute=300,
        rate_burst=60,
        ttl_days=None,
    )
    new_key = APIKey(**kw)
    session.add(new_key)
    await session.flush()
    await record(
        session, actor=actor_id, actor_kind="ui_session",
        action="apikey.issue_pico", target_kind="apikey",
        target_id=new_key.id,
        detail={"dna": dna, "group_id": group.id, "tier": "pico"},
    )
    return secret


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
    w = await session.get(WiFiNetwork, wifi_id)
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

    # Per-Pico API key with tier=pico (narrowly scoped) — owner encodes DNA.
    api_secret = await _issue_pico_api_key(session, group, dna, actor.id)

    # Pin the master's TLS cert into the bundle so the Pico refuses to
    # speak to any TLS server with a different fingerprint.
    try:
        cert_fp = _fetch_server_cert_sha256(NEURON_HOST, NEURON_HTTPS_PORT)
    except Exception as e:
        # Don't block provisioning if cert fetch fails; pin is empty and
        # the Pico will refuse all HTTPS until re-provisioned. Operator
        # can still test UART + emergency local control.
        cert_fp = ""

    # Read app layer (Layer 2) — SmartPlotter Pico firmware.
    app_main = (_PICO_FW_DIR / "main.py").read_text(encoding="utf-8")
    app_tmc  = (_PICO_FW_DIR / "tmc.py").read_text(encoding="utf-8")
    # Read baseline (Layer 1) — Neuron platform primitives.
    baseline_files = {
        "boot.py":              (_BASELINE_DIR / "boot.py").read_text(encoding="utf-8"),
        "neuron/__init__.py":   (_BASELINE_DIR / "neuron" / "__init__.py").read_text(encoding="utf-8"),
        "neuron/config.py":     (_BASELINE_DIR / "neuron" / "config.py").read_text(encoding="utf-8"),
        "neuron/identity.py":   (_BASELINE_DIR / "neuron" / "identity.py").read_text(encoding="utf-8"),
        "neuron/security.py":   (_BASELINE_DIR / "neuron" / "security.py").read_text(encoding="utf-8"),
        "neuron/api.py":        (_BASELINE_DIR / "neuron" / "api.py").read_text(encoding="utf-8"),
        "neuron/channels.py":   (_BASELINE_DIR / "neuron" / "channels.py").read_text(encoding="utf-8"),
        "neuron/watchdog.py":   (_BASELINE_DIR / "neuron" / "watchdog.py").read_text(encoding="utf-8"),
        "neuron/failsafe.py":   (_BASELINE_DIR / "neuron" / "failsafe.py").read_text(encoding="utf-8"),
    }

    config = {
        "schema_version":     "1",
        "device_dna":         dna,
        "group_id":           group.id,
        "group_name":         group.name,
        "compute":            group.compute_stable_id,
        "hardware_revision":  group.hardware_revision,
        "base_firmware_version": group.base_firmware_version,
        "api_key":            api_secret,
        "allowed_hosts":      [NEURON_HOST],
        "tls_cert_sha256":    cert_fp,
        "wifi":               wifi,
        "brain":              brain,
        "channels":           channels,
        "generated_at":       datetime.now(timezone.utc).isoformat(),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # Layer 1 — baseline
        for path, content in baseline_files.items():
            z.writestr(path, content)
        # Layer 2 — app
        z.writestr("app/__init__.py", "")
        z.writestr("app/main.py", app_main)
        z.writestr("app/tmc.py",  app_tmc)
        # Per-Pico config
        z.writestr("config.json", json.dumps(config, indent=2))
        z.writestr("README.txt", _BUNDLE_README.format(
            dna=dna, group_name=group.name,
            wifi=wifi["ssid"] if wifi else "(none configured)",
            cert_fp=cert_fp[:16] + "…" if cert_fp else "(NOT PINNED — re-provision once VPS reachable)",
        ))
    buf.seek(0)
    await session.commit()
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

_BUNDLE_README = """Pico 2 W bundle — {group_name}
Device DNA: {dna}
WiFi:       {wifi}
Cert pin:   SHA-256 {cert_fp}

Layered firmware:

  Layer 0  MicroPython runtime           (you flash this once via BOOTSEL)
  Layer 1  Neuron baseline               (boot.py + neuron/*.py)
  Layer 2  SmartPlotter app              (app/main.py + app/tmc.py)
  Config   Per-Pico identity + creds     (config.json)

Layer 1 enforces (for ALL apps, not just SmartPlotter):
  - host allow-list — only talks to the master, refuses anywhere else
  - TLS cert pinning — refuses any server cert with a different SHA-256
  - per-Pico API key — narrow tier=pico scope, revocable from master
  - parent-Pi watchdog — autonomous failsafe on link loss
  - 4 comms channels: control, emergency, OTA, UART (local)

To flash (laptop USB-cabled to the Pico):

  1. Make sure MicroPython is already on the Pico
     (hold BOOTSEL, plug USB, drag the .uf2 from micropython.org)
  2. pip install --user mpremote
  3. cd into the unzipped directory
  4. mpremote connect auto fs cp -r . :
  5. mpremote connect auto reset

After reset the Pico will:
  - Validate config.json (refuses to boot if missing or malformed)
  - Bring up WiFi
  - Construct identity + allow-list + cert-pinner + API client
  - Hand off to app.main()
  - Hold motors disabled until parent issues 'release' over UART

Re-provision any time WiFi password, brain, or pin map changes — this
also rotates the API key (old one is revoked server-side).
"""
