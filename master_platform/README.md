# Neuron Platform — Master Platform (Stage 2)

Single unified app: **Admin + Build + Config + Library Registry + API Management**.

## Database isolation

The Neuron Master Platform's database is **fully independent** from
ShitalEco. There is no shared schema, shared connection, or shared
volume:

| Aspect              | ShitalEco backend            | Neuron Master            |
| ------------------- | ---------------------------- | ------------------------ |
| Engine              | Postgres                     | SQLite (or Postgres)     |
| Env var             | `DATABASE_URL`               | `NEURON_DB_URL`          |
| Default URL         | `postgresql+asyncpg://…`     | `sqlite+aiosqlite:///./data/neuron.db` |
| Docker volume       | `pgdata`                     | `neuron_data`            |
| Docker network      | ShitalEco compose network    | `neuron_internal`        |

`backend/db.py` actively **refuses to start** if `NEURON_DB_URL` ever
contains `shital` or `shitaleco`, so a misconfigured deploy can't
accidentally share storage with the host monorepo.

## Run locally

```bash
cd master_platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# from master_platform/, the libraries dir lives one level up
uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
```

On first boot the Master auto-issues a **bootstrap admin API key** and
writes it (chmod 0600) to `master_platform/data/bootstrap_admin.txt`.
Open the Master at `http://localhost:8088/login`, paste the key, then
**delete the file**. From that point on, every key is created, rotated
and revoked from the **Secrets** section in the UI (`/ui/secrets`).

There is intentionally no environment variable for this key — secret
material doesn't belong in env files or container env vars.

## Endpoints

All `/api/*` endpoints require `X-API-Key: <secret>`. Scopes are tier-derived:

| Tier         | Granted scopes                                              |
| ------------ | ----------------------------------------------------------- |
| admin        | everything (incl. `apikeys:*`, `audit:read`)                |
| integration  | `library:read`, `systems:*`, `devices:*`, `processes:*`     |
| device       | `library:read`, `devices:*`                                 |
| readonly     | `*:read`                                                    |

| Method | Path                                          | Purpose                            |
| ------ | --------------------------------------------- | ---------------------------------- |
| GET    | `/healthz`                                    | health + library item count        |
| GET    | `/api/library/{components,control-boards,micro-compute,digital-twin-controls,ui-controls}` | library listings |
| GET    | `/api/library/item/{stable_id}`               | single item                        |
| POST   | `/api/library/reload`                         | re-scan library tree (admin)       |
| POST   | `/api/root-systems`                           | create root                        |
| POST   | `/api/node-systems`                           | create node                        |
| POST   | `/api/edge-systems`                           | create edge                        |
| POST   | `/api/devices`                                | register a device (assigns DNA)    |
| POST   | `/api/devices/{dna}/pinmap/auto`              | run auto pin allocator             |
| POST   | `/api/devices/{dna}/pinmap/manual`            | save manual pin override           |
| POST   | `/api/devices/{dna}/generate-dna`             | emit signed DNA                    |
| POST   | `/api/devices/{dna}/generate-brain-config`    | emit Brain config                  |
| POST   | `/api/devices/{dna}/build-firmware-bundle`    | build OTA-ready zip                |
| GET    | `/api/devices/{dna}/firmware-bundle`          | download zip                       |
| POST   | `/api/processes`                              | save process graph                 |
| GET    | `/api/processes/{id}`                         | fetch process                      |
| POST   | `/api/apikeys`                                | issue API key (admin)              |
| POST   | `/api/apikeys/{id}/rotate`                    | rotate (overlap window)            |
| POST   | `/api/apikeys/{id}/revoke`                    | revoke                             |
| GET    | `/api/audit`                                  | audit query (admin)                |

## UI

| Path                   | What                                       |
| ---------------------- | ------------------------------------------ |
| `/`                    | dashboard                                  |
| `/login` / `/logout`   | session sign-in (paste API key)            |
| `/ui/library`          | catalog with search filter                 |
| `/ui/library/manage`   | **Library Management** (Hardware / Twin / UI / Business / Functional / API) — create, edit, delete DB-backed items |
| `/ui/systems`          | root/node/edge listing                     |
| `/ui/devices`          | devices with status badges                 |
| `/ui/devices/{dna}`    | DNA + Brain + pin map + firmware download  |
| `/ui/secrets`          | **Secrets Management** — issue/rotate/revoke API keys, plaintext shown exactly once at issuance |
| `/ui/audit`            | audit log viewer                           |
| `/docs`                | OpenAPI / Swagger UI                       |

The UI uses Tailwind via CDN + HTMX (zero build step), is mobile-responsive,
and renders rich tiles for library cards and devices.

## Quick walkthrough

```bash
KEY=neu_...   # the bootstrap admin key

curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"Acme Foods"}' http://localhost:8080/api/root-systems

ROOT=$(curl -s -H "X-API-Key: $KEY" http://localhost:8080/api/root-systems | jq -r '.[0].id')

curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"root_id\":\"$ROOT\",\"name\":\"UK\"}" http://localhost:8080/api/node-systems

NODE=$(curl -s -H "X-API-Key: $KEY" http://localhost:8080/api/node-systems | jq -r '.[0].id')

curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"node_id\":\"$NODE\",\"name\":\"Wembley\",\"site_id\":\"wembley-1\"}" \
  http://localhost:8080/api/edge-systems

EDGE=$(curl -s -H "X-API-Key: $KEY" http://localhost:8080/api/edge-systems | jq -r '.[0].id')

curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"edge_id\":\"$EDGE\",\"device_type\":\"pico2w\",\"compute_stable_id\":\"compute.pico2w\",\"components\":[
        {\"component_stable_id\":\"sensor.temp.ds18b20\",\"instance_id\":\"probe_a\"},
        {\"component_stable_id\":\"actuator.relay.ssr\",\"instance_id\":\"ssr_main\"},
        {\"component_stable_id\":\"actuator.heater.element\",\"instance_id\":\"element_a\"}]}" \
  http://localhost:8080/api/devices

DNA=$(curl -s -H "X-API-Key: $KEY" http://localhost:8080/api/devices | jq -r '.[0].device_dna')

curl -s -X POST -H "X-API-Key: $KEY" http://localhost:8080/api/devices/$DNA/pinmap/auto
curl -s -X POST -H "X-API-Key: $KEY" http://localhost:8080/api/devices/$DNA/generate-dna
curl -s -X POST -H "X-API-Key: $KEY" http://localhost:8080/api/devices/$DNA/generate-brain-config
curl -s -X POST -H "X-API-Key: $KEY" http://localhost:8080/api/devices/$DNA/build-firmware-bundle
```

The bundle ends up under `master_platform/build_artifacts/<DNA>.zip` and can
be downloaded via `/api/devices/{dna}/firmware-bundle`.

## Deployment in the ShitalEco pipeline

The Master Platform is shipped as a Docker image built from
`neuron-platform/master_platform/Dockerfile`. The existing nginx/Render
config in the ShitalEco repo can route `neuron.shital.org.uk` to this
container on port `8080`.
