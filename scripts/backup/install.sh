#!/usr/bin/env bash
# Install the Neuron Master backup script + systemd timer.
# Run as root on the VPS:    sudo bash install.sh
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "Must run as root (use sudo)" >&2
  exit 1
fi

HERE=$(dirname "$(readlink -f "$0")")

install -m 0755 "$HERE/neuron-backup.sh"      /usr/local/bin/neuron-backup.sh
install -m 0644 "$HERE/neuron-backup.service" /etc/systemd/system/neuron-backup.service
install -m 0644 "$HERE/neuron-backup.timer"   /etc/systemd/system/neuron-backup.timer

if [ ! -f /etc/default/neuron-backup ]; then
  cat > /etc/default/neuron-backup <<'EOF'
# Override any of these to tune the nightly backup.
# NEURON_COMPOSE_FILE=/opt/neuron-platform/master_platform/docker-compose.yml
# NEURON_MASTER_SERVICE=neuron-master
# NEURON_BACKUP_DIR=/opt/neuron-backups
# NEURON_BACKUP_KEEP=14
# Off-site (one of):
#   NEURON_BACKUP_S3_URL=s3://my-bucket/neuron      # requires `aws` CLI configured
#   NEURON_BACKUP_S3_URL=vultr-objstor:my-bucket/neuron  # rclone remote
EOF
  chmod 0600 /etc/default/neuron-backup
fi

systemctl daemon-reload
systemctl enable --now neuron-backup.timer
echo
echo "Installed. First run will be at the next scheduled time:"
systemctl list-timers neuron-backup.timer --no-pager
echo
echo "Run one immediately to test:"
echo "  sudo systemctl start neuron-backup.service && sudo journalctl -u neuron-backup -e -n 40"
