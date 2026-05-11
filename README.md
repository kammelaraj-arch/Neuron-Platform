# Neuron Platform — Smart Factory Platform (block of ShitalEco)

Production-grade smart factory platform delivered as a new block inside the
ShitalEco monorepo, deployed via the existing ShitalEco pipeline at
`https://neuron.shital.org.uk`.

This is **Stage 1** of a staged build:

| Stage | Scope                                                                  | Status |
| ----- | ---------------------------------------------------------------------- | ------ |
| 1     | Five libraries + shared schemas + example manifests + Python validator | ✅ done |
| 2     | Master Platform core (Admin + Build + Config, FastAPI + minimal UI)    | next   |
| 3     | Edge Runtime + strict allow-list security + Pico 2 W firmware slice    | —      |
| 4     | OTA base-version gating, API management/keys, audit                    | —      |
| 5     | Digital Twin runtime, UI controls runtime, Process Builder, AI agent   | —      |

## Architecture (Level 0 → Level 3)

```
Level 3  Master / Root   (single unified app: Admin + Build + Config)
Level 2  Regional Node   (optional sync/aggregation; never blocks Level 1)
Level 1  Edge Site       (real-time orchestration, allow-list firewall, mTLS)
Level 0  Devices         (Pico 2 W via MicroPython, sensors, actuators)
```

Real-time control on the edge **must never** depend on Level 2 or Level 3
connectivity.

## Stage 1 — what's in this commit

```
neuron-platform/
├── libraries/
│   ├── components_library/         # sensors, actuators, cameras, comms, power
│   ├── control_board_library/      # motor drivers, relay boards, servo HATs, ADC/DAC, IO expand
│   ├── micro_compute_library/      # Pico 2 W, Raspberry Pi 5, Coral edge AI
│   ├── digital_twin_controls_library/  # heater/motor/scale twins, alarm + effects models
│   └── ui_controls_library/        # gauges, status tiles, alarm banner, recipe runner, SVG symbols
├── shared_schemas/                 # manifest, pinmap, dna, brain, ota, api_key, access_policy
├── tools/
│   ├── validate_manifests.py
│   └── requirements.txt
└── docs/
    ├── libraries_overview.md
    └── PROJECT_MEMORY.md
```

## Run the validator

```bash
cd neuron-platform
python -m venv .venv && source .venv/bin/activate
pip install -r tools/requirements.txt
python tools/validate_manifests.py --root .
```

Output on a clean tree:

```
Manifests scanned: 21
OK: 21 manifests valid (0 warning(s)).
```

## Deployment

This block ships as part of the ShitalEco pipeline. The forthcoming
Stage 2 backend will run as a FastAPI service alongside the existing
`backend/` service and be exposed at `neuron.shital.org.uk` via the
existing nginx + Render setup.

## Project memory

See `docs/PROJECT_MEMORY.md` — that is the source of truth across stages.
