"""Admin UI for the reusable function / library registry.

See CLAUDE.md for the durable rules. Pages:
  GET  /ui/functions               → list with filter chips + search
  GET  /ui/functions/new           → create form
  POST /ui/functions/new           → persist
  GET  /ui/functions/{name}        → detail + edit form
  POST /ui/functions/{name}        → update
  POST /ui/functions/{name}/delete → delete (admin only)

inputs / outputs are entered as JSON arrays in the form's textarea so a
single field can capture the full schema-like shape:
    [{"name":"x","type":"int","required":true,"description":"…"}]
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import (
    FUNCTION_LANGUAGES,
    FUNCTION_STATUSES,
    APIKey,
    CodeFunction,
)
from ..security.audit import record
from ..security.ui_auth import ui_require_admin


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-functions"])


def _parse_json_list(raw: str, field: str) -> list[Any]:
    """Parse a JSON list from a textarea. Empty → []; invalid → 400."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"{field} is not valid JSON: {exc}") from exc
    if not isinstance(v, list):
        raise HTTPException(400, f"{field} must be a JSON array")
    return v


def _parse_tags(raw: str) -> list[str]:
    """Comma- or whitespace-separated tags → list[str], deduped, kebab-case-ish."""
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = [p.strip().lower() for p in raw.replace(",", " ").split()]
    out: list[str] = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return out


@router.get("/ui/functions", response_class=HTMLResponse)
async def functions_page(
    request: Request,
    status: Optional[str] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    stmt = select(CodeFunction)
    if status and status in FUNCTION_STATUSES:
        stmt = stmt.where(CodeFunction.status == status)
    if language and language in FUNCTION_LANGUAGES:
        stmt = stmt.where(CodeFunction.language == language)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (CodeFunction.name.ilike(like))
            | (CodeFunction.description.ilike(like))
            | (CodeFunction.source_path.ilike(like))
            | (CodeFunction.api_endpoint.ilike(like))
        )
    stmt = stmt.order_by(CodeFunction.name)
    rows = (await session.execute(stmt)).scalars().all()

    counts_stmt = select(CodeFunction.status, func.count()).group_by(CodeFunction.status)
    raw = (await session.execute(counts_stmt)).all()
    counts = {s: 0 for s in FUNCTION_STATUSES}
    counts.update({s: n for s, n in raw})

    return templates.TemplateResponse(
        "functions.html",
        {
            "request": request,
            "rows": rows,
            "counts": counts,
            "filter_status": status,
            "filter_language": language,
            "filter_q": q or "",
            "STATUSES": FUNCTION_STATUSES,
            "LANGUAGES": FUNCTION_LANGUAGES,
        },
    )


@router.get("/ui/functions/new", response_class=HTMLResponse)
async def function_new_page(
    request: Request,
    actor: APIKey = Depends(ui_require_admin),
):
    return templates.TemplateResponse(
        "function_form.html",
        {
            "request": request,
            "row": None,
            "STATUSES": FUNCTION_STATUSES,
            "LANGUAGES": FUNCTION_LANGUAGES,
        },
    )


async def _upsert_function(
    *,
    session: AsyncSession,
    actor: APIKey,
    existing: Optional[CodeFunction],
    name: str,
    description: str,
    language: str,
    inputs_raw: str,
    outputs_raw: str,
    api_endpoint: str,
    source_path: str,
    tags_raw: str,
    status: str,
    example: str,
) -> CodeFunction:
    name = name.strip().lower().replace(" ", "-")
    if not name:
        raise HTTPException(400, "name required")
    if language not in FUNCTION_LANGUAGES:
        raise HTTPException(400, f"invalid language: {language}")
    if status not in FUNCTION_STATUSES:
        raise HTTPException(400, f"invalid status: {status}")
    inputs = _parse_json_list(inputs_raw, "inputs_json")
    outputs = _parse_json_list(outputs_raw, "outputs_json")
    tags = _parse_tags(tags_raw)

    if existing is None:
        clash = await session.scalar(select(CodeFunction).where(CodeFunction.name == name))
        if clash is not None:
            raise HTTPException(409, f"function '{name}' already exists")
        row = CodeFunction(
            name=name,
            description=description.strip() or None,
            language=language,
            inputs_json=inputs,
            outputs_json=outputs,
            api_endpoint=api_endpoint.strip() or None,
            source_path=source_path.strip() or None,
            tags_json=tags,
            status=status,
            example=example.strip() or None,
        )
        session.add(row)
        await session.flush()
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="function.create", target_kind="code_function", target_id=row.name,
            detail={"language": language, "status": status},
        )
        return row
    else:
        existing.description = description.strip() or None
        existing.language = language
        existing.inputs_json = inputs
        existing.outputs_json = outputs
        existing.api_endpoint = api_endpoint.strip() or None
        existing.source_path = source_path.strip() or None
        existing.tags_json = tags
        existing.status = status
        existing.example = example.strip() or None
        await record(
            session, actor=actor.id, actor_kind="ui_session",
            action="function.update", target_kind="code_function", target_id=existing.name,
            detail={"language": language, "status": status},
        )
        return existing


@router.post("/ui/functions/new")
async def function_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    language: str = Form("python"),
    inputs_json: str = Form(""),
    outputs_json: str = Form(""),
    api_endpoint: str = Form(""),
    source_path: str = Form(""),
    tags: str = Form(""),
    status: str = Form("stable"),
    example: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = await _upsert_function(
        session=session, actor=actor, existing=None,
        name=name, description=description, language=language,
        inputs_raw=inputs_json, outputs_raw=outputs_json,
        api_endpoint=api_endpoint, source_path=source_path,
        tags_raw=tags, status=status, example=example,
    )
    await session.commit()
    return RedirectResponse(f"/ui/functions/{row.name}", status_code=303)


@router.get("/ui/functions/{name}", response_class=HTMLResponse)
async def function_detail_page(
    request: Request,
    name: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = (
        await session.execute(select(CodeFunction).where(CodeFunction.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "function not found")
    return templates.TemplateResponse(
        "function_detail.html",
        {
            "request": request,
            "row": row,
            "STATUSES": FUNCTION_STATUSES,
            "LANGUAGES": FUNCTION_LANGUAGES,
            "inputs_pretty": json.dumps(row.inputs_json or [], indent=2),
            "outputs_pretty": json.dumps(row.outputs_json or [], indent=2),
            "tags_pretty": ", ".join(row.tags_json or []),
        },
    )


@router.post("/ui/functions/{name}")
async def function_update(
    request: Request,
    name: str,
    description: str = Form(""),
    language: str = Form("python"),
    inputs_json: str = Form(""),
    outputs_json: str = Form(""),
    api_endpoint: str = Form(""),
    source_path: str = Form(""),
    tags: str = Form(""),
    status: str = Form("stable"),
    example: str = Form(""),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = (
        await session.execute(select(CodeFunction).where(CodeFunction.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "function not found")
    await _upsert_function(
        session=session, actor=actor, existing=row,
        name=row.name, description=description, language=language,
        inputs_raw=inputs_json, outputs_raw=outputs_json,
        api_endpoint=api_endpoint, source_path=source_path,
        tags_raw=tags, status=status, example=example,
    )
    await session.commit()
    return RedirectResponse(f"/ui/functions/{row.name}", status_code=303)


@router.post("/ui/functions/{name}/delete")
async def function_delete(
    request: Request,
    name: str,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    row = (
        await session.execute(select(CodeFunction).where(CodeFunction.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "function not found")
    await session.delete(row)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="function.delete", target_kind="code_function", target_id=name,
    )
    await session.commit()
    return RedirectResponse("/ui/functions", status_code=303)
