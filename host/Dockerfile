# Runs the TabLink host: scripts/daemon.sh (host + plug-in watcher).
# Build context: host/. The APK comes from the "app" build context, i.e. the
# image built by android/Dockerfile (see compose.yaml for the devices and
# sockets the container needs).

FROM ubuntu:24.04
COPY scripts/install-deps.sh /opt/tablink/scripts/
RUN /opt/tablink/scripts/install-deps.sh && rm -rf /var/lib/apt/lists/*
COPY . /opt/tablink
COPY --from=app /app-debug.apk /opt/tablink/app-debug.apk
# adb keys and lib.sh's install stamp live under $HOME.
RUN mkdir -p /home/tablink && chmod 1777 /home/tablink

ENV HOME=/home/tablink \
    TABLINK_APK=/opt/tablink/app-debug.apk \
    XDG_RUNTIME_DIR=/run/user/1000 \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
    PYTHONDONTWRITEBYTECODE=1
# Must match the desktop user: D-Bus authenticates by uid.
USER 1000:1000
ENTRYPOINT ["/opt/tablink/scripts/daemon.sh"]
