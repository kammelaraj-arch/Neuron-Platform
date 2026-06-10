#!/usr/bin/env bash
# Restore the Neuron Master from a backup archive produced by
# neuron-backup.sh. Usage:
#
#   sudo bash restore.sh /opt/neuron-backups/neuron-20260616-031700Z.tgz
#
# Stops neuron-master, swaps in the snapshot DB + data dir, starts
# it back up. The current volume is moved aside to data-pre-restore-*
# so a botched restore is reversible.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Must run as root (use sudo)" >&2
  exit 1
fi
if [ -z "${1:-}" ] || [ ! -f "$1" ]; then
  echo "Usage: $0 <path-to-neuron-*.tgz>" >&2
  exit 2
fi

ARCHIVE=$(readlink -f "$1")
COMPOSE_FILE=${NEURON_COMPOSE_FILE:-/opt/neuron-platform/master_platform/docker-compose.yml}
SERVICE=${NEURON_MASTER_SERVICE:-neuron-master}
ts=$(date -u +%Y%m%d-%H%M%SZ)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "[restore] using $ARCHIVE"
tar -xzf "$ARCHIVE" -C "$work"

if [ ! -f "$work/neuron.db" ] || [ ! -f "$work/data.tgz" ]; then
  echo "ERROR: archive doesn't contain neuron.db + data.tgz" >&2
  exit 3
fi

echo "[restore] stopping $SERVICE"
docker compose -f "$COMPOSE_FILE" stop "$SERVICE"

# Snapshot the live volume first.
docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T "$SERVICE" \
    sh -c "cd / && tar -czf /tmp/pre-restore-$ts.tgz app/master_platform/data && cp /tmp/pre-restore-$ts.tgz /app/master_platform/data/" || true

echo "[restore] copying snapshot into the volume"
docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T \
    -v "$work":/restore "$SERVICE" \
    sh -c "rm -rf /app/master_platform/data/* && tar -xzf /restore/data.tgz -C /app/master_platform/data && cp /restore/neuron.db /app/master_platform/data/neuron.db"

echo "[restore] starting $SERVICE"
docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
echo "[restore] follow logs:"
echo "  docker compose -f $COMPOSE_FILE logs -f $SERVICE"
