"""FR-0005 step 6 — multi-board firmware bundle builder for an EdgeGroup.

Generates a deterministic .zip containing:
  dna.json     immutable identity + capability record for the group
  brain.json   control loops + per-instance interlocks + safety + channels
  wifi.json    primary + secondary WiFi profiles (passwords decrypted at
                build time, NEVER persisted in plaintext outside the bundle)
  manifest.json bundle metadata (sha256 per artifact + bundle-level sha)

The Brain encodes the four durable channel-contract rules from CLAUDE.md:

  1. Command-and-control channel (parent ↔ child) over mTLS — recipe
     commands, parameter updates, telemetry uploads. Parent endpoint
     is the immediate parent in the hierarchy:
       Device's parent = Edge.address  (always required)
       Edge's parent   = Node.address if present else Root URL
       Node's parent   = Root URL

  2. Emergency channel — separate mTLS cert, strictly-limited command
     set (safe_stop / safe_shutdown / status). Always reachable.

  3. OTA channel — base-firmware / app-bundle / config updates.
     Subject to OTA base-version gating (see docs/ota_base_version_policy.md).

  4. Heartbeat — bidirectional:
        child → parent  every heartbeat_ms (default 1000ms)
        parent → child  every heartbeat_ms (default 1000ms)
     Child marks the parent "lost" after disconnect_grace_seconds (from
     ComponentInstance.disconnect_grace_seconds — defaults to 30s) and
     enforces failsafe_action autonomously.

Plus per-instance interlocks derived from ComponentInstance:
   risk_level / risk_types_json / failsafe_action / failsafe_value_json
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import (
    BoardInstance,
    ComponentInstance,
    DriverInstance,
    EdgeGroup,
    EdgeSystem,
    GpioMapping,
    NodeSystem,
    RootSystem,
    VendorAccount,
    WifiNetwork,
)
from .security.secret_crypto import decrypt_secret


_BUNDLE_VERSION = "1.0.0"


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _det_dumps(obj: Any) -> bytes:
    """Deterministic JSON serialisation (sorted keys, fixed separators) so
    the same inputs always produce a byte-identical bundle (required for
    reproducible OTA + signed-bundle workflows)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _short_dna() -> str:
    """e.g. DNA-A1B2-C3D4-E5F6-G7H8"""
    h = uuid.uuid4().hex.upper()
    return f"DNA-{h[:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


# ─── DNA ─────────────────────────────────────────────────────────────────────
def _build_dna(group: EdgeGroup, boards: list[tuple[BoardInstance, list[ComponentInstance], list[GpioMapping]]]) -> dict:
    dna_id = group.device_dna or _short_dna()
    components_flat: list[dict] = []
    drivers_flat: list[dict] = []
    for board, comps, _pins, drvs in boards:
        for d in drvs:
            drivers_flat.append({
                "instance_id": d.instance_id,
                "driver_stable_id": d.driver_stable_id,
                "board_instance_id": board.id,
                "board_slot": d.board_slot,
                "asset_id": d.asset_id,
                "label": d.label,
            })
        for c in comps:
            components_flat.append({
                "instance_id": c.instance_id,
                "component_stable_id": c.component_stable_id,
                "board_instance_id": board.id,
                "board_pin": c.board_pin,
                "function_label": c.function_label,
                "asset_id": c.asset_id,
            })

    payload = {
        "device_dna": dna_id,
        "schema_version": "1.0.0",
        "group_id": group.id,
        "group_name": group.name,
        "role": group.role or "edge",
        "asset_id": group.asset_id,
        "location": {
            "factory": group.factory,
            "line": group.line,
            "machine": group.machine,
        },
        "parent": {
            "kind": group.parent_kind or "edge",
            "id":   group.parent_id   or group.edge_id,
        },
        "compute_stable_id": group.compute_stable_id,
        "hardware_revision": group.hardware_revision,
        "base_firmware_version": group.base_firmware_version,
        "app_bundle_version": group.app_bundle_version,
        "config_schema_version": group.config_schema_version,
        "boards": [
            {"id": b.id, "stable_id": b.board_stable_id, "label": b.label}
            for b, _c, _p, _d in boards
        ],
        "components": components_flat,
        "drivers": drivers_flat,
        "issued_at": _utc_iso(),
    }
    payload["fingerprint_sha256"] = _sha256(_det_dumps({k: v for k, v in payload.items() if k != "fingerprint_sha256"}))
    return payload


# ─── Brain ───────────────────────────────────────────────────────────────────
def _build_channels(parent_url: str | None, device_dna: str) -> dict:
    """The three durable channels every device gets by default."""
    return {
        "command_and_control": {
            "type": "mtls",
            "parent_url": parent_url,
            "topics": {
                "inbound":  f"cmd/{device_dna}",
                "outbound": f"twin/{device_dna}/reported",
            },
            "qos": 1,
            "heartbeat_ms": 1000,
        },
        "emergency": {
            "type": "mtls",
            "parent_url": parent_url,
            "cert_id": "emergency",   # separate cert distinct from main C&C
            "allowed_commands": ["safe_stop", "safe_shutdown", "status"],
            "topic": f"emergency/{device_dna}",
            "qos": 2,
            "always_reachable": True,
        },
        "ota": {
            "type": "https",
            "parent_url": parent_url,
            "manifest_path": f"/api/ota/manifest?dna={device_dna}",
            "check_interval_seconds": 3600,
            "base_version_gating": True,
        },
    }


def _build_interlocks_from_components(comps: list[ComponentInstance]) -> list[dict]:
    """Translate per-ComponentInstance risk + failsafe metadata into Brain
    interlocks the local brain enforces autonomously when disconnected."""
    interlocks: list[dict] = []
    for c in comps:
        # Sensors with alarm_only or no failsafe → skip (no actuator to drive).
        if not c.failsafe_action and c.risk_level == "nominal":
            continue
        rule = {
            "id": f"{c.instance_id}_failsafe",
            "trigger": "parent_link_lost",
            "applies_to_instance": c.instance_id,
            "applies_to_board": c.board_instance_id,
            "board_pin": c.board_pin,
            "function_label": c.function_label,
            "risk_level": c.risk_level,
            "risk_types": c.risk_types_json or [],
            "action": c.failsafe_action or ("alarm_only" if c.risk_level in ("advisory",) else "off"),
            "value": c.failsafe_value_json,
            "grace_seconds": int(c.disconnect_grace_seconds or 30),
            "watchdog_ms": int(c.watchdog_ms or 1000),
        }
        interlocks.append(rule)
    return interlocks


def _build_brain(
    group: EdgeGroup,
    boards: list[tuple[BoardInstance, list[ComponentInstance], list[GpioMapping]]],
    parent_url: str | None,
    device_dna: str,
) -> dict:
    all_comps = [c for _b, comps, _p, _d in boards for c in comps]
    interlocks = _build_interlocks_from_components(all_comps)

    pinmap_flat = []
    for _b, _c, pins, _d in boards:
        for m in pins:
            pinmap_flat.append({
                "board_instance_id": m.board_instance_id,
                "compute_pin": m.compute_pin,
                "board_pin": m.board_pin,
                "signal_name": m.signal_name,
                "direction": m.direction,
                "locked": m.locked_by_operator,
            })

    return {
        "device_dna": device_dna,
        "brain_version": "1.0.0",
        "config_schema_version": group.config_schema_version,
        "compute_stable_id": group.compute_stable_id,
        "role": group.role or "edge",
        "asset_id": group.asset_id,
        "location": {
            "factory": group.factory,
            "line": group.line,
            "machine": group.machine,
        },
        "pinmap": pinmap_flat,
        "drivers": [
            {
                "instance_id": d.instance_id,
                "driver_stable_id": d.driver_stable_id,
                "board_instance_id": _b.id,
                "board_slot": d.board_slot,
                "asset_id": d.asset_id,
                "label": d.label,
            }
            for _b, _c, _p, drvs in boards for d in drvs
        ],
        "channels": _build_channels(parent_url, device_dna),
        # Parent-only communication contract (CLAUDE.md hard rule).
        # Firmware first-boot script enforces this via nftables.
        "network_policy": {
            "inbound_policy": "deny",       # deny-all on input chain
            "allow_loopback": True,
            "allow_established_outbound": True,
            "parent_only": True,
            "ssh_enabled_at_boot": False,   # parent reverse-tunnels SSH on demand
            "open_listeners_allowed": False,
        },
        "heartbeat": {
            "child_to_parent_ms": 1000,
            "parent_to_child_ms": 1000,
            "default_disconnect_grace_seconds": 30,
            "default_watchdog_ms": 1000,
            "bidirectional": True,
        },
        "safety": {
            "interlocks": interlocks,
            # Operator-defined IF→THEN local rules. Critical=true means
            # enforced on-device even when the parent link is up (parent
            # is advisory for these).
            "rules": list(group.rules_json or []),
            "watchdog_ms": 1000,
            "default_failsafe_on_parent_loss": "off",
        },
        "protocols": dict(group.protocols_json or {}),
        "telemetry": dict(group.telemetry_json or {}),
        "ntp_servers": list(group.ntp_servers_json or ["pool.ntp.org"]),
        "bluetooth_enabled": bool(group.bluetooth_enabled),
        "state_change_push": {
            "enabled": True,
            "endpoint": (parent_url or "") + "/api/state-change",
            "queue_on_disconnect": True,
            "replay_on_reconnect": True,
        },
        "offline_policy": {
            "mode": "continue_safe",
            "max_offline_seconds": 3600,
            "buffer_size": 8192,
            "local_decision": True,
        },
        "generated_at": _utc_iso(),
    }


# ─── WiFi (decrypted only at build time) ─────────────────────────────────────
def _wifi_payload(primary: WifiNetwork | None, secondary: WifiNetwork | None) -> dict:
    def serialize(w: WifiNetwork | None, role: str) -> dict | None:
        if w is None:
            return None
        pw = decrypt_secret(w.password_encrypted) if w.password_encrypted else None
        return {
            "role": role,
            "name": w.name,
            "ssid": w.ssid,
            "security": w.security,
            "username": w.username,
            "password": pw,
            "hidden": w.hidden,
            "country_code": w.country_code,
        }
    return {
        "schema_version": "1.0.0",
        "primary": serialize(primary, "primary"),
        "secondary": serialize(secondary, "secondary"),
    }


async def _vendor_accounts_payload(
    session: AsyncSession,
    boards: list,
) -> dict:
    """Collect every distinct VendorAccount referenced by a
    ComponentInstance in this group and emit a decrypted snapshot for
    the on-device agent. Plaintext only appears in this function — at
    rest in the DB the password / api_key / refresh_token are Fernet-
    encrypted, and the resulting .zip should be transported only over
    the mTLS deploy channel.

    Output shape (per account):
        {
          id, provider, label, username, region, base_url,
          password, api_key, refresh_token,
          components: ["instance_id", …]   # which on-device instances use it
        }
    """
    by_id: dict[str, dict] = {}
    for _b, comps, _p, _d in boards:
        for c in comps:
            if not c.vendor_account_id:
                continue
            entry = by_id.get(c.vendor_account_id)
            if entry is None:
                row = await session.get(VendorAccount, c.vendor_account_id)
                if row is None or row.status != "active":
                    continue
                entry = {
                    "id": row.id,
                    "provider": row.provider,
                    "label": row.label,
                    "username": row.username,
                    "region": row.region,
                    "base_url": row.base_url,
                    "password": decrypt_secret(row.password_encrypted) if row.password_encrypted else None,
                    "api_key":  decrypt_secret(row.api_key_encrypted)  if row.api_key_encrypted  else None,
                    "refresh_token": decrypt_secret(row.refresh_token_encrypted) if row.refresh_token_encrypted else None,
                    "components": [],
                }
                by_id[row.id] = entry
            entry["components"].append(c.instance_id)
    return {
        "schema_version": "1.0.0",
        "accounts": list(by_id.values()),
    }



# ─── Parent URL derivation (Master vs Node vs Edge hierarchy) ───────────────
async def _derive_parent_url(session: AsyncSession, group: EdgeGroup) -> str | None:
    """Resolve the URL the device dials home to. The Group's parent is
    given by (parent_kind, parent_id); only Edge currently carries an
    .address column, so Node/Root parents return None (caller substitutes
    the Master URL)."""
    pk = group.parent_kind or "edge"
    pid = group.parent_id or group.edge_id   # legacy fallback
    if not pid:
        return None
    if pk == "edge":
        edge = await session.get(EdgeSystem, pid)
        if edge is not None and edge.address:
            return edge.address.rstrip("/")
        return None
    if pk == "node":
        # Node has no .address column today; caller substitutes Master URL.
        return None
    if pk == "root":
        # Master itself — no upstream parent.
        return None
    return None


# ─── Public entry ────────────────────────────────────────────────────────────
async def build_group_bundle(
    session: AsyncSession,
    group: EdgeGroup,
    artifacts_dir: Path,
) -> tuple[Path, dict, dict]:
    """Assemble the full bundle for an EdgeGroup. Returns
    (bundle_path, dna_dict, brain_dict)."""
    # Pull everything we need in one go.
    boards_q = (
        await session.execute(
            select(BoardInstance).where(BoardInstance.group_id == group.id)
            .order_by(BoardInstance.position)
        )
    ).scalars().all()

    boards: list[tuple[BoardInstance, list[ComponentInstance], list[GpioMapping]]] = []
    for b in boards_q:
        comps = (
            await session.execute(
                select(ComponentInstance).where(ComponentInstance.board_instance_id == b.id)
                .order_by(ComponentInstance.position)
            )
        ).scalars().all()
        pins = (
            await session.execute(
                select(GpioMapping).where(GpioMapping.board_instance_id == b.id)
                .order_by(GpioMapping.compute_pin)
            )
        ).scalars().all()
        drivers = (
            await session.execute(
                select(DriverInstance).where(DriverInstance.board_instance_id == b.id)
                .order_by(DriverInstance.position)
            )
        ).scalars().all()
        boards.append((b, list(comps), list(pins), list(drivers)))

    primary = await session.get(WifiNetwork, group.primary_wifi_id) if group.primary_wifi_id else None
    secondary = await session.get(WifiNetwork, group.secondary_wifi_id) if group.secondary_wifi_id else None

    parent_url = await _derive_parent_url(session, group)

    dna = _build_dna(group, boards)
    device_dna = dna["device_dna"]
    brain = _build_brain(group, boards, parent_url, device_dna)
    wifi = _wifi_payload(primary, secondary)
    vendor_accounts = await _vendor_accounts_payload(session, boards)

    # Bundle artifacts deterministically (fixed mtime + sorted names).
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    bundle_filename = f"{device_dna}.zip"
    bundle_path = artifacts_dir / bundle_filename

    files = [
        ("dna.json",   _det_dumps(dna)),
        ("brain.json", _det_dumps(brain)),
        ("wifi.json",  _det_dumps(wifi)),
        ("vendor_accounts.json", _det_dumps(vendor_accounts)),
    ]

    manifest = {
        "bundle_version": _BUNDLE_VERSION,
        "device_dna": device_dna,
        "group_id": group.id,
        "compute_stable_id": group.compute_stable_id,
        "files": [{"name": n, "sha256": _sha256(d), "size": len(d)} for n, d in files],
        "built_at": _utc_iso(),
    }
    manifest_bytes = _det_dumps(manifest)
    files.append(("manifest.json", manifest_bytes))

    # Deterministic zip: fixed time + sorted entry order + no extra fields.
    fixed_time = (2020, 1, 1, 0, 0, 0)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(files):
            info = zipfile.ZipInfo(name, date_time=fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    bundle_bytes = buf.getvalue()
    bundle_path.write_bytes(bundle_bytes)
    return bundle_path, dna, brain
