# Neuron Platform — Smart Factory Platform

Standalone, production-grade smart factory platform. Deploys at
`https://neuron.shital.org.uk` from `/opt/neuron-platform/` on the
host.

## Architecture (Level 0 → Level 3)

```
Level 3  Master / Root   (single unified app: Admin + Build + Config)
Level 2  Regional Node   (optional sync/aggregation; never blocks Level 1)
Level 1  Edge Site       (real-time orchestration, allow-list firewall, mTLS)
Level 0  Devices         (Pico 2 W via MicroPython, sensors, actuators)
```

Real-time control on the edge **must never** depend on Level 2 or
Level 3 connectivity.

## Repo layout

```
neuron-platform/
├── .github/workflows/        # webhook-based CI/CD
├── deploy.sh                 # standalone deploy entrypoint
├── deployer/                 # webhook listener container
├── master_platform/          # Level-3 FastAPI app + UI + docker-compose
├── edge_runtime/             # Level-1 edge container
├── level0-pico2w/            # Level-0 MicroPython firmware
├── libraries/                # hardware + twin + UI + business + functional + API library manifests
├── shared_schemas/           # JSON Schemas referenced platform-wide
├── tools/                    # manifest validator + utilities
└── docs/                     # PROJECT_MEMORY, HOST_SETUP, api, ota policy, ...
```

## Quick start (local dev)

```bash
cd master_platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn backend.main:app --host 0.0.0.0 --port 8088 --reload
```

Open `http://localhost:8088/login`, paste the bootstrap admin key
printed in `master_platform/data/bootstrap_admin.txt`, then delete that
file.

## Run the manifest validator

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r tools/requirements.txt
python tools/validate_manifests.py --root .
```

## Production deployment

- **One-time host setup**: `docs/HOST_SETUP.md`.
- **Webhook-based auto-deploy**: `AUTO_DEPLOY.md`.
- **Manual deploy / rollback**: `DEPLOY.md`.

## Project memory

`docs/PROJECT_MEMORY.md` — source of truth across stages.
