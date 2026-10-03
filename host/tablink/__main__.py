import argparse
import logging
import sys
import threading

from gi.repository import GLib

from . import protocol


def parse_args(argv):
    p = argparse.ArgumentParser(prog="tablink",
                                description="Stream a GNOME virtual monitor to an Android tablet over adb.")
    p.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    p.add_argument("--encoder", choices=["auto", "vaapi", "x264"], default="auto")
    p.add_argument("--bitrate", type=int, default=12000, help="kbit/s (default: 12000)")
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--scale", type=float, default=1.0,
                   help="monitor size relative to the tablet's native resolution (e.g. 0.5)")
    p.add_argument("--position", choices=["left", "right", "above", "below"],
                   help="where to put the tablet relative to the main display (default: GNOME's choice, right)")
    p.add_argument("--no-touch", dest="touch", action="store_false",
                   help="don't pass touch input from the tablet to the desktop")
    p.add_argument("--selftest", nargs="?", const="1920x1200", metavar="WxH",
                   help="create a virtual monitor without a tablet and record 5s to selftest.h264")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def selftest(opts):
    from .display import place_virtual_monitor
    from .mutter import VirtualMonitor
    from .pipeline import EncoderPipeline

    width, height = (int(x) for x in opts.selftest.lower().split("x"))
    loop = GLib.MainLoop()
    out = open("selftest.h264", "wb")
    stats = {"frames": 0, "bytes": 0, "keyframes": 0, "error": None}

    def on_frame(_pts, data, key):
        out.write(data)
        stats["frames"] += 1
        stats["bytes"] += len(data)
        stats["keyframes"] += key

    def on_error(msg):
        stats["error"] = msg
        loop.quit()

    mon = VirtualMonitor()
    pipe = None

    # VirtualMonitor.start() blocks on a D-Bus signal that the main loop
    # dispatches, so it has to run off the main thread.
    def run():
        nonlocal pipe
        try:
            node = mon.start()
            pipe = EncoderPipeline(node, width, height, opts.fps, opts.bitrate, opts.encoder, on_frame, on_error)
            pipe.start()
        except Exception as e:
            stats["error"] = str(e)
            GLib.idle_add(loop.quit)
            return
        logging.info("virtual monitor %dx%d is up; check Settings → Displays. Recording 5s…", width, height)
        if opts.position:
            threading.Thread(target=place_virtual_monitor, args=(opts.position,), daemon=True).start()
        GLib.timeout_add_seconds(5, loop.quit)

    threading.Thread(target=run, daemon=True).start()
    loop.run()
    if pipe:
        pipe.stop()
    mon.stop()
    out.close()

    if stats["error"]:
        logging.error("selftest failed: %s", stats["error"])
        return 1
    logging.info("wrote selftest.h264: %d frames (%d keyframes), %.1f KiB",
                 stats["frames"], stats["keyframes"], stats["bytes"] / 1024)
    logging.info("play it with: gst-play-1.0 selftest.h264")
    return 0 if stats["frames"] else 1


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.DEBUG if opts.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if opts.selftest:
        return selftest(opts)

    from .server import Server
    Server(opts).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
