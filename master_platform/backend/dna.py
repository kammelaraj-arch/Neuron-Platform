from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

import jsonschema

from .library_loader import shared_schema


def new_dna_id() -> str:
    """Generate a fresh, schema-conformant device DNA id."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    chunks = [
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)
    ]
    return "DNA-" + "-".join(chunks)


def fingerprint(payload: dict[str, Any]) -> dict[str, str]:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    return {"alg": "sha256", "hash": digest}


def build_dna(
    *,
    device_dna: str,
    device_type: str,
    compute_stable_id: str,
    board_stable_id: str | None,
    hardware_revision: str,
    serial_number: str | None,
    mac_address: str | None,
    base_firmware_version: str,
    app_bundle_version: str,
    config_schema_version: str,
    parent_node_id: str,
    site_id: str,
    issued_by: str,
    components: list[dict[str, Any]],
    twin_controls: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "device_dna": device_dna,
        "device_type": device_type,
        "compute_stable_id": compute_stable_id,
        "hardware_revision": hardware_revision,
        "base_firmware_version": base_firmware_version,
        "app_bundle_version": app_bundle_version,
        "config_schema_version": config_schema_version,
        "parent_node_id": parent_node_id,
        "site_id": site_id,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "issued_by": issued_by,
        "components": components,
        "twin_controls": twin_controls,
    }
    if board_stable_id:
        payload["board_stable_id"] = board_stable_id
    if serial_number:
        payload["serial_number"] = serial_number
    if mac_address:
        payload["mac_address"] = mac_address

    # Fingerprint covers everything except the fingerprint itself.
    payload["fingerprint"] = fingerprint(payload)

    schema = shared_schema("dna_schema")
    jsonschema.Draft7Validator(schema).validate(payload)
    return payload
