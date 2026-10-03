# Shared by run.sh and daemon.sh. Source it; don't execute it.
# Set ANDROID_SERIAL to pick a tablet when more than one device is attached.

PORT=27183
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the host/ project
# The app to install: a local Android build by default; the Docker image sets its own.
APK="${TABLINK_APK:-$HOST_DIR/../android/app/build/outputs/apk/debug/app-debug.apk}"
PKG="dev.mgade.tablink"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/tablink"
# Shared secret for this host run: `adb reverse` lets every app on the tablet reach
# the port, so the host only accepts the app we launch with this token.
export TABLINK_TOKEN="${TABLINK_TOKEN:-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')}"

# Make sure the app on the attached tablet is current, tunnel the port over
# USB and bring the app to the front.
connect_tablet() {
    # (Re)install when the app is missing or the built APK changed since the last install.
    if [ -f "$APK" ]; then
        local stamp sum
        mkdir -p "$STATE"
        stamp="$STATE/installed-$(adb get-serialno)"
        sum="$(sha256sum "$APK" | cut -d' ' -f1)"
        if ! adb shell pm path "$PKG" >/dev/null 2>&1 || [ "$(cat "$stamp" 2>/dev/null)" != "$sum" ]; then
            echo "Installing app…"
            adb install -r "$APK"
            echo "$sum" > "$stamp"
        fi
    elif ! adb shell pm path "$PKG" >/dev/null 2>&1; then
        echo "App not installed and $APK not built. Build it in android/ with: ./gradlew assembleDebug" >&2
        return 1
    fi

    adb reverse "tcp:$PORT" "tcp:$PORT" >/dev/null
    adb shell input keyevent KEYCODE_WAKEUP
    # Plugging in is a clear "use the tablet", so also reconnect if the desktop
    # user had stopped the previous session (TabLinkService.EXTRA_RECONNECT).
    # An explicit intent: of the tablet apps, only TabLink receives the token.
    adb shell am start -n "$PKG/.MainActivity" --ez dev.mgade.tablink.extra.RECONNECT true \
        --es dev.mgade.tablink.extra.TOKEN "$TABLINK_TOKEN" >/dev/null
}
