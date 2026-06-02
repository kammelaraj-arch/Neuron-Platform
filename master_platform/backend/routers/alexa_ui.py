"""Custom Alexa Smart Home Skill — setup UI + OAuth + directive webhook.

Three responsibilities in this file:

  1. /ui/alexa             — operator setup page (bootstrap client_id +
                              secret, paste-friendly URLs for Amazon
                              Developer Console).
  2. /api/alexa/oauth/*    — OAuth 2.0 Account Linking endpoints Alexa
                              calls (authorize page + token endpoint).
  3. /api/alexa/directive  — Smart Home Skill webhook. Lambda POSTs
                              here; we proxy directives to Tado / Ring.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse)
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..integrations.alexa import AlexaStore
from ..integrations.alexa.directives import handle_directive
from ..models import APIKey
from ..security.audit import record
from ..security.ui_auth import SESSION_USER_KEY, ui_require_admin

_log = logging.getLogger("neuron.alexa")
_BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

router = APIRouter(tags=["alexa"])
_store = AlexaStore()

# Sensible default Alexa account email — overwritten the first time the
# operator bootstraps the skill with a different address.
_DEFAULT_ALEXA_EMAIL = "rajusreee@msn.com"


def _public_base(request: Request) -> str:
    # Prefer the X-Forwarded-Proto/Host nginx sets so we generate
    # https://neuron.shital.org.uk URLs even when uvicorn sees http.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


# ── Setup UI ────────────────────────────────────────────────────────
@router.get("/ui/alexa", response_class=HTMLResponse)
async def ui_alexa(
    request: Request,
    actor: APIKey = Depends(ui_require_admin),
):
    state = await _store.get_state()
    skill = state.skill or {}
    flash = request.session.pop("alexa_flash", None)
    one_time_secret = request.session.pop("alexa_one_time_secret", None)
    return templates.TemplateResponse(
        "alexa.html",
        {
            "request": request,
            "signed_in": True,
            "skill": skill,
            "provisioned": bool(skill.get("client_id")),
            "default_email": skill.get("alexa_account_email") or _DEFAULT_ALEXA_EMAIL,
            "public_base": _public_base(request),
            "one_time_secret": one_time_secret,
            "flash": flash,
        },
    )


@router.post("/ui/alexa/bootstrap")
async def ui_alexa_bootstrap(
    request: Request,
    alexa_account_email: str = Form(_DEFAULT_ALEXA_EMAIL),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    if "@" not in alexa_account_email:
        raise HTTPException(400, "invalid email")
    client_id, client_secret = await _store.bootstrap_skill(
        alexa_account_email=alexa_account_email,
        public_base_url=_public_base(request),
    )
    # Show the plaintext secret ONCE on the next page render.
    request.session["alexa_one_time_secret"] = client_secret
    request.session["alexa_flash"] = {
        "kind": "emerald",
        "msg": f"Skill credentials generated for {alexa_account_email}. "
               "Copy the client secret now — it won't be shown again.",
    }
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="alexa.skill.bootstrap", target_kind="alexa_skill",
        target_id=client_id,
        detail={"alexa_account_email": alexa_account_email},
    )
    await session.commit()
    return RedirectResponse("/ui/alexa", status_code=303)


@router.post("/ui/alexa/skill-id")
async def ui_alexa_set_skill_id(
    request: Request,
    skill_id: str = Form(...),
    session: AsyncSession = Depends(get_session),
    actor: APIKey = Depends(ui_require_admin),
):
    try:
        await _store.set_skill_id(skill_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    request.session["alexa_flash"] = {"kind": "emerald", "msg": "Skill ID saved."}
    await record(
        session, actor=actor.id, actor_kind="ui_session",
        action="alexa.skill.set_id", target_kind="alexa_skill",
        target_id=skill_id,
    )
    await session.commit()
    return RedirectResponse("/ui/alexa", status_code=303)


# ── OAuth 2.0 Account Linking ───────────────────────────────────────
# Alexa redirects the operator's browser here when they tap "Link" in
# the Alexa app. We require a Neuron UI session (admin); on consent we
# issue a one-shot authorization code and redirect back to Alexa.
@router.get("/api/alexa/oauth/authorize", response_class=HTMLResponse)
async def alexa_oauth_authorize_get(
    request: Request,
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    state: str = "",
    scope: str = "",
):
    if response_type != "code":
        return PlainTextResponse("unsupported response_type", status_code=400)
    st = await _store.get_state()
    if not st.skill or st.skill.get("client_id") != client_id:
        return PlainTextResponse("unknown client", status_code=400)
    if redirect_uri not in (st.skill.get("redirect_uris") or []):
        return PlainTextResponse(
            f"redirect_uri not registered: {redirect_uri}", status_code=400)

    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        # Bounce through /login then come back with the same query string.
        nxt = f"/api/alexa/oauth/authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type={response_type}&state={state}&scope={scope}"
        return RedirectResponse(f"/login?next={nxt}", status_code=303)

    # Render minimal consent. POST goes back to the same path with
    # confirmed=1 to issue the code.
    return templates.TemplateResponse(
        "alexa_consent.html",
        {
            "request": request,
            "signed_in": True,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": scope,
            "alexa_email": st.skill.get("alexa_account_email", ""),
        },
    )


@router.post("/api/alexa/oauth/authorize")
async def alexa_oauth_authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        raise HTTPException(401, "must be signed in")
    st = await _store.get_state()
    if not st.skill or st.skill.get("client_id") != client_id:
        raise HTTPException(400, "unknown client")
    if redirect_uri not in (st.skill.get("redirect_uris") or []):
        raise HTTPException(400, "redirect_uri not registered")
    code = await _store.issue_auth_code(user_id, redirect_uri)
    await record(
        session, actor=user_id, actor_kind="ui_session",
        action="alexa.oauth.consent", target_kind="alexa_skill",
        target_id=client_id,
    )
    await session.commit()
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{sep}code={code}&state={state}",
        status_code=303,
    )


@router.post("/api/alexa/oauth/token", response_class=JSONResponse)
async def alexa_oauth_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    code: str = Form(""),
    refresh_token: str = Form(""),
    redirect_uri: str = Form(""),
):
    if not await _store.verify_client(client_id, client_secret):
        raise HTTPException(401, "invalid_client")
    if grant_type == "authorization_code":
        if not code:
            raise HTTPException(400, "missing code")
        user_id = await _store.consume_auth_code(code, redirect_uri or None)
        if user_id is None:
            raise HTTPException(400, "invalid_grant")
        return await _store.issue_token_pair(user_id)
    if grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(400, "missing refresh_token")
        pair = await _store.refresh(refresh_token)
        if pair is None:
            raise HTTPException(400, "invalid_grant")
        return pair
    raise HTTPException(400, "unsupported_grant_type")


# ── Smart Home Skill directive endpoint ─────────────────────────────
@router.post("/api/alexa/directive", response_class=JSONResponse)
async def alexa_directive(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Lambda POSTs here with one directive envelope. Auth: the
    bearerToken on the directive (Discovery) or endpoint.scope (Control)
    must resolve to a Neuron user via our token store."""
    envelope = await request.json()
    directive = (envelope or {}).get("directive") or {}
    if not directive:
        raise HTTPException(400, "missing directive")

    # Extract the Alexa-issued bearer token. Discovery puts it on
    # payload.scope.token; control directives put it on endpoint.scope.token.
    scope = (directive.get("endpoint") or {}).get("scope") \
            or (directive.get("payload") or {}).get("scope") \
            or {}
    token = scope.get("token") if isinstance(scope, dict) else None
    if not token:
        raise HTTPException(401, "missing scope.token")
    user_id = await _store.resolve_access_token(token)
    if user_id is None:
        raise HTTPException(401, "invalid_token")

    try:
        response = await handle_directive(directive, session)
    except Exception:
        _log.exception("alexa directive crashed")
        return {
            "event": {
                "header": {
                    "namespace": "Alexa",
                    "name": "ErrorResponse",
                    "messageId": directive.get("header", {}).get("messageId", ""),
                    "correlationToken": directive.get("header", {}).get("correlationToken", ""),
                    "payloadVersion": "3",
                },
                "endpoint": directive.get("endpoint", {}),
                "payload": {"type": "INTERNAL_ERROR",
                            "message": "Neuron failed to handle directive"},
            }
        }

    # Audit every state-changing call.
    name = directive.get("header", {}).get("name", "")
    if name not in ("Discover",):
        await record(
            session, actor=user_id, actor_kind="alexa_token",
            action=f"alexa.directive.{directive.get('header',{}).get('namespace','')}.{name}",
            target_kind="alexa_endpoint",
            target_id=(directive.get("endpoint") or {}).get("endpointId", ""),
            detail={"namespace": directive.get("header",{}).get("namespace",""),
                    "name": name},
        )
        await session.commit()
    return response
