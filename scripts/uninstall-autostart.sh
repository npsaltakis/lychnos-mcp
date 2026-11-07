#!/usr/bin/env bash
# Linux: stop and remove the systemd user service.
set -euo pipefail
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/smarthome-control.service"
systemctl --user disable --now smarthome-control.service 2>/dev/null || true
rm -f "$unit"
systemctl --user daemon-reload
echo "Autostart removed."
