#!/usr/bin/env bash
# Toolchain to build the tablet app: JDK 17, Android SDK (cmdline-tools), Gradle wrapper.
set -euo pipefail

SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
CMDLINE_TOOLS_ZIP="commandlinetools-linux-11076708_latest.zip"
GRADLE_VERSION="8.11.1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

sudo apt-get install -y openjdk-17-jdk-headless unzip curl

if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
    echo "Installing Android cmdline-tools into $SDK"
    tmp="$(mktemp -d)"
    curl -fL -o "$tmp/tools.zip" "https://dl.google.com/android/repository/$CMDLINE_TOOLS_ZIP"
    unzip -q "$tmp/tools.zip" -d "$tmp"
    mkdir -p "$SDK/cmdline-tools"
    rm -rf "$SDK/cmdline-tools/latest"
    mv "$tmp/cmdline-tools" "$SDK/cmdline-tools/latest"
    rm -rf "$tmp"
fi

SDKMANAGER="$SDK/cmdline-tools/latest/bin/sdkmanager"
yes | "$SDKMANAGER" --sdk_root="$SDK" --licenses >/dev/null || true
"$SDKMANAGER" --sdk_root="$SDK" "platforms;android-35" "build-tools;35.0.0" "platform-tools"

echo "sdk.dir=$SDK" > "$ROOT/android/local.properties"

if [ ! -f "$ROOT/android/gradle/wrapper/gradle-wrapper.jar" ]; then
    echo "Generating Gradle $GRADLE_VERSION wrapper"
    tmp="$(mktemp -d)"
    curl -fL -o "$tmp/gradle.zip" "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
    unzip -q "$tmp/gradle.zip" -d "$tmp"
    (cd "$ROOT/android" && "$tmp/gradle-$GRADLE_VERSION/bin/gradle" wrapper --gradle-version "$GRADLE_VERSION" --no-daemon)
    rm -rf "$tmp"
fi

echo
echo "Done. Build the app with:  (cd android && ./gradlew assembleDebug)"
echo "Add to your shell profile:  export ANDROID_HOME=$SDK"
