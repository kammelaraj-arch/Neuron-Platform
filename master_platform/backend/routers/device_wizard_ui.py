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

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..library_loader import load_catalog
from ..models import (
    APIKey,
    BoardInstance,
    ComponentInstance,
    EdgeGroup,
    EdgeSystem,
    GpioMapping,
)
from ..security.audit import record
from ..security.ui_auth import ui_require_login


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-device-wizard"])


WIZARD_STEPS = [
    ("group", "Group"),
    ("compute", "Compute"),
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


def _step_index(step: str) -> int:
    for i, (k, _) in enumerate(WIZARD_STEPS):
        if k == step:
            return i
    raise HTTPException(404, f"unknown wizard step: {step}")


# ─── Entry point: choose Edge + name Group ──────────────────────────────────
@router.get("/ui/devices/wizard", response_class=HTMLResponse)
async def wizard_entry(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    edges = (await session.execute(
        select(EdgeSystem).order_by(EdgeSystem.created_at)
    )).scalars().all()
    return templates.TemplateResponse(
        "device_wizard_step1.html",
        {
            "request": request,
            "edges": edges,
            "steps": WIZARD_STEPS,
            "step_idx": 0,
        },
    )


@router.post("/ui/devices/wizard/create-group")
async def wizard_create_group(
    request: Request,
    edge_id: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_login),
):
    edge = await session.get(EdgeSystem, edge_id)
    if edge is None:
        raise HTTPException(404, "edge not found")
    name = name.strip()
    if not name:
        raise HTTPException(400, "group name required")

    # Reuse existing group if name matches; otherwise create.
    existing = (
        await session.execute(
            select(EdgeGroup).where(
                EdgeGroup.edge_id == edge.id, EdgeGroup.name == name
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        group = existing
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
    return RedirectResponse(f"/ui/devices/wizard/{group.id}/boards", status_code=303)


# ─── Step 3: add control boards ─────────────────────────────────────────────
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
            "step_idx": 2,
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


# Step 4-6 land in the next commit (components, pin-map, review+build).
