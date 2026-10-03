#!/usr/bin/env bash
# Desktop-side dependencies: adb, GStreamer (VAAPI encoder + h264parse), PyGObject.
# Also used by host/Dockerfile (runs as root there, so no sudo).
set -euo pipefail
SUDO=sudo; [ "$(id -u)" -eq 0 ] && SUDO=
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update
$SUDO apt-get install -y \
    adb \
    mesa-va-drivers \
    python3-gi gir1.2-gstreamer-1.0 \
    gstreamer1.0-pipewire gstreamer1.0-vaapi \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
echo "Host dependencies installed."
