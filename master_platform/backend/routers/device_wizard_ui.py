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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import delete as sa_delete
from sqlalchemy import func as sa_func

from ..db import get_session
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
    _require_unlocked(group, pin)  # verifies
    group.lock_pin_hash = None
    group.locked_at = None
    group.locked_by = None
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

    # next-step routing
    if not g.compute_stable_id:
        next_step, status = "compute", "incomplete · pick compute"
    elif boards_count == 0:
        next_step, status = "boards", "incomplete · no boards"
    elif comps_count == 0:
        next_step, status = "components", "incomplete · no components"
    elif pins_count == 0:
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
    roots = (await session.execute(
        select(RootSystem).order_by(RootSystem.created_at)
    )).scalars().all()
    nodes = (await session.execute(
        select(NodeSystem).order_by(NodeSystem.created_at)
    )).scalars().all()
    edges = (await session.execute(
        select(EdgeSystem).order_by(EdgeSystem.created_at)
    )).scalars().all()

    # Existing groups (so the operator can resume / inspect partial work)
    groups = (await session.execute(
        select(EdgeGroup).order_by(EdgeGroup.updated_at.desc())
    )).scalars().all()
    catalog = load_catalog()
    edge_by_id = {e.id: e for e in edges}
    group_rows: list[dict] = []
    for g in groups:
        st = await _group_status(session, g)
        edge = edge_by_id.get(g.edge_id)
        compute = catalog.get(g.compute_stable_id) if g.compute_stable_id else None
        group_rows.append({
            "group": g,
            "edge": edge,
            "compute_name": (compute.name if compute else (g.compute_stable_id or "—")),
            **st,
        })

    return templates.TemplateResponse(
        "device_wizard_step1.html",
        {
            "request": request,
            "roots": roots,
            "nodes": nodes,
            "edges": edges,
            "group_rows": group_rows,
            "steps": WIZARD_STEPS,
            "step_idx": 0,
        },
    )


@router.post("/ui/devices/wizard/create-group")
async def wizard_create_group(
    request: Request,
    # Root: pick existing OR create new (one of these is required)
    root_id: str = Form(""),
    new_root_name: str = Form(""),
    # Node (optional): pick existing OR create new OR skip (edge attaches to root directly)
    node_mode: str = Form("skip"),   # "skip" | "existing" | "new"
    node_id: str = Form(""),
    new_node_name: str = Form(""),
    new_node_region: str = Form(""),
    # Edge: pick existing OR create new
    edge_mode: str = Form("new"),    # "existing" | "new"
    edge_id: str = Form(""),
    new_edge_name: str = Form(""),
    new_edge_site_id: str = Form(""),
    new_edge_address: str = Form(""),
    # Group
    name: str = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    # ── 1. Resolve / create Root ────────────────────────────────────────────
    root: RootSystem | None = None
    if root_id:
        root = await session.get(RootSystem, root_id)
        if root is None:
            raise HTTPException(404, f"root {root_id} not found")
    elif new_root_name.strip():
        root = RootSystem(name=new_root_name.strip())
        session.add(root)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="root.create", target_kind="root_system", target_id=root.id,
            detail={"name": root.name, "source": "wizard"},
        )

    # ── 2. Resolve / create Node (optional) ─────────────────────────────────
    node: NodeSystem | None = None
    if node_mode == "existing" and node_id:
        node = await session.get(NodeSystem, node_id)
        if node is None:
            raise HTTPException(404, f"node {node_id} not found")
        if root is None:
            root = await session.get(RootSystem, node.root_id)
    elif node_mode == "new" and new_node_name.strip():
        if root is None:
            raise HTTPException(400, "node requires a root — pick one or create a new one")
        node = NodeSystem(
            root_id=root.id,
            name=new_node_name.strip(),
            region=new_node_region.strip() or None,
        )
        session.add(node)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="node.create", target_kind="node_system", target_id=node.id,
            detail={"root_id": root.id, "name": node.name, "source": "wizard"},
        )

    # ── 3. Resolve / create Edge ────────────────────────────────────────────
    edge: EdgeSystem | None = None
    if edge_mode == "existing" and edge_id:
        edge = await session.get(EdgeSystem, edge_id)
        if edge is None:
            raise HTTPException(404, f"edge {edge_id} not found")
    elif edge_mode == "new" and new_edge_name.strip() and new_edge_site_id.strip():
        if node is None and root is None:
            raise HTTPException(400, "edge requires a parent (node or root)")
        edge = EdgeSystem(
            node_id=node.id if node is not None else None,
            root_id=root.id if node is None else None,
            name=new_edge_name.strip(),
            site_id=new_edge_site_id.strip(),
            address=new_edge_address.strip() or None,
        )
        session.add(edge)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="edge.create", target_kind="edge_system", target_id=edge.id,
            detail={
                "node_id": node.id if node is not None else None,
                "root_id": root.id if node is None else None,
                "name": edge.name,
                "site_id": edge.site_id,
                "source": "wizard",
            },
        )
    if edge is None:
        raise HTTPException(400, "couldn't resolve an Edge — pick one or fill the new-edge fields")

    # ── 4. Create Group ─────────────────────────────────────────────────────
    name = name.strip()
    if not name:
        raise HTTPException(400, "group name required")
    existing_group = (
        await session.execute(
            select(EdgeGroup).where(
                EdgeGroup.edge_id == edge.id, EdgeGroup.name == name
            )
        )
    ).scalar_one_or_none()
    if existing_group is not None:
        group = existing_group
    else:
        group = EdgeGroup(edge_id=edge.id, name=name, description=description.strip() or None)
        session.add(group)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="group.create", target_kind="edge_group", target_id=group.id,
            detail={"edge_id": edge.id, "name": name},
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
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    group = await _load_group(session, group_id)
    group.compute_stable_id = compute_stable_id
    group.hardware_revision = hardware_revision.strip() or "rev_a"
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="group.set_compute", target_kind="edge_group", target_id=group.id,
        detail={"compute": compute_stable_id},
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
    edge = await session.get(EdgeSystem, group.edge_id)
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
    edge = await session.get(EdgeSystem, group.edge_id)
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

        for assn in result.get("assignments", []):
            for pin in assn.get("pins", []):
                # Allocator returns {"physical": "GP4", "function": "PWM"}.
                # We previously read non-existent keys, so no rows ever got
                # created (auto-allocate appeared broken).
                compute_pin = pin.get("physical") or pin.get("compute_pin") or pin.get("pin")
                if not compute_pin or compute_pin in locked:
                    continue
                function = pin.get("function") or pin.get("signal")
                bpin = pin.get("board_pin") or _suggest_board_pin(b.board_stable_id, function or "")
                # Skip if this compute_pin is already mapped to this board (UNIQUE constraint).
                if any(em.compute_pin == compute_pin and em.board_instance_id == b.id for em in existing):
                    continue
                m = GpioMapping(
                    board_instance_id=b.id,
                    compute_pin=compute_pin,
                    board_pin=bpin,
                    signal_name=function,
                    direction=pin.get("direction"),
                    locked_by_operator=False,
                )
                session.add(m)
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

    # Connection-type label per existing GpioMapping, for the table view.
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
            conn_types[_m.id] = connection_type(ck, bk)

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
