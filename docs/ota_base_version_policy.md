# OTA Base-Version Policy

The Neuron Platform applies a **deny-by-default** OTA contract. Every
firmware update must pass four independent gates before it is staged on
a device. Three gates are enforced on the **device**, one is enforced
on the **Master** at promote/rollback time.

## The signed OTA manifest

Each generated firmware bundle carries a `manifest.json` matching
`shared_schemas/ota_manifest_schema.json`:

```json
{
  "manifest_id": "bundle-DNA-XXXX-XXXX-XXXX-XXXX-1.1.0",
  "kind": "app_bundle",
  "issued_at": "2026-05-09T12:34:56Z",
  "issuer": "master_platform",
  "channel": "stable",
  "min_supported_base_firmware_version": "1.0.0",
  "supported_hardware_revisions": ["rev_a"],
  "target_app_bundle_version": "1.1.0",
  "target_config_schema_version": "1.0.0",
  "rollback_allowed": false,
  "artifacts": [
    { "path": "dna.json", "size_bytes": 412, "sha256": "…" },
    { "path": "brain.json", "size_bytes": 880, "sha256": "…" }
  ],
  "signature": {
    "alg": "ed25519",
    "kid": "ota-default-1",
    "value": "<base64 signature over the canonical manifest minus this field>"
  }
}
```

## Gates

### 1. Signature gate (device)

The device computes the canonical JSON of the manifest **without the
`signature` field** (sorted keys, no spaces) and verifies the Ed25519
signature against the master public key it was provisioned with
(`ota_pubkey.txt`). On any mismatch the staged update is rejected and
a `ota_sig_rejected` trace is recorded.

The master signing key is generated on first boot and persisted at
`master_platform/data/ota_signing.key` (chmod 0600). The matching
public key is exposed unauthenticated at `GET /api/ota/pubkey` so
provisioning can `curl … | jq -r .public_key_b64 > ota_pubkey.txt`
once per device.

The key can be rotated by an admin via `POST /api/ota/rotate-key`. A
new `kid` is issued; previously-signed manifests remain verifiable
against the archived public key the operator stores externally.

### 2. Hardware-revision gate (device)

The device's `hardware_revision` (from its DNA) must appear verbatim
in `manifest.supported_hardware_revisions`. Out-of-revision bundles
are rejected.

### 3. Base-firmware-version gate (device)

The device's `base_firmware_version` (from its DNA) must be **>=**
`manifest.min_supported_base_firmware_version`. Devices on a
too-old base refuse the update. This protects against the
"new app needs new base API" scenario.

### 4. Downgrade gate (device)

If `target_app_bundle_version < device.app_bundle_version` the
manifest **must** carry `rollback_allowed: true` and re-sign that
modified manifest. Otherwise the device refuses to install. This
prevents accidental rollbacks via cached bundles while still allowing
an operator to roll back deliberately.

### 5. Promote-time gate (master)

When an admin calls `POST /api/devices/{dna}/promote` to move a
release to a higher channel (e.g. `dev` → `stable`), the master
re-runs the hardware + downgrade checks server-side and refuses with
`400` if they fail. This catches the case where a build's manifest
was wrong before any device ever asks for it.

## Rollback

`POST /api/devices/{dna}/rollback` switches the channel pointer to the
**previous** release (next-newest by `created_at`). The pointed-at
bundle becomes the active one for that channel; subsequent
`/api/devices/{dna}/firmware-bundle?channel=…` calls return it. The
old release is **not** deleted, so successive rollbacks/promotes are
always possible.

## Channels

Three channels are supported per device:

| Channel | Auto-populated by | Promotion | Use case                         |
| ------- | ----------------- | --------- | -------------------------------- |
| `dev`   | each successful build | implicit | bench validation                  |
| `beta`  | explicit promote      | manual   | a single pilot site               |
| `stable`| explicit promote      | manual   | factory production                |

The Pico OTA client polls `firmware-manifest?channel=stable` by
default; the channel can be overridden in `config.json` for pilot
deployments.

## Operator runbook

```bash
# Read the pubkey (one-time, at provisioning)
curl https://neuron.shital.org.uk/api/ota/pubkey | jq -r .public_key_b64 > ota_pubkey.txt
mpremote cp ota_pubkey.txt :ota_pubkey.txt

# Build + promote
KEY="X-API-Key: $NEURON_KEY"
curl -X POST -H "$KEY" .../api/devices/$DNA/build-firmware-bundle
RID=$(curl -H "$KEY" .../api/devices/$DNA/releases | jq -r '.releases[0].id')
curl -X POST -H "$KEY" -H 'Content-Type: application/json' \
  -d "{\"channel\":\"stable\",\"release_id\":\"$RID\"}" \
  .../api/devices/$DNA/promote

# Rollback
curl -X POST -H "$KEY" -H 'Content-Type: application/json' \
  -d '{"channel":"stable"}' \
  .../api/devices/$DNA/rollback
```
