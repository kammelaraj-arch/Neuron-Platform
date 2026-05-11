#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Neuron Platform — VPS standup (parallel mode, non-disruptive).
#
#  Run this on the production VPS, AS THE `neuron` USER. It is idempotent
#  and safe to re-run.
#
#    sudo -iu neuron
#    cd /opt/neuron-platform   # (if the repo is already cloned)
#    bash scripts/vps-standup.sh
#
#  This script:
#    1. Verifies it is running as the `neuron` user and is in the docker group.
#    2. Generates an SSH deploy key under ~/.ssh/ if not present, and prints
#       the public half so you can register it as a GitHub Deploy Key.
#    3. Pauses for you to register the key and confirm.
#    4. Clones the repo into /opt/neuron-platform if not already cloned.
#    5. Creates master_platform/.env if not present, with a random
#       NEURON_DEPLOY_SECRET.
#    6. Brings up the Neuron stack on TEMPORARY ports
#       (172.17.0.1:8090 master, 172.17.0.1:8091 deployer) so it runs in
#       parallel with whatever is currently on :8088 / :8089. No public
#       traffic touches the new stack until you cut over with
#       scripts/vps-cutover.sh.
#    7. Waits for /healthz, then prints next steps.
#
#  NOTHING in /opt/shitaleco/ is read, written, or otherwise touched.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_URL_SSH="${NEURON_REPO_URL_SSH:-git@github.com:kammelaraj-arch/neuron-platform.git}"
REPO_BRANCH="${NEURON_REPO_BRANCH:-claude/init-neuron-platform-YEIak}"
INSTALL_DIR="${NEURON_INSTALL_DIR:-/opt/neuron-platform}"
TEMP_MASTER_PORT="${NEURON_MASTER_BIND_PORT:-8090}"
TEMP_DEPLOYER_PORT="${NEURON_DEPLOYER_BIND_PORT:-8091}"

R="\033[0;31m"; G="\033[0;32m"; Y="\033[1;33m"; B="\033[1m"; N="\033[0m"
ok()   { echo -e "${G}  ✓ $*${N}"; }
warn() { echo -e "${Y}  ⚠ $*${N}"; }
err()  { echo -e "${R}  ✗ $*${N}"; }
step() { echo -e "\n${B}▶ $*${N}"; }
pause(){ echo ""; read -r -p "  Press Enter when ready, or Ctrl-C to abort... " _; }

# ─── 1. Identity + docker group ──────────────────────────────────────────────
step "[1/7] Checking identity and docker access"
if [ "$(whoami)" != "neuron" ]; then
  err "This script must run as the 'neuron' user. Currently: $(whoami)"
  err "Switch with:  sudo -iu neuron"
  exit 1
fi
ok "Running as $(whoami)"

if ! id -nG | tr ' ' '\n' | grep -qx docker; then
  err "User 'neuron' is not in the 'docker' group in this shell."
  err "If you just added it, log out and back in:  exit  &&  sudo -iu neuron"
  exit 1
fi
ok "docker group present in this shell"

if ! docker info >/dev/null 2>&1; then
  err "docker daemon not reachable. Is docker running? Is the socket readable?"
  exit 1
fi
ok "docker daemon reachable"

# ─── 2. SSH deploy key ───────────────────────────────────────────────────────
step "[2/7] SSH deploy key for GitHub"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
KEY=~/.ssh/neuron_platform_deploy
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N "" -C "neuron-platform-deploy@$(hostname)" -f "$KEY" >/dev/null
  ok "Generated $KEY"
else
  ok "Existing key at $KEY"
fi

# Make sure git over SSH uses this specific key for github.com.
HOST_BLOCK_MARKER="# neuron-platform deploy key"
if ! grep -q "$HOST_BLOCK_MARKER" ~/.ssh/config 2>/dev/null; then
  cat >> ~/.ssh/config <<EOF

$HOST_BLOCK_MARKER
Host github.com
    HostName github.com
    User git
    IdentityFile $KEY
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
EOF
  chmod 600 ~/.ssh/config
  ok "Wrote ~/.ssh/config block for github.com"
else
  ok "~/.ssh/config already configured for github.com"
fi

echo ""
echo "─────────────────────────────────────────────────────────────────────"
echo " Register this public key as a GitHub DEPLOY KEY on the repo:"
echo "   https://github.com/kammelaraj-arch/neuron-platform/settings/keys/new"
echo ""
echo "   Title:        neuron-platform deploy ($(hostname))"
echo "   Allow write:  NO (read-only is enough)"
echo "   Key:"
echo ""
cat "$KEY.pub" | sed 's/^/     /'
echo "─────────────────────────────────────────────────────────────────────"
pause

# Verify SSH auth works against github.com
step "[3/7] Verifying SSH auth to github.com"
# `ssh -T git@github.com` always exits 1 because GitHub denies shell
# access — even on a fully successful auth. With pipefail on, piping
# straight to grep would propagate that 1 and fail this check. So we
# capture first, then grep without piping.
SSH_OUT=$(ssh -T -o BatchMode=yes git@github.com 2>&1 || true)
if echo "$SSH_OUT" | grep -q "successfully authenticated"; then
  ok "GitHub SSH auth working"
else
  err "GitHub did not accept the deploy key. Did you register it?"
  err "Server said:"
  echo "$SSH_OUT" | sed 's/^/    /'
  err ""
  err "Test manually:  ssh -T git@github.com"
  exit 1
fi

# ─── 4. Clone or verify the repo ─────────────────────────────────────────────
step "[4/7] Repo checkout at $INSTALL_DIR"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  if [ "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]; then
    err "$INSTALL_DIR exists and is non-empty but is not a git checkout."
    err "Move or empty it first, then re-run."
    exit 1
  fi
  git clone "$REPO_URL_SSH" "$INSTALL_DIR"
  ok "Cloned into $INSTALL_DIR"
else
  ok "Repo already present"
fi
cd "$INSTALL_DIR"
git fetch origin "$REPO_BRANCH" --quiet
git checkout -B "$REPO_BRANCH" "origin/$REPO_BRANCH" --quiet
git reset --hard "origin/$REPO_BRANCH" --quiet
ok "On $REPO_BRANCH at $(git rev-parse --short HEAD)"

# ─── 5. .env (webhook secret + parallel ports) ───────────────────────────────
step "[5/7] master_platform/.env"
ENV_FILE="$INSTALL_DIR/master_platform/.env"
if [ ! -f "$ENV_FILE" ]; then
  SECRET=$(openssl rand -hex 32)
  cat > "$ENV_FILE" <<EOF
# Auto-generated by scripts/vps-standup.sh on $(date -Iseconds).
# Keep chmod 600. Webhook secret must match GitHub repo secret of same name.
NEURON_DEPLOY_SECRET=$SECRET
NEURON_DEPLOY_BRANCH=$REPO_BRANCH
NEURON_REPO_PATH=$INSTALL_DIR

# Parallel-standup mode — remove these two lines at cutover so the new
# stack rebinds to the canonical 172.17.0.1:8088 / :8089.
NEURON_MASTER_BIND_PORT=$TEMP_MASTER_PORT
NEURON_DEPLOYER_BIND_PORT=$TEMP_DEPLOYER_PORT
EOF
  chmod 600 "$ENV_FILE"
  ok "Wrote $ENV_FILE (chmod 600). Webhook secret generated."
  echo ""
  echo "─────────────────────────────────────────────────────────────────────"
  echo " Register this WEBHOOK SECRET as a GitHub Actions REPOSITORY SECRET:"
  echo "   https://github.com/kammelaraj-arch/neuron-platform/settings/secrets/actions/new"
  echo ""
  echo "   Name:   NEURON_DEPLOY_SECRET"
  echo "   Value:  $SECRET"
  echo "─────────────────────────────────────────────────────────────────────"
  pause
else
  ok "Existing $ENV_FILE — leaving alone"
  # Make sure parallel ports are set so we don't accidentally bind :8088.
  if ! grep -q '^NEURON_MASTER_BIND_PORT=' "$ENV_FILE"; then
    echo "NEURON_MASTER_BIND_PORT=$TEMP_MASTER_PORT" >> "$ENV_FILE"
    echo "NEURON_DEPLOYER_BIND_PORT=$TEMP_DEPLOYER_PORT" >> "$ENV_FILE"
    ok "Appended parallel-port overrides to $ENV_FILE"
  fi
fi

# ─── 6. Build + up (parallel mode) ───────────────────────────────────────────
step "[6/7] Building and starting the Neuron stack (parallel ports)"
COMPOSE_FILE="$INSTALL_DIR/master_platform/docker-compose.yml"

# If the OLD neuron-master container already owns the canonical name,
# our new stack must use a parallel name so the two coexist.
if docker ps -a --format '{{.Names}}' | grep -qx neuron-master; then
  if ! grep -q '^NEURON_MASTER_CONTAINER=' "$ENV_FILE"; then
    {
      echo "NEURON_MASTER_CONTAINER=neuron-master-new"
      echo "NEURON_DEPLOYER_CONTAINER=neuron-deployer-new"
    } >> "$ENV_FILE"
    ok "Old neuron-master detected — appended parallel container names to .env"
  else
    ok "Old neuron-master detected — parallel container names already in .env"
  fi
else
  ok "No prior neuron-master container — canonical names safe to use"
fi

cd "$INSTALL_DIR/master_platform"
# Explicit COMPOSE_PROJECT_NAME so we NEVER share a namespace with any
# other compose stack on this host. Without this, docker compose
# inherits the project name from the containing directory
# ("master_platform"), which collides with any previous compose run from
# the same path and causes orphaned-container management to wreck
# unrelated containers. Hard-coding "neuron" guarantees isolation.
export COMPOSE_PROJECT_NAME=neuron
docker compose -f "$COMPOSE_FILE" build
docker compose -f "$COMPOSE_FILE" up -d
ok "Stack started (compose project: neuron)"

# ─── 7. Health check ─────────────────────────────────────────────────────────
step "[7/7] Health check (parallel port $TEMP_MASTER_PORT)"
for i in $(seq 1 24); do
  if curl -sS --max-time 3 "http://172.17.0.1:$TEMP_MASTER_PORT/healthz" \
       | grep -q '"status":"ok"'; then
    ok "neuron-master healthy on 172.17.0.1:$TEMP_MASTER_PORT (${i}×2s)"
    HEALTHY=1
    break
  fi
  printf "."
  sleep 2
done
echo ""
if [ -z "${HEALTHY:-}" ]; then
  err "neuron-master did not become healthy in 48 s"
  echo ""
  warn "Last 30 lines of container logs:"
  docker compose -f "$COMPOSE_FILE" logs --tail=30 || true
  exit 1
fi

if curl -sS --max-time 3 "http://172.17.0.1:$TEMP_DEPLOYER_PORT/healthz" \
     | grep -q '"status":"ok"'; then
  ok "neuron-deployer healthy on 172.17.0.1:$TEMP_DEPLOYER_PORT"
else
  warn "neuron-deployer /healthz not responding — check logs"
fi

# ─── Done ───────────────────────────────────────────────────────────────────
cat <<EOF

${B}✅ Parallel standup complete.${N}

The OLD neuron-master container is still running on 172.17.0.1:8088 and
still serving public traffic for neuron.shital.org.uk. The NEW stack is
running alongside it on these ports (VPS-internal only):

  master:    http://172.17.0.1:$TEMP_MASTER_PORT
  deployer:  http://172.17.0.1:$TEMP_DEPLOYER_PORT

Smoke-test from THIS VPS:

  curl http://172.17.0.1:$TEMP_MASTER_PORT/healthz
  curl http://172.17.0.1:$TEMP_DEPLOYER_PORT/healthz

When you are ready to cut over (public traffic switches to the new
stack, ~30 s downtime), run:

  bash $INSTALL_DIR/scripts/vps-cutover.sh

That script is non-destructive until you explicitly confirm.
EOF
