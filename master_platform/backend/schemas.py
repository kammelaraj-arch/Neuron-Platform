from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RootSystemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class RootSystemOut(_Base):
    id: str
    name: str
    description: str | None = None
    created_at: datetime


class NodeSystemCreate(BaseModel):
    root_id: str
    name: str
    region: str | None = None


class NodeSystemOut(_Base):
    id: str
    root_id: str
    name: str
    region: str | None = None
    created_at: datetime


class EdgeSystemCreate(BaseModel):
    node_id: str
    name: str
    site_id: str
    address: str | None = None


class EdgeSystemOut(_Base):
    id: str
    node_id: str
    name: str
    site_id: str
    address: str | None = None
    created_at: datetime


class ComponentAssignment(BaseModel):
    component_stable_id: str
    instance_id: str | None = None
    role: str | None = None


class DeviceCreate(BaseModel):
    edge_id: str
    device_type: str = Field(pattern=r"^(pico2w|linux_edge|ai_edge|gateway)$")
    compute_stable_id: str
    board_stable_id: str | None = None
    hardware_revision: str = "rev_a"
    serial_number: str | None = None
    mac_address: str | None = None
    base_firmware_version: str = "1.0.0"
    app_bundle_version: str = "1.0.0"
    config_schema_version: str = "1.0.0"
    components: list[ComponentAssignment] = Field(default_factory=list)


class DeviceOut(_Base):
    device_dna: str
    edge_id: str
    device_type: str
    compute_stable_id: str
    board_stable_id: str | None = None
    hardware_revision: str
    base_firmware_version: str
    app_bundle_version: str
    config_schema_version: str
    components_json: list = Field(default_factory=list)
    pinmap_json: dict | None = None
    dna_json: dict | None = None
    brain_json: dict | None = None
    firmware_bundle_path: str | None = None
    created_at: datetime
    updated_at: datetime


class PinmapManual(BaseModel):
    assignments: list[dict[str, Any]]


class ProcessCreate(BaseModel):
    name: str
    edge_id: str | None = None
    graph_json: dict[str, Any]
    version: str = "1.0.0"


class ProcessOut(_Base):
    id: str
    name: str
    edge_id: str | None
    graph_json: dict
    version: str
    created_at: datetime
    updated_at: datetime


class APIKeyCreate(BaseModel):
    label: str | None = None
    owner: str
    tier: str = Field(pattern=r"^(admin|integration|device|readonly)$", default="integration")
    scopes: list[str] = Field(default_factory=list)
    rate_per_minute: int = Field(default=120, ge=1, le=100000)
    rate_burst: int = Field(default=30, ge=1, le=10000)
    ttl_days: int | None = 365


class APIKeyOut(_Base):
    id: str
    label: str | None
    owner: str
    tier: str
    scopes_json: list
    rate_per_minute: int
    rate_burst: int
    issued_at: datetime
    expires_at: datetime | None
    rotated_from: str | None
    status: str
    last_used_at: datetime | None


class APIKeyIssued(BaseModel):
    """Returned exactly once at creation/rotation time."""

    record: APIKeyOut
    secret: str


class AuditEventOut(_Base):
    id: str
    at: datetime
    actor: str
    actor_kind: str
    action: str
    target_kind: str | None
    target_id: str | None
    outcome: str
    detail_json: dict | None
