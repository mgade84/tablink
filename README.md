# TabLink

<img src="docs/icon.svg" align="right" alt="TabLink icon: a monitor with Tux and a tablet with the Android robot, joined by a cable" width="128" height="128">

Use an Android tablet as an **extended second monitor** for an Ubuntu (GNOME/Wayland) desktop over a USB cable. It is display-only; nothing is sent back from the tablet.

## How it works

```
GNOME virtual monitor ─▶ PipeWire ─▶ VAAPI H.264 ─▶ TCP 127.0.0.1:27183
                                                         │  adb reverse (USB)
Tablet app ◀── MediaCodec (low latency) ◀───────────────┘
```

- `host/` (Python): asks Mutter (`org.gnome.Mutter.ScreenCast.RecordVirtual`) to create a virtual monitor the size of the tablet's screen, encodes it with GStreamer, and serves it on loopback. Its `scripts/` install the app on the tablet whenever it's plugged in and start the host.
- `android/` (Kotlin): a fullscreen app that connects to `127.0.0.1:27183` (forwarded over USB by `adb reverse`), sends its screen size, and decodes the stream onto a `SurfaceView`.

The two folders are independent projects, each with its own scripts and `Dockerfile`. The virtual monitor works like a real one. You can arrange it in **Settings → Displays**, and it goes away when the tablet disconnects.

## Requirements
- Ubuntu 24.04 / GNOME 46 on Wayland (GNOME 44+ should work)
- AMD/Intel GPU with VAAPI. Without it, the host falls back to `x264enc`.
- Android 8.0+ tablet with **Developer options → USB debugging** turned on. Plug it in and accept the "Allow USB debugging?" prompt.

There are two ways to run TabLink. Pick one: both use port 27183.

## Run with Docker
```bash
loginctl enable-linger "$USER"               # lets Docker start TabLink at boot (see below)
echo 'TABLINK_ARGS=--position left' > .env   # optional host options, e.g. --position, --scale, --bitrate
docker compose up -d --build                 # build both images and start
```
TabLink then runs in the background and restarts with Docker. Plug in the tablet: the app opens and the new display appears.

| Task | Command |
|---|---|
| Follow the logs | `docker compose logs -f` |
| Update after `git pull` | `docker compose up -d --build` |
| Restart (e.g. after editing `.env`) | `docker compose up -d` |
| Stop / remove | `docker compose stop` / `docker compose down` |

**How it's built.** `compose.yaml` builds two images:
- `tablink-app` comes from `android/Dockerfile`. It holds only the APK. The build signs it with your `~/.android/debug.keystore`, passed in as a build secret that isn't stored in either image, so the tablet accepts it as an update to a copy you built yourself. The key exists once you've built the app outside Docker (`cd android && ./gradlew assembleDebug`). Otherwise create one with `keytool -genkeypair -keystore ~/.android/debug.keystore -alias androiddebugkey -storepass android -keypass android -keyalg RSA -validity 10000 -dname "CN=Android Debug,O=Android,C=US"`.
- `tablink` comes from `host/Dockerfile`. It runs `host/scripts/daemon.sh`, the same plug-in watcher the native autostart uses, with the APK copied in from `tablink-app`. The `app` service exists only to build that image and never starts a container.

**What the container can reach.** It works with your desktop session, so `compose.yaml` passes in:

| Access | Why |
|---|---|
| `/run/user/1000/bus` (session D-Bus) | Ask GNOME for the virtual monitor and place it |
| `/run/user/1000/pipewire-0` | Receive the virtual monitor's video |
| `/dev/dri` + `video`/`render` groups | Hardware H.264 encoding |
| `/dev/bus/usb` + `plugdev` group | Talk to the tablet over adb |
| `~/.android` | Reuse your adb key, so the tablet doesn't ask to authorise a new computer |
| host network | `adb reverse` reaches the host on 127.0.0.1:27183 |
| `apparmor=unconfined` | Docker's default AppArmor profile blocks the session D-Bus |

It runs as uid/gid 1000, because D-Bus checks the uid. If your user, or the `render` group (`getent group render`), has a different id, change `compose.yaml`.

**Starting at boot.** The D-Bus and PipeWire sockets live in `/run/user/1000`, which only exists while you're logged in unless lingering is enabled. With lingering, Docker starts TabLink at boot. It waits for the tablet and works once you've logged in to GNOME. Without lingering, run `docker compose up -d` after logging in.

## Run natively
Setup (once):
```bash
host/scripts/install-deps.sh    # adb, GStreamer plugins
android/scripts/install-sdk.sh  # JDK 17, Android SDK, Gradle wrapper (~1.5 GB)
(cd android && ./gradlew assembleDebug)
```
Run it in a terminal:
```bash
host/scripts/run.sh                  # installs the app if needed, tunnels the port, starts the host (--scale 0.5)
host/scripts/run.sh --scale 1        # full tablet resolution (sharpest; set GNOME scale to 200%)
host/scripts/run.sh --position left  # put the tablet left of the main display (also: right, above, below)
host/scripts/run.sh --bitrate 20000 --fps 60 --encoder vaapi
```
Without `--position`, GNOME puts the tablet to the right of your main display. You can still drag it anywhere in Settings → Displays for the current session.

Or start it automatically on plug-in, as a systemd user service:
```bash
host/scripts/install-autostart.sh                                 # starts with your session
TABLINK_ARGS="--position left" host/scripts/install-autostart.sh  # same, with custom host options
host/scripts/install-autostart.sh --uninstall
journalctl --user -u tablink -f                                   # logs
```
The service keeps the host listening on loopback and runs `host/scripts/daemon.sh`. Each time the tablet is plugged in, it tunnels the port, wakes the tablet and opens TabLink. Stop it (`systemctl --user stop tablink`) before using `run.sh`. If several Android devices are attached, set `ANDROID_SERIAL`.

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
| Tablet stuck on "Waiting for host…" | Is TabLink running (`docker compose ps`, the service, or `run.sh`)? Check `adb reverse --list`. |
| Docker build: `secret debug_keystore: not found` | `~/.android/debug.keystore` is missing. Create it (see "How it's built"). |
| Docker: `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | The installed app was signed with a different key. Run `adb uninstall dev.mgade.tablink` once. |
| Docker: `AccessDenied … AppArmor` in the logs | Keep `apparmor=unconfined` in `compose.yaml`. |
| `address already in use` on port 27183 | Only run one of: the Docker container, the systemd service, or `run.sh`. |
| `ScreenCast.CreateSession failed` | You need a GNOME Wayland session (`echo $XDG_SESSION_TYPE`). "Session creation inhibited" means the screen is locked; it works once you unlock. |
| Choppy or laggy | Lower `--bitrate` or `--scale`. Use a USB 3 cable/port. Run with `-v` to see RTT logs. |
| Static screen shows few frames | That's expected: Mutter only sends frames when something changes, plus a 1 s keepalive. |

## Protocol
Big-endian `[type:u8][len:u32][payload]`. `HELLO` (app→host: w, h, dpi, version), `CONFIG` (host→app: w, h), `VIDEO` (u64 pts µs + Annex-B access unit), `PING`/`PONG`. See `host/tablink/protocol.py` and `android/.../Protocol.kt`.

## License
[MIT](LICENSE) © 2026 mgade84

The app icon (`docs/icon.svg`) includes simplified drawings based on:
- **Tux**, the Linux mascot, originally created by Larry Ewing (lewing@isc.tamu.edu) with The GIMP.
- **The Android robot**, reproduced or modified from work created and shared by Google and used according to terms described in the [Creative Commons 3.0 Attribution License](https://creativecommons.org/licenses/by/3.0/).
