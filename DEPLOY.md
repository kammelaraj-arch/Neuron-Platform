# Deploying the Neuron Platform

The Neuron Master Platform deploys **independently** from ShitalEco.
Running `neuron-platform/deploy.sh` never restarts, pulls, or
reconfigures any ShitalEco service. The only shared action is a
`SIGHUP` to the existing nginx (graceful config reload, no downtime,
no container restart) so that `https://neuron.shital.org.uk` starts
routing to the new container — and even that step is opt-out via
`--no-nginx`.

## What "independent" means here

| Aspect | ShitalEco | Neuron |
| --- | --- | --- |
| docker-compose file | `/opt/shitaleco/docker-compose.prod.yml` | `neuron-platform/master_platform/docker-compose.yml` |
| Image | `ghcr.io/kammelaraj-arch/shitaleco-*:latest` | `neuron/master:0.3.0` (built locally) |
| Database | Postgres in the `pgdata` volume | private SQLite in the `neuron_data` volume |
| Volumes | `pgdata` | `neuron_data`, `neuron_artifacts` |
| Docker network | ShitalEco compose `internal` | `neuron_internal` (separate bridge) |
| Bind | varies | `172.17.0.1:8088` (docker bridge gateway only — not on the public IP) |
| Lifecycle | `bash deploy.sh --update` | `bash neuron-platform/deploy.sh` |
| nginx config file | `nginx/conf.d/shital.conf` | `nginx/conf.d/neuron.conf` |
| nginx interaction | restart | **reload only** |

`backend/db.py` actively **refuses to start** if `NEURON_DB_URL` ever
contains `shital` or `shitaleco`, so a misconfigured deploy can't
accidentally point the Master at the ShitalEco database.

## Production deploy steps

The Neuron Master and ShitalEco services run on the same VPS at
`/opt/shitaleco/`, but their lifecycles never cross.

### 1. Pull the deploy branch (one-time, or after upstream merges)

```bash
ssh root@<vps>
cd /opt/shitaleco
git pull
```

### 2. Deploy ONLY Neuron (does not touch any ShitalEco container)

```bash
sudo bash neuron-platform/deploy.sh
```

Output:

```
▶ Neuron Master Platform — independent deploy
  compose:        /opt/shitaleco/neuron-platform/master_platform/docker-compose.yml
  nginx compose:  /opt/shitaleco/docker-compose.prod.yml (reload only, never restart)
  pull:           yes
  nginx reload:   yes

▶ [1/5] Syncing repo
  ✓ Updated to <sha> (claude/shital-erp-platform-iR2UF)

▶ [2/5] Building neuron-master image
  ✓ Image built

▶ [3/5] Starting neuron-master
  ✓ Container started

▶ [4/5] Waiting for /healthz
  ✓ neuron-master healthy (3×2s)

▶ [5/5] Reloading shared nginx (SIGHUP, never restart)
  ✓ Nginx config reloaded

✅ Deploy complete — https://neuron.shital.org.uk
```

The script flags:

```
--no-pull               skip the git fetch+reset (use already-checked-out files)
--no-nginx              skip the nginx reload entirely
--logs                  tail container logs after a successful deploy
--nginx-compose=PATH    point at a different shared nginx compose file
```

### 3. Issue / extend the TLS certificate (one-time)

`neuron.shital.org.uk` already needs to be a SAN on the
`/etc/letsencrypt/live/shital.org.uk/` cert. If you haven't done this
yet:

```bash
docker compose -f /opt/shitaleco/docker-compose.prod.yml run --rm certbot \
  certonly --webroot -w /var/www/certbot \
  --expand --cert-name shital.org.uk \
  -d shital.org.uk -d www.shital.org.uk \
  -d admin.shital.org.uk -d kiosk.shital.org.uk \
  -d donate.shital.org.uk -d screen.shital.org.uk \
  -d service.shital.org.uk -d neuron.shital.org.uk
```

Then `bash neuron-platform/deploy.sh --no-pull` will reload nginx and
serve the new vhost.

### 4. First-time login

`neuron-platform/deploy.sh` detects the bootstrap-key file on first
run and prints the exact commands to copy and delete it:

```bash
docker compose -f /opt/shitaleco/neuron-platform/master_platform/docker-compose.yml \
  exec neuron-master cat master_platform/data/bootstrap_admin.txt
```

Open `https://neuron.shital.org.uk/login`, paste the line that doesn't
start with `#`, then immediately delete the file:

```bash
docker compose -f /opt/shitaleco/neuron-platform/master_platform/docker-compose.yml \
  exec neuron-master rm master_platform/data/bootstrap_admin.txt
```

From there every API key is created, rotated, and revoked from the
in-app **Secrets** section at `/ui/secrets`.

## Lifecycle — what each script does and doesn't touch

| Script | Touches ShitalEco services? | Touches Neuron? | Touches nginx? |
| --- | --- | --- | --- |
| `bash deploy.sh --update` | yes (pulls + restarts all of them) | **no** | reloads |
| `bash neuron-platform/deploy.sh` | **no** | builds + restarts neuron-master | reloads (opt-out) |

## Rollback

Neuron rollback is a separate operation — it never affects ShitalEco
either:

```bash
docker compose -f /opt/shitaleco/neuron-platform/master_platform/docker-compose.yml \
  down neuron-master
docker image rm neuron/master:0.3.0
docker compose -f /opt/shitaleco/neuron-platform/master_platform/docker-compose.yml \
  up -d neuron-master
```

OTA rollback for **devices** is unrelated — that's
`POST /api/devices/{dna}/rollback` and is documented in
`docs/ota_base_version_policy.md`.

## Deploying onto a Raspberry Pi (Edge Runtime)

The Edge Runtime (`neuron-platform/edge_runtime/`) is the piece that
runs at each factory site, typically on a Raspberry Pi 5. It also
deploys via its own stand-alone compose:

```bash
ssh pi@<pi-ip>
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
git clone https://github.com/kammelaraj-arch/ShitalEco.git
cd ShitalEco/neuron-platform/edge_runtime
cp .env.example .env
# edit .env: set EDGE_ID, EDGE_SITE_ID, EDGE_PARENT_NODE_URL
docker compose up -d --build
docker compose logs -f neuron-edge
```

The Pi's Edge will heartbeat to `EDGE_PARENT_NODE_URL` (your Master)
and start receiving twin commands. No ShitalEco services involved on
either side.
