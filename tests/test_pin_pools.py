"""Pin allocator must support every shipped compute manifest."""
from __future__ import annotations

import pytest


@pytest.fixture
def catalog(isolated_master):
    from backend.library_loader import load_catalog
    return load_catalog(force=True)


def test_every_compute_manifest_has_a_pin_pool(catalog):
    """Every micro_compute_library item that exposes physical pins
    must have an entry in COMPUTE_POOLS, otherwise auto pin-mapping
    will throw on devices that pick that compute."""
    from backend.pin_allocator import COMPUTE_POOLS

    pin_capable = []
    for it in catalog.list_library("micro_compute_library"):
        ifaces = it.manifest.get("interfaces") or []
        if any(i.get("type") in ("GPIO", "I2C", "SPI", "ADC", "1-Wire") for i in ifaces):
            pin_capable.append(it.stable_id)

    missing = [sid for sid in pin_capable if sid not in COMPUTE_POOLS]
    assert not missing, (
        f"Compute manifests with physical pins but no entry in "
        f"COMPUTE_POOLS: {missing}"
    )


def test_pin_pools_have_required_keys(catalog):
    """Every pool dict needs the canonical pin-category keys so the
    allocator's _category_for_function lookups don't KeyError."""
    from backend.pin_allocator import COMPUTE_POOLS
    required = {"GPIO", "PWM", "ADC", "I2C0", "I2C1", "1-Wire"}
    for sid, pool in COMPUTE_POOLS.items():
        missing = required - set(pool.keys())
        assert not missing, f"{sid} pool is missing keys: {missing}"


def test_alloc_for_pico_w_works_end_to_end(catalog):
    """Pico W is one of the new computes — confirm an end-to-end
    auto-allocate run with a sensor + relay against it produces a
    conflict-free pinmap."""
    from backend.pin_allocator import auto_allocate
    pinmap = auto_allocate(
        compute_stable_id="compute.pico_w",
        component_assignments=[
            {"component_stable_id": "sensor.temp.ds18b20", "instance_id": "probe_a"},
            {"component_stable_id": "actuator.relay.ssr", "instance_id": "ssr_main"},
        ],
        catalog=catalog,
        device_dna="DNA-PW-1-2-3-4",
    )
    assert pinmap["compute_id"] == "compute.pico_w"
    assert pinmap["conflicts"] == []
    assert len(pinmap["assignments"]) == 2


def test_alloc_for_rpi3_works_end_to_end(catalog):
    """Same end-to-end check for a Pi 3 (40-pin BCM header)."""
    from backend.pin_allocator import auto_allocate
    pinmap = auto_allocate(
        compute_stable_id="compute.rpi3",
        component_assignments=[
            {"component_stable_id": "actuator.relay.ssr", "instance_id": "ssr_main"},
        ],
        catalog=catalog,
        device_dna="DNA-RPI3-1-2-3-4",
    )
    assert pinmap["compute_id"] == "compute.rpi3"
    assert pinmap["conflicts"] == []
    pin_names = [p["physical"] for a in pinmap["assignments"] for p in a["pins"]]
    assert all(p.startswith("GPIO") for p in pin_names), pin_names
