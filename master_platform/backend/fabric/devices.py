"""Fabric adapter — every device kind → one common shape.

list_devices() runs four small queries (native, vendor, plotter device,
plotter scene) and emits a unified list. New integrations slot in by
adding one more `_adapt_*` function below and one call in list_devices.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Device,
    DeviceAsset,
    DeviceGroupMembership,
    DeviceOrgAssignment,
    DeviceSafety,
    PlotterDevice,
    PlotterScene,
    VendorAccount,
    VendorDevice,
)


# ── unified shape ───────────────────────────────────────────────────
@dataclass
class FabricDevice:
    fabric_id: str            # "<kind>:<source_id>"
    kind: str                 # native | vendor | plotter | plotter-scene | biometric | media
    provider: str | None      # tado | ring | tapo | neuron | None
    label: str                # human-friendly name
    category: str             # thermostat | camera | scene-trigger | plotter | sensor | ...
    status: str               # active | offline | revoked | unknown
    capabilities: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    detail_url: str = ""      # deep link to the integration's own page
    # ── Asset register (joined from DeviceAsset; nullable) ─────────
    sub_category: str | None = None
    location: str | None = None       # "Living room"
    address: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    install_date: str | None = None        # ISO-8601 date
    warranty_expires_at: str | None = None # ISO-8601 date
    vendor_name: str | None = None
    purchase_ref: str | None = None
    purchase_price: float | None = None
    notes: str | None = None
    # ── Safety + risk (joined from DeviceSafety; nullable) ─────────
    risk_level: str = "nominal"
    risk_types: list[str] = field(default_factory=list)
    failsafe_action: str = "alarm_only"
    failsafe_value: dict[str, Any] | None = None
    disconnect_grace_seconds: int = 30
    watchdog_ms: int = 1000
    hazard_notes: str | None = None
    # ── Org assignment (joined from DeviceOrgAssignment) ───────────
    org_name: str | None = None
    # Free-form per-kind metadata that doesn't deserve a column.
    extras: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


# ── fabric id helpers (the canonical "this is how we name a thing") ─
def fabric_id_for_native_device(dna: str) -> str:
    return f"native:{dna}"

def fabric_id_for_vendor_device(vendor_device_pk: str) -> str:
    return f"vendor:{vendor_device_pk}"

def fabric_id_for_plotter_device(device_pk: str,
                                 hw_kind: str | None = None,
                                 hw_id: str | None = None) -> str:
    """Prefer the hardware-anchored form so the id survives the device
    row being recreated (operator deletes + re-registers). Falls back
    to the source PK only when the agent hasn't yet self-reported."""
    if hw_kind and hw_id:
        return f"plotter:{hw_kind}:{hw_id}"
    return f"plotter:{device_pk}"

def fabric_id_for_plotter_scene(scene_pk: str) -> str:
    return f"plotter-scene:{scene_pk}"


# ── per-kind adapters ───────────────────────────────────────────────
async def _adapt_native(session: AsyncSession) -> list[FabricDevice]:
    rows = (await session.execute(select(Device))).scalars().all()
    out: list[FabricDevice] = []
    for d in rows:
        out.append(FabricDevice(
            fabric_id=fabric_id_for_native_device(d.device_dna),
            kind="native",
            provider="neuron",
            label=(d.device_dna or "(no DNA)"),
            category=(getattr(d, "device_type", None) or "device"),
            status=getattr(d, "status", "active") or "active",
            capabilities=["twin.read", "twin.write"],
            detail_url=f"/ui/devices",
            extras={
                "dna": d.device_dna,
                "serial_number": getattr(d, "serial_number", None),
                "mac_address": getattr(d, "mac_address", None),
            },
        ))
    return out


async def _adapt_vendor(session: AsyncSession) -> list[FabricDevice]:
    accounts = {a.id: a for a in (await session.execute(
        select(VendorAccount))).scalars().all()}
    rows = (await session.execute(select(VendorDevice))).scalars().all()
    out: list[FabricDevice] = []
    for v in rows:
        acct = accounts.get(v.vendor_account_id)
        provider = (acct.provider if acct else None) or "vendor"
        # category derived from device_type when present, else the
        # provider-specific default.
        category = (v.device_type or {
            "tado": "thermostat",
            "ring": "camera",
            "tapo": "plug",
            "hue":  "bulb",
        }.get(provider, "device")).lower()
        capabilities: list[str] = []
        if provider == "tado":
            capabilities = ["set_temp", "set_off", "resume_schedule"]
        elif provider == "ring":
            if category in ("camera", "doorbell", "doorbell_shared"):
                capabilities = ["floodlight", "siren", "snapshot"]
            elif category == "alarm_hub":
                capabilities = ["arm_away", "arm_home", "disarm"]
            elif category == "chime":
                capabilities = ["play_ding", "play_motion"]
        elif provider == "tapo":
            capabilities = ["set_power"]
        out.append(FabricDevice(
            fabric_id=fabric_id_for_vendor_device(v.id),
            kind="vendor",
            provider=provider,
            label=v.name,
            category=category,
            status="active" if (acct and acct.status == "active") else "offline",
            capabilities=capabilities,
            detail_url=f"/ui/{provider}" if provider in ("tado", "ring") else "/ui/vendor-accounts",
            extras={
                "vendor_device_id": v.vendor_device_id,
                "vendor_account_id": v.vendor_account_id,
                "vendor_account_label": acct.label if acct else None,
                "model": v.model,
                "mac": v.mac,
                "ip_local": v.ip_local,
                "firmware_version": v.firmware_version,
            },
        ))
    return out


async def _adapt_plotter_devices(session: AsyncSession) -> list[FabricDevice]:
    rows = (await session.execute(select(PlotterDevice))).scalars().all()
    out: list[FabricDevice] = []
    for p in rows:
        out.append(FabricDevice(
            fabric_id=fabric_id_for_plotter_device(p.id, p.hw_kind, p.hw_id),
            kind="plotter",
            provider="neuron",
            label=p.label,
            category="plotter",
            status=p.status or "active",
            capabilities=["run_profile", "abort_profile", "home"],
            detail_url="/ui/plotter",
            extras={
                "base_url": p.base_url,
                "uses_pull_agent": bool(p.agent_token),
                "hw_kind": p.hw_kind,
                "hw_id": p.hw_id,
                "hw_identity_status": ("anchored" if p.hw_id else "pending agent poll"),
                "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
            },
        ))
    return out


async def _adapt_plotter_scenes(session: AsyncSession) -> list[FabricDevice]:
    rows = (await session.execute(select(PlotterScene))).scalars().all()
    out: list[FabricDevice] = []
    for s in rows:
        out.append(FabricDevice(
            fabric_id=fabric_id_for_plotter_scene(s.id),
            kind="plotter-scene",
            provider="neuron",
            label=s.name,
            category="scene-trigger",
            status="active",
            capabilities=["activate"],
            detail_url="/ui/plotter",
            extras={
                "device_id": s.device_id,
                "pi_profile_id": s.pi_profile_id,
                "description": s.description,
            },
        ))
    return out


# ── public API ──────────────────────────────────────────────────────
async def list_devices(session: AsyncSession, *,
                       kind: str | None = None,
                       provider: str | None = None,
                       category: str | None = None,
                       group: str | None = None,
                       q: str | None = None) -> list[FabricDevice]:
    """Aggregate every kind into one list, apply filters, attach
    group memberships from device_group_memberships."""
    devices: list[FabricDevice] = []
    devices.extend(await _adapt_native(session))
    devices.extend(await _adapt_vendor(session))
    devices.extend(await _adapt_plotter_devices(session))
    devices.extend(await _adapt_plotter_scenes(session))

    # Attach groups in a single join — one query for all memberships
    # rather than N queries per device.
    members = (await session.execute(
        select(DeviceGroupMembership)
    )).scalars().all()
    groups_by_fabric: dict[str, list[str]] = {}
    for m in members:
        groups_by_fabric.setdefault(m.fabric_id, []).append(m.group_name)
    for d in devices:
        d.groups = sorted(groups_by_fabric.get(d.fabric_id, []))

    # Attach asset-register fields in one query, same shape.
    assets = (await session.execute(select(DeviceAsset))).scalars().all()
    assets_by_fabric = {a.fabric_id: a for a in assets}
    for d in devices:
        a = assets_by_fabric.get(d.fabric_id)
        if a is None:
            continue
        # Asset.category, if set, overrides the source-derived category
        # so the operator's taxonomy wins.
        if a.category:
            d.category = a.category
        d.sub_category = a.sub_category
        d.location = a.location
        d.address = a.address
        d.gps_lat = a.gps_lat
        d.gps_lon = a.gps_lon
        d.install_date = a.install_date.isoformat()[:10] if a.install_date else None
        d.warranty_expires_at = a.warranty_expires_at.isoformat()[:10] if a.warranty_expires_at else None
        d.vendor_name = a.vendor
        d.purchase_ref = a.purchase_ref
        d.purchase_price = a.purchase_price
        d.notes = a.notes

    # Attach safety + risk profile (CLAUDE.md failsafe contract).
    safety_rows = (await session.execute(select(DeviceSafety))).scalars().all()
    safety_by_fabric = {s.fabric_id: s for s in safety_rows}
    for d in devices:
        s = safety_by_fabric.get(d.fabric_id)
        if s is None:
            continue
        d.risk_level = s.risk_level or "nominal"
        d.risk_types = s.risk_types_json or []
        d.failsafe_action = s.failsafe_action or "alarm_only"
        d.failsafe_value = s.failsafe_value_json
        d.disconnect_grace_seconds = s.disconnect_grace_seconds
        d.watchdog_ms = s.watchdog_ms
        d.hazard_notes = s.hazard_notes

    # Attach org assignment.
    org_rows = (await session.execute(select(DeviceOrgAssignment))).scalars().all()
    org_by_fabric = {r.fabric_id: r.org_name for r in org_rows}
    for d in devices:
        d.org_name = org_by_fabric.get(d.fabric_id)

    # Filters (simple — substring + exact).
    if kind:
        devices = [d for d in devices if d.kind == kind]
    if provider:
        devices = [d for d in devices if (d.provider or "") == provider]
    if category:
        devices = [d for d in devices if d.category == category]
    if group:
        devices = [d for d in devices if group in d.groups]
    if q:
        needle = q.lower().strip()
        devices = [d for d in devices
                   if needle in d.label.lower()
                   or needle in (d.provider or "").lower()
                   or needle in d.category.lower()
                   or needle in d.fabric_id.lower()]
    devices.sort(key=lambda d: (d.kind, d.label.lower()))
    return devices


async def get_device(session: AsyncSession, fabric_id: str
                     ) -> FabricDevice | None:
    """Resolve one by fabric_id. Cheap path — only adapts the kind we
    actually need, not the whole list."""
    if not fabric_id or ":" not in fabric_id:
        return None
    kind, _, source_id = fabric_id.partition(":")
    candidates: list[FabricDevice]
    if kind == "native":
        candidates = await _adapt_native(session)
    elif kind == "vendor":
        candidates = await _adapt_vendor(session)
    elif kind == "plotter":
        candidates = await _adapt_plotter_devices(session)
    elif kind == "plotter-scene":
        candidates = await _adapt_plotter_scenes(session)
    else:
        return None
    for d in candidates:
        if d.fabric_id == fabric_id:
            # Attach groups for this one device only.
            rows = (await session.execute(
                select(DeviceGroupMembership)
                .where(DeviceGroupMembership.fabric_id == fabric_id)
            )).scalars().all()
            d.groups = sorted(r.group_name for r in rows)
            return d
    return None
