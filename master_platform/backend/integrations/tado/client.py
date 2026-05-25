"""Tado (legacy V3+) cloud client.

Auth: OAuth2 device-code flow. Tado shut down the username/password
grant in early 2025, so first connection is interactive:

    1. start_device_login()  → returns a verification URL + user code.
       Operator opens the URL, logs in, approves "Neuron".
    2. poll_device_login()   → exchanges the device_code for an
       access_token + refresh_token once approved.
    3. The refresh_token is stored (Fernet-encrypted) on the
       VendorAccount; every later call refreshes silently.

Control surface (all per home → per zone):
    list_homes()                       homes on the account
    list_zones(home_id)                heating / hot-water zones
    zone_state(home_id, zone_id)       current temp / setpoint / power
    set_zone_temperature(...)          manual overlay to a setpoint
    set_zone_off(...)                  manual overlay OFF (frost-protect)
    resume_schedule(...)               clear overlay, back to schedule
    set_presence(home_id, HOME|AWAY)   whole-home presence lock

This is a CLOUD integration. Per the platform's local-brain rule it
lives at the Master level only — a Tado zone can't be driven by an
offline leaf and the local-brain failsafe contract does NOT extend to
it. Treat Tado zones as advisory comfort control, never as a safety
interlock.
"""
from __future__ import annotations

import time
from typing import Any


# Public Tado OAuth2 client id used by the device-code flow (same id the
# official Tado app + community libraries use post-2025). Overridable via
# VendorAccount.extra_json["client_id"] if Tado rotates it.
_DEFAULT_CLIENT_ID = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"
_AUTH_BASE = "https://login.tado.com/oauth2"
_API_BASE = "https://my.tado.com/api/v2"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# Refresh a little before the token actually expires to avoid races.
_EXPIRY_SKEW_S = 30


class TadoError(RuntimeError):
    pass


class TadoClient:
    """Stateless-ish client built around one account's tokens.

    Construct with whatever is known (refresh_token for an already-
    connected account; nothing for a fresh device-code login). The
    caller is responsible for persisting any rotated refresh_token via
    the ``on_token_refresh`` callback so the next process can reuse it.
    """

    def __init__(
        self,
        *,
        refresh_token: str | None = None,
        client_id: str | None = None,
        api_base: str | None = None,
        auth_base: str | None = None,
        on_token_refresh=None,
    ):
        self.client_id = client_id or _DEFAULT_CLIENT_ID
        self.api_base = (api_base or _API_BASE).rstrip("/")
        self.auth_base = (auth_base or _AUTH_BASE).rstrip("/")
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._access_expires_at = 0.0
        self._on_token_refresh = on_token_refresh

    # ── HTTP helper ──────────────────────────────────────────────────
    def _client(self):
        import httpx
        return httpx.AsyncClient(timeout=20.0)

    # ── Device-code login ────────────────────────────────────────────
    async def start_device_login(self) -> dict:
        """Begin the device-code flow. Returns the dict the operator
        needs:  user_code, verification_uri, verification_uri_complete,
        device_code, interval, expires_in."""
        async with self._client() as c:
            r = await c.post(
                f"{self.auth_base}/device_authorize",
                params={"client_id": self.client_id, "scope": "offline_access"},
            )
            if r.status_code != 200:
                raise TadoError(f"device_authorize failed: HTTP {r.status_code} {r.text[:200]}")
            return r.json()

    async def poll_device_login(self, device_code: str) -> dict:
        """Exchange a device_code for tokens. Returns:
            {"status":"pending"} while the operator hasn't approved yet,
            {"status":"ok","refresh_token":...} once approved,
        raises TadoError on hard failure (expired / denied)."""
        async with self._client() as c:
            r = await c.post(
                f"{self.auth_base}/token",
                data={
                    "client_id": self.client_id,
                    "device_code": device_code,
                    "grant_type": _DEVICE_GRANT,
                },
            )
        if r.status_code == 200:
            tok = r.json()
            self._absorb_token(tok)
            return {"status": "ok", "refresh_token": self._refresh_token}
        # Pending / slow_down are expected during polling.
        try:
            err = (r.json() or {}).get("error", "")
        except Exception:
            err = r.text[:120]
        if err in ("authorization_pending", "slow_down"):
            return {"status": "pending", "error": err}
        raise TadoError(f"device token exchange failed: {err or r.status_code}")

    # ── Token lifecycle ──────────────────────────────────────────────
    def _absorb_token(self, tok: dict) -> None:
        self._access_token = tok.get("access_token")
        self._access_expires_at = time.time() + int(tok.get("expires_in", 599))
        new_refresh = tok.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_token_refresh:
                # Persist the rotated refresh token (Tado rotates on use).
                self._on_token_refresh(new_refresh)

    async def _ensure_access(self) -> str:
        if self._access_token and time.time() < (self._access_expires_at - _EXPIRY_SKEW_S):
            return self._access_token
        if not self._refresh_token:
            raise TadoError("not connected — no refresh token (run device login first)")
        async with self._client() as c:
            r = await c.post(
                f"{self.auth_base}/token",
                data={
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
            )
        if r.status_code != 200:
            raise TadoError(
                f"token refresh failed: HTTP {r.status_code} {r.text[:200]} "
                "— the Tado connection may have been revoked; reconnect."
            )
        self._absorb_token(r.json())
        if not self._access_token:
            raise TadoError("token refresh returned no access_token")
        return self._access_token

    async def _api(self, method: str, path: str, *, json: Any = None) -> Any:
        token = await self._ensure_access()
        async with self._client() as c:
            r = await c.request(
                method,
                f"{self.api_base}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json,
            )
        if r.status_code == 401:
            # Access token rejected — force one refresh + retry once.
            self._access_token = None
            token = await self._ensure_access()
            async with self._client() as c:
                r = await c.request(
                    method,
                    f"{self.api_base}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    json=json,
                )
        if r.status_code >= 400:
            raise TadoError(f"{method} {path} → HTTP {r.status_code} {r.text[:200]}")
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return None

    # ── Read ─────────────────────────────────────────────────────────
    async def list_homes(self) -> list[dict]:
        me = await self._api("GET", "/me")
        return (me or {}).get("homes", []) or []

    async def list_zones(self, home_id) -> list[dict]:
        return await self._api("GET", f"/homes/{home_id}/zones") or []

    async def zone_state(self, home_id, zone_id) -> dict:
        return await self._api("GET", f"/homes/{home_id}/zones/{zone_id}/state") or {}

    # ── Control ──────────────────────────────────────────────────────
    async def set_zone_temperature(
        self, home_id, zone_id, celsius: float,
        *, termination: str = "MANUAL", duration_s: int | None = None,
        zone_type: str = "HEATING",
    ) -> dict:
        """Manual overlay to a setpoint. termination:
            MANUAL    — until operator clears it (default)
            TADO_MODE — until the next scheduled block
            TIMER     — for duration_s seconds (required if TIMER)."""
        term: dict[str, Any] = {"type": termination}
        if termination == "TIMER":
            if not duration_s:
                raise TadoError("TIMER termination requires duration_s")
            term["durationInSeconds"] = int(duration_s)
        body = {
            "setting": {
                "type": zone_type,
                "power": "ON",
                "temperature": {"celsius": round(float(celsius), 2)},
            },
            "termination": term,
        }
        return await self._api("PUT", f"/homes/{home_id}/zones/{zone_id}/overlay", json=body)

    async def set_zone_off(
        self, home_id, zone_id,
        *, termination: str = "MANUAL", duration_s: int | None = None,
        zone_type: str = "HEATING",
    ) -> dict:
        """Manual overlay OFF (frost-protection on heating zones)."""
        term: dict[str, Any] = {"type": termination}
        if termination == "TIMER":
            if not duration_s:
                raise TadoError("TIMER termination requires duration_s")
            term["durationInSeconds"] = int(duration_s)
        body = {"setting": {"type": zone_type, "power": "OFF"}, "termination": term}
        return await self._api("PUT", f"/homes/{home_id}/zones/{zone_id}/overlay", json=body)

    async def resume_schedule(self, home_id, zone_id) -> None:
        """Clear any manual overlay — zone returns to its smart schedule."""
        await self._api("DELETE", f"/homes/{home_id}/zones/{zone_id}/overlay")

    async def set_presence(self, home_id, presence: str) -> dict:
        """Whole-home presence lock. presence: HOME | AWAY | AUTO.
        AUTO clears the lock so geofencing decides again."""
        p = (presence or "").upper()
        if p == "AUTO":
            await self._api("DELETE", f"/homes/{home_id}/presenceLock")
            return {"homePresence": "AUTO"}
        if p not in ("HOME", "AWAY"):
            raise TadoError("presence must be HOME, AWAY or AUTO")
        return await self._api("PUT", f"/homes/{home_id}/presenceLock", json={"homePresence": p})
