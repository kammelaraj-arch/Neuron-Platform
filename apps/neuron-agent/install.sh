#!/usr/bin/env bash
# Install the Neuron pull-agent as a systemd service on a Raspberry Pi.
# Run as root:    sudo bash install.sh
set -euo pipefail

INSTALL_DIR=/opt/neuron-agent
ENV_FILE=/etc/default/neuron-agent
UNIT_FILE=/etc/systemd/system/neuron-agent.service
SCRIPT_SRC="$(dirname "$(readlink -f "$0")")/neuron_agent.py"

if [ "$EUID" -ne 0 ]; then
  echo "Must run as root (use sudo)" >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
install -m 0755 "$SCRIPT_SRC" "$INSTALL_DIR/neuron_agent.py"

if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# Neuron pull-agent configuration. Fill in the three required values
# from your Neuron Master's /ui/plotter page, then:
#   sudo systemctl restart neuron-agent
NEURON_MASTER_URL=https://neuron.shital.org.uk
NEURON_DEVICE_ID=
NEURON_AGENT_TOKEN=

# Optional — only set if your local SmartPlotter listens on a non-default
# URL or enforces an API key.
NEURON_LOCAL_BASE=http://127.0.0.1:5001
# NEURON_LOCAL_API_KEY=
EOF
  chmod 0600 "$ENV_FILE"
  echo "Created $ENV_FILE — fill in DEVICE_ID + AGENT_TOKEN before restarting."
fi

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Neuron pull-agent (polls Master for queued commands)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $INSTALL_DIR/neuron_agent.py
Restart=on-failure
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable neuron-agent.service
systemctl restart neuron-agent.service
echo
echo "neuron-agent installed. Live logs:"
echo "  journalctl -u neuron-agent -f"
