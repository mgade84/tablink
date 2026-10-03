# TabLink

<img src="docs/icon.svg" align="right" alt="TabLink icon: a monitor with Tux and a tablet with the Android robot, joined by a cable" width="128" height="128">

Use an Android tablet as an **extended second monitor** for an Ubuntu (GNOME/Wayland) desktop over a USB cable. It is display-only; nothing is sent back from the tablet.

## How it works

```
GNOME virtual monitor ─▶ PipeWire ─▶ VAAPI H.264 ─▶ TCP 127.0.0.1:27183
                                                         │  adb reverse (USB)
Tablet app ◀── MediaCodec (low latency) ◀───────────────┘
```

- `host/` (Python): asks Mutter (`org.gnome.Mutter.ScreenCast.RecordVirtual`) to create a virtual monitor the size of the tablet's screen, encodes it with GStreamer, and serves it on loopback.
- `android/` (Kotlin): a fullscreen app that connects to `127.0.0.1:27183` (forwarded over USB by `adb reverse`), sends its screen size, and decodes the stream onto a `SurfaceView`.

The virtual monitor works like a real one. You can arrange it in **Settings → Displays**, and it goes away when the tablet disconnects.

## Requirements
- Ubuntu 24.04 / GNOME 46 on Wayland (GNOME 44+ should work)
- AMD/Intel GPU with VAAPI. Without it, the host falls back to `x264enc`.
- Android 8.0+ tablet with **Developer options → USB debugging** turned on

## Setup (once)
```bash
host/scripts/install-deps.sh    # adb, GStreamer plugins
android/scripts/install-sdk.sh  # JDK 17, Android SDK, Gradle wrapper (~1.5 GB)
(cd android && ./gradlew assembleDebug)
```
Plug in the tablet and accept the "Allow USB debugging?" prompt. `adb devices` should list it as `device`.

## Run
```bash
host/scripts/run.sh                  # installs the app if needed, tunnels the port, starts the host (--scale 0.5)
host/scripts/run.sh --scale 1        # full tablet resolution (sharpest; set GNOME scale to 200%)
host/scripts/run.sh --position left  # put the tablet left of the main display (also: right, above, below)
host/scripts/run.sh --bitrate 20000 --fps 60 --encoder vaapi
```
Without `--position`, GNOME puts the tablet to the right of your main display. You can still drag it anywhere in Settings → Displays for the current session.

## Start automatically on plug-in
```bash
host/scripts/install-autostart.sh                                 # systemd user service, starts with your session
TABLINK_ARGS="--position left" host/scripts/install-autostart.sh  # same, with custom host options
host/scripts/install-autostart.sh --uninstall
journalctl --user -u tablink -f                                   # logs
```
The service keeps the host listening on loopback and runs `host/scripts/daemon.sh`. Each time the tablet is plugged in, it tunnels the port, wakes the tablet and opens TabLink. Don't use `host/scripts/run.sh` while the service is running, because both want port 27183. Stop the service first with `systemctl --user stop tablink`. If several Android devices are attached, set `ANDROID_SERIAL`.

## Run in Docker
The container replaces the autostart service. It runs the same `host/scripts/daemon.sh`, with the app built in a separate image (`android/Dockerfile`) and copied into the host image (`host/Dockerfile`).
```bash
host/scripts/install-autostart.sh --uninstall  # use one or the other: both want port 27183
loginctl enable-linger "$USER"                 # keep the D-Bus/PipeWire sockets around from boot (see below)
echo 'TABLINK_ARGS=--position left' > .env     # optional host options
docker compose up -d --build
docker compose logs -f
```
The image build signs the app with your `~/.android/debug.keystore`, passed in as a build secret and not stored in the image. That way the tablet accepts it as an update to a copy you built yourself. If you've never built the app outside Docker, create the key first with `(cd android && ./gradlew assembleDebug)`, or with `keytool`.

The container needs to reach your desktop session. `compose.yaml` passes through the session D-Bus and PipeWire sockets, the GPU (`/dev/dri`) for hardware encoding, USB for the tablet, and `~/.android` so the tablet keeps trusting the same adb key. It runs as uid 1000, because D-Bus checks the uid. If your user or the `render` group has a different id, adjust `compose.yaml`.

Those sockets live in `/run/user/1000`, which only exists while you're logged in, unless lingering is enabled. With lingering, Docker can start TabLink at boot. It then waits for the tablet and works once you've logged in to GNOME.

## Checking the host without a tablet
```bash
cd host
python3 -m tablink --selftest 1920x1200  # 5 s, writes selftest.h264
gst-play-1.0 selftest.h264
python3 -m unittest discover -s tests
```

## App icon
`docs/icon.svg` is the only source for the icon. After editing it, run `scripts/gen-icon.py`. That regenerates the Android launcher layers (`ic_launcher_{background,foreground,monochrome}.xml`; the monochrome one is the foreground in a single colour, for themed icons) and `docs/icon-preview.png`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `adb devices` shows `unauthorized` | Unlock the tablet and accept the RSA prompt. Reconnect the cable if no prompt appears. |
| Tablet stuck on "Waiting for host…" | Is `host/scripts/run.sh` running? Check `adb reverse --list`. |
| `ScreenCast.CreateSession failed` | You need a GNOME Wayland session. Check `echo $XDG_SESSION_TYPE`. |
| Choppy or laggy | Lower `--bitrate` or `--scale`. Use a USB 3 cable/port. Run with `-v` to see RTT logs. |
| Static screen shows few frames | That's expected: Mutter only sends frames when something changes, plus a 1 s keepalive. |

## Protocol
Big-endian `[type:u8][len:u32][payload]`. `HELLO` (app→host: w, h, dpi, version), `CONFIG` (host→app: w, h), `VIDEO` (u64 pts µs + Annex-B access unit), `PING`/`PONG`. See `host/tablink/protocol.py` and `android/.../Protocol.kt`.

## License
[MIT](LICENSE) © 2026 mgade84

The app icon (`docs/icon.svg`) includes simplified drawings based on:
- **Tux**, the Linux mascot, originally created by Larry Ewing (lewing@isc.tamu.edu) with The GIMP.
- **The Android robot**, reproduced or modified from work created and shared by Google and used according to terms described in the [Creative Commons 3.0 Attribution License](https://creativecommons.org/licenses/by/3.0/).
