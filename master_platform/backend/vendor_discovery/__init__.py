"""Per-vendor device-discovery dispatch.

Each registered provider implements `discover(account, ...)` and
returns a list of dicts with keys:
    vendor_device_id (str, required) — the vendor's own opaque id
    name              (str)           — operator-friendly label
    model             (str | None)
    device_type       (str | None)    — plug / camera / bulb / hub / …
    mac               (str | None)
    ip_local          (str | None)
    firmware_version  (str | None)
    metadata          (dict, free-form)

`discover_devices(account)` picks the right module based on
account.provider and upserts the result into VendorDevice rows by
(vendor_account_id, vendor_device_id). Re-running is idempotent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import VendorAccount, VendorDevice


log = logging.getLogger("neuron.vendor_discovery")


class DiscoveryError(RuntimeError):
    pass


async def discover_devices(session: AsyncSession,
                           account: VendorAccount) -> dict:
    """Run discovery for `account`, upsert VendorDevice rows, return
    a per-action report:
        {ok, provider, discovered, added, updated, errors[]}
    """
    if account.status != "active":
        raise DiscoveryError(f"account {account.id} status={account.status!r}; expected 'active'")

    provider = (account.provider or "").lower()
    fn = _PROVIDERS.get(provider)
    if fn is None:
        return {
            "ok": False,
            "provider": provider,
            "discovered": 0, "added": 0, "updated": 0,
            "errors": [f"no discovery driver for provider {provider!r}"],
        }

    try:
        devices = await fn(account)
    except Exception as e:
        log.exception("discovery failed for %s/%s", account.provider, account.label)
        return {
            "ok": False, "provider": provider,
            "discovered": 0, "added": 0, "updated": 0,
            "errors": [f"{type(e).__name__}: {str(e)[:200]}"],
        }

    existing = {d.vendor_device_id: d for d in (await session.execute(
        select(VendorDevice).where(VendorDevice.vendor_account_id == account.id)
    )).scalars().all()}

    added = updated = 0
    now = datetime.now(timezone.utc)
    for d in devices:
        vid = (d.get("vendor_device_id") or "").strip()
        if not vid:
            continue
        row = existing.get(vid)
        if row is None:
            row = VendorDevice(
                vendor_account_id=account.id,
                vendor_device_id=vid,
                name=d.get("name") or vid,
                model=d.get("model"),
                device_type=d.get("device_type"),
                mac=d.get("mac"),
                ip_local=d.get("ip_local"),
                firmware_version=d.get("firmware_version"),
                metadata_json=d.get("metadata") or {},
                last_seen_at=now,
            )
            session.add(row)
            added += 1
        else:
            row.name = d.get("name") or row.name
            row.model = d.get("model") or row.model
            row.device_type = d.get("device_type") or row.device_type
            row.mac = d.get("mac") or row.mac
            row.ip_local = d.get("ip_local") or row.ip_local
            row.firmware_version = d.get("firmware_version") or row.firmware_version
            if d.get("metadata"):
                row.metadata_json = d["metadata"]
            row.last_seen_at = now
            updated += 1
    return {
        "ok": True, "provider": provider,
        "discovered": len(devices), "added": added, "updated": updated,
        "errors": [],
    }


# ─── Provider drivers ──────────────────────────────────────────────────
async def _discover_tapo(account: VendorAccount) -> list[dict]:
    """TP-Link Tapo cloud discovery. Login → token, then getDeviceList.
    Endpoint: https://wap.tplinkcloud.com (or eu-wap, us-wap, etc.)."""
    import httpx
    import uuid as _uuid
    from ..security.secret_crypto import decrypt_secret

    if not account.password_encrypted:
        raise DiscoveryError("Tapo account has no password — cannot login")
    password = decrypt_secret(account.password_encrypted)
    email = account.username or ""
    if not email:
        raise DiscoveryError("Tapo account has no username (email)")

    base = (account.base_url or "").rstrip("/") or "https://wap.tplinkcloud.com"
    term_uuid = (account.extra_json or {}).get("terminal_uuid") or str(_uuid.uuid4())

    async with httpx.AsyncClient(timeout=15.0) as client:
        login_payload = {
            "method": "login",
            "params": {
                "appType": "Tapo_Ios", "cloudUserName": email,
                "cloudPassword": password, "terminalUUID": term_uuid,
            },
        }
        r = await client.post(base, json=login_payload)
        r.raise_for_status()
        body = r.json()
        if body.get("error_code") != 0:
            raise DiscoveryError(
                f"Tapo login error_code={body.get('error_code')} msg={body.get('msg','')[:120]}"
            )
        token = body["result"]["token"]

        r = await client.post(f"{base}?token={token}", json={"method": "getDeviceList"})
        r.raise_for_status()
        body = r.json()
        if body.get("error_code") != 0:
            raise DiscoveryError(
                f"Tapo getDeviceList error_code={body.get('error_code')}"
            )

    out: list[dict] = []
    import base64
    for d in (body.get("result") or {}).get("deviceList") or []:
        # Tapo returns the alias base64-encoded.
        alias = d.get("alias") or ""
        try:
            alias = base64.b64decode(alias).decode("utf-8")
        except Exception:
            pass
        out.append({
            "vendor_device_id": d.get("deviceId") or "",
            "name": alias or d.get("deviceName") or d.get("deviceId") or "",
            "model": d.get("deviceModel"),
            "device_type": d.get("deviceType"),
            "mac": d.get("deviceMac"),
            "ip_local": d.get("deviceLocalIP"),
            "firmware_version": d.get("fwVer"),
            "metadata": {
                "deviceRegion": d.get("deviceRegion"),
                "deviceHwVer":  d.get("deviceHwVer"),
                "role":          d.get("role"),
                "appServerUrl":  d.get("appServerUrl"),
            },
        })
    return out


async def _discover_ring(account: VendorAccount) -> list[dict]:
    """Ring (Amazon doorbell / camera / alarm) device discovery via the
    official OAuth API.

    Ring's first-login flow requires interactive 2FA (SMS / TOTP) which
    the master can't drive headlessly, so this driver works off a
    long-lived refresh_token the operator generates out-of-band:

        pip install ring-doorbell
        ring-doorbell auth-token --username you@example.com

    Paste the resulting token into the "Refresh token" field on the
    VendorAccount and click 🔄 Discover.

    Ring binds tokens to a `hardware_id` UUID; we reuse the same one
    across discovery calls (stored under extra_json.hardware_id) so
    repeat calls don't trigger fresh 2FA challenges.

    Returns endpoints across all six device classes Ring exposes:
    doorbells, stickup cams, chimes, alarm base stations, beams
    bridges, plus authorized (shared) doorbells.
    """
    import httpx, uuid as _uuid
    from ..security.secret_crypto import decrypt_secret

    if not account.refresh_token_encrypted:
        raise DiscoveryError(
            "Ring needs a refresh token. Generate one with "
            "`ring-doorbell auth-token --username <email>` (pip install "
            "ring-doorbell), then paste it into the Refresh token field."
        )
    refresh_token = decrypt_secret(account.refresh_token_encrypted)

    extra = dict(account.extra_json or {})
    hardware_id = extra.get("hardware_id") or str(_uuid.uuid4())

    base = (account.base_url or "").rstrip("/") or "https://oauth.ring.com"
    api_base = "https://api.ring.com"
    headers_common = {
        "User-Agent": "Neuron Platform/0.3",
        "hardware_id": hardware_id,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1. Refresh → access_token. Ring rotates refresh tokens, so
        # the response carries a fresh one we should capture.
        r = await client.post(
            f"{base}/oauth/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     "ring_official_android",
                "scope":         "client",
            },
            headers={"User-Agent": headers_common["User-Agent"]},
        )
        if r.status_code != 200:
            raise DiscoveryError(
                f"Ring oauth refresh failed: HTTP {r.status_code} "
                f"{r.text[:200]}"
            )
        tok = r.json()
        access_token = tok.get("access_token")
        if not access_token:
            raise DiscoveryError("Ring oauth response missing access_token")

        auth_headers = {**headers_common,
                        "Authorization": f"Bearer {access_token}"}

        # 2. Bind session to hardware_id (Ring may 200 or 401 here —
        # the device list endpoint works either way as long as the
        # token is valid).
        try:
            await client.post(
                f"{api_base}/clients_api/session",
                json={
                    "device": {
                        "hardware_id": hardware_id,
                        "metadata": {"api_version": 11,
                                     "device_model": "neuron-master"},
                        "os": "linux",
                        "app_brand": "ring",
                    }
                },
                headers=auth_headers,
            )
        except httpx.HTTPError:
            pass   # session bind is best-effort

        # 3. Device list
        r = await client.get(
            f"{api_base}/clients_api/ring_devices",
            headers=auth_headers,
        )
        if r.status_code != 200:
            raise DiscoveryError(
                f"Ring device list failed: HTTP {r.status_code} "
                f"{r.text[:200]}"
            )
        body = r.json()

    out: list[dict] = []

    def _add(entries, device_type: str) -> None:
        for d in entries or []:
            settings = d.get("settings") or {}
            out.append({
                "vendor_device_id": str(d.get("id") or ""),
                "name":             (d.get("description")
                                     or settings.get("device_id")
                                     or str(d.get("id"))),
                "model":            d.get("kind"),
                "device_type":      device_type,
                "mac":              d.get("device_id"),
                "firmware_version": d.get("firmware_version"),
                "metadata": {
                    "battery_life":  d.get("battery_life"),
                    "address":       d.get("address"),
                    "time_zone":     d.get("time_zone"),
                    "subscribed":    d.get("subscribed"),
                    "owner":         (d.get("owner") or {}).get("email"),
                    "ring_kind":     d.get("kind"),
                    "led_status":    settings.get("led_status"),
                    "siren_seconds": settings.get("chime_settings"),
                },
            })

    _add(body.get("doorbots"),            "doorbell")
    _add(body.get("authorized_doorbots"), "doorbell_shared")
    _add(body.get("stickup_cams"),        "camera")
    _add(body.get("chimes"),              "chime")
    _add(body.get("base_stations"),       "alarm_hub")
    _add(body.get("beams_bridges"),       "smart_lighting_bridge")
    return out


async def _discover_alexa(account: VendorAccount) -> list[dict]:
    """Amazon Alexa Smart Home discovery via Login With Amazon (LWA) +
    the Alexa Smart Home Skill API.

    Full OAuth flow not yet wired — needs an LWA developer registration
    + skill setup on the master side, which is a separate piece of
    operator setup. Until that lands the operator adds Alexa devices
    manually with the device serial / endpointId from the Alexa app
    (Settings → Device Settings → "About"), or by exporting the device
    list from alexa.amazon.com.

    When the OAuth path is wired this becomes:
      1. POST https://api.amazon.com/auth/o2/token with refresh_token
         + LWA client_id/secret → access_token.
      2. POST https://api.eu.amazonalexa.com/v3/events with a
         Discovery.DiscoverRequest directive → endpoints[] list.
    """
    log.warning("Alexa discovery not implemented — add devices manually "
                "(Alexa app → Settings → Device Settings → 'About' → "
                "copy the Device Serial Number)")
    return []


async def _discover_google_home(account: VendorAccount) -> list[dict]:
    """Google Home / Nest device discovery via Smart Device Management
    API. Requires Google Cloud project + OAuth client + SDM API enable
    fee ($5 one-time). Stubbed for now — operator adds devices manually
    with the resource name from the Google Home app."""
    log.warning("Google Home discovery not implemented — add devices "
                "manually (Google Home app → device → Settings → Device "
                "information → copy the device id)")
    return []


async def _discover_stub(account: VendorAccount) -> list[dict]:
    """Placeholder for providers we haven't wired discovery for yet.
    Returns an empty list so the operator gets a clear "no devices
    discovered" rather than a 500."""
    log.warning("discovery stub for %s — returning empty list", account.provider)
    return []


_PROVIDERS = {
    # ── Real drivers ────────────────────────────────────────────────────
    "tapo":              _discover_tapo,
    # ── Voice / hub fleets ──────────────────────────────────────────────
    "alexa":             _discover_alexa,
    "google_home":       _discover_google_home,
    "homekit":           _discover_stub,
    "smartthings":       _discover_stub,
    "hubitat":           _discover_stub,
    "home_assistant":    _discover_stub,
    "ifttt":             _discover_stub,
    # ── Smart plugs / switches ──────────────────────────────────────────
    "kasa":              _discover_stub,
    "shelly":            _discover_stub,
    "sonoff":            _discover_stub,
    "meross":            _discover_stub,
    "wyze":              _discover_stub,
    "wemo":              _discover_stub,
    "gosund":            _discover_stub,
    "teckin":            _discover_stub,
    "athom":             _discover_stub,
    # ── Lighting ────────────────────────────────────────────────────────
    "hue":               _discover_stub,
    "lifx":              _discover_stub,
    "yeelight":          _discover_stub,
    "nanoleaf":          _discover_stub,
    "ikea_tradfri":      _discover_stub,
    "govee":             _discover_stub,
    "innr":              _discover_stub,
    "wiz":               _discover_stub,
    # ── Climate / HVAC ──────────────────────────────────────────────────
    "nest":              _discover_stub,
    "ecobee":            _discover_stub,
    "honeywell":         _discover_stub,
    "tado":              _discover_stub,
    "sensi":             _discover_stub,
    "drayton_wiser":     _discover_stub,
    # ── Cameras / doorbells / security ──────────────────────────────────
    "ring":              _discover_ring,
    "eufy":              _discover_stub,
    "arlo":              _discover_stub,
    "blink":             _discover_stub,
    "reolink":           _discover_stub,
    "amcrest":           _discover_stub,
    "swann":             _discover_stub,
    "wyze_cam":          _discover_stub,
    "google_nest_cam":   _discover_stub,
    # ── Locks ───────────────────────────────────────────────────────────
    "august":            _discover_stub,
    "yale":              _discover_stub,
    "schlage":           _discover_stub,
    "nuki":              _discover_stub,
    # ── Garage / shutters ───────────────────────────────────────────────
    "myq":               _discover_stub,
    "garadget":          _discover_stub,
    "somfy":             _discover_stub,
    "lutron":            _discover_stub,
    # ── Sensors / hubs / mesh ───────────────────────────────────────────
    "aqara":             _discover_stub,
    "xiaomi_mihome":     _discover_stub,
    "fibaro":            _discover_stub,
    "smartwings":        _discover_stub,
    # ── Robotic vacuums / floor care ────────────────────────────────────
    "roborock":          _discover_stub,
    "irobot":            _discover_stub,
    "shark_ion":         _discover_stub,
    "dyson":             _discover_stub,
    "ecovacs":           _discover_stub,
    "dreame":            _discover_stub,
    # ── Energy / solar / EV ─────────────────────────────────────────────
    "sense_energy":      _discover_stub,
    "emporia":           _discover_stub,
    "tesla":             _discover_stub,
    "enphase":           _discover_stub,
    "solaredge":         _discover_stub,
    "chargepoint":       _discover_stub,
    "wallbox":           _discover_stub,
    "juicebox":          _discover_stub,
    "ohme":              _discover_stub,
    # ── Network gear / infrastructure ───────────────────────────────────
    "unifi":             _discover_stub,
    "tp_link_omada":     _discover_stub,
    "asus_router":       _discover_stub,
    "eero":              _discover_stub,
    # ── Irrigation / outdoor ────────────────────────────────────────────
    "rachio":            _discover_stub,
    "gardena":           _discover_stub,
    "orbit_bhyve":       _discover_stub,
    # ── Audio / media ───────────────────────────────────────────────────
    "sonos":             _discover_stub,
    "bose":              _discover_stub,
    "chromecast":        _discover_stub,
    "roku":              _discover_stub,
    "apple_tv":          _discover_stub,
    "spotify_connect":   _discover_stub,
    # ── Appliances ──────────────────────────────────────────────────────
    "lg_thinq":          _discover_stub,
    "samsung_smartthings_app": _discover_stub,
    "miele":             _discover_stub,
    "bosch_home_connect":_discover_stub,
    "whirlpool_smarthq": _discover_stub,
    # ── Generic / industrial protocols ──────────────────────────────────
    "mqtt_generic":      _discover_stub,
    "modbus_generic":    _discover_stub,
    "http_generic":      _discover_stub,
    "rest_api_generic":  _discover_stub,
    "websocket_generic": _discover_stub,
    # ── Catch-all ───────────────────────────────────────────────────────
    "other":             _discover_stub,
}
