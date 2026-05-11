from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import schemas
from ..brain import build_brain
from ..db import get_session
from ..dna import build_dna, new_dna_id
from ..firmware import build_bundle
from ..library_loader import load_catalog
from ..models import APIKey, Device, EdgeSystem, FirmwareChannel, FirmwareRelease, NodeSystem
from ..pin_allocator import auto_allocate, PinAllocationError
from ..security.audit import record
from ..security.auth import require_scopes
from sqlalchemy import desc


router = APIRouter(prefix="/api/devices", tags=["devices"])


def _to_components_json(assignments):
    out = []
    for a in assignments or []:
        if hasattr(a, "model_dump"):
            d = a.model_dump()
        else:
            d = dict(a)
        if not d.get("instance_id"):
            d["instance_id"] = d["component_stable_id"].split(".")[-1]
        out.append(d)
    return out


@router.post("", response_model=schemas.DeviceOut, status_code=201)
async def register_device(
    payload: schemas.DeviceCreate,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("devices:write")),
):
    edge = await session.get(EdgeSystem, payload.edge_id)
    if edge is None:
        raise HTTPException(404, "edge system not found")
    catalog = load_catalog()
    if catalog.get(payload.compute_stable_id) is None:
        raise HTTPException(400, f"unknown compute '{payload.compute_stable_id}'")
    if payload.board_stable_id and catalog.get(payload.board_stable_id) is None:
        raise HTTPException(400, f"unknown board '{payload.board_stable_id}'")
    for comp in payload.components:
        if catalog.get(comp.component_stable_id) is None:
            raise HTTPException(400, f"unknown component '{comp.component_stable_id}'")

    device = Device(
        device_dna=new_dna_id(),
        edge_id=edge.id,
        device_type=payload.device_type,
        compute_stable_id=payload.compute_stable_id,
        board_stable_id=payload.board_stable_id,
        hardware_revision=payload.hardware_revision,
        serial_number=payload.serial_number,
        mac_address=payload.mac_address,
        base_firmware_version=payload.base_firmware_version,
        app_bundle_version=payload.app_bundle_version,
        config_schema_version=payload.config_schema_version,
        components_json=_to_components_json(payload.components),
    )
    session.add(device)
    await record(session, actor=api_key.id, action="register_device",
                 target_kind="device", target_id=device.device_dna)
    await session.commit()
    await session.refresh(device)
    return device


@router.get("", response_model=list[schemas.DeviceOut])
async def list_devices(
    edge_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    stmt = select(Device).order_by(Device.created_at.desc())
    if edge_id:
        stmt = stmt.where(Device.edge_id == edge_id)
    res = await session.execute(stmt)
    return list(res.scalars())


@router.get("/{device_dna}", response_model=schemas.DeviceOut)
async def get_device(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    device = await session.get(Device, device_dna)
    if device is None:
        raise HTTPException(404, "device not found")
    return device


async def _load_device(session: AsyncSession, dna: str) -> Device:
    device = await session.get(Device, dna)
    if device is None:
        raise HTTPException(404, "device not found")
    return device


@router.post("/{device_dna}/pinmap/auto")
async def pinmap_auto(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("devices:write")),
):
    device = await _load_device(session, device_dna)
    catalog = load_catalog()
    components = [
        {
            "component_stable_id": c["component_stable_id"],
            "instance_id": c.get("instance_id"),
        }
        for c in (device.components_json or [])
    ]
    try:
        pinmap = auto_allocate(
            compute_stable_id=device.compute_stable_id,
            component_assignments=components,
            catalog=catalog,
            device_dna=device.device_dna,
            board_stable_id=device.board_stable_id,
        )
    except PinAllocationError as exc:
        raise HTTPException(400, str(exc))
    device.pinmap_json = pinmap
    await record(session, actor=api_key.id, action="pinmap_auto",
                 target_kind="device", target_id=device.device_dna,
                 detail={"conflicts": pinmap["conflicts"]})
    await session.commit()
    return pinmap


@router.post("/{device_dna}/pinmap/manual")
async def pinmap_manual(
    device_dna: str,
    payload: schemas.PinmapManual,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("devices:write")),
):
    device = await _load_device(session, device_dna)
    base = device.pinmap_json or {
        "device_dna": device.device_dna,
        "compute_id": device.compute_stable_id,
        "board_id": device.board_stable_id,
        "version": "1.0.0",
        "conflicts": [],
    }
    from datetime import datetime, timezone
    base.update({
        "assignments": payload.assignments,
        "generated_by": "manual_override",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })
    device.pinmap_json = base
    await record(session, actor=api_key.id, action="pinmap_manual",
                 target_kind="device", target_id=device.device_dna)
    await session.commit()
    return base


@router.post("/{device_dna}/generate-dna")
async def generate_dna(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("devices:write")),
):
    device = await _load_device(session, device_dna)
    edge = await session.get(EdgeSystem, device.edge_id)
    node = await session.get(NodeSystem, edge.node_id) if edge else None
    catalog = load_catalog()
    components = [
        {
            "stable_id": c["component_stable_id"],
            "instance_id": c.get("instance_id") or c["component_stable_id"].split(".")[-1],
            "version": (catalog.get(c["component_stable_id"]).version
                        if catalog.get(c["component_stable_id"]) else "0.0.0"),
            "role": c.get("role") or "component",
        }
        for c in (device.components_json or [])
    ]
    twin_controls = sorted({
        tc
        for c in components
        for tc in (catalog.get(c["stable_id"]).manifest.get("compatibility", {}).get("twin_controls", [])
                   if catalog.get(c["stable_id"]) else [])
    })
    dna = build_dna(
        device_dna=device.device_dna,
        device_type=device.device_type,
        compute_stable_id=device.compute_stable_id,
        board_stable_id=device.board_stable_id,
        hardware_revision=device.hardware_revision,
        serial_number=device.serial_number,
        mac_address=device.mac_address,
        base_firmware_version=device.base_firmware_version,
        app_bundle_version=device.app_bundle_version,
        config_schema_version=device.config_schema_version,
        parent_node_id=node.id if node else "unknown",
        site_id=edge.site_id if edge else "unknown",
        issued_by=api_key.id,
        components=components,
        twin_controls=twin_controls,
    )
    device.dna_json = dna
    await record(session, actor=api_key.id, action="generate_dna",
                 target_kind="device", target_id=device.device_dna)
    await session.commit()
    return dna


@router.post("/{device_dna}/generate-brain-config")
async def generate_brain_config(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("devices:write")),
):
    device = await _load_device(session, device_dna)
    if not device.dna_json:
        raise HTTPException(400, "DNA must be generated first")
    catalog = load_catalog()
    brain = build_brain(
        device_dna=device.device_dna,
        config_schema_version=device.config_schema_version,
        twin_controls=device.dna_json.get("twin_controls", []),
        catalog=catalog,
    )
    device.brain_json = brain
    await record(session, actor=api_key.id, action="generate_brain",
                 target_kind="device", target_id=device.device_dna)
    await session.commit()
    return brain


_VALID_CHANNELS = ("dev", "beta", "stable")


async def _set_channel_active(
    session: AsyncSession, *, device_dna: str, channel: str, release_id: str, actor_id: str
) -> FirmwareChannel:
    row = await session.get(FirmwareChannel, (device_dna, channel))
    if row is None:
        row = FirmwareChannel(device_dna=device_dna, channel=channel)
        session.add(row)
    row.active_release_id = release_id
    row.updated_by = actor_id
    return row


@router.post("/{device_dna}/build-firmware-bundle")
async def build_firmware(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("devices:write")),
):
    device = await _load_device(session, device_dna)
    if not (device.dna_json and device.brain_json and device.pinmap_json):
        raise HTTPException(400, "device needs DNA + brain + pinmap before firmware build")
    catalog = load_catalog()
    path, info = build_bundle(
        device_dna=device.device_dna,
        dna=device.dna_json,
        brain=device.brain_json,
        pinmap=device.pinmap_json,
        catalog=catalog,
    )
    sig = info["manifest"].get("signature") or {}
    release = FirmwareRelease(
        device_dna=device.device_dna,
        app_bundle_version=device.app_bundle_version,
        base_firmware_version=device.base_firmware_version,
        config_schema_version=device.config_schema_version,
        hardware_revision=device.hardware_revision,
        bundle_path=str(path),
        bundle_sha256=info["sha256"],
        manifest_json=info["manifest"],
        sig_alg=sig.get("alg", "ed25519"),
        sig_kid=sig.get("kid", ""),
        sig_value=sig.get("value", ""),
    )
    session.add(release)
    await session.flush()
    # Default behaviour: a fresh build is the active release on the 'dev'
    # channel. Promotion to beta/stable is explicit.
    await _set_channel_active(
        session, device_dna=device.device_dna, channel="dev",
        release_id=release.id, actor_id=api_key.id,
    )
    device.firmware_bundle_path = str(path)
    await record(session, actor=api_key.id, action="build_firmware",
                 target_kind="device", target_id=device.device_dna,
                 detail={
                     "size_bytes": info["size_bytes"], "sha256": info["sha256"],
                     "release_id": release.id,
                     "app_bundle_version": device.app_bundle_version,
                 })
    await session.commit()
    return {
        "device_dna": device.device_dna,
        "release_id": release.id,
        "bundle_path": str(path),
        "size_bytes": info["size_bytes"],
        "sha256": info["sha256"],
        "manifest": info["manifest"],
    }


async def _resolve_release(
    session: AsyncSession, device_dna: str, channel: str | None
) -> FirmwareRelease | None:
    if channel:
        if channel not in _VALID_CHANNELS:
            raise HTTPException(400, f"channel must be one of {_VALID_CHANNELS}")
        ch = await session.get(FirmwareChannel, (device_dna, channel))
        if ch is None or ch.active_release_id is None:
            return None
        return await session.get(FirmwareRelease, ch.active_release_id)
    # No channel requested → newest non-retired release.
    res = await session.execute(
        select(FirmwareRelease)
        .where(FirmwareRelease.device_dna == device_dna, FirmwareRelease.retired_at.is_(None))
        .order_by(desc(FirmwareRelease.created_at))
        .limit(1)
    )
    return res.scalars().first()


class DesiredBody(BaseModel):
    twin_kind: str
    field: str
    value: object | None = None


@router.post("/{device_dna}/desired")
async def set_desired(
    device_dna: str,
    body: DesiredBody,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_scopes("devices:write")),
):
    """Master → Edge desired-state push.

    Looks up the device's Edge address and POSTs a desired field
    immediately. Edge merges into its twin cache, then echoes back to
    Master via /api/v1/internal/twin-push so the SSE feed updates the
    browser within a network round-trip.
    """
    from ..runtime.edge_bridge import push_desired, EdgeUnreachable
    device = await _load_device(session, device_dna)
    try:
        edge_resp = await push_desired(
            session,
            edge_id=device.edge_id,
            device_dna=device_dna,
            twin_kind=body.twin_kind,
            field=body.field,
            value=body.value,
        )
    except EdgeUnreachable as exc:
        raise HTTPException(503, f"edge unreachable: {exc}")
    await record(session, actor=api_key.id, action="set_desired",
                 target_kind="device", target_id=device_dna,
                 detail={"twin_kind": body.twin_kind, "field": body.field, "value": body.value})
    await session.commit()
    return {"ok": True, "edge": edge_resp}


@router.get("/{device_dna}/twin/stream")
async def stream_twin(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    """Server-Sent Events stream of live twin telemetry. The Master polls
    the relevant Edge once per second (one task shared across all
    subscribers) and pushes only-when-changed payloads to the browser.

    Each event is a JSON line:
      data: {"online": true, "twin": {...}, "at": "..."}
    Heartbeat ': ping' comments arrive every 15s to keep the connection
    alive through proxies.
    """
    from fastapi.responses import StreamingResponse
    from ..runtime.twin_stream import hub

    # Ensure the device actually exists before opening a long-lived stream.
    device = await session.get(Device, device_dna)
    if device is None:
        raise HTTPException(404, "device not found")

    async def gen():
        async for chunk in hub.subscribe(device_dna):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: don't buffer the stream
            "Connection": "keep-alive",
        },
    )


@router.get("/{device_dna}/twin")
async def fetch_twin_from_edge(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    """Live read-through to the device's Edge twin cache.

    Returns ``{"online": false, "reason": ...}`` when the Edge is
    unreachable so the UI can render the offline state without errors.
    """
    import httpx
    device = await _load_device(session, device_dna)
    edge = await session.get(EdgeSystem, device.edge_id)
    if edge is None or not edge.address:
        return {"online": False, "reason": "edge_address_unset"}
    url = f"{edge.address.rstrip('/')}/api/v1/twin/{device_dna}"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return {"online": False, "reason": f"edge_status_{resp.status_code}"}
        return {"online": True, "twin": resp.json(), "edge_url": edge.address}
    except httpx.HTTPError as exc:
        return {"online": False, "reason": f"edge_unreachable: {exc.__class__.__name__}"}


@router.get("/{device_dna}/firmware-bundle")
async def download_firmware(
    device_dna: str,
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    device = await _load_device(session, device_dna)
    release = await _resolve_release(session, device.device_dna, channel)
    path = release.bundle_path if release else device.firmware_bundle_path
    if not path:
        raise HTTPException(404, "no firmware bundle built for this device" + (f" on channel={channel}" if channel else ""))
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{device.device_dna}.zip",
    )


@router.get("/{device_dna}/firmware-manifest")
async def get_firmware_manifest(
    device_dna: str,
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    device = await _load_device(session, device_dna)
    release = await _resolve_release(session, device.device_dna, channel)
    if release is None:
        raise HTTPException(404, "no release for this device on the requested channel")
    return release.manifest_json


@router.get("/{device_dna}/releases")
async def list_releases(
    device_dna: str,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    device = await _load_device(session, device_dna)
    res = await session.execute(
        select(FirmwareRelease)
        .where(FirmwareRelease.device_dna == device.device_dna)
        .order_by(desc(FirmwareRelease.created_at))
    )
    rows = res.scalars().all()
    chs = (await session.execute(
        select(FirmwareChannel).where(FirmwareChannel.device_dna == device.device_dna)
    )).scalars().all()
    chmap = {c.channel: c.active_release_id for c in chs}
    return {
        "device_dna": device.device_dna,
        "channels": chmap,
        "releases": [
            {
                "id": r.id,
                "app_bundle_version": r.app_bundle_version,
                "base_firmware_version": r.base_firmware_version,
                "config_schema_version": r.config_schema_version,
                "hardware_revision": r.hardware_revision,
                "bundle_sha256": r.bundle_sha256,
                "sig_alg": r.sig_alg,
                "sig_kid": r.sig_kid,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "retired_at": r.retired_at.isoformat() if r.retired_at else None,
            }
            for r in rows
        ],
    }


class PromoteBody(BaseModel):
    channel: str
    release_id: str
    rollback_allowed: bool = False


@router.post("/{device_dna}/dry-run-promote")
async def dry_run_promote(
    device_dna: str,
    body: PromoteBody,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("devices:read")),
):
    """Walk every promote gate without committing. Returns a structured
    'verdict' showing which gates pass/fail, so an operator can preview a
    promotion before issuing one."""
    if body.channel not in _VALID_CHANNELS:
        return {"ok": False, "reason": f"channel must be one of {_VALID_CHANNELS}"}
    device = await session.get(Device, device_dna)
    if device is None:
        return {"ok": False, "reason": "device_not_found"}
    release = await session.get(FirmwareRelease, body.release_id)
    if release is None or release.device_dna != device.device_dna:
        return {"ok": False, "reason": "release_not_found_for_device"}

    manifest = release.manifest_json or {}
    gates = []

    # Gate 1: hardware revision
    hw_supported = device.hardware_revision in (manifest.get("supported_hardware_revisions") or [])
    gates.append({
        "id": "hardware_revision", "ok": hw_supported,
        "detail": f"device hw '{device.hardware_revision}' "
                  f"{'in' if hw_supported else 'NOT in'} "
                  f"supported {manifest.get('supported_hardware_revisions') or []}",
    })

    # Gate 2: signature presence
    sig = manifest.get("signature") or {}
    sig_ok = sig.get("alg") == "ed25519" and bool(sig.get("value"))
    gates.append({
        "id": "signature_present", "ok": sig_ok,
        "detail": f"alg={sig.get('alg')} kid={sig.get('kid','')} "
                  f"{'has value' if sig.get('value') else 'NO value'}",
    })

    # Gate 3: downgrade check
    current = await session.get(FirmwareChannel, (device.device_dna, body.channel))
    is_downgrade = False
    downgrade_ok = True
    cur_version = None
    if current and current.active_release_id and current.active_release_id != release.id:
        cur = await session.get(FirmwareRelease, current.active_release_id)
        if cur:
            cur_version = cur.app_bundle_version
            if _semver(cur.app_bundle_version) > _semver(release.app_bundle_version):
                is_downgrade = True
                downgrade_ok = bool(body.rollback_allowed and manifest.get("rollback_allowed"))
    gates.append({
        "id": "downgrade", "ok": downgrade_ok,
        "detail": (f"current={cur_version} target={release.app_bundle_version}"
                   + (" (downgrade requires rollback_allowed=true on body AND manifest)"
                      if is_downgrade and not downgrade_ok else "")),
    })

    all_ok = all(g["ok"] for g in gates)
    return {
        "ok": all_ok,
        "device_dna": device_dna,
        "channel": body.channel,
        "release_id": release.id,
        "target_app_bundle_version": release.app_bundle_version,
        "gates": gates,
        "would_promote": all_ok,
    }


@router.post("/{device_dna}/promote")
async def promote_release(
    device_dna: str,
    body: PromoteBody,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("devices:write")),
):
    if body.channel not in _VALID_CHANNELS:
        raise HTTPException(400, f"channel must be one of {_VALID_CHANNELS}")
    device = await _load_device(session, device_dna)
    release = await session.get(FirmwareRelease, body.release_id)
    if release is None or release.device_dna != device.device_dna:
        raise HTTPException(404, "release not found for this device")

    # Check downgrade / hardware gates against the release manifest itself.
    manifest = release.manifest_json or {}
    if device.hardware_revision not in manifest.get("supported_hardware_revisions", []):
        raise HTTPException(
            400,
            f"hardware_revision {device.hardware_revision} is not in supported_hardware_revisions",
        )

    current = await session.get(FirmwareChannel, (device.device_dna, body.channel))
    if current and current.active_release_id and current.active_release_id != release.id:
        cur = await session.get(FirmwareRelease, current.active_release_id)
        if cur and _semver(cur.app_bundle_version) > _semver(release.app_bundle_version):
            if not body.rollback_allowed or not manifest.get("rollback_allowed"):
                raise HTTPException(
                    400,
                    "downgrade requires rollback_allowed=true on both the request "
                    "and a re-signed manifest with rollback_allowed=true",
                )

    await _set_channel_active(
        session, device_dna=device.device_dna, channel=body.channel,
        release_id=release.id, actor_id=actor.id,
    )
    if body.channel == "stable":
        device.firmware_bundle_path = release.bundle_path
    await record(
        session, actor=actor.id, action="promote_firmware",
        target_kind="device", target_id=device.device_dna,
        detail={"channel": body.channel, "release_id": release.id,
                "app_bundle_version": release.app_bundle_version,
                "rollback_allowed": body.rollback_allowed},
    )
    await session.commit()
    return {"channel": body.channel, "active_release_id": release.id}


class RollbackBody(BaseModel):
    channel: str = "stable"


@router.post("/{device_dna}/rollback")
async def rollback_release(
    device_dna: str,
    body: RollbackBody,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(require_scopes("devices:write")),
):
    if body.channel not in _VALID_CHANNELS:
        raise HTTPException(400, f"channel must be one of {_VALID_CHANNELS}")
    device = await _load_device(session, device_dna)
    res = await session.execute(
        select(FirmwareRelease)
        .where(FirmwareRelease.device_dna == device.device_dna)
        .order_by(desc(FirmwareRelease.created_at))
    )
    rows = res.scalars().all()
    if len(rows) < 2:
        raise HTTPException(400, "no prior release to roll back to")
    prev = rows[1]
    await _set_channel_active(
        session, device_dna=device.device_dna, channel=body.channel,
        release_id=prev.id, actor_id=actor.id,
    )
    if body.channel == "stable":
        device.firmware_bundle_path = prev.bundle_path
    await record(
        session, actor=actor.id, action="rollback_firmware",
        target_kind="device", target_id=device.device_dna,
        detail={"channel": body.channel, "release_id": prev.id,
                "app_bundle_version": prev.app_bundle_version},
    )
    await session.commit()
    return {"channel": body.channel, "active_release_id": prev.id}


def _semver(v: str) -> tuple[int, int, int]:
    parts = (v.split("-")[0].split(".") + ["0", "0", "0"])[:3]
    try:
        return tuple(int(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        return (0, 0, 0)
