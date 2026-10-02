#!/usr/bin/env bash
# Desktop-side dependencies: adb, GStreamer (VAAPI encoder + h264parse), PyGObject.
set -euo pipefail
sudo apt-get update
sudo apt-get install -y \
    adb git unzip curl \
    python3-gi gir1.2-gstreamer-1.0 \
    gstreamer1.0-pipewire gstreamer1.0-vaapi \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
echo "Host dependencies installed."
