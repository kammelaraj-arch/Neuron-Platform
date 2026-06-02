"""Ring (Amazon) cloud client.

Auth: long-lived refresh token generated out-of-band by the operator
(`pip install ring-doorbell; ring-doorbell auth-token --username …`)
because Ring's first login requires interactive 2FA the master can't
drive headlessly. The same token model is used by the discovery driver
in vendor_discovery — this client just adds control endpoints on top.

Control surface (per device class):
    Cameras / doorbells:
        get_health(device_id)            battery, signal, firmware
        floodlight_on/off(device_id)     floodlight cams only
        siren_on/off(device_id)          cams with built-in siren
        snapshot_url(device_id)          most-recent snapshot bytes URL
    Chimes:
        play_chime(chime_id, kind)       kind: 'ding' | 'motion'
    Alarm hubs:
        list_locations()                 location ids for alarm modes
        set_alarm_mode(location_id, m)   m: 'all' (away) | 'home' | 'none'

Ring is a CLOUD integration — Master level only. Cameras / sirens are
advisory; the local-brain failsafe contract does NOT extend to a Ring
device. Never treat siren_on as a safety actuator.
"""
from __future__ import annotations

import time
from typing import Any
import uuid as _uuid


_OAUTH_URL = "https://oauth.ring.com/oauth/token"
_API_BASE = "https://api.ring.com"
_CLIENT_ID = "ring_official_android"
_USER_AGENT = "Neuron Platform/0.3"
_EXPIRY_SKEW_S = 30


class RingError(RuntimeError):
    pass


class RingClient:
    """One client per VendorAccount. Rotated refresh tokens are
    surfaced via the ``on_token_refresh`` callback so the caller can
    persist them (Ring rotates refresh tokens on use)."""

    def __init__(
        self,
        *,
        refresh_token: str,
        hardware_id: str | None = None,
        on_token_refresh=None,
    ):
        if not refresh_token:
            raise RingError("refresh_token is required")
        self._refresh_token = refresh_token
        self.hardware_id = hardware_id or str(_uuid.uuid4())
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._on_token_refresh = on_token_refresh

    def _client(self):
        import httpx
        return httpx.AsyncClient(timeout=20.0)

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    # ── token lifecycle ──────────────────────────────────────────────
    async def _ensure_access(self) -> str:
        if self._access_token and time.time() < (self._access_expires_at - _EXPIRY_SKEW_S):
            return self._access_token
        async with self._client() as c:
            r = await c.post(
                _OAUTH_URL,
                data={
                    "grant_type":    "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id":     _CLIENT_ID,
                    "scope":         "client",
                },
                headers={"User-Agent": _USER_AGENT},
            )
        if r.status_code != 200:
            raise RingError(
                f"Ring oauth refresh failed: HTTP {r.status_code} {r.text[:200]} "
                "— the Ring token may be revoked; regenerate with "
                "`ring-doorbell auth-token --username <email>`."
            )
        tok = r.json()
        self._access_token = tok.get("access_token")
        self._access_expires_at = time.time() + int(tok.get("expires_in", 3599))
        new_refresh = tok.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_token_refresh:
                self._on_token_refresh(new_refresh)
        if not self._access_token:
            raise RingError("Ring oauth response missing access_token")
        return self._access_token

    async def _api(self, method: str, path: str, *,
                   base: str = _API_BASE, params: dict | None = None,
                   json: Any = None, expect_binary: bool = False) -> Any:
        token = await self._ensure_access()
        headers = {
            "User-Agent": _USER_AGENT,
            "hardware_id": self.hardware_id,
            "Authorization": f"Bearer {token}",
        }
        async with self._client() as c:
            r = await c.request(method, f"{base}{path}",
                                headers=headers, params=params, json=json)
        if r.status_code == 401:
            self._access_token = None
            token = await self._ensure_access()
            headers["Authorization"] = f"Bearer {token}"
            async with self._client() as c:
                r = await c.request(method, f"{base}{path}",
                                    headers=headers, params=params, json=json)
        if r.status_code >= 400:
            raise RingError(f"{method} {path} → HTTP {r.status_code} {r.text[:200]}")
        if expect_binary:
            return r.content
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return r.text

    # ── cameras / doorbells ──────────────────────────────────────────
    async def get_health(self, device_id) -> dict:
        body = await self._api("GET", f"/clients_api/doorbots/{device_id}/health")
        return (body or {}).get("device_health") or body or {}

    async def floodlight_on(self, device_id) -> None:
        await self._api("PUT", f"/clients_api/doorbots/{device_id}/floodlight_light_on")

    async def floodlight_off(self, device_id) -> None:
        await self._api("PUT", f"/clients_api/doorbots/{device_id}/floodlight_light_off")

    async def siren_on(self, device_id, *, duration_s: int = 30) -> None:
        await self._api("PUT", f"/clients_api/doorbots/{device_id}/siren_on",
                        params={"duration": int(duration_s)})

    async def siren_off(self, device_id) -> None:
        await self._api("PUT", f"/clients_api/doorbots/{device_id}/siren_off")

    async def latest_snapshot_ts(self, device_ids: list) -> dict:
        """Returns {device_id: unix_ts_ms} for the most-recent snapshot
        Ring has cached for each device."""
        if not device_ids:
            return {}
        params = [("doorbotIds[]", str(d)) for d in device_ids]
        body = await self._api("GET", "/clients_api/snapshots/timestamps", params=params)
        out: dict = {}
        for entry in (body or {}).get("timestamps", []) or []:
            did = entry.get("doorbotId")
            ts = entry.get("timestamp")
            if did is not None and ts is not None:
                out[str(did)] = int(ts)
        return out

    async def snapshot_bytes(self, device_id) -> bytes:
        """Most-recent snapshot JPEG. Ring caches snapshots ~once/min for
        battery-powered cams, on-motion for wired cams."""
        return await self._api("GET", f"/clients_api/snapshots/image/{device_id}",
                               expect_binary=True)

    # ── chimes ───────────────────────────────────────────────────────
    async def play_chime(self, chime_id, kind: str = "ding") -> None:
        """kind: 'ding' (front-door chime) or 'motion' (motion chime)."""
        k = (kind or "").lower()
        if k not in ("ding", "motion"):
            raise RingError("kind must be 'ding' or 'motion'")
        await self._api("POST", f"/clients_api/chimes/{chime_id}/play_sound",
                        params={"kind": k})

    # ── alarm modes ──────────────────────────────────────────────────
    async def list_locations(self) -> list[dict]:
        body = await self._api("GET", "/devices/v1/locations")
        return (body or {}).get("user_locations", []) or []

    async def set_alarm_mode(self, location_id, mode: str) -> dict:
        """mode: 'all' (away/armed-away), 'home' (armed-home),
        'none' (disarmed)."""
        m = (mode or "").lower()
        if m not in ("all", "home", "none"):
            raise RingError("mode must be 'all', 'home', or 'none'")
        return await self._api(
            "POST",
            f"/api/v1/mode/location/{location_id}",
            json={"mode": m},
        ) or {"mode": m}
