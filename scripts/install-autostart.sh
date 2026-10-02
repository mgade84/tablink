#!/usr/bin/env bash
# Install TabLink as a systemd user service that starts with your desktop
# session and connects the tablet whenever it is plugged in.
#   scripts/install-autostart.sh              install + start
#   scripts/install-autostart.sh --uninstall  stop + remove
# Logs: journalctl --user -u tablink -f
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT="$HOME/.config/systemd/user/tablink.service"

if [ "${1:-}" = "--uninstall" ]; then
    systemctl --user disable --now tablink.service 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload
    echo "TabLink autostart removed."
    exit 0
fi

mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<UNIT
[Unit]
Description=TabLink (Android tablet as a USB monitor)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=$ROOT/scripts/daemon.sh ${TABLINK_ARGS:-}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now tablink.service
echo "Installed $UNIT"
echo "TabLink now starts with your session. Logs: journalctl --user -u tablink -f"
