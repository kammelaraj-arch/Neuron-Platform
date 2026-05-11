"""Access policy loader + enforcement helpers for the Edge Gateway."""
from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from .config import settings


SHARED_SCHEMA_CANDIDATES = [
    Path(__file__).resolve().parent.parent.parent / "shared_schemas" / "access_policy_schema.json",
    Path("/app/shared_schemas/access_policy_schema.json"),
]


def _shared_schema() -> dict[str, Any]:
    for p in SHARED_SCHEMA_CANDIDATES:
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                return json.load(fh)
    raise RuntimeError(
        f"access_policy_schema.json not found in any of {SHARED_SCHEMA_CANDIDATES}"
    )


@dataclass
class CompiledPolicy:
    raw: dict[str, Any]
    inbound_default_deny: bool
    inbound_allow: list[dict[str, Any]]
    outbound_default_deny: bool
    outbound_allow: list[dict[str, Any]]
    emergency: dict[str, Any]
    edge_id: str

    def evaluate_inbound(
        self,
        *,
        client_ip: str,
        port: int,
        principal_hint: str | None = None,
        client_cert_fingerprint: str | None = None,
        is_emergency: bool = False,
    ) -> tuple[bool, str]:
        try:
            ip = ipaddress.ip_address(client_ip)
        except ValueError:
            return False, f"invalid client_ip: {client_ip}"

        for rule in self.inbound_allow:
            principal = rule.get("principal")
            if is_emergency and principal != "emergency":
                continue
            if (not is_emergency) and principal == "emergency":
                continue
            if principal_hint and principal != principal_hint:
                continue
            try:
                cidr = ipaddress.ip_network(rule["cidr"], strict=False)
            except ValueError:
                continue
            if ip not in cidr:
                continue
            if rule.get("ports") and port not in rule["ports"]:
                continue
            if rule.get("mtls_required") and not client_cert_fingerprint:
                continue
            fingerprints = rule.get("cert_fingerprints_sha256") or []
            if rule.get("mtls_required") and fingerprints:
                if not client_cert_fingerprint or client_cert_fingerprint.lower() not in (
                    fp.lower() for fp in fingerprints
                ):
                    continue
            return True, f"matched principal={principal} purpose={rule.get('purpose','')}"
        return False, "no inbound allow-rule matched (deny by default)"


def load_policy(path: Path | None = None) -> CompiledPolicy:
    p = Path(path) if path else settings.policy_path
    if not p.exists():
        raise RuntimeError(f"edge access policy not found: {p}")
    with p.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    schema = _shared_schema()
    jsonschema.Draft7Validator(schema).validate(raw)
    return CompiledPolicy(
        raw=raw,
        inbound_default_deny=raw["inbound"]["default"] == "deny",
        inbound_allow=raw["inbound"]["allow"],
        outbound_default_deny=raw["outbound"]["default"] == "deny",
        outbound_allow=raw["outbound"]["allow"],
        emergency=raw["emergency"],
        edge_id=raw["edge_id"],
    )
