# TabLink

<img src="docs/icon.svg" align="right" alt="TabLink icon: a monitor with Tux and a tablet with the Android robot, joined by a cable" width="128" height="128">

Use an Android tablet as an **extended second monitor** for an Ubuntu (GNOME/Wayland) desktop over a USB cable, with touch input (multi-touch and S Pen) back to the desktop.

## How it works

```
GNOME virtual monitor ─▶ PipeWire ─▶ VAAPI H.264 ─▶ TCP 127.0.0.1:27183
         ▲                                               │  adb reverse (USB)
         │                                               ▼
Mutter RemoteDesktop ◀── touch events ◀──── Tablet app (MediaCodec, low latency)
```

- `host/` (Python): asks Mutter (`org.gnome.Mutter.ScreenCast.RecordVirtual`) to create a virtual monitor the size of the tablet's screen, encodes it with GStreamer, and serves it on loopback. Touches from the tablet are injected into that monitor through a linked `org.gnome.Mutter.RemoteDesktop` session. Its `scripts/` install the app on the tablet whenever it's plugged in and start the host.
- `android/` (Kotlin): a fullscreen app that connects to `127.0.0.1:27183` (forwarded over USB by `adb reverse`), sends its screen size, decodes the stream onto a `SurfaceView`, and sends touches back.

The two folders are independent projects, each with its own scripts and `Dockerfile`. The virtual monitor works like a real one. You can arrange it in **Settings → Displays**, and it goes away when the tablet disconnects.

The tablet works as a touch screen for its part of the desktop. Each finger is a separate touch point, and the S Pen counts as one too. Apps that support touch on Wayland (GNOME apps, Firefox, Chrome) get real touch events, and GNOME turns them into mouse clicks for the rest. While it's connected, GNOME may show a remote-control indicator in the top bar. If you don't want touch, run the host with `--no-touch`.

Whenever no video is showing (waiting for the desktop, connecting, or stopped), the tablet shows a black status screen with the TabLink icon in the middle and the current status just below it.

You can switch to other apps on the tablet without losing the monitor. The connection runs in a foreground service, shown as a "TabLink" notification. While the app is in the background the desktop keeps the virtual monitor and only pauses the video, and the picture comes back when you return. Tap **Disconnect** in the notification to end the session. If the desktop is gone and the app has been in the background for a minute, the service stops by itself.

GNOME's screen-sharing indicator, the pill in the top bar, shows while TabLink is connected. Clicking its **stop** button ends the session for good. The monitor disappears, and the tablet shows "Stopped from the desktop. Tap to reconnect." instead of reconnecting by itself. To start again, tap the tablet screen, press **Reconnect** in the notification, or unplug and replug the tablet. Other disconnects, such as a cable glitch or a host restart, still reconnect automatically. So does a session GNOME closes while the screen is locked.

**Only TabLink can connect.** `adb reverse` makes the port reachable by every app on the tablet, so each host run uses a random token. The scripts pass it to the host and give it to TabLink when they launch it, and the app stores it privately. The host closes any connection without the right token, and only the first such rejection is logged as a warning. If you start TabLink yourself after the host restarted, and it stays on "Waiting for host…", unplug and replug the tablet so it gets the current token.

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
| Follow the logs | `docker compose logs -f`, which Docker caps at 3 × 10 MB. Add `-v` to `TABLINK_ARGS` for latency and round-trip timings. |
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
host/scripts/run.sh --scale 1 --monitor-scale 2  # sharpest text, more latency (see "Sharpness vs latency")
host/scripts/run.sh --position left  # put the tablet left of the main display (also: right, above, below)
host/scripts/run.sh --bitrate 20000 --fps 60 --encoder vaapi
host/scripts/run.sh --no-touch       # display only, no input from the tablet
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

## Sharpness vs latency
By default the tablet gets a monitor at half its resolution (`--scale 0.5`) at GNOME's 100% scale, which the tablet stretches to fill its screen. That gives the lowest latency, but text is a little soft. For sharper text, stream more pixels and let GNOME scale the monitor up so things stay the same size. `--monitor-scale` sets that GNOME scale for the tablet's monitor.

Measured on an AMD Ryzen 4000/5000 laptop iGPU with a 2960×1848 tablet (encode time per frame):

| Options | Stream | Text | Encode | Max fps |
|---|---|---|---|---|
| *(default)* | 1480×924 | soft | ~8 ms | 60 |
| `--scale 0.75 --monitor-scale 1.5` | 2220×1386 | sharper | ~18 ms | ~55 |
| `--scale 1 --monitor-scale 2` | 2960×1848 | pixel-perfect | ~30 ms | ~33 |

All three give the same text size. Faster GPUs encode quicker, so it's worth trying on yours. Put the options in `TABLINK_ARGS` (`.env` for Docker), and consider a higher `--bitrate` for the larger sizes. Fractional scales such as 1.5 need GNOME's fractional scaling. If a scale isn't supported, TabLink uses the closest supported one and logs a warning.

## Checking the host without a tablet
```bash
cd host
python3 -m tablink --selftest 1920x1200  # 5 s, writes selftest.h264
gst-play-1.0 selftest.h264
python3 -m unittest discover -s tests
```

## App icon
`docs/icon.svg` is the only source for the icon. After editing it, run `scripts/gen-icon.py`. That regenerates the Android launcher layers (`ic_launcher_{background,foreground,monochrome}.xml`; the monochrome one is the foreground in a single colour, for themed icons) and `docs/icon-preview.png`. The app uses the launcher icon on its status screens too, and the monochrome layer as the notification icon, so all of them update together.

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
| Choppy or laggy | Lower `--bitrate` or `--scale`. Use a USB 3 cable/port. Run with `-v` for encoder latency and round-trip timings, and use `adb logcat -s Decoder:D` for decode timings. |
| Static screen shows few frames | That's expected: Mutter only sends frames when something changes, plus a 1 s keepalive. |

## Protocol
Big-endian `[type:u8][len:u32][payload]`. `HELLO` (app→host: w, h, dpi, version 2, then the session token), `CONFIG` (host→app: w, h), `STOPPED` (host→app: the desktop user pressed GNOME's stop button; the app waits until asked to reconnect), `VISIBILITY` (app→host: u8; 0 pauses video while the app is in the background, 1 resumes on a keyframe), `VIDEO` (u64 pts µs + Annex-B access unit), `TOUCH` (app→host: u8 action 0 down/1 move/2 up, u8 slot, f32 x, f32 y in monitor pixels), `PING`/`PONG`. See `host/tablink/protocol.py` and `android/.../Protocol.kt`.

## License
[MIT](LICENSE) © 2026 mgade84

The app icon (`docs/icon.svg`) includes simplified drawings based on:
- **Tux**, the Linux mascot, originally created by Larry Ewing (lewing@isc.tamu.edu) with The GIMP.
- **The Android robot**, reproduced or modified from work created and shared by Google and used according to terms described in the [Creative Commons 3.0 Attribution License](https://creativecommons.org/licenses/by/3.0/).
