"""Alexa announcer — push voice / push-notification announcements via
the third-party Notify Me skill.

The Smart Home Skill API doesn't let a developer send arbitrary text
to a user's Echo without piggybacking on a Routine. The Notify Me
skill exists precisely to fill that gap: the operator enables it once
in the Alexa app, gets a long access code by email, and Notify Me
exposes a public HTTPS endpoint that any caller can POST to with that
code + a message string. The Echo announces (or shows as a
notification) within ~1 s.

We treat Notify Me as the announce channel; the operator configures
the code at /ui/alexa, and any caller with the 'alexa.announce' (or
'admin') scope on a Neuron API key can POST /api/alexa/announce with
{text, urgency?} to fire one. Audit-logged each time.

Trade-off: Notify Me is a third-party skill. If it ever goes away or
rate-limits, the operator needs to switch to the Alexa Proactive
Events API path (announcer.send_via_proactive — not yet wired).
"""
from __future__ import annotations

import logging
from typing import Any


_log = logging.getLogger("neuron.alexa.announcer")

NOTIFY_ME_URL = "https://api.notifymyecho.com/v1/NotifyMe"
NOTIFY_ME_TIMEOUT_S = 8.0


class AnnouncerError(RuntimeError):
    pass


async def send_via_notify_me(text: str, access_code: str,
                             *, title: str | None = None) -> dict[str, Any]:
    """Fire one announcement via Notify Me. Returns the parsed JSON
    response on success; raises AnnouncerError on any failure so the
    caller can surface a concrete reason rather than a generic 500."""
    import httpx
    body = (text or "").strip()
    if not body:
        raise AnnouncerError("text is empty")
    if not access_code or not access_code.strip():
        raise AnnouncerError("Notify Me access code is not configured")
    payload: dict[str, Any] = {
        "notification": body[:255],         # Notify Me limits to ~255 chars
        "accessCode":   access_code.strip(),
    }
    if title:
        payload["title"] = title[:60]
    async with httpx.AsyncClient(timeout=NOTIFY_ME_TIMEOUT_S) as c:
        try:
            r = await c.post(NOTIFY_ME_URL, json=payload)
        except httpx.RequestError as e:
            raise AnnouncerError(f"Notify Me unreachable: {e}") from e
    if r.status_code != 200:
        raise AnnouncerError(
            f"Notify Me rejected the announcement: HTTP {r.status_code} "
            f"{r.text[:200]}"
        )
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}
