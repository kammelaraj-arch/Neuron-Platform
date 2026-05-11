# Auto-deploy setup (GitHub Actions → webhook → VPS)

`.github/workflows/deploy.yml` POSTs to the Neuron deployer webhook at
`https://neuron.shital.org.uk/deploy` whenever a relevant change lands
on a deploy branch. No SSH, no private key in GitHub — the workflow's
only capability is "ask the VPS to redeploy from the deploy branch tip".

The webhook listener (`neuron-deployer` container) lives in the same
`master_platform/docker-compose.yml` as the Master itself.

## One-time setup

For the full one-time host setup (Linux user, repo clone, secrets,
TLS, reverse proxy vhost) see `docs/HOST_SETUP.md`. The webhook
secret on the VPS side must match the one in GitHub:

| Where                                    | Name                    |
| ---------------------------------------- | ----------------------- |
| `/opt/neuron-platform/master_platform/.env` | `NEURON_DEPLOY_SECRET=…` |
| GitHub repo → Settings → Secrets → Actions  | `NEURON_DEPLOY_SECRET`   |

Optional repo **variables** to override defaults:

| Name                | Default                                  |
| ------------------- | ---------------------------------------- |
| `NEURON_DEPLOY_URL` | `https://neuron.shital.org.uk/deploy`    |
| `NEURON_HEALTH_URL` | `https://neuron.shital.org.uk/healthz`   |

## What triggers a deploy

| Event                                                              | Behaviour |
| ------------------------------------------------------------------ | --------- |
| Push to `main` touching deploy paths (see `paths:` in `deploy.yml`) | Auto-deploys |
| Push to `claude/init-neuron-platform-YEIak` touching deploy paths   | Auto-deploys (will be removed at cutover) |
| Push touching only `docs/`, `tests/`, `tools/`, etc.                | Skipped — no deploy |
| Manual run from the Actions tab (`workflow_dispatch`)               | Optional flag: `skip_health_check` |
| Concurrent pushes on the same ref                                   | Coalesced (latest wins, in-flight cancelled) |

## What the workflow does

1. Refuses to start if `NEURON_DEPLOY_SECRET` is unset.
2. `POST https://neuron.shital.org.uk/deploy` with header
   `X-Deploy-Secret: $NEURON_DEPLOY_SECRET`. Expects `HTTP 202`.
3. Polls `https://neuron.shital.org.uk/healthz` for up to 5 min until
   it sees `{"status":"ok",...}`, then reports the run green.

## What runs on the VPS

The `neuron-deployer` container validates the secret, then spawns
`/app/deploy.sh` in the background. That script:

```bash
cd /workspace                       # bind-mounted from /opt/neuron-platform
git fetch origin "$NEURON_DEPLOY_BRANCH" --quiet
git reset --hard "origin/$NEURON_DEPLOY_BRANCH" --quiet
bash deploy.sh                      # rebuild + restart neuron-master, healthz check
```

`deploy.sh` only touches the Neuron stack. It does not interact with
the host reverse proxy.

## Smoke-test the webhook

From a workstation:

```bash
curl -sS -i -X POST https://neuron.shital.org.uk/deploy \
  -H "X-Deploy-Secret: <the-hex>"
# → HTTP/1.1 202 Accepted
```

A `403` means the secret on the VPS doesn't match. A `502/504` means
the reverse proxy can't reach the deployer container.

## Disabling auto-deploy

Either:

- Comment out the `push:` block in `.github/workflows/deploy.yml` so
  only `workflow_dispatch` works, or
- Remove `NEURON_DEPLOY_SECRET` from the repository secrets — the
  workflow then refuses to run with a clear "Missing secret" error, or
- Stop the deployer container on the VPS:
  `docker compose -f master_platform/docker-compose.yml stop neuron-deployer`

## Manual emergency rollback

See "Rollback" in `DEPLOY.md`.
