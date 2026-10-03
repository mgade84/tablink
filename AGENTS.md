# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, etc.) working on TabLink. For what the project does and how to set it up and run it, read [README.md](README.md). This file doesn't repeat that.

## Principles
- **Reuse, don't duplicate.** Reference existing code and assets instead of copying them or adding variant files. If a variant really seems necessary, generate it from the single source or ask first.
- **Single sources of truth:**
  - Wire protocol: `host/tablink/protocol.py` ⇄ `android/.../Protocol.kt`. These are the one deliberate mirror (two languages). Change both together and bump `PROTO_VERSION` / `VERSION` for incompatible changes.
  - App icon: `docs/icon.svg` → `scripts/gen-icon.py` → `res/drawable/ic_launcher_*.xml` and `docs/icon-preview.png`. Never hand-edit the generated files.
  - Tablet connect steps: `host/scripts/lib.sh` (`connect_tablet`), used by both `run.sh` and `daemon.sh`.
- Keep it dependency-light. The host uses only system Python + PyGObject (`gi`): no pip packages, and no `GstVideo` typelib, which isn't installed. The app has no AndroidX or other libraries.

## Layout
- `host/tablink/` (Python, runs on the Ubuntu desktop)
  - `mutter.py`: `org.gnome.Mutter.ScreenCast` `RecordVirtual` creates the virtual monitor
  - `pipeline.py`: GStreamer `pipewiresrc` → VAAPI (or x264) H.264 → appsink
  - `server.py`: loopback TCP server, one tablet at a time, threads + GLib main loop
  - `display.py`: `org.gnome.Mutter.DisplayConfig` places the monitor (`--position`)
  - `protocol.py`, `__main__.py` (CLI, `--selftest`)
- `android/app/src/main/java/dev/mgade/tablink/`: `MainActivity` (fullscreen SurfaceView), `Connection` (socket + reconnect loop), `Decoder` (MediaCodec), `Protocol`
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
- **`h264parse`** is optional in `pipeline.py`. Keep it that way.
- **Desktop changes:** creating a virtual monitor or calling `display.py` changes the user's real display layout. Ask before doing it on their machine. Restarting the service or container (any `docker compose up` that recreates it, including `--build`), or reinstalling the APK, briefly cuts the tablet display.
- **App updates:** `host/scripts/lib.sh` reinstalls the APK only when its sha256 changes (stamp files in `~/.local/state/tablink/`).

## Docker
- **Images:** `android/Dockerfile` builds `tablink-app`, which holds only `/app-debug.apk` (a `FROM scratch` final stage). `host/Dockerfile` builds `tablink`, runs `scripts/daemon.sh` and copies the APK in through the `app` build context (`additional_contexts: app: service:app`). The image sets `TABLINK_APK` to that copy.
- **Reuse the scripts:** the images run `android/scripts/install-sdk.sh`, `host/scripts/install-deps.sh`, `daemon.sh` and `lib.sh` unchanged. The install scripts drop `sudo` when run as root. Fix behaviour in the scripts, never in Docker-only copies.
- **Build-only service:** the `app` service uses `scale: 0` and `network_mode: none` so it's built but never started. Don't use a `profiles:` entry for it. With a profile, `docker compose up` builds it without its build secret and fails with `secret debug_keystore: not found`, after it has already removed the running container.
- **Signing:** the APK is signed with the desktop's `~/.android/debug.keystore`, passed in as a build secret (`required=true`) and never stored in an image. A different key makes `adb install -r` fail with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`.
- **Desktop access:** the container needs `apparmor=unconfined` (the default profile blocks the session D-Bus). It runs as uid/gid 1000, because D-Bus authenticates by uid. It also needs these group ids: `video` 44, `render` 992 and `plugdev` 46 (the `render` id varies by machine). It uses bind mounts of `/run/user/1000/{bus,pipewire-0}` with `create_host_path: false`, `/dev/dri`, `/dev/bus/usb` plus a `c 189:* rmw` cgroup rule for hotplug, and `~/.android` for the adb key. It uses the host network for 27183 and the adb server on 5037.
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
