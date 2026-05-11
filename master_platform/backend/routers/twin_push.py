"""Internal endpoint Edge Runtimes call to push twin updates instantly to the
Master, which fans them out via SSE to any subscribed browser.

Auth: any API key with the ``devices:write`` scope. In a hardened
deployment, issue a dedicated 'edge' tier key per Edge and pin its IP
in the Edge's outbound allow-list.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..models import APIKey
from ..runtime.twin_stream import hub
from ..security.auth import require_scopes


router = APIRouter(prefix="/api/v1/internal", tags=["twin-push"])


class PushBody(BaseModel):
    device_dna: str
    twin: dict[str, Any]
    edge_id: str | None = None


@router.post("/twin-push")
async def twin_push(
    body: PushBody,
    _: APIKey = Depends(require_scopes("devices:write")),
) -> dict:
    payload = {
        "online": True,
        "twin": body.twin,
        "at": datetime.now(timezone.utc).isoformat(),
        "source": "edge_push",
        "edge_id": body.edge_id,
    }
    hub.push(body.device_dna, payload)
    return {"ok": True, "delivered": True}
