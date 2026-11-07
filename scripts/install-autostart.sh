#!/usr/bin/env bash
# Linux: run the backend as a systemd *user* service that starts when you log in
# (has access to your desktop keyring) and restarts if it crashes.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
unit="$unit_dir/smarthome-control.service"

if [[ ! -x "$root/.venv/bin/python" ]]; then
  echo "Python environment not found at $root/.venv. See README -> Quick start." >&2
  exit 1
fi

mkdir -p "$unit_dir"
cat > "$unit" <<EOF
[Unit]
Description=Smart Home Control backend
After=network-online.target

[Service]
WorkingDirectory=$root/backend
ExecStart=$root/.venv/bin/python -m app
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now smarthome-control.service
echo "Installed $unit"
systemctl --user --no-pager status smarthome-control.service | head -n 5 || true
echo
echo "Manage it with: systemctl --user {status|stop|start|restart} smarthome-control"
echo "Logs:           journalctl --user -u smarthome-control -f"
