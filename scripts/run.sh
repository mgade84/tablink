#!/usr/bin/env bash
# Wait for the tablet, tunnel the port over USB, launch the app, run the host.
# Defaults to --scale 0.5 (readable text at 100% GNOME scaling on high-DPI tablets).
# Extra arguments go to the host and override it, e.g.:  scripts/run.sh --scale 1 --bitrate 20000
# For starting automatically on plug-in, see scripts/install-autostart.sh.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

echo "Waiting for tablet (USB debugging must be enabled and authorized)…"
adb wait-for-usb-device
connect_tablet
trap 'adb reverse --remove "tcp:$PORT" 2>/dev/null || true' EXIT

cd "$ROOT/host"
python3 -m tablink --port "$PORT" --scale 0.5 "$@"
