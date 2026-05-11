"""
Auto pin allocator for the Master Platform.

Given a compute (e.g. Pico 2 W) and a list of components with required
interfaces, produce a pinmap conforming to ``shared_schemas/pinmap_schema.json``.
The allocator is intentionally deterministic so the same inputs always
produce the same pinmap (important for DNA stability and reproducible
firmware bundles).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .library_loader import LibraryCatalog, LibraryItem


# Compute-specific pin pools. Order matters: the allocator drains from the
# top of each list. The Pico family shares the RP2040 / RP2350 26-pin
# layout; the Pi family shares the 40-pin BCM header.

# RP2040 / RP2350 base pin pool — Pico, Pico W, Pico 2, Pico 2 W.
_PICO_POOL = {
    "GPIO": [
        "GP0", "GP1", "GP2", "GP3", "GP6", "GP7", "GP8", "GP9",
        "GP10", "GP11", "GP12", "GP13", "GP14", "GP15",
        "GP16", "GP17", "GP18", "GP19", "GP20", "GP21", "GP22",
    ],
    "PWM": [
        "GP0", "GP1", "GP2", "GP3", "GP6", "GP7", "GP8", "GP9",
        "GP10", "GP11", "GP12", "GP13", "GP14", "GP15",
    ],
    "ADC":   ["GP26", "GP27", "GP28"],
    "I2C0":  ["GP4_SDA", "GP5_SCL"],
    "I2C1":  ["GP2_SDA", "GP3_SCL"],
    "1-Wire": ["GP22"],
}

# Pico 2 / 2 W gain a 4th ADC channel on GP29.
_PICO_2_POOL = {
    **_PICO_POOL,
    "ADC": ["GP26", "GP27", "GP28", "GP29"],
}

# Standard Raspberry Pi 40-pin BCM header — shared across Pi 3 / 4 / 5 /
# Zero / Zero 2 W. The default I2C is bus 1 (GPIO2 SDA / GPIO3 SCL); bus
# 0 (GPIO0/1) is reserved for HAT EEPROMs and listed second.
_PI_40PIN_POOL = {
    "GPIO": [f"GPIO{i}" for i in (4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27)],
    "PWM":  ["GPIO12", "GPIO13", "GPIO18", "GPIO19"],
    "ADC":  [],
    "I2C0": ["GPIO2_SDA", "GPIO3_SCL"],
    "I2C1": ["GPIO0_SDA", "GPIO1_SCL"],
    "1-Wire": ["GPIO4"],
}

COMPUTE_POOLS: dict[str, dict[str, list[str]]] = {
    "compute.pico":       _PICO_POOL,
    "compute.pico_w":     _PICO_POOL,
    "compute.pico_2":     _PICO_2_POOL,
    "compute.pico2w":     _PICO_2_POOL,           # alias for the existing manifest stable_id
    "compute.rpi3":       _PI_40PIN_POOL,
    "compute.rpi4":       _PI_40PIN_POOL,
    "compute.rpi5":       _PI_40PIN_POOL,
    "compute.rpi_zero":   _PI_40PIN_POOL,
    "compute.rpi_zero_2w":_PI_40PIN_POOL,
    # Custom Jagath board — placeholder pool, update when schematic is
    # finalised. Same shape as a 40-pin Pi for now so allocator works.
    "compute.jagath":     _PI_40PIN_POOL,
}


class PinAllocationError(RuntimeError):
    pass


def _category_for_function(func: str) -> str:
    if func in ("GPIO_IN", "GPIO_OUT"):
        return "GPIO"
    if func == "PWM":
        return "PWM"
    if func in ("ADC", "DAC"):
        return "ADC"
    if func in ("I2C_SDA", "I2C_SCL"):
        return "I2C0"
    if func == "1-WIRE":
        return "1-Wire"
    return func


def _required_pins_for_component(item: LibraryItem) -> list[dict[str, Any]]:
    """Translate a component manifest's interfaces into pin slots."""
    out: list[dict[str, Any]] = []
    for iface in item.manifest.get("interfaces", []) or []:
        itype = iface.get("type")
        direction = iface.get("direction", "out")
        count = int(iface.get("pins_required", 0) or 0)
        if itype == "GPIO":
            func = "GPIO_OUT" if direction == "out" else "GPIO_IN"
            for _ in range(max(count, 1)):
                out.append({"function": func})
        elif itype == "PWM":
            for _ in range(max(count, 1)):
                out.append({"function": "PWM"})
        elif itype in ("ADC", "DAC"):
            for _ in range(max(count, 1)):
                out.append({"function": itype})
        elif itype == "I2C":
            out.append({"function": "I2C_SDA", "address": iface.get("address")})
            out.append({"function": "I2C_SCL", "address": iface.get("address")})
        elif itype == "1-Wire":
            out.append({"function": "1-WIRE"})
        # WiFi / BLE / USB / MQTT / HTTP do not consume physical pins on
        # the compute pool — handled elsewhere.
    return out


def auto_allocate(
    *,
    compute_stable_id: str,
    component_assignments: list[dict[str, str]],
    catalog: LibraryCatalog,
    device_dna: str,
    board_stable_id: str | None = None,
) -> dict[str, Any]:
    """Build a pinmap document for a compute + ordered component list."""
    pools_template = COMPUTE_POOLS.get(compute_stable_id)
    if pools_template is None:
        raise PinAllocationError(f"no pin pool for compute '{compute_stable_id}'")

    pools = {key: list(vals) for key, vals in pools_template.items()}

    # Track if I2C bus is already initialised so we don't reallocate SDA/SCL.
    i2c_assigned: dict[str, tuple[str, str]] = {}

    assignments_out: list[dict[str, Any]] = []
    conflicts: list[str] = []

    for entry in component_assignments:
        comp_id = entry["component_stable_id"]
        instance_id = entry.get("instance_id", comp_id.split(".")[-1])
        item = catalog.get(comp_id)
        if item is None:
            raise PinAllocationError(f"unknown component '{comp_id}'")

        required = _required_pins_for_component(item)
        pins_out: list[dict[str, Any]] = []
        interface_meta: dict[str, Any] | None = None

        skip_next_i2c = False
        for slot in required:
            func = slot["function"]
            cat = _category_for_function(func)

            if cat in ("I2C0", "I2C1"):
                bus_key = "I2C0" if "I2C0" in pools else "I2C1"
                if bus_key not in i2c_assigned:
                    if len(pools.get(bus_key, [])) < 2:
                        conflicts.append(f"{comp_id}: no free pins for {bus_key}")
                        break
                    sda, scl = pools[bus_key][0], pools[bus_key][1]
                    pools[bus_key] = pools[bus_key][2:]
                    # Also mark these pins as no longer available as plain GPIO.
                    pools["GPIO"] = [p for p in pools["GPIO"] if p not in (sda.split("_")[0], scl.split("_")[0])]
                    i2c_assigned[bus_key] = (sda, scl)
                sda, scl = i2c_assigned[bus_key]
                if not skip_next_i2c:
                    pins_out.append({"physical": sda.split("_")[0], "function": "I2C_SDA"})
                    pins_out.append({"physical": scl.split("_")[0], "function": "I2C_SCL"})
                    interface_meta = {
                        "type": "I2C",
                        "address": slot.get("address") or item.manifest.get("interfaces", [{}])[0].get("address"),
                        "speed_hz": 400000,
                    }
                    skip_next_i2c = True
                continue

            pool = pools.get(cat) or pools.get("GPIO")
            if not pool:
                conflicts.append(f"{comp_id}: pool '{cat}' exhausted")
                break
            phys = pool.pop(0)
            # Pulled pin must also be removed from any aliasing pool.
            for other_pool_key in pools:
                if other_pool_key in (cat,):
                    continue
                if phys in pools[other_pool_key]:
                    pools[other_pool_key].remove(phys)
            pins_out.append({"physical": phys, "function": func})

        if not pins_out:
            # Items that consume no physical pins (twin/UI items, WiFi-only) — skip.
            continue

        assignment: dict[str, Any] = {
            "component_stable_id": comp_id,
            "instance_id": instance_id,
            "pins": pins_out,
        }
        if interface_meta is not None:
            assignment["interface"] = interface_meta
        assignments_out.append(assignment)

    return {
        "device_dna": device_dna,
        "compute_id": compute_stable_id,
        "board_id": board_stable_id,
        "generated_by": "auto_allocator",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "assignments": assignments_out,
        "conflicts": conflicts,
    }
