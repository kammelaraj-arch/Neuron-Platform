from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RootSystem(Base):
    __tablename__ = "root_systems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    nodes: Mapped[list[NodeSystem]] = relationship(back_populates="root", cascade="all, delete-orphan")


class NodeSystem(Base):
    __tablename__ = "node_systems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    root_id: Mapped[str] = mapped_column(ForeignKey("root_systems.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(default=_now)

    root: Mapped[RootSystem] = relationship(back_populates="nodes")
    edges: Mapped[list[EdgeSystem]] = relationship(back_populates="node", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("root_id", "name", name="uq_node_per_root"),)


class EdgeSystem(Base):
    """An edge (factory site). Attaches to EITHER a NodeSystem (full
    hierarchy) OR a RootSystem (Node skipped, smaller deployments).
    Exactly one of node_id / root_id must be set — enforced by the
    check constraint below.
    """
    __tablename__ = "edge_systems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("node_systems.id"))
    root_id: Mapped[str | None] = mapped_column(ForeignKey("root_systems.id"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    site_id: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    node: Mapped[NodeSystem | None] = relationship(back_populates="edges")
    root: Mapped[RootSystem | None] = relationship()
    devices: Mapped[list[Device]] = relationship(back_populates="edge", cascade="all, delete-orphan")

    # NOTE: the XOR constraint (exactly one of node_id/root_id set) is
    # enforced at the application layer in routers/systems.py. SQLite
    # can't add CHECK constraints to an existing table via ALTER, and
    # we want existing edges (all node_id-attached) to keep working
    # without a destructive table rewrite.


class Device(Base):
    __tablename__ = "devices"

    device_dna: Mapped[str] = mapped_column(String(40), primary_key=True)
    edge_id: Mapped[str] = mapped_column(ForeignKey("edge_systems.id"), nullable=False)
    device_type: Mapped[str] = mapped_column(String(40), nullable=False)
    compute_stable_id: Mapped[str] = mapped_column(String(120), nullable=False)
    board_stable_id: Mapped[str | None] = mapped_column(String(120))
    hardware_revision: Mapped[str] = mapped_column(String(40), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(80))
    mac_address: Mapped[str | None] = mapped_column(String(20))
    base_firmware_version: Mapped[str] = mapped_column(String(20), nullable=False)
    app_bundle_version: Mapped[str] = mapped_column(String(20), nullable=False)
    config_schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    components_json: Mapped[list] = mapped_column(JSON, default=list)
    pinmap_json: Mapped[dict | None] = mapped_column(JSON)
    dna_json: Mapped[dict | None] = mapped_column(JSON)
    brain_json: Mapped[dict | None] = mapped_column(JSON)
    firmware_bundle_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    edge: Mapped[EdgeSystem] = relationship(back_populates="devices")


class FirmwareRelease(Base):
    """A built firmware bundle. Multiple per device — rollback target lives
    in this table, the active one per channel is pointed at from
    ``FirmwareChannel``.
    """

    __tablename__ = "firmware_releases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_dna: Mapped[str] = mapped_column(ForeignKey("devices.device_dna"), nullable=False, index=True)
    app_bundle_version: Mapped[str] = mapped_column(String(20), nullable=False)
    base_firmware_version: Mapped[str] = mapped_column(String(20), nullable=False)
    config_schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    hardware_revision: Mapped[str] = mapped_column(String(40), nullable=False)
    bundle_path: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    sig_alg: Mapped[str] = mapped_column(String(16), default="ed25519")
    sig_kid: Mapped[str] = mapped_column(String(60), default="")
    sig_value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    retired_at: Mapped[datetime | None] = mapped_column()


class FirmwareChannel(Base):
    """Per-device per-channel pointer to the active release."""

    __tablename__ = "firmware_channels"

    device_dna: Mapped[str] = mapped_column(ForeignKey("devices.device_dna"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), primary_key=True)  # dev|beta|stable
    active_release_id: Mapped[str | None] = mapped_column(ForeignKey("firmware_releases.id"))
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(String(80))


class Process(Base):
    __tablename__ = "processes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    edge_id: Mapped[str | None] = mapped_column(ForeignKey("edge_systems.id"))
    graph_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class RecipeDef(Base):
    """DB-backed recipe definition. The same recipe shape used by file-based
    recipes (libraries/digital_twin_controls_library/recipes/*.json) but
    editable via /ui/recipes/edit. DB wins on recipe_id collision with files.
    """

    __tablename__ = "recipe_defs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    recipe_id: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(20), default="0.1.0")
    recipe_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
    deleted_at: Mapped[datetime | None] = mapped_column()


class RecipeRun(Base):
    """A recipe execution against a device. Stage 5 orchestration unit."""

    __tablename__ = "recipe_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    recipe_id: Mapped[str] = mapped_column(String(160), nullable=False)
    device_dna: Mapped[str] = mapped_column(ForeignKey("devices.device_dna"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|paused|completed|aborted|failed
    current_step: Mapped[str | None] = mapped_column(String(80))
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)
    progress_pct: Mapped[int] = mapped_column(default=0)
    note: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column()


class EdgeCert(Base):
    """mTLS client cert issued by the platform CA, pinned for an Edge.

    The plaintext cert+key PEMs are returned ONCE at issuance; we only
    persist the fingerprint + metadata so a leaked DB doesn't reveal the
    key material.
    """

    __tablename__ = "edge_certs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    edge_id: Mapped[str | None] = mapped_column(ForeignKey("edge_systems.id"))
    subject_cn: Mapped[str] = mapped_column(String(160), nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(String(95), nullable=False, unique=True)
    serial: Mapped[str] = mapped_column(String(80))
    issued_at: Mapped[datetime] = mapped_column(default=_now)
    expires_at: Mapped[datetime | None] = mapped_column()
    issued_by: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|revoked|expired
    notes: Mapped[str | None] = mapped_column(Text)


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    label: Mapped[str | None] = mapped_column(String(120))
    owner: Mapped[str] = mapped_column(String(120), nullable=False)
    tier: Mapped[str] = mapped_column(String(20), nullable=False, default="integration")
    scopes_json: Mapped[list] = mapped_column(JSON, default=list)
    rate_per_minute: Mapped[int] = mapped_column(default=120)
    rate_burst: Mapped[int] = mapped_column(default=30)
    issued_at: Mapped[datetime] = mapped_column(default=_now)
    expires_at: Mapped[datetime | None] = mapped_column()
    rotated_from: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="active")
    hash_alg: Mapped[str] = mapped_column(String(20), default="argon2id")
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    at: Mapped[datetime] = mapped_column(default=_now, index=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(20), default="api_key")
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_kind: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(120))
    outcome: Mapped[str] = mapped_column(String(20), default="ok")
    detail_json: Mapped[dict | None] = mapped_column(JSON)


# ─── Feature/capability request tracker ──────────────────────────────────────
# Product-owner-facing registry of every requested feature with a
# lifecycle status (requested → in_dev → deployed_dev → deployed_prod
# → rejected). Admin UI at /ui/features. See CLAUDE.md for the rules.
FEATURE_STATUSES = (
    "requested",
    "in_dev",
    "deployed_dev",
    "deployed_prod",
    "rejected",
)
FEATURE_PRIORITIES = ("low", "normal", "high", "critical")

# ─── Reusable function / library registry ───────────────────────────────────
# Catalog of every reusable function/library in the platform with its
# inputs, outputs, API surface, and source location. Admin UI at
# /ui/functions. See CLAUDE.md for the rules.
FUNCTION_STATUSES = ("draft", "stable", "deprecated")
FUNCTION_LANGUAGES = (
    "python",
    "micropython",
    "typescript",
    "javascript",
    "sql",
    "shell",
    "yaml",
    "json",
    "other",
)
# Criticality ladder matches shared_schemas/*.json safety_class so a
# function's blast-radius lines up with the existing edge alarm /
# allow-list machinery. AI Agent must NEVER auto-invoke critical or
# life_safety functions without an explicit human-in-the-loop override.
FUNCTION_CRITICALITIES = ("nominal", "advisory", "critical", "life_safety")

WIFI_SECURITY_TYPES = ("wpa2", "wpa3", "wpa2_enterprise", "open")


class WifiNetwork(Base):
    """A WiFi network profile that devices can be configured to connect to.

    Password is stored encrypted (Fernet with a key derived from the
    deployment session secret); plaintext is only decrypted at firmware-
    bundle build time. Operators can mark a network 'hidden' if it
    doesn't broadcast its SSID.
    """
    __tablename__ = "wifi_networks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    ssid: Mapped[str] = mapped_column(String(120), nullable=False)
    security: Mapped[str] = mapped_column(String(20), default="wpa2", nullable=False)
    # WPA2-Enterprise also needs a username; for WPA2-Personal it stays NULL.
    username: Mapped[str | None] = mapped_column(String(120))
    # Always encrypted at rest. NULL for open networks.
    password_encrypted: Mapped[str | None] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(default=False, nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(4))  # e.g. "GB", "US"
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class FeatureRequest(Base):
    __tablename__ = "feature_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    short_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)  # e.g. FR-0001
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="requested", nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    git_sha_dev: Mapped[str | None] = mapped_column(String(40))
    git_sha_prod: Mapped[str | None] = mapped_column(String(40))
    deployed_dev_at: Mapped[datetime | None] = mapped_column()
    deployed_prod_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


# ─── Device-registration wizard (FR-0005) ────────────────────────────────────
# Hierarchy:
#   EdgeSystem  →  EdgeGroup  →  BoardInstance  →  ComponentInstance
#                                       ↑
#                                  GpioMapping
# An EdgeGroup is a logical sub-section of an edge ("Heater bank",
# "Production Line A") with ONE compute module (Pico 2 W, Pi 5, etc.)
# controlling N boards. Each board has N components.
class EdgeGroup(Base):
    __tablename__ = "edge_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    edge_id: Mapped[str] = mapped_column(ForeignKey("edge_systems.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    compute_stable_id: Mapped[str | None] = mapped_column(String(120))  # e.g. compute.pico2w
    hardware_revision: Mapped[str] = mapped_column(String(40), default="rev_a", nullable=False)
    base_firmware_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    app_bundle_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    config_schema_version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    device_dna: Mapped[str | None] = mapped_column(String(40), unique=True)  # filled after DNA generation
    dna_json: Mapped[dict | None] = mapped_column(JSON)
    brain_json: Mapped[dict | None] = mapped_column(JSON)
    firmware_bundle_path: Mapped[str | None] = mapped_column(Text)
    # WiFi networks the devices in this group will try on boot. Primary
    # is required for groups that need network; secondary is the
    # fallback. Both may be NULL for offline-only groups.
    primary_wifi_id: Mapped[str | None] = mapped_column(ForeignKey("wifi_networks.id"))
    secondary_wifi_id: Mapped[str | None] = mapped_column(ForeignKey("wifi_networks.id"))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    edge: Mapped[EdgeSystem] = relationship()
    boards: Mapped[list["BoardInstance"]] = relationship(
        back_populates="group", cascade="all, delete-orphan",
        order_by="BoardInstance.position",
    )
    primary_wifi: Mapped["WifiNetwork | None"] = relationship(
        foreign_keys=[primary_wifi_id]
    )
    secondary_wifi: Mapped["WifiNetwork | None"] = relationship(
        foreign_keys=[secondary_wifi_id]
    )

    __table_args__ = (UniqueConstraint("edge_id", "name", name="uq_group_per_edge"),)


class BoardInstance(Base):
    """A control board added to a group (L298 stepper driver, SSR relay,
    ADC HAT, IO expander, etc.). Picked from control_board_library."""
    __tablename__ = "board_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(ForeignKey("edge_groups.id"), nullable=False, index=True)
    board_stable_id: Mapped[str] = mapped_column(String(120), nullable=False)  # FK-by-name to library
    label: Mapped[str] = mapped_column(String(120), nullable=False)  # operator-given name
    role: Mapped[str | None] = mapped_column(String(80))  # e.g. "main", "redundant"
    position: Mapped[int] = mapped_column(default=0, nullable=False)  # display order
    created_at: Mapped[datetime] = mapped_column(default=_now)

    group: Mapped[EdgeGroup] = relationship(back_populates="boards")
    components: Mapped[list["ComponentInstance"]] = relationship(
        back_populates="board", cascade="all, delete-orphan",
        order_by="ComponentInstance.position",
    )
    gpio_mappings: Mapped[list["GpioMapping"]] = relationship(
        back_populates="board", cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("group_id", "label", name="uq_board_per_group"),)


class ComponentInstance(Base):
    """A sensor / actuator / camera wired to a specific board.
    Picked from components_library."""
    __tablename__ = "component_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    board_instance_id: Mapped[str] = mapped_column(
        ForeignKey("board_instances.id"), nullable=False, index=True
    )
    component_stable_id: Mapped[str] = mapped_column(String(120), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. "probe_a"
    label: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str | None] = mapped_column(String(80))
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)
    position: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    board: Mapped[BoardInstance] = relationship(back_populates="components")

    __table_args__ = (
        UniqueConstraint("board_instance_id", "instance_id", name="uq_component_per_board"),
    )


class GpioMapping(Base):
    """A single pin connection: compute pin ↔ board pin.
    Auto-allocator fills these on first run; operator overrides per row
    by setting locked_by_operator=True."""
    __tablename__ = "gpio_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    board_instance_id: Mapped[str] = mapped_column(
        ForeignKey("board_instances.id"), nullable=False, index=True
    )
    compute_pin: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. "GP2"
    board_pin: Mapped[str] = mapped_column(String(40), nullable=False)    # e.g. "EN"
    signal_name: Mapped[str | None] = mapped_column(String(80))
    direction: Mapped[str | None] = mapped_column(String(20))  # in | out | bidir
    locked_by_operator: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    board: Mapped[BoardInstance] = relationship(back_populates="gpio_mappings")

    __table_args__ = (
        UniqueConstraint("board_instance_id", "compute_pin", name="uq_pin_per_board"),
    )


class CodeFunction(Base):
    """A reusable function or library catalogued for repurpose.

    inputs / outputs are stored as JSON arrays of
        [{"name": str, "type": str, "required": bool, "description": str}, ...]
    so the UI can render a structured spec without us hard-coding columns.
    """
    __tablename__ = "code_functions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(20), default="python", nullable=False)
    inputs_json: Mapped[list] = mapped_column(JSON, default=list)
    outputs_json: Mapped[list] = mapped_column(JSON, default=list)
    api_endpoint: Mapped[str | None] = mapped_column(String(200))
    source_path: Mapped[str | None] = mapped_column(String(300))
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="stable", nullable=False, index=True)
    criticality: Mapped[str] = mapped_column(String(20), default="nominal", nullable=False, index=True)
    # AI Agent composability flag: True = the AI Agent may pick this
    # function up automatically when building a plan; False = humans only.
    agent_accessible: Mapped[bool] = mapped_column(default=True, nullable=False)
    example: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
