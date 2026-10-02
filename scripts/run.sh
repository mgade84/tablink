#!/usr/bin/env bash
# Wait for the tablet, tunnel the port over USB, launch the app, run the host.
# Defaults to --scale 0.5 (readable text at 100% GNOME scaling on high-DPI tablets).
# Extra arguments go to the host and override it, e.g.:  scripts/run.sh --scale 1 --bitrate 20000
set -euo pipefail

PORT=27183
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APK="$ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
PKG="dev.mgade.tablink"

echo "Waiting for tablet (USB debugging must be enabled and authorized)…"
adb wait-for-device

# (Re)install when the app is missing or the built APK changed since the last install.
if [ -f "$APK" ]; then
    STAMP="$ROOT/android/app/build/.installed-$(adb get-serialno)"
    SUM="$(sha256sum "$APK" | cut -d' ' -f1)"
    if ! adb shell pm path "$PKG" >/dev/null 2>&1 || [ "$(cat "$STAMP" 2>/dev/null)" != "$SUM" ]; then
        echo "Installing app…"
        adb install -r "$APK"
        echo "$SUM" > "$STAMP"
    fi
elif ! adb shell pm path "$PKG" >/dev/null 2>&1; then
    echo "App not installed and $APK not built. Run: (cd android && ./gradlew assembleDebug)" >&2
    exit 1
fi

adb reverse "tcp:$PORT" "tcp:$PORT"
trap 'adb reverse --remove "tcp:$PORT" 2>/dev/null || true' EXIT
adb shell am start -n "$PKG/.MainActivity" >/dev/null

cd "$ROOT/host"
python3 -m tablink --port "$PORT" --scale 0.5 "$@"
