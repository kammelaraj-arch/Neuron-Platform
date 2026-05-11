#!/bin/bash
# Runs inside the neuron-deployer container when the webhook fires.
# Self-heals the /workspace checkout to the deploy branch tip, then
# invokes deploy.sh which restarts only the Neuron Docker stack.
set -eo pipefail

LOG=/tmp/neuron-deploy-$(date +%s).log
exec >> "$LOG" 2>&1

echo "=== Neuron deploy started $(date) ==="
cd /workspace

# /workspace is bind-mounted from the host where it's owned by UID
# 1001 (the neuron user), but inside this Alpine container we run as
# root (UID 0). Without safe.directory, git aborts with "dubious
# ownership". This is a no-op inside the container; nothing persists.
git config --global --add safe.directory /workspace

# Stage the SSH deploy key into /root/.ssh with strict perms. The host
# mounts the key read-only at /tmp/deploy_key; SSH refuses keys whose
# owner UID differs from the runtime user (root here) or whose perms
# aren't 600, so we copy rather than symlink. Idempotent.
if [ -f /tmp/deploy_key ]; then
  mkdir -p /root/.ssh
  chmod 700 /root/.ssh
  install -m 600 /tmp/deploy_key /root/.ssh/id_ed25519
  export GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/root/.ssh/known_hosts"
else
  echo "::warning::no /tmp/deploy_key mounted; git fetch will use host defaults"
fi

BRANCH="${NEURON_DEPLOY_BRANCH:-main}"

git fetch origin "$BRANCH" --quiet || {
  echo "::error::git fetch failed on /workspace"
  exit 1
}
git checkout -B "$BRANCH" "origin/$BRANCH" --quiet
git reset --hard "origin/$BRANCH" --quiet

if [ ! -f deploy.sh ]; then
  echo "::error::deploy.sh missing after reset to origin/$BRANCH"
  git log -1 --oneline
  exit 1
fi

echo "=== Running deploy.sh @ $(git rev-parse --short HEAD) ==="
bash deploy.sh
echo "=== Neuron deploy complete $(date) ==="
