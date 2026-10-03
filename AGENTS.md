# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, etc.) working on TabLink. For what the project does and how to set it up and run it, read [README.md](README.md). This file doesn't repeat that.

## Principles
- **Reuse, don't duplicate.** Reference existing code and assets instead of copying them or adding variant files. If a variant really seems necessary, generate it from the single source or ask first.
- **Single sources of truth:**
  - Wire protocol: `host/tablink/protocol.py` ⇄ `android/.../Protocol.kt`. These are the one deliberate mirror (two languages). Change both together and bump `PROTO_VERSION` / `VERSION` for incompatible changes.
  - App icon: `docs/icon.svg` → `scripts/gen-icon.py` → `res/drawable/ic_launcher_*.xml` and `docs/icon-preview.png`. Never hand-edit the generated files. The app reuses them: `@mipmap/ic_launcher` on the status screen, `@drawable/ic_launcher_monochrome` as the notification icon.
  - Tablet connect steps: `host/scripts/lib.sh` (`connect_tablet`), used by both `run.sh` and `daemon.sh`. It also creates the per-run session token (`TABLINK_TOKEN`).
- Keep it dependency-light. The host uses only system Python + PyGObject (`gi`): no pip packages, and no `GstVideo` typelib, which isn't installed. The app has no AndroidX or other libraries.

## Layout
- `host/tablink/` (Python, runs on the Ubuntu desktop)
  - `mutter.py`: `org.gnome.Mutter.ScreenCast` `RecordVirtual` creates the virtual monitor. With touch (the default), the ScreenCast session is linked to an `org.gnome.Mutter.RemoteDesktop` session that injects `NotifyTouch*` events relative to the stream.
  - `pipeline.py`: GStreamer `pipewiresrc` → VAAPI (or x264) H.264 → appsink
  - `server.py`: loopback TCP server, threads + GLib main loop. Each connection gets its own thread and must authenticate within `HELLO_TIMEOUT` (1 s). One session streams at a time: an authenticated client takes over and waits for the old session to finish (`Server._takeover`).
  - `display.py`: `org.gnome.Mutter.DisplayConfig` places the monitor (`--position`) and sets its GNOME scale (`--monitor-scale`). Without `--position`, a scale change keeps the monitor on the side it's already on. Unsupported scales snap to the nearest one Mutter lists.
  - `protocol.py`, `__main__.py` (CLI, `--selftest`)
- `android/app/src/main/java/dev/mgade/tablink/`
  - `TabLinkService`: foreground service (`connectedDevice`) that owns the connection, so the desktop monitor survives the app going to the background
  - `MainActivity`: fullscreen SurfaceView that binds the service and attaches/detaches its surface. On top is `statusScreen`, a black screen with the launcher icon and the status text centred below it. It's shown for every non-null status (waiting, connecting, stopped), and tapping it calls `reconnect()`.
  - `Connection`: socket, reconnect loop, VISIBILITY and TOUCH messages (scaled from view pixels to monitor pixels). The decoder only exists while a surface is attached.
  - `Decoder` (MediaCodec), `Protocol`
- `host/scripts/`: `install-deps.sh`, `run.sh`, `daemon.sh` + `lib.sh` (plug-in handling), `install-autostart.sh` (systemd user service)
- `android/scripts/install-sdk.sh`: JDK, Android SDK, Gradle wrapper
- `scripts/gen-icon.py`: the only root-level script, because it links `docs/` and `android/`
- `android/` and `host/` are self-contained: each is its own Docker build context with its own `Dockerfile` and `.dockerignore`. Don't reach across them. The host finds the APK through `TABLINK_APK`, which defaults to the local Android build.
- `compose.yaml`: builds both images and runs the host container (see [Docker](#docker)). `.env`, which git ignores, holds the user's host options as `TABLINK_ARGS`.

## Commands
```bash
cd host && python3 -m unittest discover -s tests       # host tests (fast, no hardware)
cd host && python3 -m tablink --selftest 1280x800 -v   # real virtual monitor + encoder, 5 s, no tablet
(cd android && ./gradlew assembleDebug)                # build the app
python3 scripts/gen-icon.py                            # after editing docs/icon.svg
journalctl --user -u tablink -f                        # logs when the autostart service is running
docker compose logs -f                                 # logs when running in Docker
docker compose up -d --build                           # rebuild both images + recreate the container
docker compose run --rm --no-deps --entrypoint python3 tablink -c '…'   # one-off check inside the image
adb logcat -s Decoder Connection                       # app logs (decode latency every 5 s)
```
To test a pipeline change without disturbing the user's desktop, feed `videotestsrc` through the same encoder chain with `gst-launch-1.0` instead of creating a virtual monitor.

## Gotchas
- **Screen lock:** Mutter refuses `CreateSession` with "Session creation inhibited" while the screen is locked. That's expected, and the app keeps retrying.
- **Port 27183** belongs to whichever of these is running: the Docker container, the systemd service or `host/scripts/run.sh`. Only one can run at a time. Check `docker compose ps` and `systemctl --user status tablink` before starting another, and stop it first (`docker compose stop` / `systemctl --user stop tablink`).
- **Latency:** keep H.264 **constrained-baseline**, no B-frames and a small CPB. With High profile the Qualcomm decoder (`c2.qti.avc.decoder`) buffers frames, which caused visible lag. The `vendor.qti-ext-dec-*` keys in `Decoder.kt` matter for the same reason.
- **Damage-driven frames:** Mutter only sends frames when the screen changes (plus `keepalive-time`), so low fps on a static screen is normal.
- **`h264parse`** is optional in `pipeline.py`, and `gstreamer1.0-plugins-bad` isn't installed (it pulls in GTK, icon themes and more, ~300 MB of the image). Both encoders repeat SPS/PPS on every keyframe themselves (VAAPI by default, x264 with `repeat-headers=1`), and the app's resume path depends on that. Keep exactly one caps filter after the encoder: GStreamer can't parse two in a row. `tests/test_pipeline.py` parses the description with and without `h264parse`.
- **Zero-copy capture:** with VAAPI, `pipewiresrc` negotiates `video/x-raw(memory:DMABuf)` (no `always-copy`) straight into `vaapipostproc`. On this machine that measured identical pixels to the copy path, ~1 ms less per frame, tighter p95 and about half the CPU. If negotiation fails before the first frame, `EncoderPipeline._fall_back` rebuilds with the copying path. A failure can show up both as `start()` returning FAILURE and as a bus error, so the fallback takes a lock and uses a per-build generation number, and errors from a replaced pipeline are ignored. The log line `encoding with vaapi (zero-copy DMA-BUF capture|copying frames)` shows which path is in use. x264 always copies.
- **Encoder cost:** on this machine's Renoir iGPU, VAAPI H.264 takes ~8 ms per frame at 1480×924 and ~30 ms at 2960×1848. That's why the default is `--scale 0.5` and sharper settings are opt-in (README "Sharpness vs latency"). `vah264enc` (gst va plugin) is no faster than `vaapih264enc`. To benchmark, feed `videotestsrc ! imagefreeze` through the same chain in a one-off container (`docker compose run --rm --no-deps --entrypoint python3 tablink …`). Don't time `videotestsrc` patterns directly: generating the pattern on the CPU dominates the timing at large sizes.
- **Linked sessions:** when a ScreenCast session is created with `remote-desktop-session-id`, Mutter expects `Start`/`Stop` and the `Closed` signal on the RemoteDesktop session, not the ScreenCast one (see `VirtualMonitor._control`). `NotifyTouch*` calls are fire-and-forget async calls. D-Bus keeps them in order, and only the first failure per session is logged as a warning.
- **Stop button:** `VirtualMonitor` only receives Mutter's `Closed` signal when GNOME ends the session itself (stop button, etc.), because `stop()` unsubscribes before closing. The server then sends `STOPPED`, unless the screen is locked (`org.gnome.ScreenSaver.GetActive`). The app holds its reconnect loop until `resume()`, which is triggered by a tap, the notification's Reconnect action, or `lib.sh` launching it with `--ez dev.mgade.tablink.extra.RECONNECT true` on plug-in. Mutter refuses `Stop` from any process other than the session owner, so this can't be tested by script. Ask the user to press the button.
- **Status screen layout:** the status text needs its own `LayoutParams(WRAP_CONTENT, WRAP_CONTENT)`. The LinearLayout default (match parent) wraps it to the icon's width. Inside `View.apply { }` blocks, don't name things `overlay`, because that resolves to `View.getOverlay()`. Check layout changes with a screenshot of the waiting screen: `docker compose stop`, then `adb exec-out screencap -p`, then `docker compose start`. That screen contains nothing private.
- **App lifecycle:** Android can destroy the surface after `onStop`, so `MainActivity.onStop` detaches explicitly. `attach`/`detach` must stay idempotent: a duplicate attach would create a new decoder after the resume keyframe has already been sent, and it would stay black until the next keyframe. When the app becomes visible again, the host forces a keyframe.
- **Host tests:** `tests/test_imports.py` imports every module. Keep it, so syntax errors in modules without their own tests (like `server.py`) fail `unittest`.
- **Desktop changes:** creating a virtual monitor or calling `display.py` changes the user's real display layout. Ask before doing it on their machine. Restarting the service or container (any `docker compose up` that recreates it, including `--build`), or reinstalling the APK, briefly cuts the tablet display.
- **Session token:** `adb reverse` exposes 27183 to every app on the tablet, so the host only accepts a `HELLO` carrying `TABLINK_TOKEN` (compared with `hmac.compare_digest`; protocol v2). `lib.sh` creates and exports the token and passes it to the app as the `dev.mgade.tablink.extra.TOKEN` extra on `am start`. The app saves it in SharedPreferences (`TabLinkService.PREFS`) and reads it on every connect attempt. Running `python3 -m tablink` without the variable generates a token and logs the `am start` command to use. `tests/test_auth.py` covers this. Never log the token, and never weaken or skip the check.
- **Logging:** per-frame and per-second numbers (rtt, capture→encoded, decode) are debug-level (`-v` / `Log.d`). Connections, sessions and errors are info or warning. Repeated per-attempt events (connection from…, token rejections after the first) are debug, so a retrying client can't flood the logs. Container logs are capped by `logging:` in `compose.yaml`.
- **App updates:** `host/scripts/lib.sh` reinstalls the APK only when its sha256 changes (stamp files in `~/.local/state/tablink/`).

## Docker
- **Images:** `android/Dockerfile` builds `tablink-app`, which holds only `/app-debug.apk` (a `FROM scratch` final stage). `host/Dockerfile` builds `tablink`, runs `scripts/daemon.sh` and copies the APK in through the `app` build context (`additional_contexts: app: service:app`). The image sets `TABLINK_APK` to that copy.
- **Reuse the scripts:** the images run `android/scripts/install-sdk.sh`, `host/scripts/install-deps.sh`, `daemon.sh` and `lib.sh` unchanged. The install scripts drop `sudo` when run as root, and `NO_RECOMMENDS=1` (set by `host/Dockerfile`) skips recommended packages. The host image is ~630 MB, and most of that is LLVM for the AMD VAAPI driver. Fix behaviour in the scripts, never in Docker-only copies.
- **Build-only service:** the `app` service uses `scale: 0` and `network_mode: none` so it's built but never started. Don't use a `profiles:` entry for it. With a profile, `docker compose up` builds it without its build secret and fails with `secret debug_keystore: not found`, after it has already removed the running container.
- **Signing:** the APK is signed with the desktop's `~/.android/debug.keystore`, passed in as a build secret (`required=true`) and never stored in an image. A different key makes `adb install -r` fail with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`.
- **Desktop access:** the container needs `apparmor=unconfined` (the default profile blocks the session D-Bus). It runs as the desktop user (`TABLINK_UID`/`TABLINK_GID`, default 1000), because D-Bus authenticates by uid. It also needs the `VIDEO_GID`, `RENDER_GID` and `PLUGDEV_GID` group ids (defaults 44/992/46; the `render` id varies by machine). All of these come from `.env`. It bind-mounts `/run/user/$TABLINK_UID/{bus,pipewire-0}` to the fixed `/run/tablink/` inside the container (`create_host_path: false`), `/dev/dri`, `/dev/bus/usb` plus a `c 189:* rmw` cgroup rule for hotplug, and `~/.android` for the adb key. It uses the host network for 27183 and the adb server on 5037.
- **Boot:** those sockets only exist at boot when lingering is enabled (`loginctl enable-linger`). Without it, the container can't start until the user logs in.
- **Testing:**
  - `docker compose build` alone isn't enough. Also run `docker compose up -d` and check the logs for `placed virtual monitor` (or `encoding with` when no `--position` is set) with the tablet attached.
  - The image has no `gst-launch-1.0`, so do in-container GStreamer checks through Python (`gi`).
  - Container logs show a harmless `amdgpu: os_same_file_description` warning, and a harmless `resolv.conf` line during the build.

## Conventions
- Match the existing style: small modules, short docstrings, comments that explain *why*.
- The README is user-facing, with plain explanations of what changed and why. Update it when behaviour or options change.
- Commit only when asked. The branch is `main`, pushed to `github.com:mgade84/tablink`. Use the user's global git identity.
- License: MIT. Icon credits (Tux, Android robot) live in the README.
