from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .log_collector import traces
from .models import DeviceRecord


router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


class HeartbeatBody(BaseModel):
    device_dna: str
    device_type: str | None = None
    compute_stable_id: str | None = None
    hardware_revision: str | None = None
    base_firmware_version: str | None = None
    app_bundle_version: str | None = None
    config_schema_version: str | None = None
    twin_controls: list[str] | None = None
    metadata: dict[str, Any] | None = None


class DeviceOut(BaseModel):
    device_dna: str
    device_type: str
    compute_stable_id: str | None
    hardware_revision: str | None
    base_firmware_version: str | None
    app_bundle_version: str | None
    config_schema_version: str | None
    last_seen_at: datetime | None
    last_ip: str | None
    online: bool
    twin_controls_json: list
    first_seen_at: datetime

    class Config:
        from_attributes = True


@router.post("/heartbeat", response_model=DeviceOut)
async def heartbeat(
    body: HeartbeatBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not body.device_dna.startswith("DNA-"):
        raise HTTPException(400, "device_dna must look like DNA-XXXX-XXXX-XXXX-XXXX")
    row = await session.get(DeviceRecord, body.device_dna)
    now = datetime.now(timezone.utc)
    if row is None:
        row = DeviceRecord(
            device_dna=body.device_dna,
            device_type=body.device_type or "pico2w",
            compute_stable_id=body.compute_stable_id,
            hardware_revision=body.hardware_revision,
            base_firmware_version=body.base_firmware_version,
            app_bundle_version=body.app_bundle_version,
            config_schema_version=body.config_schema_version,
            last_seen_at=now,
            last_ip=request.client.host if request.client else None,
            online=True,
            twin_controls_json=body.twin_controls or [],
            metadata_json=body.metadata,
        )
        session.add(row)
        await traces.record(session, source="device_registry", message=f"new device {row.device_dna}", device_dna=row.device_dna)
    else:
        row.last_seen_at = now
        row.last_ip = request.client.host if request.client else row.last_ip
        row.online = True
        if body.compute_stable_id: row.compute_stable_id = body.compute_stable_id
        if body.base_firmware_version: row.base_firmware_version = body.base_firmware_version
        if body.app_bundle_version: row.app_bundle_version = body.app_bundle_version
        if body.config_schema_version: row.config_schema_version = body.config_schema_version
        if body.twin_controls is not None: row.twin_controls_json = body.twin_controls
    await session.commit()
    await session.refresh(row)
    return row


@router.get("", response_model=list[DeviceOut])
async def list_devices(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(DeviceRecord).order_by(DeviceRecord.first_seen_at.desc()))).scalars().all()
    return list(rows)


@router.get("/{device_dna}", response_model=DeviceOut)
async def get_device(device_dna: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(DeviceRecord, device_dna)
    if row is None:
        raise HTTPException(404, "device not found")
    return row
