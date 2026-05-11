"""Pin allocator unit tests."""
from __future__ import annotations

import pytest


def _catalog():
    from backend.library_loader import load_catalog
    return load_catalog(force=True)


def test_auto_allocate_pico_simple_chain(isolated_master):
    from backend.pin_allocator import auto_allocate

    cat = _catalog()
    pinmap = auto_allocate(
        compute_stable_id="compute.pico2w",
        component_assignments=[
            {"component_stable_id": "sensor.temp.ds18b20", "instance_id": "probe_a"},
            {"component_stable_id": "actuator.relay.ssr", "instance_id": "ssr_main"},
        ],
        catalog=cat,
        device_dna="DNA-TEST-1234-5678-9ABC",
    )
    assert pinmap["compute_id"] == "compute.pico2w"
    assert pinmap["generated_by"] == "auto_allocator"
    assert pinmap["conflicts"] == []
    # Each component got its physical pins.
    ids = [a["component_stable_id"] for a in pinmap["assignments"]]
    assert "sensor.temp.ds18b20" in ids
    assert "actuator.relay.ssr" in ids
    # No pin appears twice across assignments.
    seen = []
    for a in pinmap["assignments"]:
        for p in a["pins"]:
            seen.append(p["physical"])
    assert len(seen) == len(set(seen)), f"duplicate pin allocation: {seen}"


def test_auto_allocate_unknown_compute_raises(isolated_master):
    from backend.pin_allocator import auto_allocate, PinAllocationError

    with pytest.raises(PinAllocationError, match="no pin pool"):
        auto_allocate(
            compute_stable_id="compute.unknown",
            component_assignments=[],
            catalog=_catalog(),
            device_dna="DNA-X-X-X-X",
        )


def test_auto_allocate_unknown_component_raises(isolated_master):
    from backend.pin_allocator import auto_allocate, PinAllocationError

    with pytest.raises(PinAllocationError, match="unknown component"):
        auto_allocate(
            compute_stable_id="compute.pico2w",
            component_assignments=[{"component_stable_id": "sensor.does.not.exist"}],
            catalog=_catalog(),
            device_dna="DNA-X-X-X-X",
        )
