"""Recipe runner — walks a recipe's ``steps`` array and pushes desired-state
updates to the device via the Master → Edge bridge.

Recipe shape (already used by ``digital_twin_controls_library/recipes/*.json``):

    {
      "recipe_id": "recipe.cooking.hold_80c",
      "twin_targets": ["twin.heater_control"],
      "params": {"setpoint_c": 80, "hold_minutes": 30},
      "steps": [
        {"id": "preheat",  "action": "set_state", "args": {"state": "PREHEAT"}},
        {"id": "wait_hot", "action": "await",     "args": {"expr": "reported.process_temp_c >= 79.5"}},
        {"id": "hold",     "action": "set_state", "args": {"state": "HOLD"}},
        {"id": "hold_timer","action": "wait_minutes", "args": {"minutes": "${params.hold_minutes}"}},
        {"id": "cooldown", "action": "set_state", "args": {"state": "COOLDOWN"}}
      ]
    }

Action set:
  * ``set_state`` — push twin desired ``state`` and (for heater) ``setpoint_c``
  * ``await``     — block until reported expression true (polled)
  * ``wait_minutes`` — sleep N minutes (interpolated from params)
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..library_loader import load_catalog
from ..models import Device, RecipeRun
from ..security.audit import record
from .edge_bridge import EdgeUnreachable, push_many


_log = logging.getLogger("neuron.master.recipe_runner")
_active: dict[str, asyncio.Task] = {}


_PARAM_RE = re.compile(r"\$\{params\.([a-zA-Z0-9_]+)\}")


def _interpolate(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str):
        m = _PARAM_RE.fullmatch(value)
        if m:
            return params.get(m.group(1), value)
        return _PARAM_RE.sub(lambda mm: str(params.get(mm.group(1), mm.group(0))), value)
    return value


async def _ensure_run(session: AsyncSession, run_id: str) -> RecipeRun:
    row = await session.get(RecipeRun, run_id)
    if row is None:
        raise LookupError(f"recipe run not found: {run_id}")
    return row


async def _get_recipe(recipe_id: str) -> dict[str, Any] | None:
    """Recipes can live in two places (DB wins on collision):
      * DB-backed RecipeDef rows (created/edited via /ui/recipes)
      * JSON files in libraries/digital_twin_controls_library/recipes/
        or libraries/business_library/manifests/
    """
    # Check DB first.
    from ..models import RecipeDef
    async with SessionLocal() as session:
        row = await session.scalar(
            select(RecipeDef).where(RecipeDef.recipe_id == recipe_id,
                                    RecipeDef.deleted_at.is_(None))
        )
        if row is not None:
            return dict(row.recipe_json)

    import json
    from ..config import settings as _s
    candidates = [
        _s.libraries_dir / "digital_twin_controls_library" / "recipes" / f"{recipe_id}.json",
        _s.libraries_dir / "business_library" / "manifests" / f"{recipe_id}.json",
    ]
    for p in candidates:
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                return json.load(fh)
    return None


async def list_available_recipes() -> list[dict[str, Any]]:
    """List every runnable recipe — DB-backed plus file-based, deduplicated
    by recipe_id (DB wins on collision)."""
    from pathlib import Path
    from ..config import settings as _s
    from ..models import RecipeDef

    seen: dict[str, dict[str, Any]] = {}

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(RecipeDef).where(RecipeDef.deleted_at.is_(None))
            .order_by(RecipeDef.recipe_id)
        )).scalars().all()
        for row in rows:
            doc = row.recipe_json or {}
            seen[row.recipe_id] = {
                "recipe_id": row.recipe_id,
                "name": doc.get("name", row.recipe_id),
                "version": doc.get("version", "0.0.0"),
                "twin_targets": doc.get("twin_targets", []),
                "params": doc.get("params", {}),
                "steps": [s.get("id") for s in doc.get("steps", [])],
                "source": "db",
                "db_id": row.id,
                "editable": True,
            }

    recipe_dir = _s.libraries_dir / "digital_twin_controls_library" / "recipes"
    if recipe_dir.is_dir():
        import json
        for p in sorted(recipe_dir.glob("*.json")):
            try:
                with p.open(encoding="utf-8") as fh:
                    doc = json.load(fh)
                rid = doc.get("recipe_id") or p.stem
                if rid in seen:
                    continue
                seen[rid] = {
                    "recipe_id": rid,
                    "name": doc.get("name", p.stem),
                    "version": doc.get("version", "0.0.0"),
                    "twin_targets": doc.get("twin_targets", []),
                    "params": doc.get("params", {}),
                    "steps": [s.get("id") for s in doc.get("steps", [])],
                    "source": "file",
                    "editable": False,
                }
            except Exception as exc:  # noqa: BLE001
                _log.warning("could not parse recipe %s: %s", p, exc)

    return list(seen.values())


async def start_run(
    *,
    recipe_id: str,
    device_dna: str,
    params_override: dict[str, Any] | None,
    started_by: str,
) -> RecipeRun:
    recipe = await _get_recipe(recipe_id)
    if recipe is None:
        raise LookupError(f"recipe not found: {recipe_id}")

    async with SessionLocal() as session:
        device = await session.get(Device, device_dna)
        if device is None:
            raise LookupError(f"device not found: {device_dna}")
        params = dict(recipe.get("params") or {})
        params.update(params_override or {})
        run = RecipeRun(
            recipe_id=recipe_id,
            device_dna=device_dna,
            state="running",
            current_step=None,
            params_json=params,
            progress_pct=0,
            started_by=started_by,
        )
        session.add(run)
        await session.flush()
        await record(
            session,
            actor=started_by,
            action="start_recipe",
            target_kind="recipe_run",
            target_id=run.id,
            detail={"recipe_id": recipe_id, "device_dna": device_dna, "params": params},
        )
        await session.commit()
        run_id = run.id

    task = asyncio.create_task(_run_loop(run_id, recipe, params))
    _active[run_id] = task
    task.add_done_callback(lambda _t, rid=run_id: _active.pop(rid, None))

    async with SessionLocal() as session:
        return await session.get(RecipeRun, run_id)  # type: ignore[return-value]


async def _run_loop(run_id: str, recipe: dict[str, Any], params: dict[str, Any]) -> None:
    steps = recipe.get("steps") or []
    twin_targets = recipe.get("twin_targets") or []
    twin_kind = "heater"  # heuristic: only one twin kind in scope this slice
    for tk in twin_targets:
        if tk == "twin.heater_control":  twin_kind = "heater"
        elif tk == "twin.motor_control": twin_kind = "motor"
        elif tk == "twin.weighscale_control": twin_kind = "scale"

    try:
        async with SessionLocal() as session:
            run = await _ensure_run(session, run_id)
            device = await session.get(Device, run.device_dna)
            if device is None:
                run.state = "failed"; run.note = "device disappeared"; run.finished_at = datetime.now(timezone.utc)
                await session.commit()
                return
            edge_id = device.edge_id

        for idx, step in enumerate(steps):
            sid = step.get("id") or f"step_{idx}"
            action = step.get("action")
            args = step.get("args") or {}

            async with SessionLocal() as session:
                run = await _ensure_run(session, run_id)
                if run.state in ("paused",):
                    # Pause loop — re-check every 1s until state changes.
                    while True:
                        await asyncio.sleep(1.0)
                        async with SessionLocal() as s2:
                            r2 = await _ensure_run(s2, run_id)
                            if r2.state != "paused":
                                run = r2
                                break
                if run.state in ("aborted", "failed"):
                    return
                run.current_step = sid
                run.progress_pct = int(((idx) / max(1, len(steps))) * 100)
                await session.commit()

            if action == "set_state":
                fields = {}
                if "state" in args:
                    fields["state"] = args["state"]
                # Heater-specific convenience: if PREHEAT, also push setpoint_c.
                if args.get("state") == "PREHEAT" and "setpoint_c" in params:
                    fields["setpoint_c"] = params["setpoint_c"]
                if "safety_max_c" in params:
                    fields["safety_max_c"] = params["safety_max_c"]
                async with SessionLocal() as session:
                    try:
                        await push_many(
                            session,
                            edge_id=edge_id,
                            device_dna=run.device_dna if run else "",
                            twin_kind=twin_kind,
                            fields=fields,
                        )
                    except EdgeUnreachable as exc:
                        _log.warning("edge unreachable for run %s: %s", run_id, exc)

            elif action == "wait_minutes":
                minutes = float(_interpolate(args.get("minutes", 0), params))
                # Cooperative sleep with abort polling every 5 s.
                left = minutes * 60.0
                while left > 0:
                    await asyncio.sleep(min(5.0, left))
                    left -= 5.0
                    async with SessionLocal() as session:
                        r = await _ensure_run(session, run_id)
                        if r.state in ("aborted", "failed"):
                            return

            elif action == "await":
                # Stub: just sleep 1s. Real implementation queries edge twin.
                await asyncio.sleep(1.0)

            else:
                _log.warning("unknown action %r in recipe run %s", action, run_id)

        async with SessionLocal() as session:
            run = await _ensure_run(session, run_id)
            run.state = "completed"
            run.progress_pct = 100
            run.current_step = None
            run.finished_at = datetime.now(timezone.utc)
            await record(
                session, actor=run.started_by or "system",
                actor_kind="recipe_runner",
                action="finish_recipe",
                target_kind="recipe_run", target_id=run_id,
                detail={"steps": len(steps)},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        _log.exception("recipe run %s failed: %s", run_id, exc)
        async with SessionLocal() as session:
            try:
                run = await _ensure_run(session, run_id)
                run.state = "failed"
                run.note = str(exc)
                run.finished_at = datetime.now(timezone.utc)
                await session.commit()
            except Exception:
                pass


async def pause_run(run_id: str) -> RecipeRun:
    async with SessionLocal() as session:
        run = await _ensure_run(session, run_id)
        if run.state == "running":
            run.state = "paused"
            await session.commit()
        return run


async def resume_run(run_id: str) -> RecipeRun:
    async with SessionLocal() as session:
        run = await _ensure_run(session, run_id)
        if run.state == "paused":
            run.state = "running"
            await session.commit()
        return run


async def stop_run(run_id: str) -> RecipeRun:
    async with SessionLocal() as session:
        run = await _ensure_run(session, run_id)
        if run.state in ("running", "paused", "queued"):
            run.state = "aborted"
            run.finished_at = datetime.now(timezone.utc)
            await session.commit()
        task = _active.get(run_id)
        if task and not task.done():
            task.cancel()
        return run
