#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Neuron Platform — VPS cutover (parallel → canonical).
#
#  Run this on the production VPS, AS THE `neuron` USER, AFTER you have
#  verified the new stack works on its parallel ports.
#
#    sudo -iu neuron
#    bash /opt/neuron-platform/scripts/vps-cutover.sh
#
#  What it does:
#    1. Confirms the new stack is healthy on its temporary parallel port.
#    2. Confirms the old neuron-master container is still on 172.17.0.1:8088.
#    3. Prompts you to type 'CUTOVER' to proceed.
#    4. Stops + removes the OLD neuron-master container (downtime starts).
#    5. Tears down the new stack and removes the parallel-port +
#       parallel-container-name overrides from master_platform/.env.
#    6. Brings the new stack back up under canonical names + ports
#       (172.17.0.1:8088 / :8089).
#    7. Verifies /healthz on :8088 and on the public URL.
#
#  Total downtime for neuron.shital.org.uk: typically 20–40 s.
#  Nothing in /opt/shitaleco/ is touched.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="${NEURON_INSTALL_DIR:-/opt/neuron-platform}"
PUBLIC_URL="${NEURON_PUBLIC_URL:-https://neuron.shital.org.uk}"

R="\033[0;31m"; G="\033[0;32m"; Y="\033[1;33m"; B="\033[1m"; N="\033[0m"
ok()   { echo -e "${G}  ✓ $*${N}"; }
warn() { echo -e "${Y}  ⚠ $*${N}"; }
err()  { echo -e "${R}  ✗ $*${N}"; }
step() { echo -e "\n${B}▶ $*${N}"; }

if [ "$(whoami)" != "neuron" ]; then
  err "Must run as the 'neuron' user. Currently: $(whoami)"
  exit 1
fi

ENV_FILE="$INSTALL_DIR/master_platform/.env"
COMPOSE_FILE="$INSTALL_DIR/master_platform/docker-compose.yml"

if [ ! -f "$ENV_FILE" ]; then
  err "$ENV_FILE missing. Run scripts/vps-standup.sh first."
  exit 1
fi

# Source the env so we know which temp port to verify.
set -a; . "$ENV_FILE"; set +a
TEMP_MASTER_PORT="${NEURON_MASTER_BIND_PORT:-8090}"

# ─── 1. Verify new stack is healthy on its parallel port ─────────────────────
step "[1/6] Verifying new stack on 172.17.0.1:$TEMP_MASTER_PORT"
if ! curl -sS --max-time 5 "http://172.17.0.1:$TEMP_MASTER_PORT/healthz" \
     | grep -q '"status":"ok"'; then
  err "New stack at 172.17.0.1:$TEMP_MASTER_PORT is not healthy. Aborting."
  err "Fix it first, then re-run."
  exit 1
fi
ok "New stack healthy"

# ─── 2. Verify old container is still on 172.17.0.1:8088 ─────────────────────
step "[2/6] Verifying old neuron-master on 172.17.0.1:8088"
if ! docker ps --format '{{.Names}}' | grep -qx neuron-master; then
  warn "No container named 'neuron-master' is running."
  warn "Either you already cut over, or the old container has a different name."
  warn "If you already cut over, this script will probably do nothing useful."
fi
if curl -sS --max-time 5 http://172.17.0.1:8088/healthz | grep -q '"status":"ok"'; then
  ok "Old neuron-master serving on :8088"
else
  warn "Nothing healthy on :8088 right now — the old container may already be down."
fi

# ─── 3. Confirm ──────────────────────────────────────────────────────────────
step "[3/6] CONFIRM"
cat <<EOF

About to do the following — this WILL briefly take neuron.shital.org.uk offline:

  a) docker stop neuron-master && docker rm neuron-master   (the old container)
  b) docker compose -f master_platform/docker-compose.yml down
  c) Remove these lines from $ENV_FILE:
       NEURON_MASTER_BIND_PORT, NEURON_DEPLOYER_BIND_PORT,
       NEURON_MASTER_CONTAINER, NEURON_DEPLOYER_CONTAINER
  d) docker compose -f master_platform/docker-compose.yml up -d
     (the new stack now binds 172.17.0.1:8088 / :8089 under canonical names)
  e) Verify /healthz on :8088 and on $PUBLIC_URL

Type CUTOVER to proceed, anything else to abort.
EOF
read -r -p "> " CONFIRM
if [ "$CONFIRM" != "CUTOVER" ]; then
  err "Aborted (got: '$CONFIRM')."
  exit 1
fi

# ─── 4. Stop the old container ───────────────────────────────────────────────
step "[4/6] Stopping the old neuron-master"
if docker ps -a --format '{{.Names}}' | grep -qx neuron-master; then
  docker stop neuron-master >/dev/null || true
  docker rm   neuron-master >/dev/null || true
  ok "Old neuron-master stopped + removed"
else
  ok "No old neuron-master to remove"
fi

# ─── 5. Strip parallel overrides from .env ───────────────────────────────────
step "[5/6] Removing parallel-mode overrides from $ENV_FILE"
cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
sed -i '/^NEURON_MASTER_BIND_PORT=/d;
        /^NEURON_DEPLOYER_BIND_PORT=/d;
        /^NEURON_MASTER_CONTAINER=/d;
        /^NEURON_DEPLOYER_CONTAINER=/d' "$ENV_FILE"
ok "Stripped (backup at $ENV_FILE.bak.*)"

# ─── 6. Restart new stack on canonical ports + names ─────────────────────────
step "[6/6] Restarting new stack under canonical names + ports"
cd "$INSTALL_DIR/master_platform"
docker compose -f "$COMPOSE_FILE" down
docker compose -f "$COMPOSE_FILE" up -d

# Wait for /healthz on :8088
for i in $(seq 1 24); do
  if curl -sS --max-time 3 http://172.17.0.1:8088/healthz \
       | grep -q '"status":"ok"'; then
    ok "neuron-master healthy on 172.17.0.1:8088 (${i}×2s)"
    HEALTHY=1
    break
  fi
  printf "."
  sleep 2
done
echo ""
if [ -z "${HEALTHY:-}" ]; then
  err "neuron-master did not become healthy on :8088"
  warn "Last 30 lines of logs:"
  docker compose -f "$COMPOSE_FILE" logs --tail=30 || true
  exit 1
fi

# Public probe — uses the host's reverse proxy
if curl -sS --max-time 10 "$PUBLIC_URL/healthz" | grep -q '"status":"ok"'; then
  ok "PUBLIC $PUBLIC_URL/healthz reports ok"
else
  warn "PUBLIC $PUBLIC_URL/healthz did not return ok."
  warn "The host reverse proxy may need its vhost re-checked. The new stack"
  warn "is fine on 172.17.0.1:8088; the issue is purely in the proxy layer."
fi

echo ""
echo -e "${B}✅ Cutover complete.${N}"
echo "Smoke-test the webhook end-to-end:"
echo ""
echo "  curl -sS -i -X POST $PUBLIC_URL/deploy \\"
echo "    -H \"X-Deploy-Secret: \$NEURON_DEPLOY_SECRET\""
echo "  # → HTTP/1.1 202 Accepted"
