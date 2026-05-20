from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import APIKey
from .keys import find_active_by_secret
from .rate_limit import limiter


SCOPE_HIERARCHY = {
    # superadmin: everything admin can do, plus protected ops like
    # tier elevation, key impersonation, audit purge. Used sparingly.
    "superadmin": {"superadmin", "admin",
                   "library:read", "library:write",
                   "systems:read", "systems:write",
                   "devices:read", "devices:write",
                   "processes:read", "processes:write",
                   "apikeys:read", "apikeys:write",
                   "users:read", "users:write",
                   "audit:read", "audit:write"},
    "admin": {"admin", "library:read", "library:write", "systems:read", "systems:write",
              "devices:read", "devices:write", "processes:read", "processes:write",
              "apikeys:read", "apikeys:write", "audit:read"},
    "integration": {"library:read", "systems:read", "systems:write",
                    "devices:read", "devices:write", "processes:read", "processes:write"},
    "device": {"devices:read", "devices:write", "library:read"},
    # pico: narrowly-scoped tier for a single Pico 2 W. Lets the device
    # publish telemetry, post heartbeats, fetch OTA bundles, and post
    # to its own emergency channel — but NOT read other devices, manage
    # users, or change config. The Master-side endpoints additionally
    # restrict by device DNA (encoded in api_key.owner).
    "pico": {"pico", "telemetry:write", "ota:read", "emergency:write",
             "heartbeat:write"},
    "readonly": {"library:read", "systems:read", "devices:read", "processes:read"},
}


def _scopes_for(api_key: APIKey) -> set[str]:
    base = set(api_key.scopes_json or [])
    base.update(SCOPE_HIERARCHY.get(api_key.tier, set()))
    return base


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_session),
) -> APIKey:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing API key")
    api_key = await find_active_by_secret(session, x_api_key)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")
    allowed = await limiter.allow(
        api_key.id, rate_per_minute=api_key.rate_per_minute, burst=api_key.rate_burst
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded"
        )
    await session.commit()
    return api_key


def require_scopes(*needed: str):
    needed_set = set(needed)

    async def _dep(api_key: APIKey = Depends(require_api_key)) -> APIKey:
        granted = _scopes_for(api_key)
        if "admin" in granted:
            return api_key
        if not needed_set.issubset(granted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing scope(s): {sorted(needed_set - granted)}",
            )
        return api_key

    return _dep
