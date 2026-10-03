# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, etc.) working on TabLink. For what the project does and how to set it up and run it, read [README.md](README.md). This file doesn't repeat that.

## Principles
- **Reuse, don't duplicate.** Reference existing code and assets instead of copying them or adding variant files. If a variant really seems necessary, generate it from the single source or ask first.
- **Single sources of truth:**
  - Wire protocol: `host/tablink/protocol.py` ⇄ `android/.../Protocol.kt`. These are the one deliberate mirror (two languages). Change both together and bump `PROTO_VERSION` / `VERSION` for incompatible changes.
  - App icon: `docs/icon.svg` → `scripts/gen-icon.py` → `res/drawable/ic_launcher_*.xml` and `docs/icon-preview.png`. Never hand-edit the generated files.
  - Tablet connect steps: `scripts/lib.sh` (`connect_tablet`), used by both `run.sh` and `daemon.sh`.
- Keep it dependency-light. The host uses only system Python + PyGObject (`gi`): no pip packages, and no `GstVideo` typelib, which isn't installed. The app has no AndroidX or other libraries.

## Layout
- `host/tablink/` (Python, runs on the Ubuntu desktop)
  - `mutter.py`: `org.gnome.Mutter.ScreenCast` `RecordVirtual` creates the virtual monitor
  - `pipeline.py`: GStreamer `pipewiresrc` → VAAPI (or x264) H.264 → appsink
  - `server.py`: loopback TCP server, one tablet at a time, threads + GLib main loop
  - `display.py`: `org.gnome.Mutter.DisplayConfig` places the monitor (`--position`)
  - `protocol.py`, `__main__.py` (CLI, `--selftest`)
- `android/app/src/main/java/dev/mgade/tablink/`: `MainActivity` (fullscreen SurfaceView), `Connection` (socket + reconnect loop), `Decoder` (MediaCodec), `Protocol`
- `scripts/`: install, run, autostart (systemd user service + `daemon.sh`), `gen-icon.py`

## Commands
```bash
cd host && python3 -m unittest discover -s tests       # host tests (fast, no hardware)
cd host && python3 -m tablink --selftest 1280x800 -v   # real virtual monitor + encoder, 5 s, no tablet
(cd android && ./gradlew assembleDebug)                # build the app
python3 scripts/gen-icon.py                            # after editing docs/icon.svg
journalctl --user -u tablink -f                        # logs when the autostart service is running
adb logcat -s Decoder Connection                       # app logs (decode latency every 5 s)
```
To test a pipeline change without disturbing the user's desktop, feed `videotestsrc` through the same encoder chain with `gst-launch-1.0` instead of creating a virtual monitor.

## Gotchas
- **Screen lock:** Mutter refuses `CreateSession` with "Session creation inhibited" while the screen is locked. That's expected, and the app keeps retrying.
- **Port 27183** is used by both the autostart service and `scripts/run.sh`. Stop the service (`systemctl --user stop tablink`) before running manually or on another port.
- **Latency:** keep H.264 **constrained-baseline**, no B-frames and a small CPB. With High profile the Qualcomm decoder (`c2.qti.avc.decoder`) buffers frames, which caused visible lag. The `vendor.qti-ext-dec-*` keys in `Decoder.kt` matter for the same reason.
- **Damage-driven frames:** Mutter only sends frames when the screen changes (plus `keepalive-time`), so low fps on a static screen is normal.
- **`h264parse`** is optional in `pipeline.py`. Keep it that way.
- **Desktop changes:** creating a virtual monitor or calling `display.py` changes the user's real display layout. Ask before doing it on their machine. Restarting the service or reinstalling the APK briefly cuts the tablet display.
- **App updates:** `scripts/lib.sh` reinstalls the APK only when its sha256 changes (stamp file in `android/app/build/`).

## Conventions
- Match the existing style: small modules, short docstrings, comments that explain *why*.
- The README is user-facing, with plain explanations of what changed and why. Update it when behaviour or options change.
- Commit only when asked. The branch is `main`, pushed to `github.com:mgade84/tablink`. Use the user's global git identity.
- License: MIT. Icon credits (Tux, Android robot) live in the README.
