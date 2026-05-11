from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import APIKey


_PH = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
_KEY_PREFIX = "neu_"


def generate_secret() -> str:
    return _KEY_PREFIX + secrets.token_urlsafe(36)


def hash_secret(secret: str) -> str:
    return _PH.hash(secret)


def verify_secret(secret: str, hashed: str) -> bool:
    try:
        return _PH.verify(hashed, secret)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


async def find_active_by_secret(session: AsyncSession, secret: str) -> APIKey | None:
    """Verify the supplied secret against every active key. O(n) on n active
    keys, which is fine: deployments hold tens, not millions."""
    if not secret or not secret.startswith(_KEY_PREFIX):
        return None
    res = await session.execute(
        select(APIKey).where(APIKey.status.in_(("active", "rotated")))
    )
    now = datetime.now(timezone.utc)
    for row in res.scalars():
        expires_at = row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at < now:
            continue
        if verify_secret(secret, row.key_hash):
            row.last_used_at = now
            return row
    return None


def issue_payload(
    *,
    label: str | None,
    owner: str,
    tier: str,
    scopes: list[str],
    rate_per_minute: int = 120,
    rate_burst: int = 30,
    ttl_days: int | None = 365,
) -> tuple[str, dict]:
    """Return (plaintext, db_kwargs) for a new key. Plaintext is shown ONCE."""
    secret = generate_secret()
    hashed = hash_secret(secret)
    expires = (
        datetime.now(timezone.utc) + timedelta(days=ttl_days)
        if ttl_days
        else None
    )
    return secret, {
        "label": label,
        "owner": owner,
        "tier": tier,
        "scopes_json": scopes,
        "rate_per_minute": rate_per_minute,
        "rate_burst": rate_burst,
        "expires_at": expires,
        "status": "active",
        "hash_alg": "argon2id",
        "key_hash": hashed,
    }
