# Shared by run.sh and daemon.sh. Source it; don't execute it.
# Set ANDROID_SERIAL to pick a tablet when more than one device is attached.

PORT=27183
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK="$ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
PKG="dev.mgade.tablink"

# Make sure the app on the attached tablet is current, tunnel the port over
# USB and bring the app to the front.
connect_tablet() {
    # (Re)install when the app is missing or the built APK changed since the last install.
    if [ -f "$APK" ]; then
        local stamp sum
        stamp="$ROOT/android/app/build/.installed-$(adb get-serialno)"
        sum="$(sha256sum "$APK" | cut -d' ' -f1)"
        if ! adb shell pm path "$PKG" >/dev/null 2>&1 || [ "$(cat "$stamp" 2>/dev/null)" != "$sum" ]; then
            echo "Installing app…"
            adb install -r "$APK"
            echo "$sum" > "$stamp"
        fi
    elif ! adb shell pm path "$PKG" >/dev/null 2>&1; then
        echo "App not installed and $APK not built. Run: (cd android && ./gradlew assembleDebug)" >&2
        return 1
    fi

    adb reverse "tcp:$PORT" "tcp:$PORT"
    adb shell input keyevent KEYCODE_WAKEUP
    adb shell am start -n "$PKG/.MainActivity" >/dev/null
}
