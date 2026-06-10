#!/usr/bin/env bash
# Neuron Master nightly backup.
#
# Pulls a consistent point-in-time snapshot of the SQLite DB via
# sqlite3's online .backup, then bundles the data dir (audit logs,
# alexa state, bootstrap files, etc.) into one tar.gz so a fresh
# Master can be rehydrated from a single artefact.
#
# Optional S3-compatible off-site upload when NEURON_BACKUP_S3_URL is
# set in the env file. Without it, backups stay on the host in
# $BACKUP_DIR and survive container rebuilds (compose volume is
# untouched) but not full host loss.
#
# Schedule via the companion systemd unit + timer in this directory.

set -euo pipefail

COMPOSE_FILE=${NEURON_COMPOSE_FILE:-/opt/neuron-platform/master_platform/docker-compose.yml}
SERVICE=${NEURON_MASTER_SERVICE:-neuron-master}
BACKUP_DIR=${NEURON_BACKUP_DIR:-/opt/neuron-backups}
KEEP=${NEURON_BACKUP_KEEP:-14}    # nightly backups to retain (~2 weeks)

mkdir -p "$BACKUP_DIR"
ts=$(date -u +%Y%m%d-%H%M%SZ)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "[neuron-backup] $ts — starting"

# 1. Online SQLite snapshot — point-in-time consistent even under
#    write load. Output written inside the container to a fresh
#    file so we don't risk corrupting the live DB.
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" \
    sqlite3 data/neuron.db ".backup '/tmp/neuron-backup.db'"

# 2. Pull the snapshot + the rest of data/ (logs, alexa state,
#    bootstrap files) out of the container to a staging dir.
docker compose -f "$COMPOSE_FILE" cp "$SERVICE:/tmp/neuron-backup.db" \
    "$work/neuron.db"
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" \
    rm -f /tmp/neuron-backup.db

# Bundle the rest of the data directory alongside (excluding the
# live neuron.db — we already grabbed a clean snapshot of that).
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" \
    tar -C data -czf - \
        --exclude='neuron.db' --exclude='neuron.db-wal' --exclude='neuron.db-shm' \
        . \
    > "$work/data.tgz"

# 3. Roll everything into one archive.
out="$BACKUP_DIR/neuron-$ts.tgz"
tar -C "$work" -czf "$out" neuron.db data.tgz
size=$(stat -c%s "$out")
echo "[neuron-backup] $out  size=$size bytes"

# 4. Optional off-site upload — S3-compatible (Vultr Object Storage,
#    Backblaze B2, AWS S3, DigitalOcean Spaces).
if [ -n "${NEURON_BACKUP_S3_URL:-}" ]; then
    if command -v aws >/dev/null 2>&1; then
        aws s3 cp "$out" "$NEURON_BACKUP_S3_URL/" --quiet
        echo "[neuron-backup] uploaded → $NEURON_BACKUP_S3_URL/$(basename "$out")"
    elif command -v rclone >/dev/null 2>&1; then
        rclone copy "$out" "$NEURON_BACKUP_S3_URL/" --quiet || true
        echo "[neuron-backup] uploaded (rclone) → $NEURON_BACKUP_S3_URL/"
    else
        echo "[neuron-backup] WARN: NEURON_BACKUP_S3_URL set but neither aws nor rclone is installed"
    fi
fi

# 5. Retention sweep — keep the last $KEEP archives, drop the rest.
ls -1t "$BACKUP_DIR"/neuron-*.tgz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
    echo "[neuron-backup] pruned $old"
done

echo "[neuron-backup] $ts — done"
