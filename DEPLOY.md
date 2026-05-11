# Deploying the Neuron Platform

The Neuron Master Platform deploys as a standalone Docker stack out of
`/opt/neuron-platform/`. The two services live in
`master_platform/docker-compose.yml`:

| Service           | What it does                                                  |
| ----------------- | ------------------------------------------------------------- |
| `neuron-master`   | The FastAPI app + Admin/Build/Config UI (port 8088)           |
| `neuron-deployer` | Webhook listener that re-runs `deploy.sh` on `POST /deploy`   |

Both bind to the docker0 bridge gateway (`172.17.0.1`) so a host-level
reverse proxy can reach them but the public internet cannot.

For one-time host setup (Linux user, reverse proxy vhost, TLS cert),
see `docs/HOST_SETUP.md`.

## Day-to-day: nothing manual

After `docs/HOST_SETUP.md` has been done once, day-to-day deploys are
fully automatic — push to `main`, the GitHub Actions workflow fires
`POST https://neuron.shital.org.uk/deploy`, the deployer container
runs `deploy.sh` in the background, the new image is built and
`neuron-master` restarted. See `AUTO_DEPLOY.md`.

## Manual deploy

To kick a deploy by hand on the VPS (as the `neuron` user, from
`/opt/neuron-platform`):

```bash
bash deploy.sh                 # full deploy
bash deploy.sh --no-pull       # skip the git pull (deploy current checkout)
bash deploy.sh --logs          # tail logs after a successful deploy
bash deploy.sh --branch=foo    # deploy a non-default branch
```

What `deploy.sh` does:

1. `git fetch` + `reset --hard` to the deploy branch tip (unless `--no-pull`)
2. `docker compose build neuron-master`
3. `docker compose up -d --no-deps neuron-master`
4. Wait up to 48 s for `/healthz` to return 200

It does **not** touch the host reverse proxy, ports, certificates, or
any other service on the host. Those are operator-managed (one-time)
infrastructure.

## Rollback

```bash
# As the neuron user, from /opt/neuron-platform:
docker compose -f master_platform/docker-compose.yml down neuron-master
docker image ls 'neuron/master'                 # find a previous tag
docker run -d --name neuron-master --restart unless-stopped \
  -p 172.17.0.1:8088:8088 \
  -v neuron-platform_neuron_data:/app/master_platform/data \
  -v neuron-platform_neuron_artifacts:/app/master_platform/build_artifacts \
  neuron/master:<previous-tag>
```

Or push the previous SHA to `main` and let the webhook redeploy it.

## Deploying onto a Raspberry Pi (Edge Runtime)

The Edge Runtime (`edge_runtime/`) is the piece that runs at each
factory site, typically on a Raspberry Pi 5. It deploys via its own
stand-alone compose:

```bash
ssh pi@<pi-ip>
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
git clone https://github.com/kammelaraj-arch/neuron-platform.git
cd neuron-platform/edge_runtime
cp .env.example .env
# edit .env: set EDGE_ID, EDGE_SITE_ID, EDGE_PARENT_NODE_URL
docker compose up -d --build
docker compose logs -f neuron-edge
```

The Pi's Edge heartbeats to `EDGE_PARENT_NODE_URL` (the Master) and
starts receiving twin commands.
