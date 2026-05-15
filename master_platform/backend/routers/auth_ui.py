"""Sign-in / sign-out routes.

The login page presents two side-by-side methods:
    • Username + password (User table — humans).
    • API key plaintext   (APIKey table — back-compat + integration
                            operators who'd rather paste a key).
Submitting either one creates a session. Logout clears everything.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import User
from ..security.audit import record
from ..security.keys import find_active_by_secret
from ..security.ui_auth import SESSION_KEY, SESSION_USER_KEY


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": error},
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    secret:   str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Accept EITHER (username + password) OR API key (`secret`).
    Username path takes priority if both are filled."""
    username = (username or "").strip()
    password = (password or "")
    secret   = (secret or "").strip()

    # ── Username + password path ──────────────────────────────────────
    if username and password:
        row = (await session.execute(
            select(User).where(User.username == username)
        )).scalar_one_or_none()
        if row is None or row.status != "active":
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid credentials."},
                status_code=401,
            )
        from argon2 import PasswordHasher
        from argon2.exceptions import VerifyMismatchError
        try:
            PasswordHasher().verify(row.password_hash, password)
        except (VerifyMismatchError, Exception):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid credentials."},
                status_code=401,
            )
        request.session.clear()
        request.session[SESSION_USER_KEY] = row.id
        row.last_login_at = datetime.now(timezone.utc)
        await record(
            session, actor=row.id, actor_kind="ui_session",
            action="ui_login", target_kind="user", target_id=row.id,
            detail={"username": row.username, "tier": row.tier},
        )
        await session.commit()
        if row.must_change_password:
            return RedirectResponse("/change-password", status_code=303)
        return RedirectResponse("/", status_code=303)

    # ── API key path (legacy / integration operators) ─────────────────
    if secret:
        key = await find_active_by_secret(session, secret)
        if key is None:
            await session.commit()
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid or revoked API key."},
                status_code=401,
            )
        request.session.clear()
        request.session[SESSION_KEY] = key.id
        await record(
            session, actor=key.id, actor_kind="ui_session",
            action="ui_login", target_kind="apikey", target_id=key.id,
        )
        await session.commit()
        return RedirectResponse("/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Enter username + password, or an API key."},
        status_code=400,
    )


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_form(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        "change_password.html", {"request": request, "error": error},
    )


@router.post("/change-password")
async def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return RedirectResponse("/login", status_code=303)
    user = await session.get(User, user_id)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    try:
        PasswordHasher().verify(user.password_hash, current_password)
    except (VerifyMismatchError, Exception):
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "error": "Current password is wrong."},
            status_code=400,
        )
    if new_password != new_password_confirm:
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "error": "New password and confirmation don't match."},
            status_code=400,
        )
    if len(new_password) < 8:
        return templates.TemplateResponse(
            "change_password.html",
            {"request": request, "error": "Password must be at least 8 characters."},
            status_code=400,
        )
    user.password_hash = PasswordHasher().hash(new_password)
    user.must_change_password = False
    await record(
        session, actor=user.id, actor_kind="ui_session",
        action="user.password_change", target_kind="user", target_id=user.id,
    )
    await session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
