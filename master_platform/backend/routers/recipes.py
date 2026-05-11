from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, RecipeRun
from ..runtime import recipe_runner
from ..security.audit import record
from ..security.auth import require_scopes


router = APIRouter(prefix="/api", tags=["recipes"])


class StartBody(BaseModel):
    device_dna: str
    params: dict[str, Any] | None = None


class RunOut(BaseModel):
    id: str
    recipe_id: str
    device_dna: str
    state: str
    current_step: str | None
    progress_pct: int
    params_json: dict
    note: str | None
    started_by: str | None
    started_at: str | None
    finished_at: str | None

    @classmethod
    def from_row(cls, row: RecipeRun) -> "RunOut":
        return cls(
            id=row.id,
            recipe_id=row.recipe_id,
            device_dna=row.device_dna,
            state=row.state,
            current_step=row.current_step,
            progress_pct=row.progress_pct,
            params_json=row.params_json or {},
            note=row.note,
            started_by=row.started_by,
            started_at=row.started_at.isoformat() if row.started_at else None,
            finished_at=row.finished_at.isoformat() if row.finished_at else None,
        )


@router.get("/recipes")
async def list_recipes(_: APIKey = Depends(require_scopes("library:read"))) -> dict:
    return {"recipes": await recipe_runner.list_available_recipes()}


@router.post("/recipes/{recipe_id}/start", response_model=RunOut, status_code=201)
async def start_recipe(
    recipe_id: str,
    body: StartBody,
    actor: APIKey = Depends(require_scopes("processes:write")),
):
    try:
        run = await recipe_runner.start_run(
            recipe_id=recipe_id,
            device_dna=body.device_dna,
            params_override=body.params,
            started_by=actor.id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return RunOut.from_row(run)


@router.get("/recipe-runs", response_model=list[RunOut])
async def list_runs(
    device_dna: str | None = None,
    state: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("processes:read")),
):
    stmt = select(RecipeRun).order_by(RecipeRun.started_at.desc()).limit(200)
    if device_dna:
        stmt = stmt.where(RecipeRun.device_dna == device_dna)
    if state:
        stmt = stmt.where(RecipeRun.state == state)
    rows = (await session.execute(stmt)).scalars().all()
    return [RunOut.from_row(r) for r in rows]


@router.get("/recipe-runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    _: APIKey = Depends(require_scopes("processes:read")),
):
    row = await session.get(RecipeRun, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    return RunOut.from_row(row)


@router.post("/recipe-runs/{run_id}/pause", response_model=RunOut)
async def pause(
    run_id: str,
    actor: APIKey = Depends(require_scopes("processes:write")),
):
    try:
        return RunOut.from_row(await recipe_runner.pause_run(run_id))
    except LookupError:
        raise HTTPException(404, "run not found")


@router.post("/recipe-runs/{run_id}/resume", response_model=RunOut)
async def resume(
    run_id: str,
    actor: APIKey = Depends(require_scopes("processes:write")),
):
    try:
        return RunOut.from_row(await recipe_runner.resume_run(run_id))
    except LookupError:
        raise HTTPException(404, "run not found")


@router.post("/recipe-runs/{run_id}/stop", response_model=RunOut)
async def stop(
    run_id: str,
    actor: APIKey = Depends(require_scopes("processes:write")),
):
    try:
        run = await recipe_runner.stop_run(run_id)
        return RunOut.from_row(run)
    except LookupError:
        raise HTTPException(404, "run not found")
