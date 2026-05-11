# Auto-deploy setup (GitHub Actions → VPS)

`.github/workflows/neuron-deploy.yml` SSHes to the production VPS and runs
`bash neuron-platform/deploy.sh` whenever a `neuron-platform/**` change
lands on the deploy branch (`claude/shital-erp-platform-iR2UF`).

## One-time setup (5 minutes)

### 1. Generate a deploy key

On any workstation:

```bash
ssh-keygen -t ed25519 -C "neuron-deploy@github" -f ./neuron_deploy
# Two files appear: neuron_deploy (private) and neuron_deploy.pub (public)
```

### 2. Authorise the public half on the VPS

```bash
# On your laptop:
cat neuron_deploy.pub
# Copy that line.

# Then on the VPS:
ssh root@<vps>
echo '<paste-the-public-key-line-here>' >> ~/.ssh/authorized_keys

# Sanity check from your laptop:
ssh -i neuron_deploy -o IdentitiesOnly=yes root@<vps> 'whoami; uptime'
# Should print 'root' + uptime.
```

### 3. Stash the private half + host info as GitHub Actions secrets

Go to **`https://github.com/kammelaraj-arch/ShitalEco/settings/secrets/actions`**
and add:

| Name | Value |
| --- | --- |
| `NEURON_DEPLOY_HOST` | the VPS hostname or IP (e.g. `192.248.147.205`) |
| `NEURON_DEPLOY_USER` | the SSH user (probably `root`) |
| `NEURON_DEPLOY_KEY`  | **the entire contents of `./neuron_deploy`** (the private half — paste the lines from `-----BEGIN OPENSSH PRIVATE KEY-----` to `-----END OPENSSH PRIVATE KEY-----` inclusive) |
| `NEURON_DEPLOY_PORT` | optional, defaults to `22` |

### 4. Wipe the local copy of the private key

```bash
shred -u neuron_deploy
```

GitHub now holds the only working copy. The public-half is on the VPS,
so revocation = remove that one line from `~/.ssh/authorized_keys`.

## What triggers a deploy

| Event | Behaviour |
| --- | --- |
| Push to `claude/shital-erp-platform-iR2UF` touching `neuron-platform/**` | Auto-deploys |
| Push to `claude/shital-erp-platform-iR2UF` touching anything else (ShitalEco services) | Skipped — does not touch Neuron |
| Manual run from the Actions tab | Optional flags: `skip_pull`, `skip_nginx` |
| Concurrent pushes | Coalesced (latest wins, in-flight cancelled) |

## What the workflow runs on the VPS

```bash
cd /opt/shitaleco
bash neuron-platform/deploy.sh         # default
# or with the workflow-dispatch overrides:
bash neuron-platform/deploy.sh --no-pull
bash neuron-platform/deploy.sh --no-nginx
```

After the deploy script finishes, the workflow probes `https://<host>/healthz`
(also tries with the SNI override) and asserts the Master returns
`{"status":"ok",...}` before reporting the run as green.

## Disabling auto-deploy

Either:

- Comment out the `push:` block in `.github/workflows/neuron-deploy.yml` so only `workflow_dispatch` works, or
- Remove `NEURON_DEPLOY_KEY` from secrets — the workflow refuses to run with a clear "Missing secret" error.

## Manual emergency rollback

If a deploy goes wrong, you can revert to the previous Neuron image without
waiting for a new commit:

```bash
ssh root@<vps>
cd /opt/shitaleco
docker rm -f neuron-master
docker run -d --name neuron-master --restart unless-stopped \
  -p 172.17.0.1:8088:8088 \
  --network master_platform_neuron_internal \
  -v master_platform_neuron_data:/app/master_platform/data \
  -v master_platform_neuron_artifacts:/app/master_platform/build_artifacts \
  neuron/master:0.3.0  # or whichever older tag you tagged
docker exec shitaleco-nginx-1 nginx -s reload
```

(or just push the previous SHA to the deploy branch and the workflow
re-runs.)
