"""
Brain config generator.

Produces a brain configuration for a device from its DNA + assigned twin
controls. Deterministic and schema-validated against
``shared_schemas/brain_schema.json``.
"""
from __future__ import annotations

from typing import Any

import jsonschema

from .library_loader import LibraryCatalog, shared_schema


_TWIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "twin.heater_control": {
        "loop": {
            "id": "heater_loop",
            "type": "pid",
            "period_ms": 250,
            "inputs": ["sensor.temp.process"],
            "outputs": ["actuator.relay.ssr"],
            "params": {"kp": 8.0, "ki": 0.4, "kd": 0.0, "out_min": 0, "out_max": 100},
            "twin_control_ref": "twin.heater_control",
        },
        "interlocks": [
            {
                "id": "overtemp",
                "condition": "process_temp_c > safety_max_c",
                "action": "go_safe",
                "alarm_severity": "critical",
            },
            {
                "id": "sensor_lost",
                "condition": "process_temp_c is null",
                "action": "go_safe",
                "alarm_severity": "critical",
            },
        ],
        "safe_state": {"ssr_duty": 0},
        "telemetry_topic": "twin/heater/{instance}/reported",
        "telemetry_fields": ["process_temp_c", "state", "ssr_duty"],
    },
    "twin.motor_control": {
        "loop": {
            "id": "motor_loop",
            "type": "state_machine",
            "period_ms": 100,
            "inputs": ["sensor.current.motor"],
            "outputs": ["board.motor.l298n"],
            "params": {"ramp_pct_per_s": 50, "fault_current_a": 5.0},
            "twin_control_ref": "twin.motor_control",
        },
        "interlocks": [
            {
                "id": "overcurrent",
                "condition": "current_a > fault_current_a",
                "action": "go_safe",
                "alarm_severity": "critical",
            }
        ],
        "safe_state": {"speed_pct": 0, "direction": "fwd"},
        "telemetry_topic": "twin/motor/{instance}/reported",
        "telemetry_fields": ["state", "speed_pct", "fault_code"],
    },
    "twin.weighscale_control": {
        "loop": {
            "id": "scale_loop",
            "type": "passthrough",
            "period_ms": 200,
            "inputs": ["sensor.loadcell.hx711"],
            "outputs": [],
            "params": {"stability_window_g": 2, "stability_time_ms": 500},
            "twin_control_ref": "twin.weighscale_control",
        },
        "interlocks": [],
        "safe_state": {},
        "telemetry_topic": "twin/scale/{instance}/reported",
        "telemetry_fields": ["raw_weight_g", "stable_weight_g", "is_stable", "in_range"],
    },
}


def build_brain(
    *,
    device_dna: str,
    config_schema_version: str,
    twin_controls: list[str],
    catalog: LibraryCatalog,
) -> dict[str, Any]:
    loops: list[dict[str, Any]] = []
    interlocks: list[dict[str, Any]] = []
    safe_state: dict[str, Any] = {}
    telemetry_topics: list[dict[str, Any]] = []

    for tc in twin_controls:
        defaults = _TWIN_DEFAULTS.get(tc)
        if defaults is None:
            # Unknown twin — keep safety conservative: include nothing.
            continue
        loops.append(defaults["loop"])
        interlocks.extend(defaults["interlocks"])
        safe_state.update(defaults["safe_state"])
        telemetry_topics.append(
            {
                "topic": defaults["telemetry_topic"].replace("{instance}", device_dna),
                "fields": list(defaults["telemetry_fields"]),
            }
        )

    if not telemetry_topics:
        telemetry_topics.append(
            {
                "topic": f"device/{device_dna}/heartbeat",
                "fields": ["uptime_s", "rssi_dbm"],
            }
        )

    payload: dict[str, Any] = {
        "device_dna": device_dna,
        "brain_version": "1.0.0",
        "config_schema_version": config_schema_version,
        "loops": loops,
        "safety": {
            "safe_state": safe_state,
            "interlocks": interlocks,
            "watchdog_ms": 1000,
        },
        "telemetry": {
            "publish_period_ms": 1000,
            "qos": 1,
            "topics": telemetry_topics,
        },
        "offline_policy": {
            "mode": "continue_safe",
            "max_offline_seconds": 3600,
            "buffer_size": 4096,
        },
    }

    schema = shared_schema("brain_schema")
    jsonschema.Draft7Validator(schema).validate(payload)
    return payload
