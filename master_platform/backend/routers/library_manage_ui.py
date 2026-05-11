from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import jsonschema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..library_loader import (
    LIBRARY_DIRS,
    LIBRARY_GROUPS,
    invalidate_catalog,
    load_catalog,
    shared_schema,
)
from ..models import APIKey
from ..models_library import LibraryItemRow
from ..security.audit import record
from ..security.ui_auth import ui_require_admin, ui_require_login


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-library-manage"])


@router.get("/ui/library/manage", response_class=HTMLResponse)
async def manage_index(
    request: Request,
    group: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(ui_require_login),
):
    catalog = load_catalog()
    selected = group if group in LIBRARY_GROUPS else next(iter(LIBRARY_GROUPS))
    libs_in_group = LIBRARY_GROUPS[selected]
    items = []
    for lib in libs_in_group:
        items.extend(catalog.list_library(lib))
    items.sort(key=lambda x: (x.library, x.stable_id))
    return templates.TemplateResponse(
        "library_manage.html",
        {
            "request": request,
            "groups": list(LIBRARY_GROUPS.keys()),
            "selected_group": selected,
            "libs_in_group": libs_in_group,
            "items": items,
        },
    )


@router.get("/ui/library/new", response_class=HTMLResponse)
async def new_form(
    request: Request,
    library: str = "components_library",
    error: str | None = None,
    _: APIKey = Depends(ui_require_admin),
):
    if library not in LIBRARY_DIRS:
        raise HTTPException(400, "unknown library")
    return templates.TemplateResponse(
        "library_form.html",
        {
            "request": request,
            "library": library,
            "library_dirs": LIBRARY_DIRS,
            "groups": LIBRARY_GROUPS,
            "mode": "create",
            "row": None,
            "manifest_text": _starter_manifest(library),
            "error": error,
        },
    )


def _starter_manifest(library: str) -> str:
    skeleton = {
        "stable_id": f"{library.replace('_library','').replace('_','.')}.example.id",
        "name": "New item",
        "vendor": "Neuron Platform",
        "version": "0.1.0",
        "library": library,
        "category": "general",
        "description": "Describe what this item provides.",
        "tags": [],
        "capabilities": ["example_capability"],
        "interfaces": [{"type": "Internal", "direction": "bidir"}],
        "power": {"voltage_v": 0, "current_ma_typical": 0, "power_source": "bus"},
        "safety_class": "nominal",
        "drivers": [],
        "self_tests": [
            {"id": "smoke", "name": "Smoke", "steps": ["instantiate"], "expected": "no error"}
        ],
        "compatibility": {},
    }
    return json.dumps(skeleton, indent=2)


@router.post("/ui/library/new")
async def new_submit(
    request: Request,
    library: str = Form(...),
    manifest_text: str = Form(...),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if library not in LIBRARY_DIRS:
        raise HTTPException(400, "unknown library")
    error = await _persist_item(
        session=session,
        actor=actor,
        manifest_text=manifest_text,
        existing=None,
        forced_library=library,
        notes=notes,
    )
    if error:
        return templates.TemplateResponse(
            "library_form.html",
            {
                "request": request,
                "library": library,
                "library_dirs": LIBRARY_DIRS,
                "groups": LIBRARY_GROUPS,
                "mode": "create",
                "row": None,
                "manifest_text": manifest_text,
                "error": error,
            },
            status_code=400,
        )
    return RedirectResponse(f"/ui/library/manage", status_code=303)


@router.get("/ui/library/edit/{db_id}", response_class=HTMLResponse)
async def edit_form(
    db_id: str,
    request: Request,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(ui_require_admin),
):
    row = await session.get(LibraryItemRow, db_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "library item not found")
    return templates.TemplateResponse(
        "library_form.html",
        {
            "request": request,
            "library": row.library,
            "library_dirs": LIBRARY_DIRS,
            "groups": LIBRARY_GROUPS,
            "mode": "edit",
            "row": row,
            "manifest_text": json.dumps(row.manifest_json, indent=2),
            "error": error,
        },
    )


@router.post("/ui/library/edit/{db_id}")
async def edit_submit(
    db_id: str,
    request: Request,
    manifest_text: str = Form(...),
    notes: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await session.get(LibraryItemRow, db_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "library item not found")
    error = await _persist_item(
        session=session,
        actor=actor,
        manifest_text=manifest_text,
        existing=row,
        forced_library=row.library,
        notes=notes,
    )
    if error:
        return templates.TemplateResponse(
            "library_form.html",
            {
                "request": request,
                "library": row.library,
                "library_dirs": LIBRARY_DIRS,
                "groups": LIBRARY_GROUPS,
                "mode": "edit",
                "row": row,
                "manifest_text": manifest_text,
                "error": error,
            },
            status_code=400,
        )
    return RedirectResponse(f"/ui/library/manage?group=" + _group_for(row.library), status_code=303)


@router.post("/ui/library/delete/{db_id}")
async def delete_item(
    db_id: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    from datetime import datetime, timezone
    row = await session.get(LibraryItemRow, db_id)
    if row is None:
        raise HTTPException(404, "library item not found")
    row.deleted_at = datetime.now(timezone.utc)
    await record(
        session,
        actor=actor.id,
        actor_kind="ui_session",
        action="delete_library_item",
        target_kind="library_item",
        target_id=row.stable_id,
    )
    await session.commit()
    invalidate_catalog()
    return RedirectResponse("/ui/library/manage?group=" + _group_for(row.library), status_code=303)


def _group_for(library: str) -> str:
    for group, libs in LIBRARY_GROUPS.items():
        if library in libs:
            return group
    return next(iter(LIBRARY_GROUPS))


async def _persist_item(
    *,
    session: AsyncSession,
    actor: APIKey,
    manifest_text: str,
    existing,
    forced_library: str,
    notes: str,
) -> str | None:
    try:
        doc = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    if not isinstance(doc, dict):
        return "Manifest must be a JSON object."
    doc["library"] = forced_library
    schema = shared_schema("manifest_schema")
    errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
    if errs:
        first = errs[0]
        path = "/".join(str(p) for p in first.path) or "<root>"
        return f"Schema error at {path}: {first.message}"

    stable_id = doc["stable_id"]

    if existing is None:
        clash = await session.scalar(
            select(LibraryItemRow).where(LibraryItemRow.stable_id == stable_id, LibraryItemRow.deleted_at.is_(None))
        )
        if clash is not None:
            return f"stable_id '{stable_id}' is already taken by another DB item."
        row = LibraryItemRow(
            stable_id=stable_id,
            library=forced_library,
            category=doc.get("category", "general"),
            name=doc.get("name", stable_id),
            vendor=doc.get("vendor", "Unknown"),
            version=doc.get("version", "0.1.0"),
            manifest_json=doc,
            notes=notes or None,
            created_by=actor.id,
        )
        session.add(row)
        await session.flush()
        action = "create_library_item"
        target_id = row.stable_id
    else:
        if stable_id != existing.stable_id:
            return "stable_id is immutable for an existing item."
        existing.category = doc.get("category", existing.category)
        existing.name = doc.get("name", existing.name)
        existing.vendor = doc.get("vendor", existing.vendor)
        existing.version = doc.get("version", existing.version)
        existing.manifest_json = doc
        if notes:
            existing.notes = notes
        action = "update_library_item"
        target_id = existing.stable_id

    await record(
        session,
        actor=actor.id,
        actor_kind="ui_session",
        action=action,
        target_kind="library_item",
        target_id=target_id,
        detail={"library": forced_library, "version": doc.get("version")},
    )
    await session.commit()
    invalidate_catalog()
    return None
