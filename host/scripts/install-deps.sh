#!/usr/bin/env bash
# Desktop-side dependencies: adb, GStreamer (PipeWire capture, VAAPI encoder, x264
# fallback), PyGObject. gstreamer1.0-plugins-bad isn't needed (see pipeline.py).
# Also used by host/Dockerfile (runs as root there, so no sudo; NO_RECOMMENDS=1 keeps
# the image small by skipping recommended packages).
set -euo pipefail
SUDO=sudo; [ "$(id -u)" -eq 0 ] && SUDO=
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update
$SUDO apt-get install -y ${NO_RECOMMENDS:+--no-install-recommends} \
    adb \
    mesa-va-drivers \
    python3-gi gir1.2-gstreamer-1.0 \
    gstreamer1.0-pipewire gstreamer1.0-vaapi \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-ugly
echo "Host dependencies installed."
