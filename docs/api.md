# Master Platform API

The Neuron Master Platform exposes a single FastAPI surface served from
`master_platform/backend/main.py`. OpenAPI/Swagger lives at `/docs`,
ReDoc at `/redoc`, the JSON spec at `/openapi.json`.

## Authentication

All `/api/*` endpoints require an API key:

```
X-API-Key: neu_xxxxxxxxxxxxxxxxxxxxxxxx
```

Keys are stored as Argon2id hashes; only the plaintext secret is shown
once at creation/rotation. On first deploy a single `admin`-tier
bootstrap key is generated and logged exactly once
(or pre-set via `NEURON_BOOTSTRAP_ADMIN_KEY`).

### Tiers and scopes

| Tier         | Inherited scopes                                            |
| ------------ | ----------------------------------------------------------- |
| admin        | everything (incl. `apikeys:*`, `audit:read`)                |
| integration  | `library:read`, `systems:*`, `devices:*`, `processes:*`     |
| device       | `library:read`, `devices:*`                                 |
| readonly     | `library:read`, `systems:read`, `devices:read`, `processes:read` |

### Rate limiting

Each key carries `rate_per_minute` and `rate_burst`. The Master uses an
in-process token-bucket limiter (per key id). When exceeded, requests
return `429 Too Many Requests`.

## Endpoints

### Library Registry

| Method | Path                                     | Scope          |
| ------ | ---------------------------------------- | -------------- |
| GET    | `/api/library/components`                | library:read   |
| GET    | `/api/library/control-boards`            | library:read   |
| GET    | `/api/library/micro-compute`             | library:read   |
| GET    | `/api/library/digital-twin-controls`     | library:read   |
| GET    | `/api/library/ui-controls`               | library:read   |
| GET    | `/api/library/item/{stable_id}`          | library:read   |
| POST   | `/api/library/reload`                    | admin          |

### System Designer

| Method | Path                | Scope            |
| ------ | ------------------- | ---------------- |
| POST   | `/api/root-systems` | systems:write    |
| GET    | `/api/root-systems` | systems:read     |
| POST   | `/api/node-systems` | systems:write    |
| GET    | `/api/node-systems` | systems:read     |
| POST   | `/api/edge-systems` | systems:write    |
| GET    | `/api/edge-systems` | systems:read     |

### Devices (full lifecycle)

| Method | Path                                                | Scope          |
| ------ | --------------------------------------------------- | -------------- |
| POST   | `/api/devices`                                      | devices:write  |
| GET    | `/api/devices`                                      | devices:read   |
| GET    | `/api/devices/{dna}`                                | devices:read   |
| POST   | `/api/devices/{dna}/pinmap/auto`                    | devices:write  |
| POST   | `/api/devices/{dna}/pinmap/manual`                  | devices:write  |
| POST   | `/api/devices/{dna}/generate-dna`                   | devices:write  |
| POST   | `/api/devices/{dna}/generate-brain-config`          | devices:write  |
| POST   | `/api/devices/{dna}/build-firmware-bundle`          | devices:write  |
| GET    | `/api/devices/{dna}/firmware-bundle`                | devices:read   |

### Processes

| Method | Path                       | Scope             |
| ------ | -------------------------- | ----------------- |
| POST   | `/api/processes`           | processes:write   |
| GET    | `/api/processes`           | processes:read    |
| GET    | `/api/processes/{id}`      | processes:read    |

### API Management

| Method | Path                         | Scope          |
| ------ | ---------------------------- | -------------- |
| POST   | `/api/apikeys`               | apikeys:write  |
| GET    | `/api/apikeys`               | apikeys:read   |
| POST   | `/api/apikeys/{id}/rotate`   | apikeys:write  |
| POST   | `/api/apikeys/{id}/revoke`   | apikeys:write  |

### Audit

| Method | Path           | Scope        | Filters                                   |
| ------ | -------------- | ------------ | ----------------------------------------- |
| GET    | `/api/audit`   | audit:read   | `actor`, `action`, `since`, `until`, `limit` |

### Meta

| Method | Path        | Description                              |
| ------ | ----------- | ---------------------------------------- |
| GET    | `/healthz`  | health + library item count + version    |

## Server-side flow guarantees

- **Pin allocation is deterministic.** Same compute + same component order
  always yields the same pinmap. Conflicts are reported in
  `pinmap.conflicts[]`; firmware build is rejected if `pinmap_json` is
  missing.
- **DNA is fingerprinted.** `fingerprint.alg = sha256`, `fingerprint.hash`
  is computed over the canonical-JSON-serialised DNA without the
  fingerprint field itself.
- **Brain is schema-validated** against `shared_schemas/brain_schema.json`
  on every generation.
- **Firmware bundle** is a deterministic ZIP (fixed mtime, sorted
  filenames) containing `dna.json`, `brain.json`, `pinmap.json`,
  `manifest.json`, and a `main_stub.py`. The bundle manifest carries
  `min_supported_base_firmware_version`,
  `supported_hardware_revisions`, and per-artifact SHA-256 hashes for
  Stage 4 OTA gating.
- **Audit** captures `actor`, `actor_kind`, `action`, `target_kind`,
  `target_id`, `outcome` and an optional `detail` JSON for every write.
