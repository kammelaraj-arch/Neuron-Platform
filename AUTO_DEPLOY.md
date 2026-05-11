# Auto-deploy setup (GitHub Actions → webhook → VPS)

`.github/workflows/deploy.yml` POSTs to the Neuron deployer webhook at
`https://neuron.shital.org.uk/deploy` whenever a relevant change lands
on a deploy branch. No SSH, no private key in GitHub — the workflow's
only capability is "ask the VPS to redeploy from the deploy branch tip".

Same trust model as ShitalEco's proven `/deploy` endpoint, but with a
fully independent deployer container (`neuron-deployer`) — separate
image, separate secret, separate port (`172.17.0.1:8089` on the host).

## One-time setup

### 1. Set the shared webhook secret on the VPS

The `neuron-deployer` container needs `NEURON_DEPLOY_SECRET` set to a
long random string. The simplest source-of-truth is
`/opt/neuron-platform/master_platform/.env` (chmod 600, owned by the
`neuron` user):

```bash
# On the VPS, as the neuron user:
openssl rand -hex 32   # use the output below
echo 'NEURON_DEPLOY_SECRET=<paste-the-hex>' >> \
  /opt/neuron-platform/master_platform/.env
chmod 600 /opt/neuron-platform/master_platform/.env
```

That same value goes into GitHub in step 3.

### 2. Stand up the deployer container

The `neuron-deployer` service is declared in
`master_platform/docker-compose.yml`. It runs as part of the standard
`docker compose up -d` for the Neuron stack:

```bash
# On the VPS, as the neuron user, from /opt/neuron-platform:
docker compose -f master_platform/docker-compose.yml up -d neuron-deployer
docker compose -f master_platform/docker-compose.yml logs neuron-deployer
# Expect: "Serving HTTP on 0.0.0.0 port 9090"

# Sanity check from the VPS itself:
curl -sS http://172.17.0.1:8089/healthz
# → {"status":"ok","service":"neuron-deployer"}
```

The shared nginx already proxies `https://neuron.shital.org.uk/deploy`
→ `172.17.0.1:8089` (see `nginx/neuron.conf`).

### 3. Stash the secret in GitHub Actions

Go to **Settings → Secrets and variables → Actions → New repository secret**
on `https://github.com/kammelaraj-arch/neuron-platform/` and add:

| Name | Value |
| --- | --- |
| `NEURON_DEPLOY_SECRET` | the same hex string set on the VPS in step 1 |

Optional repository **variables** (not secrets) to override defaults:

| Name | Default | Notes |
| --- | --- | --- |
| `NEURON_DEPLOY_URL`  | `https://neuron.shital.org.uk/deploy`  | webhook target |
| `NEURON_HEALTH_URL`  | `https://neuron.shital.org.uk/healthz` | post-deploy probe |

### 4. Smoke-test from a workstation

```bash
curl -sS -i -X POST https://neuron.shital.org.uk/deploy \
  -H "X-Deploy-Secret: <the-hex>"
# → HTTP/1.1 202 Accepted
```

A 403 means the secret is wrong or missing on either side.

## What triggers a deploy

| Event | Behaviour |
| --- | --- |
| Push to `main` touching deploy paths (see `paths:` in `deploy.yml`) | Auto-deploys |
| Push to `claude/init-neuron-platform-YEIak` touching deploy paths | Auto-deploys (will be removed at cutover) |
| Push touching only `docs/`, `tests/`, `tools/`, etc. | Skipped — no deploy |
| Manual run from the Actions tab (`workflow_dispatch`) | Optional flag: `skip_health_check` |
| Concurrent pushes on the same ref | Coalesced (latest wins, in-flight cancelled) |

## What the workflow does

1. Refuses to start if `NEURON_DEPLOY_SECRET` is unset (clear error).
2. `POST https://neuron.shital.org.uk/deploy` with header
   `X-Deploy-Secret: $NEURON_DEPLOY_SECRET`. Expects `HTTP 202`.
3. Polls `https://neuron.shital.org.uk/healthz` for up to 5 min until
   it sees `{"status":"ok",...}`, then reports the run green.

## What runs on the VPS

The deployer container's `server.py` validates the secret, then spawns
`/app/deploy.sh` in the background. That script:

```bash
cd /workspace                        # bind-mounted from /opt/neuron-platform
git fetch origin "$NEURON_DEPLOY_BRANCH" --quiet
git reset --hard "origin/$NEURON_DEPLOY_BRANCH" --quiet
bash deploy.sh                       # rebuild + restart neuron-master, reload nginx
```

It NEVER touches a ShitalEco container — the deploy compose file it
invokes is the Neuron one, the volumes are Neuron-only volumes, and
the nginx interaction is `nginx -s reload` (SIGHUP), never a restart.

## Disabling auto-deploy

Either:

- Comment out the `push:` block in `.github/workflows/deploy.yml` so
  only `workflow_dispatch` works, or
- Remove `NEURON_DEPLOY_SECRET` from the repository secrets — the
  workflow then refuses to run with a clear "Missing secret" error, or
- Stop the deployer container on the VPS:
  `docker compose -f master_platform/docker-compose.yml stop neuron-deployer`

## Manual emergency rollback

If a deploy goes wrong, revert to the previous Neuron image without
waiting for a new commit (all commands run as the `neuron` user from
`/opt/neuron-platform`):

```bash
docker compose -f master_platform/docker-compose.yml down neuron-master
docker run -d --name neuron-master --restart unless-stopped \
  -p 172.17.0.1:8088:8088 \
  --network master_platform_neuron_internal \
  -v master_platform_neuron_data:/app/master_platform/data \
  -v master_platform_neuron_artifacts:/app/master_platform/build_artifacts \
  neuron/master:<previous-tag>
docker exec shitaleco-nginx-1 nginx -s reload
```

Or push the previous SHA to the deploy branch and let the workflow
re-run.

## Migrating from the old SSH workflow

The previous setup (`NEURON_DEPLOY_KEY`, `NEURON_DEPLOY_HOST`, etc.)
lived in the ShitalEco repo and SSH'd into the VPS as root. That
workflow stays untouched on the ShitalEco side; it just no longer fires
for `neuron-platform/**` changes because those changes now come from
this repo. Once the new webhook deploy is verified end-to-end, the old
workflow can be deleted from ShitalEco — but that's a ShitalEco-side
change, not done here.
