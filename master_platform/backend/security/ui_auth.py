"""
Session-cookie auth for the Admin UI.

Machine clients keep using ``X-API-Key`` headers (see ``security/auth.py``).
Browser users sign in once at ``/login`` with their API key plaintext and
get a signed session cookie via Starlette's ``SessionMiddleware``. Cookies
store only the API key id; the plaintext secret never leaves
``/login`` once submitted.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey
from .auth import SCOPE_HIERARCHY


SESSION_KEY = "neuron_api_key_id"
LAST_SECRET_KEY = "neuron_last_secret"


class UILoginRequired(Exception):
    """Raised when a UI route is hit without a session."""


class UIPermissionDenied(Exception):
    """Raised when a session lacks the required tier/scope."""


def _scopes_for(api_key: APIKey) -> set[str]:
    base = set(api_key.scopes_json or [])
    base.update(SCOPE_HIERARCHY.get(api_key.tier, set()))
    return base


async def ui_current_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> APIKey | None:
    key_id = request.session.get(SESSION_KEY)
    if not key_id:
        return None
    row = await session.get(APIKey, key_id)
    if row is None or row.status not in ("active",):
        return None
    if row.expires_at:
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return None
    return row


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
    if "admin" not in _scopes_for(api_key):
        raise UIPermissionDenied()
    return api_key


def consume_last_secret(request: Request) -> str | None:
    """Return and clear the one-shot 'last issued plaintext secret'."""
    return request.session.pop(LAST_SECRET_KEY, None)


def stash_last_secret(request: Request, secret: str) -> None:
    request.session[LAST_SECRET_KEY] = secret
