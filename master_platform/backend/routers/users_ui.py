"""Admin UI for human user accounts (User table).

Distinct from /ui/secrets which is for API keys. Both auth paths
converge into the same session; this page manages the human side.
"""
from __future__ import annotations

import secrets as _secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, USER_TIERS, USER_STATUSES, User
from ..security.audit import record
from ..security.ui_auth import ui_require_admin


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-users"])


@router.get("/ui/users", response_class=HTMLResponse)
async def ui_users_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    rows = (await session.execute(
        select(User).order_by(User.tier, User.username)
    )).scalars().all()
    flash = request.session.pop("users_flash", None)
    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "users": rows,
            "tiers": USER_TIERS,
            "statuses": USER_STATUSES,
            "flash": flash,
            "signed_in": True,
        },
    )


@router.post("/ui/users/new")
async def ui_users_new(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    email: str = Form(""),
    full_name: str = Form(""),
    tier: str = Form("operator"),
    must_change_password: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if tier not in USER_TIERS:
        raise HTTPException(400, f"tier must be one of {USER_TIERS}")
    if len(password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    from argon2 import PasswordHasher
    existing = (await session.execute(
        select(User).where(User.username == username.strip())
    )).scalar_one_or_none()
    if existing is not None:
        request.session["users_flash"] = {"kind": "amber",
                                          "msg": f"Username '{username}' already exists."}
        return RedirectResponse("/ui/users", status_code=303)
    user = User(
        username=username.strip(),
        password_hash=PasswordHasher().hash(password),
        email=email.strip() or None,
        full_name=full_name.strip() or None,
        tier=tier,
        must_change_password=bool(must_change_password),
    )
    session.add(user)
    await session.flush()
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="user.create", target_kind="user", target_id=user.id,
        detail={"username": user.username, "tier": user.tier,
                "must_change_password": user.must_change_password},
    )
    await session.commit()
    request.session["users_flash"] = {"kind": "emerald",
                                      "msg": f"User '{user.username}' created."}
    return RedirectResponse("/ui/users", status_code=303)


@router.post("/ui/users/{user_id}/reset-password")
async def ui_users_reset_password(
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    from argon2 import PasswordHasher
    new_pw = _secrets.token_urlsafe(12)
    user.password_hash = PasswordHasher().hash(new_pw)
    user.must_change_password = True
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="user.reset_password", target_kind="user", target_id=user.id,
        detail={"username": user.username},
    )
    await session.commit()
    request.session["users_flash"] = {
        "kind": "emerald",
        "msg": f"Temporary password for '{user.username}': {new_pw}  "
               "(shown once — copy now). User must change on next login.",
    }
    return RedirectResponse("/ui/users", status_code=303)


@router.post("/ui/users/{user_id}/status")
async def ui_users_set_status(
    user_id: str,
    request: Request,
    status: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if status not in USER_STATUSES:
        raise HTTPException(400, f"status must be one of {USER_STATUSES}")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    prev = user.status
    user.status = status
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="user.set_status", target_kind="user", target_id=user.id,
        detail={"from": prev, "to": status, "username": user.username},
    )
    await session.commit()
    return RedirectResponse("/ui/users", status_code=303)


@router.post("/ui/users/{user_id}/tier")
async def ui_users_set_tier(
    user_id: str,
    request: Request,
    tier: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if tier not in USER_TIERS:
        raise HTTPException(400, f"tier must be one of {USER_TIERS}")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    prev = user.tier
    user.tier = tier
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="user.set_tier", target_kind="user", target_id=user.id,
        detail={"from": prev, "to": tier, "username": user.username},
    )
    await session.commit()
    return RedirectResponse("/ui/users", status_code=303)


@router.post("/ui/users/{user_id}/delete")
async def ui_users_delete(
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    label = user.username
    await session.delete(user)
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="user.delete", target_kind="user", target_id=user_id,
        detail={"username": label},
    )
    await session.commit()
    request.session["users_flash"] = {"kind": "red", "msg": f"Deleted user '{label}'."}
    return RedirectResponse("/ui/users", status_code=303)
