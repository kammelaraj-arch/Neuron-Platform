from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEvent


async def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    target_kind: str | None = None,
    target_id: str | None = None,
    actor_kind: str = "api_key",
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor=actor,
            actor_kind=actor_kind,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            outcome=outcome,
            detail_json=detail,
        )
    )
