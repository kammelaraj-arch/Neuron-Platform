"""Alexa Smart Home directive handling.

The directive endpoint receives one JSON envelope per Alexa request and
returns one response envelope. We support the handful of directives
that actually map cleanly to what the platform owns:

    Alexa.Discovery / Discover                  → enumerate endpoints
    Alexa.ThermostatController / SetTargetTemperature  → Tado
    Alexa.ThermostatController / AdjustTargetTemperature  → Tado +/-
    Alexa.PowerController / TurnOn               → Ring floodlight ON
    Alexa.PowerController / TurnOff              → Ring floodlight OFF
                                                  / Tado zone OFF
    Alexa.SecurityPanelController / Arm/Disarm   → Ring alarm hub

Anything else returns Alexa.ErrorResponse INVALID_DIRECTIVE so Alexa
surfaces a clean "I can't do that" rather than hanging.

Endpoint IDs are namespaced so the dispatcher always knows which
integration to call:
    tado:<vendor_device_id>      e.g. tado:1234567:89
    ring:<vendor_device_id>      e.g. ring:223344
    ring-alarm:<account_id>:<location_id>
"""
from __future__ import annotations

import time
import uuid as _uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import VendorAccount, VendorDevice
from ...security.secret_crypto import decrypt_secret, encrypt_secret


def _hdr(name: str, namespace: str = "Alexa", *,
         correlation_token: str | None = None,
         message_id: str | None = None) -> dict:
    h = {
        "namespace": namespace,
        "name": name,
        "messageId": message_id or str(_uuid.uuid4()),
        "payloadVersion": "3",
    }
    if correlation_token:
        h["correlationToken"] = correlation_token
    return h


def _err(directive: dict, code: str, message: str) -> dict:
    h = directive.get("header", {})
    return {
        "event": {
            "header": _hdr("ErrorResponse",
                           correlation_token=h.get("correlationToken")),
            "endpoint": directive.get("endpoint", {}),
            "payload": {"type": code, "message": message},
        }
    }


# ── Discovery ─────────────────────────────────────────────────────────
async def _build_endpoints(session: AsyncSession) -> list[dict]:
    endpoints: list[dict] = []

    # Tado zones — every zone on every connected tado account.
    tado_accounts = (await session.execute(
        select(VendorAccount).where(VendorAccount.provider == "tado")
    )).scalars().all()
    tado_account_ids = [a.id for a in tado_accounts if a.refresh_token_encrypted]
    if tado_account_ids:
        zones = (await session.execute(
            select(VendorDevice).where(VendorDevice.vendor_account_id.in_(tado_account_ids))
        )).scalars().all()
        for z in zones:
            md = z.metadata_json or {}
            endpoints.append({
                "endpointId": f"tado:{z.vendor_device_id}",
                "manufacturerName": "Tado / Neuron",
                "friendlyName": z.name,
                "description": f"Tado zone via Neuron · {md.get('home_name','')}".strip(),
                "displayCategories": ["THERMOSTAT"],
                "capabilities": [
                    {
                        "type": "AlexaInterface",
                        "interface": "Alexa.ThermostatController",
                        "version": "3",
                        "properties": {
                            "supported": [
                                {"name": "targetSetpoint"},
                                {"name": "thermostatMode"},
                            ],
                            "proactivelyReported": False,
                            "retrievable": False,
                        },
                    },
                    {
                        "type": "AlexaInterface",
                        "interface": "Alexa.PowerController",
                        "version": "3",
                        "properties": {
                            "supported": [{"name": "powerState"}],
                            "proactivelyReported": False,
                            "retrievable": False,
                        },
                    },
                    {"type": "AlexaInterface", "interface": "Alexa", "version": "3"},
                ],
            })

    # Ring devices — cameras get PowerController for the floodlight,
    # alarm hubs get a SecurityPanelController per location.
    ring_accounts = (await session.execute(
        select(VendorAccount).where(VendorAccount.provider == "ring")
    )).scalars().all()
    ring_account_ids = [a.id for a in ring_accounts if a.refresh_token_encrypted]
    if ring_account_ids:
        ring_devices = (await session.execute(
            select(VendorDevice).where(VendorDevice.vendor_account_id.in_(ring_account_ids))
        )).scalars().all()
        for d in ring_devices:
            dt = (d.device_type or "").lower()
            if dt in ("camera", "doorbell", "doorbell_shared"):
                endpoints.append({
                    "endpointId": f"ring:{d.vendor_device_id}",
                    "manufacturerName": "Ring / Neuron",
                    "friendlyName": d.name,
                    "description": "Ring floodlight via Neuron",
                    "displayCategories": ["LIGHT"],
                    "capabilities": [
                        {
                            "type": "AlexaInterface",
                            "interface": "Alexa.PowerController",
                            "version": "3",
                            "properties": {
                                "supported": [{"name": "powerState"}],
                                "proactivelyReported": False,
                                "retrievable": False,
                            },
                        },
                        {"type": "AlexaInterface", "interface": "Alexa", "version": "3"},
                    ],
                })
            elif dt == "alarm_hub":
                md = d.metadata_json or {}
                loc = md.get("location_id")
                if not loc:
                    continue
                endpoints.append({
                    "endpointId": f"ring-alarm:{d.vendor_account_id}:{loc}",
                    "manufacturerName": "Ring / Neuron",
                    "friendlyName": d.name + " alarm",
                    "description": "Ring alarm hub via Neuron",
                    "displayCategories": ["SECURITY_PANEL"],
                    "capabilities": [
                        {
                            "type": "AlexaInterface",
                            "interface": "Alexa.SecurityPanelController",
                            "version": "3",
                            "properties": {
                                "supported": [{"name": "armState"}],
                                "proactivelyReported": False,
                                "retrievable": False,
                            },
                            "configuration": {
                                "supportedArmStates": [
                                    {"value": "ARMED_AWAY"},
                                    {"value": "ARMED_STAY"},
                                    {"value": "DISARMED"},
                                ],
                            },
                        },
                        {"type": "AlexaInterface", "interface": "Alexa", "version": "3"},
                    ],
                })

    return endpoints


async def handle_discovery(directive: dict, session: AsyncSession) -> dict:
    endpoints = await _build_endpoints(session)
    return {
        "event": {
            "header": _hdr("Discover.Response", namespace="Alexa.Discovery"),
            "payload": {"endpoints": endpoints},
        }
    }


# ── Control dispatch ─────────────────────────────────────────────────
async def _resolve_tado_zone(session: AsyncSession, vendor_device_id: str
                             ) -> tuple[VendorAccount, str, str] | None:
    row = (await session.execute(
        select(VendorDevice).where(VendorDevice.vendor_device_id == vendor_device_id)
    )).scalar_one_or_none()
    if row is None:
        return None
    account = await session.get(VendorAccount, row.vendor_account_id)
    if account is None or account.provider != "tado":
        return None
    md = row.metadata_json or {}
    home_id = md.get("home_id")
    zone_id = md.get("zone_id")
    if home_id is None or zone_id is None:
        parts = vendor_device_id.split(":", 1)
        if len(parts) == 2:
            home_id, zone_id = parts[0], parts[1]
    if home_id is None or zone_id is None:
        return None
    return account, home_id, zone_id


def _tado_client(account: VendorAccount, rotated: list):
    from ..tado import TadoClient
    refresh = decrypt_secret(account.refresh_token_encrypted) if account.refresh_token_encrypted else None
    extra = dict(account.extra_json or {})
    return TadoClient(
        refresh_token=refresh,
        client_id=extra.get("client_id"),
        api_base=account.base_url or None,
        on_token_refresh=lambda tok: rotated.append(tok),
    )


def _ring_client(account: VendorAccount, rotated: list):
    from ..ring import RingClient
    refresh = decrypt_secret(account.refresh_token_encrypted) if account.refresh_token_encrypted else None
    extra = dict(account.extra_json or {})
    return RingClient(
        refresh_token=refresh or "",
        hardware_id=extra.get("hardware_id"),
        on_token_refresh=lambda tok: rotated.append(tok),
    )


def _persist(account: VendorAccount, rotated: list) -> None:
    if rotated:
        account.refresh_token_encrypted = encrypt_secret(rotated[-1])


async def handle_directive(directive: dict, session: AsyncSession) -> dict:
    """Top-level dispatch. ``directive`` is the dict at envelope.directive."""
    header = directive.get("header", {})
    namespace = header.get("namespace", "")
    name = header.get("name", "")
    endpoint = directive.get("endpoint", {})
    endpoint_id = endpoint.get("endpointId", "")
    payload = directive.get("payload", {}) or {}
    correlation = header.get("correlationToken")

    if namespace == "Alexa.Discovery" and name == "Discover":
        return await handle_discovery(directive, session)

    # ── Tado ────────────────────────────────────────────────────────
    if endpoint_id.startswith("tado:"):
        vendor_device_id = endpoint_id[len("tado:"):]
        resolved = await _resolve_tado_zone(session, vendor_device_id)
        if resolved is None:
            return _err(directive, "NO_SUCH_ENDPOINT",
                        f"Unknown Tado zone {vendor_device_id}")
        account, home_id, zone_id = resolved
        rotated: list = []
        client = _tado_client(account, rotated)
        try:
            if namespace == "Alexa.ThermostatController" and name == "SetTargetTemperature":
                tgt = (payload.get("targetSetpoint") or {})
                celsius = float(tgt.get("value", 21))
                scale = (tgt.get("scale") or "CELSIUS").upper()
                if scale == "FAHRENHEIT":
                    celsius = (celsius - 32) * 5.0 / 9.0
                await client.set_zone_temperature(home_id, zone_id, celsius)
                return _ack_thermostat(directive, celsius)
            if namespace == "Alexa.ThermostatController" and name == "AdjustTargetTemperature":
                delta = (payload.get("targetSetpointDelta") or {})
                d = float(delta.get("value", 0))
                state = await client.zone_state(home_id, zone_id)
                cur = (((state or {}).get("setting") or {}).get("temperature") or {}).get("celsius", 20)
                new_c = float(cur) + d
                await client.set_zone_temperature(home_id, zone_id, new_c)
                return _ack_thermostat(directive, new_c)
            if namespace == "Alexa.PowerController" and name == "TurnOff":
                await client.set_zone_off(home_id, zone_id)
                return _ack_power(directive, "OFF")
            if namespace == "Alexa.PowerController" and name == "TurnOn":
                # "On" with no temperature means resume schedule.
                await client.resume_schedule(home_id, zone_id)
                return _ack_power(directive, "ON")
        finally:
            _persist(account, rotated)
            await session.commit()
        return _err(directive, "INVALID_DIRECTIVE",
                    f"Unsupported directive {namespace}/{name} for Tado")

    # ── Ring camera (floodlight) ────────────────────────────────────
    if endpoint_id.startswith("ring:"):
        vendor_device_id = endpoint_id[len("ring:"):]
        row = (await session.execute(
            select(VendorDevice).where(VendorDevice.vendor_device_id == vendor_device_id)
        )).scalar_one_or_none()
        if row is None:
            return _err(directive, "NO_SUCH_ENDPOINT", "Ring device not found")
        account = await session.get(VendorAccount, row.vendor_account_id)
        if account is None or account.provider != "ring":
            return _err(directive, "NO_SUCH_ENDPOINT", "Ring account not found")
        rotated: list = []
        client = _ring_client(account, rotated)
        try:
            if namespace == "Alexa.PowerController" and name == "TurnOn":
                await client.floodlight_on(row.vendor_device_id)
                return _ack_power(directive, "ON")
            if namespace == "Alexa.PowerController" and name == "TurnOff":
                await client.floodlight_off(row.vendor_device_id)
                return _ack_power(directive, "OFF")
        finally:
            _persist(account, rotated)
            await session.commit()
        return _err(directive, "INVALID_DIRECTIVE",
                    f"Unsupported directive {namespace}/{name} for Ring camera")

    # ── Ring alarm hub ──────────────────────────────────────────────
    if endpoint_id.startswith("ring-alarm:"):
        _, account_id, location_id = endpoint_id.split(":", 2)
        account = await session.get(VendorAccount, account_id)
        if account is None or account.provider != "ring":
            return _err(directive, "NO_SUCH_ENDPOINT", "Ring alarm account not found")
        rotated: list = []
        client = _ring_client(account, rotated)
        try:
            if namespace == "Alexa.SecurityPanelController" and name == "Arm":
                arm_state = (payload.get("armState") or "").upper()
                mode = {"ARMED_AWAY": "all", "ARMED_STAY": "home"}.get(arm_state)
                if mode is None:
                    return _err(directive, "UNSUPPORTED_ARM_STATE",
                                f"armState {arm_state} not supported")
                await client.set_alarm_mode(location_id, mode)
                return _ack_arm(directive, arm_state)
            if namespace == "Alexa.SecurityPanelController" and name == "Disarm":
                await client.set_alarm_mode(location_id, "none")
                return _ack_arm(directive, "DISARMED")
        finally:
            _persist(account, rotated)
            await session.commit()
        return _err(directive, "INVALID_DIRECTIVE",
                    f"Unsupported directive {namespace}/{name} for Ring alarm")

    return _err(directive, "NO_SUCH_ENDPOINT",
                f"Unknown endpointId {endpoint_id}")


# ── Response builders ────────────────────────────────────────────────
def _ack_power(directive: dict, state: str) -> dict:
    h = directive.get("header", {})
    return {
        "context": {
            "properties": [
                {
                    "namespace": "Alexa.PowerController",
                    "name": "powerState",
                    "value": state,
                    "timeOfSample": _iso_now(),
                    "uncertaintyInMilliseconds": 1000,
                }
            ]
        },
        "event": {
            "header": _hdr("Response",
                           correlation_token=h.get("correlationToken")),
            "endpoint": directive.get("endpoint", {}),
            "payload": {},
        }
    }


def _ack_thermostat(directive: dict, celsius: float) -> dict:
    h = directive.get("header", {})
    return {
        "context": {
            "properties": [
                {
                    "namespace": "Alexa.ThermostatController",
                    "name": "targetSetpoint",
                    "value": {"value": round(celsius, 1), "scale": "CELSIUS"},
                    "timeOfSample": _iso_now(),
                    "uncertaintyInMilliseconds": 1000,
                },
                {
                    "namespace": "Alexa.ThermostatController",
                    "name": "thermostatMode",
                    "value": "HEAT",
                    "timeOfSample": _iso_now(),
                    "uncertaintyInMilliseconds": 1000,
                }
            ]
        },
        "event": {
            "header": _hdr("Response",
                           correlation_token=h.get("correlationToken")),
            "endpoint": directive.get("endpoint", {}),
            "payload": {},
        }
    }


def _ack_arm(directive: dict, arm_state: str) -> dict:
    h = directive.get("header", {})
    return {
        "context": {
            "properties": [
                {
                    "namespace": "Alexa.SecurityPanelController",
                    "name": "armState",
                    "value": arm_state,
                    "timeOfSample": _iso_now(),
                    "uncertaintyInMilliseconds": 1000,
                }
            ]
        },
        "event": {
            "header": _hdr("Arm.Response"
                           if arm_state != "DISARMED" else "Disarm.Response",
                           namespace="Alexa.SecurityPanelController",
                           correlation_token=h.get("correlationToken")),
            "endpoint": directive.get("endpoint", {}),
            "payload": {},
        }
    }


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
