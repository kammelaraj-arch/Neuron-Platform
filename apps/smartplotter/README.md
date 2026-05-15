# SmartPlotter (APP-0002) — Production-grade XYZ plotter for Pi 4 / Pi 5

Hardened replacement for the `sjweb` reference app. Designed to run on
a smart-factory line — E-stop wired, soft + hard limits, homing
routines, mTLS-authenticated UI, MQTT telemetry to the master, and an
emergency-channel subscriber that fires safe-stop the moment the
parent's brain says to.

## Architecture

```
smartplotter/
├── README.md
├── pyproject.toml             modern packaging
├── Dockerfile                 container-first deploy
├── compose.yml                with healthcheck + resource limits
├── systemd/
│   └── smartplotter.service   bare-metal install path
├── smartplotter/
│   ├── __init__.py
│   ├── __main__.py            python -m smartplotter
│   ├── app.py                 Flask + SocketIO web UI (auth-gated)
│   ├── config.py              env-backed Settings (NEURON_* + SMARTPLOTTER_*)
│   ├── motion.py              trapezoidal-accel motion planner
│   ├── safety.py              E-stop + limit-switch + parent-link watchdog
│   ├── homing.py              X / Y / Z homing routines w/ retract
│   ├── contour.py             image → contour pipeline (OpenCV)
│   ├── gcode.py               contour → G-code export + import
│   ├── mqtt_bridge.py         telemetry publish + emergency subscribe
│   ├── db.py                  sqlite profile store + optional master sync
│   └── auth.py                API-key gate matching the Neuron platform
├── templates/                 minimal HTML (production assumes the
│                              master's UI hits us via API anyway)
└── static/
```

## Production-grade features (vs sjweb)

| Capability                              | sjweb       | SmartPlotter |
|-----------------------------------------|-------------|--------------|
| Trapezoidal acceleration                | ❌           | ✅            |
| Soft limits (configurable bounding box) | ❌           | ✅            |
| Hard limits (BCM-pin endstops)          | ❌           | ✅            |
| Homing routines                         | ❌           | ✅            |
| E-stop GPIO listener                    | ❌           | ✅ (interrupt-driven) |
| Brain emergency-channel listener        | ❌           | ✅ (MQTT safe_stop / safe_shutdown / status) |
| Parent-link watchdog → auto safe-stop   | ❌           | ✅ (configurable grace) |
| API-key auth on web UI                  | ❌           | ✅ (Bearer header / cookie) |
| MQTT telemetry export                   | ❌           | ✅ (cpu / temp / cycle_count / fault_state / current_profile / progress_pct) |
| G-code export/import                    | ❌           | ✅ (interop with LinuxCNC / GRBL / Klipper) |
| Audit log per profile run               | ❌           | ✅ |
| `/healthz` + `/api/version`             | ✅ (healthz) | ✅ (both)    |
| Containerised                            | ❌           | ✅            |
| Resource limits                         | ❌           | ✅ (compose memory + cpu) |

## Deploy via the Neuron platform

Catalogued as **APP-0002 — SmartPlotter (production-grade XYZ plotter)** in
`/ui/apps`. Click **🚀 Deploy** on the EdgeGroup that owns the Pi + CNC
Shield V3 + 4× DRV8825 setup. The master:

1. SSHes to the device.
2. Pulls the SmartPlotter image (`neuron/smartplotter:1.0.0`).
3. Drops `/etc/systemd/system/neuron-app-app-0002.service` that wraps
   `docker compose up -d` from `/opt/smartplotter/compose.yml`.
4. Verifies the container is `healthy` against `/healthz`.

## Configuration (env vars on the device)

```
SMARTPLOTTER_BROKER_URL    mqtts://<master>:8883   (mTLS to master)
SMARTPLOTTER_DEVICE_DNA    DNA-XXXX-XXXX-…         (matches the bundle)
SMARTPLOTTER_CERTS_DIR     /boot/neuron/certs
SMARTPLOTTER_API_KEY       <issued by /ui/secrets, integration tier>
SMARTPLOTTER_MAX_X_MM      300
SMARTPLOTTER_MAX_Y_MM      300
SMARTPLOTTER_MAX_Z_MM      50
SMARTPLOTTER_MAX_FEED_MM_S 50
SMARTPLOTTER_HOMING_FEED   10
SMARTPLOTTER_ESTOP_BCM     26       (BCM pin tied to E-stop NC contact)
SMARTPLOTTER_LIMIT_X_BCM   16
SMARTPLOTTER_LIMIT_Y_BCM   20
SMARTPLOTTER_LIMIT_Z_BCM   21
SMARTPLOTTER_PARENT_GRACE_S 5
```

## Failure modes + behaviours

| Trigger                                   | Effect |
|-------------------------------------------|--------|
| E-stop pin opens                          | Driver enable HIGH (disabled) within 100 µs; current profile aborted; MQTT publishes `fault_state=estop_pressed`; UI shows red banner. |
| Limit switch hit                          | Axis pulse loop refuses to move into the trigger direction; soft-bounce back 2 mm; `fault_state=limit_hit_<axis>`. |
| Master heartbeat lost > PARENT_GRACE_S    | Run paused (hold position, motors energised); after 30 s autonomous, run aborted; safe state held. |
| MQTT `emergency/<dna>` `safe_stop`        | Same as E-stop (decelerate to zero, disable drivers, hold position). |
| MQTT `emergency/<dna>` `safe_shutdown`    | Triggers OS shutdown via systemctl after disabling drivers. |
| `/api/v1/profile/<id>/run` w/o API key    | 401. |
| Image upload over 10 MB                   | 413. |

## Source

Catalogue entry: **APP-0002** in `/ui/apps` (auto-seeded on first boot).
Repo path:        `apps/smartplotter/` in the Neuron-Platform monorepo.
