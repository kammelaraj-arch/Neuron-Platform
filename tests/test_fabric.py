"""Fabric namespace — the unified read-only API surface."""
from __future__ import annotations

import pytest


@pytest.fixture
async def initialised_master(isolated_master):
    from backend.db import init_db
    await init_db()
    yield isolated_master


async def _seed_topology() -> str:
    """Create one root → node → edge → device chain. Returns DNA."""
    from backend.db import SessionLocal
    from backend.models import Device, EdgeSystem, NodeSystem, RootSystem
    async with SessionLocal() as s:
        r = RootSystem(name="r"); s.add(r); await s.flush()
        n = NodeSystem(root_id=r.id, name="n"); s.add(n); await s.flush()
        e = EdgeSystem(node_id=n.id, name="e", site_id="s",
                       address="http://127.0.0.1:1"); s.add(e); await s.flush()
        d = Device(
            device_dna="DNA-FAB-AAAA-BBBB-CCCC", edge_id=e.id, device_type="pico2w",
            compute_stable_id="compute.pico2w", hardware_revision="rev_a",
            base_firmware_version="1.0.0", app_bundle_version="1.0.0",
            config_schema_version="1.0.0",
            dna_json={"device_dna": "DNA-FAB-AAAA-BBBB-CCCC",
                      "twin_controls": ["twin.heater_control"]},
        )
        s.add(d); await s.commit()
        return d.device_dna


def test_catalog_groups_all_libraries(initialised_master):
    from backend.routers.fabric import catalog
    import asyncio
    res = asyncio.run(catalog())  # type: ignore[arg-type]
    assert "Hardware" in res["groups"]
    assert "Digital Twin" in res["groups"]
    assert "Business" in res["groups"]
    assert "Functional" in res["groups"]
    assert "API" in res["groups"]
    assert res["all_count"] >= 25


async def test_topology_returns_full_tree(initialised_master):
    from backend.db import get_session
    from backend.routers.fabric import topology
    dna = await _seed_topology()
    async for session in get_session():
        res = await topology(session=session)  # type: ignore[arg-type]
        break
    assert res["totals"]["devices"] == 1
    root = res["roots"][0]
    assert root["nodes"][0]["edges"][0]["devices"][0]["device_dna"] == dna


def test_twin_schema_exposes_fields_and_bindings(initialised_master):
    from backend.routers.fabric import twin_schema
    import asyncio
    res = asyncio.run(twin_schema("twin.heater_control"))  # type: ignore[arg-type]
    assert res["twin_control"]["stable_id"] == "twin.heater_control"
    assert "PREHEAT" in (res["state_machine"]["states"] or [])
    assert "setpoint_c" in res["fields"]["desired"]
    assert any(w["widget_id"] == "ui.widget.temp_gauge" for w in res["ui_widgets"])
    # Every Pico-side hardware binding for this twin should appear too.
    hw_ids = [c["stable_id"] for c in res["hardware_components"]]
    assert "sensor.temp.ds18b20" in hw_ids


def test_twin_schema_404s_unknown_id(initialised_master):
    from fastapi import HTTPException
    from backend.routers.fabric import twin_schema
    import asyncio
    import pytest as _pytest
    with _pytest.raises(HTTPException) as exc:
        asyncio.run(twin_schema("twin.does.not.exist"))  # type: ignore[arg-type]
    assert exc.value.status_code == 404


def test_twin_schema_400s_non_twin_item(initialised_master):
    from fastapi import HTTPException
    from backend.routers.fabric import twin_schema
    import asyncio
    import pytest as _pytest
    with _pytest.raises(HTTPException) as exc:
        asyncio.run(twin_schema("sensor.temp.ds18b20"))  # type: ignore[arg-type]
    assert exc.value.status_code == 400


def test_field_lookup_finds_process_temp_c(initialised_master):
    from backend.routers.fabric import fields
    import asyncio
    res = asyncio.run(fields("process_temp_c"))  # type: ignore[arg-type]
    twins = {h["twin_control"] for h in res["occurrences"]}
    assert "twin.heater_control" in twins
    # Field appears in the reported channel.
    assert any(h["channel"] == "reported" for h in res["occurrences"])
