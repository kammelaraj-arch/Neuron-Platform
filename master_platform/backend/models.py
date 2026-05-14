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


class VendorDevice(Base):
    """A single device registered to a VendorAccount — a specific Tapo
    plug, IP camera, Hue bulb, etc. ComponentInstance.vendor_device_id
    points here, NOT at the parent account (the account drives many
    devices; we want the operator to bind to the *specific* one).

    Rows are populated either by auto-discovery (POST /vendor-accounts/
    {id}/discover hits the vendor's API to list devices) or manually
    (add-form on the vendor account row). Re-running discovery upserts
    by (vendor_account_id, vendor_device_id).
    """
    __tablename__ = "vendor_devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    vendor_account_id: Mapped[str] = mapped_column(
        ForeignKey("vendor_accounts.id"), nullable=False, index=True
    )
    # Vendor's own device identifier — Tapo deviceId hex, Hue 'id_v2',
    # Ring doorbell serial, etc. Treat as opaque from our side.
    vendor_device_id: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)       # operator-friendly
    model: Mapped[str | None] = mapped_column(String(80))                 # e.g. P100, C100
    device_type: Mapped[str | None] = mapped_column(String(80))           # plug / camera / bulb
    mac: Mapped[str | None] = mapped_column(String(40))
    ip_local: Mapped[str | None] = mapped_column(String(60))
    firmware_version: Mapped[str | None] = mapped_column(String(40))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("vendor_account_id", "vendor_device_id",
                         name="uq_vendor_device_per_account"),
    )


class VendorAccount(Base):
    """Cloud-account credentials for third-party vendors (TP-Link Tapo,
    Philips Hue, Google Nest, Ring, Ecobee, Aqara, …). One account
    typically drives many devices (e.g. a Tapo account controls a dozen
    plugs / cams / bulbs), so this lives at the Master level and each
    smart-home ComponentInstance binds to one row by `vendor_account_id`.

    Sensitive fields (password, refresh-token, API key) are Fernet-
    encrypted at rest via security.secret_crypto. Plaintext only
    appears in memory at firmware-bundle-build time or when a backend
    integration explicitly opts in via decrypt_secret().
    """
    __tablename__ = "vendor_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str | None] = mapped_column(String(200))   # email / OAuth subject
    password_encrypted: Mapped[str | None] = mapped_column(Text)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(String(40))         # e.g. "eu-west"
    base_url: Mapped[str | None] = mapped_column(String(400))
    extra_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column()
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
    # A Group is the Compute → Boards → Components → PinMap bundle.
    # The setup is identical for every device role; the only thing
    # that differs is which level of the hierarchy this bundle hangs
    # off. We model that with a single polymorphic parent pointer
    # (parent_kind discriminates which table parent_id targets).
    #   parent_kind="root"  → master  device (parent_id → root_systems.id)
    #   parent_kind="node"  → gateway device (parent_id → node_systems.id)
    #   parent_kind="edge"  → edge    device (parent_id → edge_systems.id)
    parent_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="edge")
    parent_id:   Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # Legacy 1-FK form. Kept nullable for back-compat with rows / queries
    # that pre-date the polymorphic parent. New code reads parent_*.
    edge_id: Mapped[str | None] = mapped_column(ForeignKey("edge_systems.id"), index=True)
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
    # SSH / first-boot deployment credentials. Used by the edge-runtime
    # (or the master directly when the device is publicly reachable) to
    # copy the firmware bundle on first registration + push OTA updates.
    # Sensitive fields are Fernet-encrypted at rest using
    # security.secret_crypto.
    ssh_host: Mapped[str | None] = mapped_column(String(200))           # IP or hostname
    ssh_port: Mapped[int] = mapped_column(default=22, nullable=False)
    ssh_username: Mapped[str | None] = mapped_column(String(120))
    ssh_password_encrypted: Mapped[str | None] = mapped_column(Text)
    ssh_private_key_encrypted: Mapped[str | None] = mapped_column(Text)
    sudo_password_encrypted: Mapped[str | None] = mapped_column(Text)
    mdns_hostname: Mapped[str | None] = mapped_column(String(200))      # e.g. raspberrypi.local
    # Whether the operator wants the master to use SSH for the
    # first-boot bundle push. The firmware itself disables ssh.service
    # at boot regardless (CLAUDE.md parent-only rule); this flag only
    # affects how the master gets the bundle onto the device.
    #   True  → 🚀 Deploy uses SSH (default)
    #   False → operator manually flashes the golden image / overlay
    ssh_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Operator-set PIN that locks the wizard configuration after testing.
    # When lock_pin_hash is set, write endpoints (boards / components /
    # gpio mappings / wifi / risk) require the matching plaintext PIN
    # passed as form field 'unlock_pin'. Hash uses Argon2id same as
    # the API-key store.
    lock_pin_hash: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column()
    locked_by: Mapped[str | None] = mapped_column(String(120))
    # 2026-05: spec-driven fields from docs/wizard_spec.md.
    # `role` distinguishes Master (control plane), Edge (sensors / actuators)
    # and Gateway (protocol bridge). `asset_id` is mandatory at the app
    # level (nullable in DB only so legacy rows back-fill cleanly).
    role: Mapped[str] = mapped_column(String(20), default="edge", nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(120), index=True)
    factory: Mapped[str | None] = mapped_column(String(120))
    line: Mapped[str | None] = mapped_column(String(120))
    machine: Mapped[str | None] = mapped_column(String(120))
    # Industrial protocols (MQTT / OPC-UA / Modbus / CAN). Each top-level
    # key is one protocol; only enabled protocols have a non-null config.
    protocols_json: Mapped[dict | None] = mapped_column(JSON)
    # IF-THEN local rules engine. Each entry:
    # {when: {instance_id, op, value}, then: {action, params}, critical}
    # `critical=true` means local-only enforcement (parent advisory).
    rules_json: Mapped[list | None] = mapped_column(JSON)
    # Telemetry channels + alert thresholds.
    telemetry_json: Mapped[dict | None] = mapped_column(JSON)
    # Connectivity extras (NTP, Bluetooth) — separate from WiFi which
    # already lives in primary_wifi_id / secondary_wifi_id.
    ntp_servers_json: Mapped[list | None] = mapped_column(JSON)
    bluetooth_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    # active | retired   (retired = decommissioned, certs revoked, no
    # further deploys / rollbacks permitted).
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    decommissioned_at: Mapped[datetime | None] = mapped_column()
    # 2026-05: connectivity & access (spec step 2). Captured on the
    # compute-pick screen alongside asset_id + UUID display.
    local_ip:    Mapped[str | None] = mapped_column(String(60))
    external_ip: Mapped[str | None] = mapped_column(String(60))
    hostname:    Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    edge: Mapped["EdgeSystem | None"] = relationship(foreign_keys=[edge_id])
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

    # Uniqueness is enforced per-parent at the app level (see
    # device_wizard_ui.wizard_create_group) — SQLite doesn't support
    # multi-column partial uniques cleanly.


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


# Risk levels and types for ComponentInstance — overrides the manifest's
# safety_class with the operator's per-install judgement (a heater wired
# next to flammable material is a different risk class from the same
# heater wired into a temperature-controlled water bath).
DEVICE_ROLES = ("master", "node", "edge", "gateway")
PROTOCOL_KEYS = ("mqtt", "opcua", "modbus", "can")
# Vendor cloud-account providers managed at the Master level. Each
# ComponentInstance that's a smart-home gadget (Tapo plug, Hue bulb,
# Nest thermostat, etc.) binds to one VendorAccount by id.
VENDOR_PROVIDERS = (
    "tapo", "kasa", "hue", "nest", "ecobee", "ring", "eufy",
    "aqara", "sonoff", "shelly", "smartthings", "homekit", "other",
)
RULE_ACTIONS = (
    "stop_motor", "set_pwm_zero", "open_relay", "close_relay",
    "publish_alarm", "safe_shutdown", "ignore",
)
RULE_OPS = (">", "<", ">=", "<=", "==", "!=")
RISK_LEVELS = ("nominal", "advisory", "critical", "life_safety")
RISK_TYPES = (
    "fire", "scald", "burn", "shock", "chemical", "biohazard",
    "pinch", "crush", "cut", "fall", "freeze", "asphyxiation",
    "explosion", "uv", "laser", "noise", "pressure",
)
# Failsafe action — what the local brain does when it has lost the
# connection to the edge/master for longer than disconnect_grace_seconds.
# The brain MUST be able to enforce these locally — it is the last line
# of defence and runs on the device itself.
FAILSAFE_ACTIONS = (
    "off",                # cut power / set duty cycle to 0 (heaters, pumps)
    "hold_last",          # freeze at last commanded value (slow conveyors)
    "go_to_safe_value",   # drive to failsafe_value_json (e.g. setpoint 0)
    "alarm_only",         # raise alarm but keep running (advisory only)
    "stop",               # halt motion / cycle (motors)
    "fail_open",          # open contact / valve (water dump valves)
    "fail_closed",        # close contact / valve (gas shut-off)
)


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
    # ── Board-pin assignment (which output channel of the board this
    # component is wired to, e.g. PCA9685 CH3 or L298N OUT1+OUT2) ───────────
    board_pin: Mapped[str | None] = mapped_column(String(40))
    # Operator-defined functional label for this instance — what it
    # physically does in the deployed system (e.g. "stirring",
    # "tilt-axis-z", "mixer", "feed-pump", "lid-open"). Used by twin
    # UI + AI agent for human-language references.
    function_label: Mapped[str | None] = mapped_column(String(80))
    # Operator's asset / inventory ID (e.g. "MTR-0042", "SRV-A12-3").
    asset_id: Mapped[str | None] = mapped_column(String(60))
    # ── Risk + failsafe (operator-set per install) ─────────────────────────
    risk_level: Mapped[str] = mapped_column(String(20), default="nominal", nullable=False)
    risk_types_json: Mapped[list] = mapped_column(JSON, default=list)
    failsafe_action: Mapped[str | None] = mapped_column(String(20))  # NULL = inherit from manifest
    failsafe_value_json: Mapped[dict | None] = mapped_column(JSON)
    # On disconnect: wait this long (heartbeat-loss tolerance) before the
    # brain enforces failsafe_action locally. 0 = immediate.
    disconnect_grace_seconds: Mapped[int] = mapped_column(default=30, nullable=False)
    # Maximum gap between commands before the brain assumes the link is dead.
    watchdog_ms: Mapped[int] = mapped_column(default=1000, nullable=False)
    # ── Direct-API integration (smart plugs, IP cameras, Hue, etc.) ────────
    # When the component is reached over WiFi/HTTP rather than GPIO, store
    # the endpoint + Fernet-encrypted API key. Plaintext never leaves
    # the master except inside the firmware bundle at build time.
    api_endpoint: Mapped[str | None] = mapped_column(String(400))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    # Cloud-account binding. Smart-home gear (Tapo plug, Hue bulb, Nest
    # thermostat, …) reaches its vendor over WiFi + the account's
    # cloud / local-LAN token. One VendorAccount drives many components,
    # so we point at it by id; the actual creds live encrypted on the
    # VendorAccount row + are decrypted at firmware-bundle-build time.
    vendor_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("vendor_accounts.id"), index=True
    )
    # The *specific* device on the account this component represents
    # (a particular Tapo camera, a specific Hue bulb). The agent on
    # the Pi targets this device id when sending commands; the parent
    # account just provides the auth context.
    vendor_device_id: Mapped[str | None] = mapped_column(
        ForeignKey("vendor_devices.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=_now)

    board: Mapped[BoardInstance] = relationship(back_populates="components")

    __table_args__ = (
        UniqueConstraint("board_instance_id", "instance_id", name="uq_component_per_board"),
    )


class BundleArtifact(Base):
    """Historical record of every firmware bundle built for an EdgeGroup.

    The group itself only tracks the *currently active* bundle
    (firmware_bundle_path, dna_json, brain_json). Every previous build
    is rolled into a row here so the operator can:
      • see deploy history with timestamps + per-build fingerprints,
      • rollback to a previous version (redeploys + flips current),
      • audit which cert fingerprints were on the wire when.

    The on-disk .zip lives at file_path until pruning policy says
    otherwise. dna_json + brain_json are persisted as snapshots so
    history is meaningful even after the .zip is GC'd.
    """
    __tablename__ = "bundle_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("edge_groups.id"), nullable=False, index=True
    )
    device_dna: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version_seq: Mapped[int] = mapped_column(nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    dna_snapshot: Mapped[dict | None] = mapped_column(JSON)
    brain_snapshot: Mapped[dict | None] = mapped_column(JSON)
    cert_fingerprint: Mapped[str | None] = mapped_column(String(80))
    emergency_fingerprint: Mapped[str | None] = mapped_column(String(80))
    # current | superseded | revoked | failed
    status: Mapped[str] = mapped_column(String(20), default="current", nullable=False)
    built_at: Mapped[datetime] = mapped_column(default=_now)
    deployed_at: Mapped[datetime | None] = mapped_column()
    decommissioned_at: Mapped[datetime | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("group_id", "version_seq", name="uq_bundle_per_group_version"),
    )


class DriverInstance(Base):
    """A plug-in driver chip / module slotted into a Board — separate from a Component.

    Per the corrected hierarchy (docs/wizard_spec.md), a Board owns two
    independent collections: Components (sensors / actuators wired to it)
    and Drivers (DRV8825, A4988, TMC22xx, relay modules, …). The Driver
    is the *chip* on the board; the actuator it drives is still a
    ComponentInstance bound to the same board. Both are 0-or-more.
    """
    __tablename__ = "driver_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    board_instance_id: Mapped[str] = mapped_column(
        ForeignKey("board_instances.id"), nullable=False, index=True
    )
    driver_stable_id: Mapped[str] = mapped_column(String(120), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120))
    asset_id: Mapped[str | None] = mapped_column(String(60))
    # Slot / socket on the board, e.g. "X-axis", "STEP1", "AXIS-A".
    board_slot: Mapped[str | None] = mapped_column(String(40))
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)
    position: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("board_instance_id", "instance_id", name="uq_driver_per_board"),
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
