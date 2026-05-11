#!/bin/bash
# Neuron-only deploy script — runs inside the neuron-deployer container
# when the webhook fires. Self-heals the /workspace checkout to the
# deploy branch tip and then invokes neuron-platform/deploy.sh.
#
# This script NEVER touches a ShitalEco service. It runs `git fetch +
# reset --hard` against /workspace (the bind-mounted repo), and then
# the underlying neuron-platform/deploy.sh restarts only the Neuron
# Docker stack. The shared nginx is only ever asked for a SIGHUP.
set -eo pipefail

LOG=/tmp/neuron-deploy-$(date +%s).log
exec >> "$LOG" 2>&1

echo "=== Neuron deploy started $(date) ==="
cd /workspace

BRANCH="${NEURON_DEPLOY_BRANCH:-claude/shital-erp-platform-iR2UF}"

git fetch origin "$BRANCH" --quiet || {
  echo "::error::git fetch failed on /workspace"
  exit 1
}
git checkout -B "$BRANCH" "origin/$BRANCH" --quiet
git reset --hard "origin/$BRANCH" --quiet

if [ ! -f neuron-platform/deploy.sh ]; then
  echo "::error::neuron-platform/deploy.sh missing after reset to origin/$BRANCH"
  git log -1 --oneline
  exit 1
fi

echo "=== Running neuron-platform/deploy.sh @ $(git rev-parse --short HEAD) ==="
bash neuron-platform/deploy.sh
echo "=== Neuron deploy complete $(date) ==="
