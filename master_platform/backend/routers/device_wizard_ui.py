"""Step-wise device-registration wizard (FR-0005).

Replaces the single flat /ui/devices/new form with a multi-step flow:

  Step 1 — pick Edge + name a Group (logical sub-section)
  Step 2 — pick Compute module (Pico 2 W, Pi 5, …)
  Step 3 — add Boards (control_board_library) into the group
  Step 4 — add Components per board (components_library)
  Step 5 — GPIO Pin Map (auto + override)
  Step 6 — Review & build firmware bundle

Aesthetic: dark, neon-emerald accents, SVG board silhouettes with pin
labels around the perimeter (sleek "digital twin" feel).

All steps mutate a single EdgeGroup row. The Group is created on
step 1 and updated as the operator progresses. Browser back works
because each step's URL is bookmarkable: /ui/devices/wizard/<group_id>/<step>.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func

from ..config import settings
from ..db import get_session
from ..group_bundle import build_group_bundle
from ..library_loader import load_catalog
from ..compute_pinouts import PIN_KIND_COLORS, header_for
from ..board_pinouts import (
    PIN_COMPAT,
    PINOUT_KIND_COLORS,
    PINOUT_KIND_COLORS_OUT,
    board_pin_kind,
    connection_type,
    is_compatible,
    pinout_for,
    split_pinout,
)
from ..models import (
    FAILSAFE_ACTIONS,
    RISK_LEVELS,
    RISK_TYPES,
    APIKey,
    BoardInstance,
    ComponentInstance,
    EdgeGroup,
    EdgeSystem,
    GpioMapping,
    NodeSystem,
    RootSystem,
    WifiNetwork,
)
from ..pin_allocator import PinAllocationError, auto_allocate
from ..security.audit import record
from ..security.ui_auth import ui_require_login


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-device-wizard"])


WIZARD_STEPS = [
    ("group", "Group"),
    ("compute", "Compute"),
    ("wifi", "WiFi"),
    ("boards", "Boards"),
    ("components", "Components"),
    ("pinmap", "Pin map"),
    ("review", "Review"),
]


async def _load_group(session: AsyncSession, group_id: str) -> EdgeGroup:
    g = (
        await session.execute(select(EdgeGroup).where(EdgeGroup.id == group_id))
    ).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, "group not found")
    return g


# ─── Configuration lock (session-backed) ─────────────────────────────────
def _session_unlocked(request: Request, group_id: str) -> bool:
    """True if this session has previously unlocked this group with the
    correct PIN. Stored in session as {'unlocked_groups': {gid: ts}}."""
    info = request.session.get("unlocked_groups", {}) or {}
    return bool(info.get(group_id))


def _set_session_unlocked(request: Request, group_id: str) -> None:
    import time
    info = request.session.get("unlocked_groups", {}) or {}
    info[group_id] = int(time.time())
    request.session["unlocked_groups"] = info


def _clear_session_unlocked(request: Request, group_id: str) -> None:
    info = request.session.get("unlocked_groups", {}) or {}
    info.pop(group_id, None)
    request.session["unlocked_groups"] = info


def _require_unlocked(group: EdgeGroup, request: Request) -> None:
    """If the group is locked, allow only when the operator has unlocked
    it in this session. Raises 403 otherwise."""
    if not group.lock_pin_hash:
        return
    if _session_unlocked(request, group.id):
        return
    raise HTTPException(
        403,
        "This configuration is locked. Open the Review page and enter the "
        "unlock PIN before making changes.",
    )


@router.post("/ui/devices/wizard/{group_id}/protocols")
async def wizard_set_protocols(
    group_id: str,
    request: Request,
    mqtt_enabled: bool = Form(False),
    mqtt_broker_url: str = Form(""),
    mqtt_port: int = Form(8883),
    mqtt_tls: bool = Form(False),
    mqtt_base_topic: str = Form(""),
    opcua_enabled: bool = Form(False),
    opcua_endpoint: str = Form(""),
    opcua_tag_map: str = Form(""),
    modbus_enabled: bool = Form(False),
    modbus_host: str = Form(""),
    modbus_port: int = Form(502),
    modbus_serial: str = Form(""),
    modbus_baud: int = Form(9600),
    modbus_slave_ids: str = Form(""),
    can_enabled: bool = Form(False),
    can_iface: str = Form(""),
    can_bitrate: int = Form(500000),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Persist the industrial-protocol config for this group. Each
    section is optional; only enabled protocols are stored."""
    import json as _json
    group = await _load_group(session, group_id)
    _require_unlocked(group, request)

    protos: dict = {}
    if mqtt_enabled:
        protos["mqtt"] = {
            "broker_url": mqtt_broker_url.strip() or None,
            "port": int(mqtt_port),
            "tls": bool(mqtt_tls),
            "base_topic": mqtt_base_topic.strip() or None,
        }
    if opcua_enabled:
        tag_map = {}
        if opcua_tag_map.strip():
            try:
                tag_map = _json.loads(opcua_tag_map)
            except _json.JSONDecodeError:
                # Keep raw text so the operator can fix it without losing data.
                tag_map = {}
        protos["opcua"] = {
            "endpoint": opcua_endpoint.strip() or None,
            "tag_map": tag_map,
            "tag_map_raw": opcua_tag_map,
        }
    if modbus_enabled:
        slaves: list[int] = []
        for chunk in (modbus_slave_ids or "").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                slaves.append(int(chunk))
        protos["modbus"] = {
            "host": modbus_host.strip() or None,
            "port": int(modbus_port),
            "serial_port": modbus_serial.strip() or None,
            "baud": int(modbus_baud),
            "slave_ids": slaves,
            "slave_ids_raw": modbus_slave_ids,
        }
    if can_enabled:
        protos["can"] = {
            "iface": can_iface.strip() or "can0",
            "bitrate": int(can_bitrate),
        }

    group.protocols_json = protos or None
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.set_protocols", target_kind="edge_group", target_id=group.id,
        detail={"enabled": list(protos.keys())},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/rules/add")
async def wizard_add_rule(
    group_id: str,
    request: Request,
    rule_instance: str = Form(...),
    rule_op: str = Form(...),
    rule_value: str = Form(...),
    rule_action: str = Form(...),
    rule_critical: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    from ..models import RULE_ACTIONS, RULE_OPS
    group = await _load_group(session, group_id)
    _require_unlocked(group, request)
    if rule_op not in RULE_OPS:
        raise HTTPException(400, f"op must be one of {RULE_OPS}")
    if rule_action not in RULE_ACTIONS:
        raise HTTPException(400, f"action must be one of {RULE_ACTIONS}")
    if not rule_instance.strip() or not rule_value.strip():
        raise HTTPException(400, "instance and value are required")

    # Try to coerce the value to a number for the brain; fall back to str.
    val: float | str = rule_value.strip()
    try:
        val = float(rule_value)
        if val.is_integer():
            val = int(val)
    except ValueError:
        pass

    rules = list(group.rules_json or [])
    rules.append({
        "when": {"instance_id": rule_instance.strip(), "op": rule_op, "value": val},
        "then": {"action": rule_action, "params": {}},
        "critical": bool(rule_critical),
    })
    group.rules_json = rules
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.add_rule", target_kind="edge_group", target_id=group.id,
        detail={"rule_count": len(rules), "action": rule_action, "critical": bool(rule_critical)},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/rules/{idx}/delete")
async def wizard_delete_rule(
    group_id: str,
    idx: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    _require_unlocked(group, request)
    rules = list(group.rules_json or [])
    if 0 <= idx < len(rules):
        rules.pop(idx)
        group.rules_json = rules
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="group.delete_rule", target_kind="edge_group", target_id=group.id,
            detail={"index": idx, "remaining": len(rules)},
        )
        await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/ssh")
async def wizard_set_ssh(
    group_id: str,
    request: Request,
    ssh_host: str = Form(""),
    ssh_port: int = Form(22),
    ssh_username: str = Form(""),
    ssh_password: str = Form(""),
    ssh_private_key: str = Form(""),
    sudo_password: str = Form(""),
    mdns_hostname: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Store the SSH / sudo credentials used by the edge runtime (or
    Master, when directly reachable) to push firmware bundles to the
    compute module. Sensitive fields are Fernet-encrypted via
    security.secret_crypto so plaintext only ever appears in memory at
    firmware-deploy time."""
    from ..security.secret_crypto import encrypt_secret

    group = await _load_group(session, group_id)
    _require_unlocked(group, request)

    group.ssh_host = ssh_host.strip() or None
    group.ssh_port = int(ssh_port) if ssh_port else 22
    group.ssh_username = ssh_username.strip() or None
    # Only re-encrypt secrets when the operator typed a new value;
    # an empty submission means "keep existing".
    if ssh_password.strip():
        group.ssh_password_encrypted = encrypt_secret(ssh_password)
    if ssh_private_key.strip():
        group.ssh_private_key_encrypted = encrypt_secret(ssh_private_key)
    if sudo_password.strip():
        group.sudo_password_encrypted = encrypt_secret(sudo_password)
    group.mdns_hostname = mdns_hostname.strip() or None

    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.set_ssh", target_kind="edge_group", target_id=group.id,
        detail={
            "host": group.ssh_host, "port": group.ssh_port,
            "username": group.ssh_username, "has_password": bool(group.ssh_password_encrypted),
            "has_private_key": bool(group.ssh_private_key_encrypted),
            "has_sudo_password": bool(group.sudo_password_encrypted),
        },
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/lock")
async def wizard_lock_group(
    group_id: str,
    request: Request,
    pin: str = Form(...),
    pin_confirm: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Set a PIN that locks the configuration. 4-12 digits."""
    group = await _load_group(session, group_id)
    pin = (pin or "").strip()
    if not pin.isdigit() or not (4 <= len(pin) <= 12):
        raise HTTPException(400, "PIN must be 4-12 digits.")
    if pin != pin_confirm.strip():
        raise HTTPException(400, "PIN and confirmation don't match.")
    from argon2 import PasswordHasher
    group.lock_pin_hash = PasswordHasher().hash(pin)
    group.locked_at = datetime.now(timezone.utc)
    group.locked_by = actor.label or actor.owner or actor.id
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.lock", target_kind="edge_group", target_id=group.id,
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/unlock")
async def wizard_unlock_group(
    group_id: str,
    request: Request,
    pin: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Remove the lock — requires the current PIN."""
    group = await _load_group(session, group_id)
    if not group.lock_pin_hash:
        return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)

    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError
    pin = (pin or "").strip()
    try:
        PasswordHasher().verify(group.lock_pin_hash, pin)
    except (VerifyMismatchError, InvalidHashError, Exception):
        raise HTTPException(403, "Incorrect PIN.")

    group.lock_pin_hash = None
    group.locked_at = None
    group.locked_by = None
    _clear_session_unlocked(request, group.id)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.unlock", target_kind="edge_group", target_id=group.id,
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)


def _step_index(step: str) -> int:
    for i, (k, _) in enumerate(WIZARD_STEPS):
        if k == step:
            return i
    raise HTTPException(404, f"unknown wizard step: {step}")


# ─── Resume-group helper ────────────────────────────────────────────────
async def _group_status(session: AsyncSession, g: EdgeGroup) -> dict:
    """Compute the resume URL + a status string for a group so the
    list view can show 'continue at step X' next to each row."""
    boards_count = await session.scalar(
        select(sa_func.count()).select_from(BoardInstance).where(BoardInstance.group_id == g.id)
    ) or 0
    comps_count = await session.scalar(
        select(sa_func.count()).select_from(ComponentInstance)
        .join(BoardInstance, BoardInstance.id == ComponentInstance.board_instance_id)
        .where(BoardInstance.group_id == g.id)
    ) or 0
    pins_count = await session.scalar(
        select(sa_func.count()).select_from(GpioMapping)
        .join(BoardInstance, BoardInstance.id == GpioMapping.board_instance_id)
        .where(BoardInstance.group_id == g.id)
    ) or 0
    has_firmware = bool(g.firmware_bundle_path)
    is_locked = bool(g.lock_pin_hash)

    # Master / Node devices may have no control boards at all (they're
    # the control plane, not the I/O endpoint). Skip the boards /
    # components / pinmap gates for those roles.
    headless = (g.role or "edge") in ("master", "node")

    # next-step routing
    if not g.compute_stable_id:
        next_step, status = "compute", "incomplete · pick compute"
    elif boards_count == 0 and not headless:
        next_step, status = "boards", "incomplete · no boards"
    elif comps_count == 0 and not headless:
        next_step, status = "components", "incomplete · no components"
    elif pins_count == 0 and not headless and boards_count > 0:
        next_step, status = "pinmap", "needs pin map"
    elif not has_firmware:
        next_step, status = "review", "ready to build"
    else:
        next_step, status = "review", "built"
    return {
        "boards_count": boards_count,
        "comps_count": comps_count,
        "pins_count": pins_count,
        "has_firmware": has_firmware,
        "is_locked": is_locked,
        "next_step": next_step,
        "status": status,
    }


# ─── Entry point: pick or create Root/Node/Edge + name Group ────────────────
@router.get("/ui/devices/wizard", response_class=HTMLResponse)
async def wizard_entry(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    # Wizard entry is now a clean "what role am I configuring + what's
    # its name" card. The Root/Node/Edge tree is managed on
    # /ui/systems; the wizard's parent dropdown lists existing rows
    # filtered by the chosen role (auto-creates a default tree if the
    # operator picks no parent).
    roots = (await session.execute(
        select(RootSystem).order_by(RootSystem.name)
    )).scalars().all()
    nodes = (await session.execute(
        select(NodeSystem).order_by(NodeSystem.name)
    )).scalars().all()
    edges = (await session.execute(
        select(EdgeSystem).order_by(EdgeSystem.name)
    )).scalars().all()
    return templates.TemplateResponse(
        "device_wizard_step1.html",
        {
            "request": request,
            "steps": WIZARD_STEPS,
            "step_idx": 0,
            "roots": roots,
            "nodes": nodes,
            "edges": edges,
        },
    )


async def _ensure_parent_for_role(
    session: AsyncSession,
    role: str,
    actor: APIKey,
) -> tuple[str, str]:
    """Auto-resolve (parent_kind, parent_id) for the given device role.
    Creates a default Root / Node / Edge in place if none exists yet, so
    the operator never has to touch the System Designer first. The tree
    can still be edited later at /ui/systems."""
    # Always need a Root — every tier hangs off it.
    root = (await session.execute(
        select(RootSystem).order_by(RootSystem.created_at).limit(1)
    )).scalar_one_or_none()
    if root is None:
        root = RootSystem(name="Default Root")
        session.add(root)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="root.create", target_kind="root_system", target_id=root.id,
            detail={"name": root.name, "source": "wizard.auto"},
        )

    if role == "master":
        return "root", root.id

    # node / edge / gateway all live under a Node.
    node = (await session.execute(
        select(NodeSystem).where(NodeSystem.root_id == root.id)
        .order_by(NodeSystem.created_at).limit(1)
    )).scalar_one_or_none()
    if node is None:
        node = NodeSystem(root_id=root.id, name="Default Node")
        session.add(node)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="node.create", target_kind="node_system", target_id=node.id,
            detail={"root_id": root.id, "name": node.name, "source": "wizard.auto"},
        )

    if role == "node":
        return "node", node.id

    # edge + gateway: live under an Edge (gateway is a protocol-bridge edge).
    edge = (await session.execute(
        select(EdgeSystem).where(EdgeSystem.node_id == node.id)
        .order_by(EdgeSystem.created_at).limit(1)
    )).scalar_one_or_none()
    if edge is None:
        edge = EdgeSystem(node_id=node.id, name="Default Edge", site_id="default")
        session.add(edge)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="edge.create", target_kind="edge_system", target_id=edge.id,
            detail={"node_id": node.id, "name": edge.name, "source": "wizard.auto"},
        )
    return "edge", edge.id


@router.post("/ui/devices/wizard/create-group")
async def wizard_create_group(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    role: str = Form("edge"),
    parent_id: str = Form(""),  # explicit pick; blank → auto-resolve
    asset_id: str = Form(""),
    factory: str = Form(""),
    line: str = Form(""),
    machine: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    # Step 1 captures role + identity. If the operator picked an
    # explicit parent in the dropdown we use it (validated below);
    # otherwise _ensure_parent_for_role auto-resolves the tree
    # (creating defaults where missing).
    from ..models import DEVICE_ROLES
    role_clean = (role or "edge").strip().lower()
    if role_clean not in DEVICE_ROLES:
        raise HTTPException(400, f"role must be one of {DEVICE_ROLES}")
    name = name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    asset_id_clean = (asset_id or "").strip() or None   # optional now

    # Map role → expected parent_kind. Master always attaches to a Root
    # (and the form disables the parent picker for Master, since there
    # is at most one Root in a single-tenant deployment).
    expected_kind = {
        "master":  "root",
        "node":    "root",
        "edge":    "node",
        "gateway": "node",
    }[role_clean]

    parent_id_clean = (parent_id or "").strip()
    if parent_id_clean and role_clean != "master":
        # Validate the picked parent is the right kind.
        if expected_kind == "root":
            row = await session.get(RootSystem, parent_id_clean)
        elif expected_kind == "node":
            row = await session.get(NodeSystem, parent_id_clean)
        else:
            row = await session.get(EdgeSystem, parent_id_clean)
        if row is None:
            raise HTTPException(400, f"parent {parent_id_clean} not found ({expected_kind})")
        parent_kind = expected_kind
        parent_id_v = parent_id_clean
        # For roles whose bundle pins to an edge (edge / gateway), the
        # operator's pick is the parent Node — we still need an Edge
        # row under that Node, so fall through to ensure helper to
        # find-or-create one.
        if role_clean in ("edge", "gateway"):
            parent_kind, parent_id_v = await _ensure_parent_for_role(session, role_clean, actor)
    else:
        parent_kind, parent_id_v = await _ensure_parent_for_role(session, role_clean, actor)

    # Per-parent uniqueness check.
    existing_group = (
        await session.execute(
            select(EdgeGroup).where(
                EdgeGroup.parent_kind == parent_kind,
                EdgeGroup.parent_id == parent_id_v,
                EdgeGroup.name == name,
            )
        )
    ).scalar_one_or_none()

    if existing_group is not None:
        group = existing_group
        # Idempotent: update fields when the operator re-saves step 1.
        group.role = role_clean
        group.asset_id = asset_id_clean
        group.factory = factory.strip() or None
        group.line = line.strip() or None
        group.machine = machine.strip() or None
        # Re-pin parent in case the role changed.
        group.parent_kind = parent_kind
        group.parent_id = parent_id_v
        group.edge_id = parent_id_v if parent_kind == "edge" else None
        if description.strip():
            group.description = description.strip()
    else:
        group = EdgeGroup(
            parent_kind=parent_kind,
            parent_id=parent_id_v,
            edge_id=parent_id_v if parent_kind == "edge" else None,
            name=name,
            description=description.strip() or None,
            role=role_clean,
            asset_id=asset_id_clean,
            factory=factory.strip() or None,
            line=line.strip() or None,
            machine=machine.strip() or None,
        )
        session.add(group)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="group.create", target_kind="edge_group", target_id=group.id,
            detail={
                "parent_kind": parent_kind, "parent_id": parent_id_v,
                "name": name, "role": role_clean, "asset_id": asset_id_clean,
                "factory": group.factory, "line": group.line, "machine": group.machine,
            },
        )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/compute", status_code=303)


# ─── Step 2: pick compute module ────────────────────────────────────────────
@router.get("/ui/devices/wizard/{group_id}/compute", response_class=HTMLResponse)
async def wizard_step_compute(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    catalog = load_catalog()
    computes = catalog.list_library("micro_compute_library")
    return templates.TemplateResponse(
        "device_wizard_step2.html",
        {
            "request": request,
            "group": group,
            "computes": computes,
            "steps": WIZARD_STEPS,
            "step_idx": 1,
        },
    )


@router.post("/ui/devices/wizard/{group_id}/compute")
async def wizard_save_compute(
    group_id: str,
    request: Request,
    compute_stable_id: str = Form(...),
    hardware_revision: str = Form("rev_a"),
    asset_id: str = Form(""),
    local_ip: str = Form(""),
    external_ip: str = Form(""),
    hostname: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    # Asset ID is mandatory at the end of step 2 — by the time the
    # operator commits the hardware, the asset reference must be set
    # (it lives with the unit for its lifetime).
    asset_id_clean = (asset_id or "").strip()
    if not asset_id_clean:
        raise HTTPException(400, "Asset ID is required.")
    group.compute_stable_id = compute_stable_id
    group.hardware_revision = hardware_revision.strip() or "rev_a"
    group.asset_id = asset_id_clean
    group.local_ip = local_ip.strip() or None
    group.external_ip = external_ip.strip() or None
    group.hostname = hostname.strip() or None
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.set_compute", target_kind="edge_group", target_id=group.id,
        detail={
            "compute": compute_stable_id,
            "asset_id": group.asset_id,
            "local_ip": group.local_ip, "external_ip": group.external_ip,
            "hostname": group.hostname,
        },
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/wifi", status_code=303)


# ─── Step 3: WiFi networks ──────────────────────────────────────────────────
@router.get("/ui/devices/wizard/{group_id}/wifi", response_class=HTMLResponse)
async def wizard_step_wifi(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    wifis = (
        await session.execute(select(WifiNetwork).order_by(WifiNetwork.name))
    ).scalars().all()
    return templates.TemplateResponse(
        "device_wizard_step_wifi.html",
        {
            "request": request,
            "group": group,
            "wifis": wifis,
            "steps": WIZARD_STEPS,
            "step_idx": 2,
        },
    )


@router.post("/ui/devices/wizard/{group_id}/wifi")
async def wizard_save_wifi(
    group_id: str,
    request: Request,
    primary_wifi_id: str = Form(""),
    secondary_wifi_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    group.primary_wifi_id = primary_wifi_id.strip() or None
    group.secondary_wifi_id = secondary_wifi_id.strip() or None
    if group.primary_wifi_id and group.primary_wifi_id == group.secondary_wifi_id:
        raise HTTPException(400, "primary and secondary must be different (or leave secondary blank)")
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.set_wifi", target_kind="edge_group", target_id=group.id,
        detail={"primary": group.primary_wifi_id, "secondary": group.secondary_wifi_id},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/boards", status_code=303)


# ─── Scan proxies (Master → Edge runtime → results) ────────────────────────
async def _proxy_edge(edge: EdgeSystem, path: str) -> dict:
    if not edge.address:
        raise HTTPException(
            400,
            "this Edge has no address — set Edge URL in /ui/systems so the Master can reach the edge runtime",
        )
    url = edge.address.rstrip("/") + path
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(url)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"edge scan failed at {url}: {exc}")


@router.post("/api/wizard/{group_id}/scan-wifi", response_class=JSONResponse)
async def wizard_scan_wifi(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    _require_unlocked(group, request)
    # Edge-side scan helpers only apply when the bundle is pinned to an
    # actual Edge unit. Master / gateway bundles don't have an edge to
    # proxy through.
    if group.parent_kind != "edge":
        raise HTTPException(400, f"edge-side scan is only available for edge devices; this group is a {group.parent_kind}-attached {group.role}")
    edge = await session.get(EdgeSystem, group.parent_id or group.edge_id)
    if edge is None:
        raise HTTPException(404, "group's edge not found")
    return await _proxy_edge(edge, "/api/v1/scan/wifi")


@router.post("/api/wizard/{group_id}/scan-devices", response_class=JSONResponse)
async def wizard_scan_devices(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    # Edge-side scan helpers only apply when the bundle is pinned to an
    # actual Edge unit. Master / gateway bundles don't have an edge to
    # proxy through.
    if group.parent_kind != "edge":
        raise HTTPException(400, f"edge-side scan is only available for edge devices; this group is a {group.parent_kind}-attached {group.role}")
    edge = await session.get(EdgeSystem, group.parent_id or group.edge_id)
    if edge is None:
        raise HTTPException(404, "group's edge not found")
    return await _proxy_edge(edge, "/api/v1/scan/devices")


# ─── Client-side scan ingestion ─────────────────────────────────────────────
# Browsers cannot scan WiFi (no JS API). The operator runs a one-line
# helper command in their own laptop terminal that POSTs scan results
# here. The endpoint caches results per-group in memory + DB so the
# wizard UI can display them next to the edge-side results.
_CLIENT_SCAN_CACHE: dict[str, dict] = {}  # group_id -> {"wifi": [...], "devices": [...], "ts": float}


@router.post("/api/wizard/{group_id}/client-scan", response_class=JSONResponse)
async def client_scan_ingest(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Receive scan results from a client-side helper script.

    Body: {"wifi": [{ssid, signal, security, ...}], "devices": [{ip, mac, hostname}]}
    """
    import time
    group = await _load_group(session, group_id)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    payload = {
        "wifi": body.get("wifi") or [],
        "devices": body.get("devices") or [],
        "host_hint": body.get("host_hint") or "",
        "ts": time.time(),
    }
    if not isinstance(payload["wifi"], list) or not isinstance(payload["devices"], list):
        raise HTTPException(400, "'wifi' and 'devices' must be arrays")
    _CLIENT_SCAN_CACHE[group.id] = payload
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="wizard.client_scan", target_kind="edge_group", target_id=group.id,
        detail={"wifi_count": len(payload["wifi"]), "device_count": len(payload["devices"])},
    )
    await session.commit()
    return {"ok": True, "wifi_count": len(payload["wifi"]), "device_count": len(payload["devices"])}


@router.get("/api/wizard/{group_id}/client-scan", response_class=JSONResponse)
async def client_scan_read(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Latest client-side scan results, if any."""
    await _load_group(session, group_id)
    return _CLIENT_SCAN_CACHE.get(group_id, {"wifi": [], "devices": [], "ts": None, "host_hint": ""})


@router.post("/ui/devices/wizard/{group_id}/wifi/save-scan-result")
async def wizard_save_scan_as_wifi(
    group_id: str,
    request: Request,
    name: str = Form(...),
    ssid: str = Form(...),
    security: str = Form("wpa2"),
    password: str = Form(""),
    assign: str = Form("primary"),  # "primary" | "secondary" | "none"
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """One-click 'Save this scanned network as a WiFi profile' from the
    wizard. Optionally assigns it as primary or secondary on the group."""
    from ..models import WIFI_SECURITY_TYPES, WifiNetwork
    from ..security.secret_crypto import encrypt_secret

    group = await _load_group(session, group_id)
    name = name.strip()
    ssid = ssid.strip()
    if not name or not ssid:
        raise HTTPException(400, "name and ssid required")
    sec = (security or "wpa2").lower()
    # nmcli reports things like "wpa2-personal", "wpa3-personal", "--", etc.
    if "wpa3" in sec:
        sec = "wpa3"
    elif "wpa2" in sec or "wpa" in sec or "psk" in sec:
        sec = "wpa2"
    elif sec in ("", "--", "none", "open"):
        sec = "open"
    if sec not in WIFI_SECURITY_TYPES:
        sec = "wpa2"
    if sec != "open" and not password:
        raise HTTPException(400, "password required for non-open networks")

    clash = await session.scalar(select(WifiNetwork).where(WifiNetwork.name == name))
    if clash is not None:
        raise HTTPException(409, f"WiFi profile '{name}' already exists; edit it directly at /ui/wifi/{clash.id}")
    wifi = WifiNetwork(
        name=name, ssid=ssid, security=sec,
        password_encrypted=encrypt_secret(password) if password else None,
    )
    session.add(wifi)
    await session.flush()
    if assign == "primary":
        group.primary_wifi_id = wifi.id
    elif assign == "secondary":
        if group.primary_wifi_id == wifi.id:
            raise HTTPException(400, "secondary must differ from primary")
        group.secondary_wifi_id = wifi.id
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="wifi.create_from_scan", target_kind="wifi_network", target_id=wifi.id,
        detail={"ssid": ssid, "assigned_to_group": group.id, "as": assign},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/wifi", status_code=303)


# ─── Step 4: add control boards ─────────────────────────────────────────────
@router.get("/ui/devices/wizard/{group_id}/boards", response_class=HTMLResponse)
async def wizard_step_boards(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    catalog = load_catalog()
    available_boards = catalog.list_library("control_board_library")
    boards = (
        await session.execute(
            select(BoardInstance).where(BoardInstance.group_id == group.id)
            .order_by(BoardInstance.position)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        "device_wizard_step3.html",
        {
            "request": request,
            "group": group,
            "available_boards": available_boards,
            "boards": boards,
            "catalog": catalog,
            "steps": WIZARD_STEPS,
            "step_idx": 3,
        },
    )


@router.post("/ui/devices/wizard/{group_id}/boards/add")
async def wizard_add_board(
    group_id: str,
    request: Request,
    board_stable_id: str = Form(...),
    label: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    catalog = load_catalog()
    bdef = catalog.get(board_stable_id)
    if bdef is None or bdef.library != "control_board_library":
        raise HTTPException(404, f"board {board_stable_id} not in control_board_library")
    label = (label or "").strip() or bdef.name
    # Ensure unique label within group
    existing = (
        await session.execute(
            select(BoardInstance).where(
                BoardInstance.group_id == group.id, BoardInstance.label == label
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # auto-suffix duplicate labels
        i = 2
        while True:
            candidate = f"{label} #{i}"
            clash = (
                await session.execute(
                    select(BoardInstance).where(
                        BoardInstance.group_id == group.id,
                        BoardInstance.label == candidate,
                    )
                )
            ).scalar_one_or_none()
            if clash is None:
                label = candidate
                break
            i += 1

    pos = (
        await session.scalar(
            select(BoardInstance.position).where(BoardInstance.group_id == group.id)
            .order_by(BoardInstance.position.desc())
        )
    ) or 0
    board = BoardInstance(
        group_id=group.id, board_stable_id=board_stable_id,
        label=label, position=pos + 1,
    )
    session.add(board)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="board.add", target_kind="board_instance", target_id=board.id,
        detail={"group_id": group.id, "board": board_stable_id, "label": label},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/boards", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/boards/{board_id}/delete")
async def wizard_delete_board(
    group_id: str,
    board_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    board = await session.get(BoardInstance, board_id)
    if board is None or board.group_id != group.id:
        raise HTTPException(404, "board not found")
    await session.delete(board)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="board.delete", target_kind="board_instance", target_id=board_id,
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/boards", status_code=303)


# ─── Step 4: components per board ───────────────────────────────────────────
@router.get("/ui/devices/wizard/{group_id}/components", response_class=HTMLResponse)
async def wizard_step_components(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    catalog = load_catalog()
    boards = (
        await session.execute(
            select(BoardInstance).where(BoardInstance.group_id == group.id)
            .order_by(BoardInstance.position)
        )
    ).scalars().all()
    boards_with_components = []
    for b in boards:
        comps = (
            await session.execute(
                select(ComponentInstance).where(ComponentInstance.board_instance_id == b.id)
                .order_by(ComponentInstance.position)
            )
        ).scalars().all()
        boards_with_components.append((b, comps))
    available_components = catalog.list_library("components_library")
    return templates.TemplateResponse(
        "device_wizard_step4.html",
        {
            "request": request,
            "group": group,
            "boards_with_components": boards_with_components,
            "available_components": available_components,
            "catalog": catalog,
            "RISK_LEVELS": RISK_LEVELS,
            "RISK_TYPES": RISK_TYPES,
            "FAILSAFE_ACTIONS": FAILSAFE_ACTIONS,
            "steps": WIZARD_STEPS,
            "step_idx": 4,
        },
    )


@router.post("/ui/devices/wizard/{group_id}/components/add")
async def wizard_add_component(
    group_id: str,
    request: Request,
    board_instance_id: str = Form(...),
    component_stable_id: str = Form(...),
    instance_id: str = Form(""),
    label: str = Form(""),
    board_pin: str = Form(""),
    function_label: str = Form(""),
    asset_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    board = await session.get(BoardInstance, board_instance_id)
    if board is None or board.group_id != group.id:
        raise HTTPException(404, "board not in this group")
    catalog = load_catalog()
    cdef = catalog.get(component_stable_id)
    if cdef is None or cdef.library != "components_library":
        raise HTTPException(404, f"component {component_stable_id} not in components_library")

    instance_id = (instance_id or "").strip() or component_stable_id.split(".")[-1]
    # Auto-uniquify instance_id within the board.
    base_iid = instance_id
    i = 2
    while True:
        clash = (
            await session.execute(
                select(ComponentInstance).where(
                    ComponentInstance.board_instance_id == board.id,
                    ComponentInstance.instance_id == instance_id,
                )
            )
        ).scalar_one_or_none()
        if clash is None:
            break
        instance_id = f"{base_iid}_{i}"
        i += 1

    pos = (
        await session.scalar(
            select(ComponentInstance.position)
            .where(ComponentInstance.board_instance_id == board.id)
            .order_by(ComponentInstance.position.desc())
        )
    ) or 0
    comp = ComponentInstance(
        board_instance_id=board.id,
        component_stable_id=component_stable_id,
        instance_id=instance_id,
        label=(label or "").strip() or None,
        position=pos + 1,
        board_pin=(board_pin or "").strip() or None,
        function_label=(function_label or "").strip() or None,
        asset_id=(asset_id or "").strip() or None,
    )
    session.add(comp)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="component.add", target_kind="component_instance", target_id=comp.id,
        detail={"board_id": board.id, "component": component_stable_id, "instance_id": instance_id},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/components", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/components/{comp_id}/pin")
async def wizard_set_component_pin(
    group_id: str,
    comp_id: str,
    request: Request,
    board_pin: str = Form(""),
    function_label: str = Form(""),
    asset_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Assign / re-assign a ComponentInstance to a specific output pin
    of its parent board, and label its function ("stirring", "tilt",
    "mixer", "feed-pump") + asset ID for inventory tracking."""
    group = await _load_group(session, group_id)
    _require_unlocked(group, request)
    comp = await session.get(ComponentInstance, comp_id)
    if comp is None:
        raise HTTPException(404, "component not found")
    board = await session.get(BoardInstance, comp.board_instance_id)
    if board is None or board.group_id != group.id:
        raise HTTPException(404, "component not in this group")
    comp.board_pin = board_pin.strip() or None
    comp.function_label = function_label.strip() or None
    comp.asset_id = asset_id.strip() or None
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="component.pin_assign", target_kind="component_instance", target_id=comp.id,
        detail={"board_pin": comp.board_pin, "function_label": comp.function_label, "asset_id": comp.asset_id},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/components", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/components/{comp_id}/risk")
async def wizard_set_component_risk(
    group_id: str,
    comp_id: str,
    request: Request,
    risk_level: str = Form("nominal"),
    risk_types: str = Form(""),          # comma-separated
    failsafe_action: str = Form(""),     # blank = inherit
    failsafe_value_json: str = Form(""), # JSON, optional
    disconnect_grace_seconds: int = Form(30),
    watchdog_ms: int = Form(1000),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Set / update the per-instance risk + failsafe rules.

    These get baked into the firmware bundle's brain.json so the device's
    LOCAL brain enforces them autonomously when the edge/master link is
    dead. Sensors typically inherit nominal+alarm_only; high-energy
    actuators (heaters, motors, valves) need a concrete failsafe_action.
    """
    import json as _json

    group = await _load_group(session, group_id)
    comp = await session.get(ComponentInstance, comp_id)
    if comp is None:
        raise HTTPException(404, "component not found")
    board = await session.get(BoardInstance, comp.board_instance_id)
    if board is None or board.group_id != group.id:
        raise HTTPException(404, "component not in this group")

    if risk_level not in RISK_LEVELS:
        raise HTTPException(400, f"invalid risk_level: {risk_level}")
    fa = (failsafe_action or "").strip() or None
    if fa is not None and fa not in FAILSAFE_ACTIONS:
        raise HTTPException(400, f"invalid failsafe_action: {fa}")

    types = [t.strip() for t in risk_types.split(",") if t.strip()]
    # Drop anything not in the canonical list (silent — operator can
    # extend RISK_TYPES later if they need a new category).
    types = [t for t in types if t in RISK_TYPES]

    fv = None
    if failsafe_value_json.strip():
        try:
            fv = _json.loads(failsafe_value_json)
        except _json.JSONDecodeError as exc:
            raise HTTPException(400, f"failsafe_value_json is not valid JSON: {exc}") from exc

    comp.risk_level = risk_level
    comp.risk_types_json = types
    comp.failsafe_action = fa
    comp.failsafe_value_json = fv
    comp.disconnect_grace_seconds = max(0, int(disconnect_grace_seconds))
    comp.watchdog_ms = max(100, int(watchdog_ms))

    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="component.set_risk", target_kind="component_instance", target_id=comp.id,
        detail={
            "risk_level": risk_level,
            "risk_types": types,
            "failsafe_action": fa,
            "disconnect_grace_seconds": comp.disconnect_grace_seconds,
            "watchdog_ms": comp.watchdog_ms,
        },
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/components", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/components/{comp_id}/delete")
async def wizard_delete_component(
    group_id: str,
    comp_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    comp = await session.get(ComponentInstance, comp_id)
    if comp is None:
        raise HTTPException(404, "component not found")
    board = await session.get(BoardInstance, comp.board_instance_id)
    if board is None or board.group_id != group.id:
        raise HTTPException(404, "component not in this group")
    await session.delete(comp)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="component.delete", target_kind="component_instance", target_id=comp_id,
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/components", status_code=303)


# ─── Step 5: GPIO pin map ───────────────────────────────────────────────────
def _suggest_board_pin(board_stable_id: str | None, function: str) -> str:
    """Pick a sensible board pin name for an allocator's function output.
    Looks at board_pinouts to find a pin whose kind matches; falls back
    to a humanised version of the function name."""
    po = pinout_for(board_stable_id)
    fn = (function or "").upper()
    # Function → preferred board kinds, in order.
    pref: list[str] = []
    if fn == "PWM":
        pref = ["pwm_in"]
    elif fn in ("I2C_SDA",):
        pref = ["i2c_sda"]
    elif fn in ("I2C_SCL",):
        pref = ["i2c_scl"]
    elif fn in ("GPIO_IN", "GPIO_OUT"):
        pref = ["input", "output", "bidir", "enable", "reset"]
    elif fn == "ADC":
        pref = ["analog_in"]
    elif fn == "1-WIRE":
        pref = ["input", "bidir"]
    if po:
        for need in pref:
            for name, kind, _desc, _hint in po:
                if kind == need:
                    return name
    # Fallback: humanise the function name itself
    return function or "—"


async def _allocate_pins_for_group(
    session: AsyncSession,
    group: EdgeGroup,
    catalog,
) -> tuple[list[tuple[BoardInstance, list[GpioMapping]]], list[str]]:
    """Run the pin allocator per-board for a group, preserving any
    operator-locked rows. Returns ([(board, mappings), ...], conflicts)."""
    if not group.compute_stable_id:
        raise HTTPException(400, "compute_stable_id not set on group")

    out: list[tuple[BoardInstance, list[GpioMapping]]] = []
    all_conflicts: list[str] = []

    boards = (
        await session.execute(
            select(BoardInstance).where(BoardInstance.group_id == group.id)
            .order_by(BoardInstance.position)
        )
    ).scalars().all()

    for b in boards:
        comps = (
            await session.execute(
                select(ComponentInstance)
                .where(ComponentInstance.board_instance_id == b.id)
                .order_by(ComponentInstance.position)
            )
        ).scalars().all()
        if not comps:
            out.append((b, []))
            continue

        existing = (
            await session.execute(
                select(GpioMapping).where(GpioMapping.board_instance_id == b.id)
            )
        ).scalars().all()
        locked = {m.compute_pin: m for m in existing if m.locked_by_operator}

        assignments = [
            {
                "component_stable_id": c.component_stable_id,
                "instance_id": c.instance_id,
            }
            for c in comps
        ]
        try:
            result = auto_allocate(
                compute_stable_id=group.compute_stable_id,
                component_assignments=assignments,
                catalog=catalog,
                device_dna=group.device_dna or f"GROUP-{b.id[:8]}",
                board_stable_id=b.board_stable_id,
            )
        except PinAllocationError as exc:
            all_conflicts.append(f"{b.label}: {exc}")
            out.append((b, list(existing)))
            continue

        # Drop existing non-locked rows; keep locked ones.
        await session.execute(
            sa_delete(GpioMapping).where(
                GpioMapping.board_instance_id == b.id,
                GpioMapping.locked_by_operator.is_(False),
            )
        )
        await session.flush()

        # Track compute_pins we've already added this run so we don't
        # violate the UNIQUE(board_instance_id, compute_pin) constraint
        # when the allocator emits the same pin twice (e.g. shared I2C bus
        # across multiple components).
        added_pins: set[str] = set(locked.keys())

        for assn in result.get("assignments", []):
            for pin in assn.get("pins", []):
                # Allocator returns {"physical": "GP4", "function": "PWM"}.
                compute_pin = pin.get("physical") or pin.get("compute_pin") or pin.get("pin")
                if not compute_pin:
                    continue
                if compute_pin in added_pins:
                    # Already allocated (locked from a previous run, or
                    # repeated across components on this run).
                    continue
                function = pin.get("function") or pin.get("signal")
                bpin = pin.get("board_pin") or _suggest_board_pin(b.board_stable_id, function or "")
                m = GpioMapping(
                    board_instance_id=b.id,
                    compute_pin=compute_pin,
                    board_pin=bpin,
                    signal_name=function,
                    direction=pin.get("direction"),
                    locked_by_operator=False,
                )
                session.add(m)
                added_pins.add(compute_pin)
        for c in result.get("conflicts", []):
            all_conflicts.append(f"{b.label}: {c}")

        await session.flush()
        refreshed = (
            await session.execute(
                select(GpioMapping).where(GpioMapping.board_instance_id == b.id)
                .order_by(GpioMapping.compute_pin)
            )
        ).scalars().all()
        out.append((b, list(refreshed)))

    return out, all_conflicts


@router.get("/ui/devices/wizard/{group_id}/pinmap", response_class=HTMLResponse)
async def wizard_step_pinmap(
    group_id: str,
    request: Request,
    auto: bool = False,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    catalog = load_catalog()
    conflicts: list[str] = []
    if auto:
        boards_pins, conflicts = await _allocate_pins_for_group(session, group, catalog)
        await session.commit()
    else:
        boards_pins = []
        boards = (
            await session.execute(
                select(BoardInstance).where(BoardInstance.group_id == group.id)
                .order_by(BoardInstance.position)
            )
        ).scalars().all()
        for b in boards:
            mappings = (
                await session.execute(
                    select(GpioMapping).where(GpioMapping.board_instance_id == b.id)
                    .order_by(GpioMapping.compute_pin)
                )
            ).scalars().all()
            boards_pins.append((b, list(mappings)))

    # Physical pin-header layout for the visual digital-twin view.
    header = header_for(group.compute_stable_id)
    # Build a quick lookup: compute_pin label → list of GpioMappings
    used_pins: dict[str, list] = {}
    for _b, _maps in boards_pins:
        for m in _maps:
            used_pins.setdefault(m.compute_pin, []).append(m)

    # Per-board pinouts: stable_id → [(name, kind, description, suggest), ...]
    board_pinouts: dict[str, list] = {}
    board_pinouts_split: dict[str, tuple[list, list]] = {}
    for _b, _ in boards_pins:
        po = pinout_for(_b.board_stable_id)
        if po:
            board_pinouts[_b.id] = po
            board_pinouts_split[_b.id] = split_pinout(po)

    # Components per board, indexed by board_pin so the actuator-side
    # rail can show which component is wired to each output pin.
    components_by_board_pin: dict[str, dict] = {}
    for _b, _ in boards_pins:
        rows = (
            await session.execute(
                select(ComponentInstance).where(ComponentInstance.board_instance_id == _b.id)
            )
        ).scalars().all()
        components_by_board_pin[_b.id] = {c.board_pin: c for c in rows if c.board_pin}

    # Connection-type label per existing GpioMapping, for the table view
    # and for the digital-twin canvas colouring. When neither the compute
    # nor the board side gives us a kind (e.g. the board pinout isn't
    # catalogued, or _suggest_board_pin fell back to the raw allocator
    # function name), use signal_name as a last-resort hint so wires are
    # still coloured by bus type rather than all-emerald "Logic".
    def _signal_to_type(sig: str | None) -> str | None:
        s = (sig or "").upper()
        if not s: return None
        if s in ("I2C_SDA", "I2C_SCL", "I²C", "I2C"): return "I²C"
        if s in ("SPI_MOSI", "SPI_MISO", "SPI_SCK", "SPI_CS", "SPI"): return "SPI"
        if s in ("UART_TX", "UART_RX", "UART"): return "UART"
        if s == "PWM": return "PWM"
        if s in ("ADC", "DAC", "ANALOG", "ANALOG_IN"): return "Analog"
        if s == "1-WIRE": return "GPIO"
        if s in ("GPIO_IN", "GPIO_OUT", "GPIO"): return "GPIO"
        return None

    conn_types: dict[str, str] = {}
    for _b, _maps in boards_pins:
        for _m in _maps:
            ck = None
            if header:
                for _n, _l, _k, _a in header["pins"]:
                    if _l == _m.compute_pin:
                        ck = _k
                        break
            bk = board_pin_kind(_b.board_stable_id, _m.board_pin)
            ct = connection_type(ck, bk)
            if ct == "Logic":
                # Both sides came back unknown — derive from signal_name
                # the allocator stored on the GpioMapping so the canvas
                # doesn't paint every wire the same emerald.
                hinted = _signal_to_type(_m.signal_name)
                if hinted:
                    ct = hinted
            conn_types[_m.id] = ct

    # Serialise PIN_COMPAT for JS so the canvas can validate clicks
    # client-side before posting.
    compat_for_js = {k: sorted(v) for k, v in PIN_COMPAT.items()}

    return templates.TemplateResponse(
        "device_wizard_step5.html",
        {
            "request": request,
            "group": group,
            "boards_pins": boards_pins,
            "conflicts": conflicts,
            "catalog": catalog,
            "header": header,
            "used_pins": used_pins,
            "board_pinouts": board_pinouts,
            "board_pinouts_split": board_pinouts_split,
            "components_by_board_pin": components_by_board_pin,
            "conn_types": conn_types,
            "compat_for_js": compat_for_js,
            "PIN_KIND_COLORS": PIN_KIND_COLORS,
            "PINOUT_KIND_COLORS": PINOUT_KIND_COLORS,
            "PINOUT_KIND_COLORS_OUT": PINOUT_KIND_COLORS_OUT,
            "steps": WIZARD_STEPS,
            "step_idx": 5,
        },
    )


@router.post("/ui/devices/wizard/{group_id}/pinmap/create")
async def wizard_create_pin(
    group_id: str,
    request: Request,
    board_instance_id: str = Form(...),
    compute_pin: str = Form(...),
    board_pin: str = Form(...),
    signal_name: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Manually create a single GPIO mapping (click-to-connect on the
    digital-twin canvas or 'Add manual connection' form)."""
    group = await _load_group(session, group_id)
    board = await session.get(BoardInstance, board_instance_id)
    if board is None or board.group_id != group.id:
        raise HTTPException(404, "board not in this group")
    compute_pin = compute_pin.strip()
    board_pin = board_pin.strip()
    if not compute_pin or not board_pin:
        raise HTTPException(400, "compute_pin and board_pin required")

    # Pin-kind compatibility check — refuses to wire 5V to a signal pin,
    # GPIO to a motor output, etc. Looks up the compute pin kind from
    # COMPUTE_HEADERS and the board pin kind from BOARD_PINOUTS.
    from ..compute_pinouts import header_for as _hdr
    hdr = _hdr(group.compute_stable_id)
    compute_kind = None
    if hdr:
        for n, label, k, _alt in hdr["pins"]:
            if label == compute_pin:
                compute_kind = k
                break
    bkind = board_pin_kind(board.board_stable_id, board_pin)
    ok, reason = is_compatible(compute_kind, bkind)
    if not ok:
        raise HTTPException(400, reason)

    # UPSERT — if a mapping for (board, compute_pin) already exists, update it
    # so the operator can reassign a Pi pin from one board pin to another
    # without first deleting the old row.
    existing = (
        await session.execute(
            select(GpioMapping).where(
                GpioMapping.board_instance_id == board.id,
                GpioMapping.compute_pin == compute_pin,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.board_pin = board_pin
        if signal_name.strip():
            existing.signal_name = signal_name.strip()
        existing.locked_by_operator = True
        target_id = existing.id
        action = "pin.reassign"
    else:
        m = GpioMapping(
            board_instance_id=board.id,
            compute_pin=compute_pin,
            board_pin=board_pin,
            signal_name=signal_name.strip() or None,
            locked_by_operator=True,
        )
        session.add(m)
        await session.flush()
        target_id = m.id
        action = "pin.create"

    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action=action, target_kind="gpio_mapping", target_id=target_id,
        detail={"board_id": board.id, "compute_pin": compute_pin, "board_pin": board_pin},
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/pinmap", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/pinmap/{mapping_id}/delete")
async def wizard_delete_pin(
    group_id: str,
    mapping_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    m = await session.get(GpioMapping, mapping_id)
    if m is None:
        raise HTTPException(404, "mapping not found")
    b = await session.get(BoardInstance, m.board_instance_id)
    if b is None or b.group_id != group.id:
        raise HTTPException(404, "mapping not in this group")
    info = {"compute_pin": m.compute_pin, "board_pin": m.board_pin, "board_id": b.id}
    await session.delete(m)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="pin.delete", target_kind="gpio_mapping", target_id=mapping_id,
        detail=info,
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/pinmap", status_code=303)


@router.post("/ui/devices/wizard/{group_id}/pinmap/lock")
async def wizard_lock_pin(
    group_id: str,
    request: Request,
    mapping_id: str = Form(...),
    compute_pin: str = Form(...),
    board_pin: str = Form(""),
    signal_name: str = Form(""),
    locked: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    m = await session.get(GpioMapping, mapping_id)
    if m is None:
        raise HTTPException(404, "mapping not found")
    b = await session.get(BoardInstance, m.board_instance_id)
    if b is None or b.group_id != group.id:
        raise HTTPException(404, "mapping not in this group")

    m.compute_pin = compute_pin.strip() or m.compute_pin
    if board_pin.strip():
        m.board_pin = board_pin.strip()
    if signal_name.strip():
        m.signal_name = signal_name.strip()
    m.locked_by_operator = (locked.lower() in ("on", "true", "1", "yes"))
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/pinmap", status_code=303)


# ─── Step 6: review + build ─────────────────────────────────────────────────
@router.get("/ui/devices/wizard/{group_id}/review", response_class=HTMLResponse)
async def wizard_step_review(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    catalog = load_catalog()
    boards = (
        await session.execute(
            select(BoardInstance).where(BoardInstance.group_id == group.id)
            .order_by(BoardInstance.position)
        )
    ).scalars().all()
    detail = []
    for b in boards:
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
        detail.append({"board": b, "components": comps, "pins": pins})
    return templates.TemplateResponse(
        "device_wizard_step6.html",
        {
            "request": request,
            "group": group,
            "detail": detail,
            "catalog": catalog,
            "steps": WIZARD_STEPS,
            "step_idx": 6,
        },
    )


@router.post("/ui/devices/wizard/{group_id}/build")
async def wizard_build_bundle(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    """Assemble DNA + Brain + WiFi into a deterministic firmware bundle
    for this group. Locked groups require a session-level unlock first."""
    group = await _load_group(session, group_id)
    _require_unlocked(group, request)

    if not group.compute_stable_id:
        raise HTTPException(400, "Pick a compute module first.")
    boards_count = (
        await session.execute(
            select(sa_func.count()).select_from(BoardInstance)
            .where(BoardInstance.group_id == group.id)
        )
    ).scalar_one()
    if boards_count == 0 and (group.role or "edge") in ("edge", "gateway"):
        raise HTTPException(400, "Add at least one board before building (edge / gateway devices wire to physical I/O).")

    bundle_path, dna, brain = await build_group_bundle(
        session, group, Path(settings.build_artifacts_dir)
    )
    group.dna_json = dna
    group.brain_json = brain
    group.firmware_bundle_path = str(bundle_path)
    group.device_dna = dna["device_dna"]

    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.build_bundle", target_kind="edge_group", target_id=group.id,
        detail={
            "device_dna": dna["device_dna"],
            "bundle_path": str(bundle_path),
            "boards": len(dna.get("boards") or []),
            "components": len(dna.get("components") or []),
            "interlocks": len((brain.get("safety") or {}).get("interlocks") or []),
        },
    )
    await session.commit()
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/review", status_code=303)


@router.get("/ui/devices/wizard/{group_id}/bundle.zip")
async def wizard_download_bundle(
    group_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    if not group.firmware_bundle_path:
        raise HTTPException(404, "No firmware bundle built yet.")
    p = Path(group.firmware_bundle_path)
    if not p.exists():
        raise HTTPException(410, "Bundle file missing on disk; rebuild required.")
    return FileResponse(
        path=str(p),
        media_type="application/zip",
        filename=f"{group.device_dna or group.id}.zip",
    )
