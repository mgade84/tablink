#!/usr/bin/env bash
# Long-running mode used by the systemd user service (install-autostart.sh) and Docker:
# keeps the host listening and connects the tablet every time it is plugged in.
# Arguments go to the host, like run.sh.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

cd "$HOST_DIR"
python3 -m tablink --port "$PORT" --scale 0.5 "$@" &
HOST_PID=$!
trap 'kill $HOST_PID 2>/dev/null || true' EXIT

while kill -0 "$HOST_PID" 2>/dev/null; do
    echo "Waiting for tablet…"
    adb wait-for-usb-device
    echo "Tablet connected ($(adb get-serialno))"
    # A failed connect (e.g. unplugged mid-way) just waits for the next plug-in.
    connect_tablet || echo "Could not set up the tablet; will retry on next plug-in" >&2
    adb wait-for-usb-disconnect
    echo "Tablet disconnected"
done
# Host exited: return its status so systemd restarts us.
wait "$HOST_PID"
