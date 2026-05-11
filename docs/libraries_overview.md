# Libraries Overview

The Neuron Platform is **library-first**. Everything that can be built,
configured, deployed or rendered comes from a library item described by a
manifest. Five libraries cover the whole stack:

```
                    ┌────────────────────────────┐
                    │   ui_controls_library      │   widgets, panels, SVG, bindings
                    └─────────────┬──────────────┘
                                  │ binds to
                    ┌─────────────▼──────────────┐
                    │ digital_twin_controls_lib  │   HeaterControl, MotorControl,
                    │                            │   WeighScale, alarms, effects
                    └─────────────┬──────────────┘
                                  │ drives
       ┌───────────────────┬──────┴──────┬─────────────────────┐
       │                   │             │                     │
┌──────▼────────┐  ┌───────▼──────┐  ┌───▼──────────┐  ┌───────▼────────┐
│ components_   │  │ control_     │  │ micro_       │  │ shared_schemas │
│ library       │  │ board_       │  │ compute_     │  │ (manifest,     │
│ (sensors,     │  │ library      │  │ library      │  │  pinmap, dna,  │
│  actuators)   │  │ (motor,relay)│  │ (Pico/RPi/AI)│  │  brain, ota…)  │
└───────────────┘  └──────────────┘  └──────────────┘  └────────────────┘
```

## How items connect

A typical heater zone resolves like this:

1. **Compute** — `compute.pico2w` (micro-compute library) — provides GPIO/ADC/PWM/WiFi.
2. **Board** — `board.relay.generic.4ch` (control-board library) — translates pins to
   isolated AC switching.
3. **Components** — `sensor.temp.ds18b20` + `actuator.relay.ssr` + `actuator.heater.element`
   (components library) — physical sensing & actuation.
4. **Twin** — `twin.heater_control` (digital twin controls library) — owns the
   state machine, setpoint, safety_max, fault behaviour.
5. **UI** — `ui.widget.temp_gauge` + `ui.widget.alarm_banner` (ui controls library)
   — render the gauge and alarms, bound to twin desired/reported/current channels.

The cross-references are declared explicitly in each manifest's
`compatibility.*` and `twin_bindings` / `ui_bindings`. The validator
(`tools/validate_manifests.py`) resolves all of them.

## Manifest contract

Every library item has the same envelope (see
`shared_schemas/manifest_schema.json`):

| Field            | Purpose                                                     |
| ---------------- | ----------------------------------------------------------- |
| `stable_id`      | Globally unique, immutable id                               |
| `name`/`vendor`  | Human-readable                                              |
| `version`        | SemVer 2.0.0                                                |
| `library`        | One of the five library names                               |
| `category`       | Subdirectory inside the library                             |
| `image`          | SVG/PNG asset path for the UI catalog                       |
| `capabilities`   | Free-form capability tags (`temperature_read`, `pwm_out`…)  |
| `dependencies`   | Other library items needed                                  |
| `interfaces`     | Electrical / network surfaces (GPIO, I2C, MQTT, …)          |
| `power`          | Voltage / current / source                                  |
| `safety_class`   | `nominal | advisory | critical | life_safety`               |
| `drivers`        | Runtime + module + version range                            |
| `self_tests`     | Commissioning steps used by Edge / Brain                    |
| `compatibility`  | Boards / compute / twin / ui that this item works with      |
| `twin_bindings`  | desired / reported / current channels                       |
| `ui_bindings`    | Widget-side binding paths                                   |

## Adding an item

1. Drop the manifest in the relevant `manifests/` directory.
2. Add an SVG to the matching subfolder (e.g. `actuators/`, `symbols_svg/`).
3. Run `python tools/validate_manifests.py --strict --root .`.
4. Reference the new `stable_id` from any binding template, twin, or UI widget.

## Stage 1 catalog (counts)

| Library                          | Items |
| -------------------------------- | ----- |
| components_library               | 6     |
| control_board_library            | 3     |
| micro_compute_library            | 3     |
| digital_twin_controls_library    | 5     |
| ui_controls_library              | 5     |
| **Total**                        | **22**|
