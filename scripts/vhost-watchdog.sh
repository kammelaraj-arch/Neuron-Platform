#!/bin/bash
# Watchdog: keep /opt/shitaleco/nginx/conf.d/neuron.conf in sync with
# /opt/neuron-platform/host-nginx/neuron.conf (the Neuron-owned source
# of truth).
#
# Without this, shitaleco-deployer-1 re-stages /opt/shitaleco/nginx/
# conf.d/ on its own deploys, which silently reverts our cert / proxy
# config. The watchdog runs every minute via systemd timer (see
# scripts/systemd/) and restores+reloads nginx if drift is detected.
#
# Idempotent and silent on the happy path. Logs to systemd journal
# only when it actually had to do something.
set -euo pipefail

CANONICAL="${NEURON_VHOST_CANONICAL:-/opt/neuron-platform/host-nginx/neuron.conf}"
TARGET="${NEURON_VHOST_TARGET:-/opt/shitaleco/nginx/conf.d/neuron.conf}"
NGINX_CONTAINER="${NEURON_NGINX_CONTAINER:-shitaleco-nginx-1}"

# If our canonical is missing, we have nothing to enforce — bail.
if [ ! -f "$CANONICAL" ]; then
  exit 0
fi

# If the target is identical, nothing to do.
if cmp -s "$CANONICAL" "$TARGET" 2>/dev/null; then
  exit 0
fi

# Restore from canonical.
mkdir -p "$(dirname "$TARGET")"
cp "$CANONICAL" "$TARGET"
echo "[neuron-vhost-watchdog] restored $TARGET from $CANONICAL"

# Validate + graceful reload. Skip the reload if the nginx container
# isn't running — there's nothing to talk to.
if docker ps --filter "name=$NGINX_CONTAINER" --format '{{.Names}}' \
   | grep -qx "$NGINX_CONTAINER"; then
  if docker exec "$NGINX_CONTAINER" nginx -t >/dev/null 2>&1; then
    docker exec "$NGINX_CONTAINER" nginx -s reload >/dev/null 2>&1 || true
    echo "[neuron-vhost-watchdog] nginx reloaded"
  else
    echo "[neuron-vhost-watchdog] nginx -t FAILED; not reloading"
    exit 1
  fi
fi
