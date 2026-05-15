"""Session-cookie auth for the Admin UI.

Two authentication paths converge into the same session:

  1. Username + password (User table)
       Humans signing in via the login form. Session stores
       `neuron_user_id`.

  2. API key plaintext (APIKey table)
       Operators with an existing integration key + anyone who
       pastes the key on the login form. Session stores
       `neuron_api_key_id`.

UI routes use `ui_require_login` which returns the principal (as an
APIKey-shaped object for back-compat — User-mode sessions synthesise
one). `ui_require_admin` checks tier == "admin" on whichever kind
authenticated.

Machine clients (HTTP API) keep using the `X-API-Key` header path —
that's in security/auth.py, untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey, User
from .auth import SCOPE_HIERARCHY


SESSION_KEY = "neuron_api_key_id"
SESSION_USER_KEY = "neuron_user_id"
LAST_SECRET_KEY = "neuron_last_secret"


class UILoginRequired(Exception):
    """Raised when a UI route is hit without a session."""


class UIPermissionDenied(Exception):
    """Raised when a session lacks the required tier/scope."""


@dataclass
class Principal:
    kind: str             # "user" | "apikey"
    id: str
    display_name: str
    tier: str
    user: User | None = None
    apikey: APIKey | None = None


def _scopes_for(api_key: APIKey) -> set[str]:
    base = set(api_key.scopes_json or [])
    base.update(SCOPE_HIERARCHY.get(api_key.tier, set()))
    return base


async def ui_current_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Principal | None:
    """Resolve the session to whichever Principal is currently signed
    in. User takes priority over API key if both somehow exist."""
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id:
        user = await session.get(User, user_id)
        if user is not None and user.status == "active":
            return Principal(
                kind="user", id=user.id,
                display_name=user.username, tier=user.tier, user=user,
            )

    key_id = request.session.get(SESSION_KEY)
    if key_id:
        row = await session.get(APIKey, key_id)
        if row is not None and row.status == "active":
            if row.expires_at:
                exp = row.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < datetime.now(timezone.utc):
                    return None
            return Principal(
                kind="apikey", id=row.id,
                display_name=row.label or row.id[:8],
                tier=row.tier, apikey=row,
            )
    return None


async def ui_current_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> APIKey | None:
    """Back-compat: returns an APIKey-shaped object for whichever kind
    authenticated. User-mode sessions synthesise a transient APIKey
    so existing routes that audit against actor.id keep working."""
    p = await ui_current_principal(request, session)
    if p is None:
        return None
    if p.kind == "apikey":
        return p.apikey
    # Synthesise a transient APIKey for the user. Not persisted — only
    # used for tier checks + audit actor strings during this request.
    return APIKey(
        id=f"user:{p.user.id}",
        tier=p.user.tier,
        label=f"user:{p.user.username}",
        owner=p.user.username,
        status="active",
        scopes_json=list(SCOPE_HIERARCHY.get(p.user.tier, set())),
    )


async def ui_require_login(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> APIKey:
    key = await ui_current_key(request, session)
    if key is None:
        raise UILoginRequired()
    return key


async def ui_require_admin(
    api_key: APIKey = Depends(ui_require_login),
) -> APIKey:
    if api_key.tier == "admin":
        return api_key
    if "admin" not in _scopes_for(api_key):
        raise UIPermissionDenied()
    return api_key


def consume_last_secret(request: Request) -> str | None:
    return request.session.pop(LAST_SECRET_KEY, None)


def stash_last_secret(request: Request, secret: str) -> None:
    request.session[LAST_SECRET_KEY] = secret
