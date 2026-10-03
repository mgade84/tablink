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
scripts/install-host-deps.sh        # adb, GStreamer plugins
scripts/install-android-sdk.sh      # JDK 17, Android SDK, Gradle wrapper (~1.5 GB)
(cd android && ./gradlew assembleDebug)
```
Plug in the tablet and accept the "Allow USB debugging?" prompt. `adb devices` should list it as `device`.

## Run
```bash
scripts/run.sh                      # installs the app if needed, tunnels the port, starts the host (--scale 0.5)
scripts/run.sh --scale 1            # full tablet resolution (sharpest; set GNOME scale to 200%)
scripts/run.sh --position left       # put the tablet left of the main display (also: right, above, below)
scripts/run.sh --bitrate 20000 --fps 60 --encoder vaapi
```
Without `--position`, GNOME puts the tablet to the right of your main display. You can still drag it anywhere in Settings → Displays for the current session.

## Start automatically on plug-in
```bash
scripts/install-autostart.sh                 # systemd user service, starts with your session
TABLINK_ARGS="--position left" scripts/install-autostart.sh   # same, with custom host options
scripts/install-autostart.sh --uninstall
journalctl --user -u tablink -f              # logs
```
The service keeps the host listening on loopback and runs `scripts/daemon.sh`. Each time the tablet is plugged in, it tunnels the port, wakes the tablet and opens TabLink. Don't use `scripts/run.sh` while the service is running, because both want port 27183. Stop the service first with `systemctl --user stop tablink`. If several Android devices are attached, set `ANDROID_SERIAL`.

## Checking the host without a tablet
```bash
cd host
python3 -m tablink --selftest 1920x1200   # 5 s, writes selftest.h264
gst-play-1.0 selftest.h264
python3 -m unittest discover -s tests
```

## App icon
`docs/icon.svg` is the only source for the icon. After editing it, run `scripts/gen-icon.py`. That regenerates the Android launcher layers (`ic_launcher_{background,foreground,monochrome}.xml`; the monochrome one is the foreground in a single colour, for themed icons) and `docs/icon-preview.png`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `adb devices` shows `unauthorized` | Unlock the tablet and accept the RSA prompt. Reconnect the cable if no prompt appears. |
| Tablet stuck on "Waiting for host…" | Is `scripts/run.sh` running? Check `adb reverse --list`. |
| `ScreenCast.CreateSession failed` | You need a GNOME Wayland session. Check `echo $XDG_SESSION_TYPE`. |
| Choppy or laggy | Lower `--bitrate` or `--scale`. Use a USB 3 cable/port. Run with `-v` to see RTT logs. |
| Static screen shows few frames | That's expected: Mutter only sends frames when something changes, plus a 1 s keepalive. |

## Protocol
Big-endian `[type:u8][len:u32][payload]`. `HELLO` (app→host: w, h, dpi, version), `CONFIG` (host→app: w, h), `VIDEO` (u64 pts µs + Annex-B access unit), `PING`/`PONG`. See `host/tablink/protocol.py` and `android/.../Protocol.kt`.
