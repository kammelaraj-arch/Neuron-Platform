"""DNA + Brain generator tests."""
from __future__ import annotations

import pytest


def _catalog():
    from backend.library_loader import load_catalog
    return load_catalog(force=True)


def test_dna_generation_is_schema_valid_and_fingerprinted(isolated_master):
    from backend.dna import build_dna, new_dna_id

    dna_id = new_dna_id()
    assert dna_id.startswith("DNA-") and len(dna_id) == 23

    dna = build_dna(
        device_dna=dna_id,
        device_type="pico2w",
        compute_stable_id="compute.pico2w",
        board_stable_id=None,
        hardware_revision="rev_a",
        serial_number=None,
        mac_address=None,
        base_firmware_version="1.0.0",
        app_bundle_version="1.0.0",
        config_schema_version="1.0.0",
        parent_node_id="node-1",
        site_id="site-1",
        issued_by="test",
        components=[
            {"stable_id": "sensor.temp.ds18b20", "instance_id": "probe_a", "version": "1.2.0"},
        ],
        twin_controls=["twin.heater_control"],
    )
    assert dna["fingerprint"]["alg"] == "sha256"
    assert len(dna["fingerprint"]["hash"]) == 64
    assert dna["device_dna"] == dna_id


def test_dna_fingerprint_is_deterministic(isolated_master):
    """Same logical inputs must yield the same fingerprint regardless of
    Python dict ordering."""
    from backend.dna import build_dna, fingerprint

    payload = {
        "device_dna": "DNA-AAAA-BBBB-CCCC-DDDD",
        "device_type": "pico2w",
        "compute_stable_id": "compute.pico2w",
        "hardware_revision": "rev_a",
        "base_firmware_version": "1.0.0",
        "app_bundle_version": "1.0.0",
        "config_schema_version": "1.0.0",
        "parent_node_id": "n",
        "site_id": "s",
        "issued_at": "2026-01-01T00:00:00Z",
        "issued_by": "t",
        "components": [{"stable_id": "x", "instance_id": "i", "version": "1.0.0"}],
        "twin_controls": [],
    }
    fp1 = fingerprint(payload)
    fp2 = fingerprint(dict(reversed(list(payload.items()))))
    assert fp1["hash"] == fp2["hash"], "fingerprint depends on key order"


def test_brain_for_heater_has_safe_state_and_interlocks(isolated_master):
    from backend.brain import build_brain

    brain = build_brain(
        device_dna="DNA-TEST",
        config_schema_version="1.0.0",
        twin_controls=["twin.heater_control"],
        catalog=_catalog(),
    )
    assert brain["safety"]["watchdog_ms"] == 1000
    assert brain["safety"]["safe_state"] == {"ssr_duty": 0}
    interlock_ids = [i["id"] for i in brain["safety"]["interlocks"]]
    assert "overtemp" in interlock_ids
    assert "sensor_lost" in interlock_ids
    assert brain["offline_policy"]["mode"] == "continue_safe"
    assert any(loop["type"] == "pid" for loop in brain["loops"])


def test_brain_with_no_known_twins_still_validates(isolated_master):
    from backend.brain import build_brain

    brain = build_brain(
        device_dna="DNA-NOTHING",
        config_schema_version="1.0.0",
        twin_controls=["twin.unrecognised"],
        catalog=_catalog(),
    )
    assert brain["loops"] == []
    # Schema requires at least one telemetry topic — generator falls back
    # to a heartbeat-only topic.
    assert len(brain["telemetry"]["topics"]) >= 1
