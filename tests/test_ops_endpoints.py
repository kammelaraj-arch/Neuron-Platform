"""OTA dry-run promote endpoint tests + audit CSV export tests."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import pytest


@pytest.fixture
async def initialised_master(isolated_master):
    from backend.db import init_db
    await init_db()
    yield isolated_master


async def test_audit_csv_export_streams_rows(initialised_master):
    """Insert a couple of audit events directly, then call the CSV
    export and parse the result."""
    from backend.db import SessionLocal
    from backend.models import AuditEvent

    async with SessionLocal() as s:
        s.add(AuditEvent(actor="alice", actor_kind="ui_session",
                         action="create_root", target_kind="root_system",
                         target_id="root-1", outcome="ok",
                         detail_json={"name": "Acme"}))
        s.add(AuditEvent(actor="bob", actor_kind="api_key",
                         action="generate_dna", target_kind="device",
                         target_id="DNA-T-1-2-3-4", outcome="ok"))
        await s.commit()

    # Test the CSV gen directly without spinning a full HTTP server.
    from backend.routers.audit import export_audit_csv
    from backend.db import get_session

    async for session in get_session():
        # Build a fake admin APIKey for the dependency
        from backend.models import APIKey
        admin = APIKey(id="t-admin", owner="t", tier="admin", scopes_json=["admin"],
                       rate_per_minute=600, rate_burst=200,
                       hash_alg="argon2id", key_hash="x", status="active")
        s = session
        break

    resp = await export_audit_csv(  # type: ignore[arg-type]
        actor=None, action=None, since=None, until=None, limit=100,
        session=s, _=admin,
    )
    chunks: list[bytes] = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    body = b"".join(chunks).decode()
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    headers = rows[0]
    assert "actor" in headers and "action" in headers
    actors = {r[2] for r in rows[1:]}
    assert "alice" in actors and "bob" in actors


async def test_dry_run_promote_blocks_on_hw_mismatch(initialised_master):
    """A release whose manifest doesn't include the device's hw revision
    must fail the dry-run gate."""
    from backend.db import SessionLocal
    from backend.models import (Device, EdgeSystem, FirmwareRelease,
                                  NodeSystem, RootSystem, APIKey)
    from backend.routers.devices import dry_run_promote, PromoteBody

    async with SessionLocal() as s:
        r = RootSystem(name="r"); s.add(r); await s.flush()
        n = NodeSystem(root_id=r.id, name="n"); s.add(n); await s.flush()
        e = EdgeSystem(node_id=n.id, name="e", site_id="s"); s.add(e); await s.flush()
        dna = "DNA-DRY-1-2-3-4"
        d = Device(device_dna=dna, edge_id=e.id, device_type="pico2w",
                   compute_stable_id="compute.pico2w", hardware_revision="rev_a",
                   base_firmware_version="1.0.0", app_bundle_version="1.0.0",
                   config_schema_version="1.0.0")
        s.add(d); await s.flush()
        rel = FirmwareRelease(
            device_dna=dna, app_bundle_version="2.0.0",
            base_firmware_version="1.0.0", config_schema_version="1.0.0",
            hardware_revision="rev_z",  # <-- different from device's rev_a
            bundle_path="/tmp/x.zip", bundle_sha256="0"*64,
            manifest_json={
                "supported_hardware_revisions": ["rev_z"],
                "signature": {"alg": "ed25519", "kid": "k", "value": "v"},
                "rollback_allowed": False,
            },
            sig_alg="ed25519", sig_kid="k", sig_value="v",
        )
        s.add(rel); await s.flush()
        rel_id = rel.id
        await s.commit()

    from backend.db import get_session
    async for session in get_session():
        admin = APIKey(id="t-admin", owner="t", tier="readonly",
                       scopes_json=["devices:read"], rate_per_minute=60,
                       rate_burst=10, hash_alg="argon2id", key_hash="x",
                       status="active")
        result = await dry_run_promote(  # type: ignore[arg-type]
            device_dna=dna,
            body=PromoteBody(channel="stable", release_id=rel_id),
            session=session, _=admin,
        )
        break

    assert result["ok"] is False
    hw_gate = next(g for g in result["gates"] if g["id"] == "hardware_revision")
    assert hw_gate["ok"] is False


async def test_dry_run_promote_passes_when_clean(initialised_master):
    from backend.db import SessionLocal
    from backend.models import (Device, EdgeSystem, FirmwareRelease,
                                  NodeSystem, RootSystem, APIKey)
    from backend.routers.devices import dry_run_promote, PromoteBody

    async with SessionLocal() as s:
        r = RootSystem(name="r"); s.add(r); await s.flush()
        n = NodeSystem(root_id=r.id, name="n"); s.add(n); await s.flush()
        e = EdgeSystem(node_id=n.id, name="e", site_id="s"); s.add(e); await s.flush()
        dna = "DNA-CLN-1-2-3-4"
        d = Device(device_dna=dna, edge_id=e.id, device_type="pico2w",
                   compute_stable_id="compute.pico2w", hardware_revision="rev_a",
                   base_firmware_version="1.0.0", app_bundle_version="1.0.0",
                   config_schema_version="1.0.0")
        s.add(d); await s.flush()
        rel = FirmwareRelease(
            device_dna=dna, app_bundle_version="1.1.0",
            base_firmware_version="1.0.0", config_schema_version="1.0.0",
            hardware_revision="rev_a",
            bundle_path="/tmp/x.zip", bundle_sha256="0"*64,
            manifest_json={
                "supported_hardware_revisions": ["rev_a"],
                "signature": {"alg": "ed25519", "kid": "k", "value": "v"},
                "rollback_allowed": False,
            },
            sig_alg="ed25519", sig_kid="k", sig_value="v",
        )
        s.add(rel); await s.flush()
        rel_id = rel.id
        await s.commit()

    from backend.db import get_session
    async for session in get_session():
        admin = APIKey(id="t-admin", owner="t", tier="readonly",
                       scopes_json=["devices:read"], rate_per_minute=60,
                       rate_burst=10, hash_alg="argon2id", key_hash="x",
                       status="active")
        result = await dry_run_promote(  # type: ignore[arg-type]
            device_dna=dna,
            body=PromoteBody(channel="stable", release_id=rel_id),
            session=session, _=admin,
        )
        break

    assert result["ok"] is True
    assert result["would_promote"] is True
    assert all(g["ok"] for g in result["gates"])
