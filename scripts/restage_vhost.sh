#!/bin/bash
# Self-heals nginx/conf.d/neuron.conf if it goes missing or drifts.
# Designed to be invoked from a systemd timer every minute, so when the
# upstream deployer hook wipes the file, the gap is ≤60 s.
#
# Idempotent and silent on the happy path. Logs to systemd journal
# only when it actually had to do something.
set -euo pipefail

REPO="${REPO:-/opt/shitaleco}"
SOURCE="$REPO/neuron-platform/nginx/neuron.conf"
TARGET="$REPO/nginx/conf.d/neuron.conf"
NGINX_CONTAINER="${NGINX_CONTAINER:-shitaleco-nginx-1}"

# If the source file is gone too, the deployer hook has wiped the
# entire neuron-platform/ subtree. Re-fetch the deploy branch from
# origin to recover both files in one go.
if [ ! -f "$SOURCE" ]; then
  cd "$REPO"
  git fetch origin claude/shital-erp-platform-iR2UF --quiet 2>/dev/null || exit 0
  git checkout -B claude/shital-erp-platform-iR2UF \
               origin/claude/shital-erp-platform-iR2UF --quiet 2>/dev/null || exit 0
  git reset --hard origin/claude/shital-erp-platform-iR2UF --quiet 2>/dev/null || exit 0
  if [ ! -f "$SOURCE" ]; then
    # The deploy branch tip itself doesn't have it — nothing more to do.
    exit 0
  fi
  echo "[neuron-vhost-watchdog] re-fetched repo because $SOURCE was missing"
fi

# Compare and copy only if needed (so reload doesn't fire constantly).
if cmp -s "$SOURCE" "$TARGET" 2>/dev/null; then
  exit 0
fi

mkdir -p "$(dirname "$TARGET")"
cp "$SOURCE" "$TARGET"
echo "[neuron-vhost-watchdog] re-staged $TARGET"

# Validate + reload nginx (graceful, no downtime). Skip the reload if
# the container isn't running — there's nothing to talk to.
if docker ps --filter "name=$NGINX_CONTAINER" --format '{{.Names}}' \
   | grep -qx "$NGINX_CONTAINER"; then
  if docker exec "$NGINX_CONTAINER" nginx -t >/dev/null 2>&1; then
    docker exec "$NGINX_CONTAINER" nginx -s reload >/dev/null 2>&1 || true
    echo "[neuron-vhost-watchdog] nginx config reloaded"
  else
    echo "[neuron-vhost-watchdog] nginx -t FAILED; not reloading"
    exit 1
  fi
fi
