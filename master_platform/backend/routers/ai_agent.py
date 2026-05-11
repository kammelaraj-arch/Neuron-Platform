"""AI Agent — the only path between natural-language intent and the
hardware. Hard rule from the architecture brief: AI **cannot** talk
directly to hardware. AI sends *intent* here; this router validates,
maps to a brain-mediated action (currently: a recipe run), and returns
the resolved plan.

Intent shape:
  {
    "intent": "heat to 80C and hold 30 minutes",
    "device_dna": "DNA-...",
    "context": {"recipe_hint": "recipe.cooking.hold_80c"}  // optional
  }

Resolution rules (Stage 5 slice):
  - If ``context.recipe_hint`` is present, run that recipe.
  - Else, do a deterministic intent-to-recipe match against the file
    catalog (substring of recipe.name).
  - Refuse anything else with 422 + a structured 'cannot translate'.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..models import APIKey
from ..runtime import recipe_runner
from ..security.auth import require_scopes


router = APIRouter(prefix="/api/ai", tags=["ai-agent"])


class IntentBody(BaseModel):
    intent: str
    device_dna: str
    context: dict[str, Any] | None = None
    dry_run: bool = False


_TEMP_RE = re.compile(r"(\d{2,3})\s*[°cC]?", re.IGNORECASE)
_HOLD_RE = re.compile(r"hold(?:\s+(?:for|at)?)?\s+(\d{1,3})\s*(?:min|minute|minutes)", re.IGNORECASE)


async def _resolve_recipe(intent: str, ctx: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    if ctx and ctx.get("recipe_hint"):
        return ctx["recipe_hint"], {}
    available = await recipe_runner.list_available_recipes()
    text = intent.lower()
    # naive: match recipe name substring
    for r in available:
        nm = (r.get("name") or "").lower()
        if nm and nm.split(" ")[0] in text:
            return r["recipe_id"], {}
    # heuristic: 'heat to 80' + 'hold N min'
    temp = _TEMP_RE.search(intent)
    hold = _HOLD_RE.search(intent)
    if "heat" in text or "cook" in text or "hold" in text:
        for r in available:
            if "heater" in (r.get("twin_targets") or [""])[0].lower() or "cook" in (r.get("name") or "").lower():
                params: dict[str, Any] = {}
                if temp:
                    params["setpoint_c"] = int(temp.group(1))
                if hold:
                    params["hold_minutes"] = int(hold.group(1))
                return r["recipe_id"], params
    return None


@router.post("/intent")
async def submit_intent(
    body: IntentBody,
    actor: APIKey = Depends(require_scopes("processes:write")),
) -> dict:
    resolved = await _resolve_recipe(body.intent, body.context)
    if resolved is None:
        raise HTTPException(
            422,
            detail={
                "ok": False,
                "reason": "cannot_translate",
                "hint": "no recipe matches; pass context.recipe_hint",
            },
        )
    recipe_id, params = resolved
    plan = {
        "intent": body.intent,
        "resolved_recipe_id": recipe_id,
        "device_dna": body.device_dna,
        "params": params,
        "executed_via": "brain_mediated",
        "dry_run": body.dry_run,
    }
    if body.dry_run:
        return {"ok": True, "plan": plan}

    try:
        run = await recipe_runner.start_run(
            recipe_id=recipe_id,
            device_dna=body.device_dna,
            params_override=params,
            started_by=actor.id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "plan": plan, "run_id": run.id, "state": run.state}
