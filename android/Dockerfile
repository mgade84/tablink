# Builds the TabLink Android app. Build context: android/ (see compose.yaml).
# The result is an image holding just the APK (/app-debug.apk), which
# host/Dockerfile copies in.

FROM ubuntu:24.04 AS build
WORKDIR /src
# SDK + Gradle wrapper first, so those layers stay cached across code changes.
COPY scripts/install-sdk.sh scripts/
COPY gradlew gradle.properties settings.gradle.kts build.gradle.kts ./
COPY gradle gradle
RUN scripts/install-sdk.sh && rm -rf /var/lib/apt/lists/*
COPY . .
# Sign with the desktop's debug key (a build secret, never stored in the image) so
# the tablet accepts the APK as an update to an app built outside Docker.
RUN --mount=type=secret,id=debug_keystore,target=/root/.android/debug.keystore,required=true \
    ./gradlew assembleDebug --no-daemon -q

FROM scratch
COPY --from=build /src/app/build/outputs/apk/debug/app-debug.apk /app-debug.apk
