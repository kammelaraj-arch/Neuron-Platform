# Neuron Edge Runtime (Stage 3)

Site-local orchestration. One container per factory site (typically on a
Raspberry Pi 5). Hard rule: **deny by default**, allow only the parent
Master + the local device LAN + the explicitly-configured emergency
channel.

## Services in this single container

| Module                    | Purpose                                                  |
| ------------------------- | -------------------------------------------------------- |
| `edge_gateway` (middleware) | mTLS / fingerprint check + IP allow-list policy enforcement |
| `device_registry`         | per-edge SQLite of devices that heartbeat in             |
| `twin_cache`              | desired / reported / current per-device JSON cache       |
| `command_router`          | REST → internal bus (MQTT-shape topics)                  |
| `log_trace_collector`     | append-only trace log + ring buffer                      |
| `health_monitor`          | marks devices offline after 90 s of silence              |
| status dashboard          | mobile-responsive Tailwind UI at `/`                     |

## Run locally

```bash
cd edge_runtime
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

uvicorn edge.main:app --host 0.0.0.0 --port 8090
```

Or with Docker:

```bash
docker compose up -d
docker compose logs -f neuron-edge
```

## Endpoints

| Method | Path                                  | Purpose                                              |
| ------ | ------------------------------------- | ---------------------------------------------------- |
| GET    | `/`                                   | Status dashboard (mobile-responsive)                 |
| GET    | `/healthz`                            | Health + uptime + device counts                      |
| POST   | `/api/v1/devices/heartbeat`           | Pico 2 W heartbeat (auto-registers on first call)    |
| GET    | `/api/v1/devices`                     | List devices                                         |
| GET    | `/api/v1/devices/{dna}`               | One device                                           |
| POST   | `/api/v1/twin/{dna}/desired`          | Push a desired field; bus-publishes a twin command   |
| POST   | `/api/v1/twin/{dna}/reported`         | Device-side reported state ingest                    |
| POST   | `/api/v1/twin/{dna}/current`          | Device-side instantaneous state ingest               |
| GET    | `/api/v1/twin/{dna}`                  | Read twin (desired + reported + current)             |
| GET    | `/api/v1/twin`                        | Read all twins                                       |
| POST   | `/api/emergency/command`              | Emergency channel — `safe_stop` / `safe_shutdown` / `status`. mTLS fingerprint check enforced. |

## Access policy

`policies/edge_default.json` conforms to
`shared_schemas/access_policy_schema.json`. The middleware
(`edge/middleware.py`) blocks any inbound request that doesn't match an
allow-rule for the client IP + port + (optional) mTLS fingerprint.
Loopback (`127.x`, `::1`) is exempt so the dashboard and `/healthz`
work locally.

To pin certificates in production, fill in
`inbound.allow[].cert_fingerprints_sha256` (SHA-256 of the DER cert,
hex with colons) and set the same fingerprint in the upstream nginx
header `X-Client-Cert-SHA256`.

## DB isolation

`EDGE_DB_URL` defaults to a private SQLite file in `./data/edge.db`.
`db.py` refuses to start if the URL ever contains `shital`, `shitaleco`
or `neuron.db` (the Master Platform's own database).

## Emergency channel

The emergency endpoint is the only path that:

1. Enforces mTLS in production (`mtls_required: true` in policy).
2. Has a strictly limited command set (`safe_stop` / `safe_shutdown` /
   `status`).
3. Always raises a critical alarm and writes a `warning`-level trace.
