from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..security.audit import record
from ..security.keys import find_active_by_secret
from ..security.ui_auth import SESSION_KEY


_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["ui-auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": error}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    secret: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    key = await find_active_by_secret(session, secret.strip())
    if key is None:
        await session.commit()
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid or revoked API key."},
            status_code=401,
        )
    request.session[SESSION_KEY] = key.id
    await record(
        session,
        actor=key.id,
        actor_kind="ui_session",
        action="ui_login",
        target_kind="apikey",
        target_id=key.id,
    )
    await session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
