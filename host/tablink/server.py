"""TCP server (loopback only, reached from the tablet via `adb reverse`).

Threads:
  main      GLib main loop (D-Bus signals, GStreamer bus messages)
  acceptor  accepts connections; each gets its own Session thread
  per client: the Session thread authenticates the HELLO (HELLO_TIMEOUT), takes
            over from any previous session, then drains the frame queue into the
            socket; a reader thread handles PING/PONG, input and EOF.

Only one session streams at a time. A client that never sends a valid HELLO
can't hold anything up: it only ever occupies its own thread, for at most
HELLO_TIMEOUT.
"""

import hmac
import logging
import os
import queue
import secrets
import socket
import struct
import threading
import time

from gi.repository import Gio, GLib

from . import protocol
from .display import place_virtual_monitor
from .mutter import MutterError, VirtualMonitor
from .pipeline import EncoderPipeline

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 1.0  # the app sends HELLO right after connecting
PING_INTERVAL = 2.0
LOCK_POLL = 0.5  # how often to check whether a locked desktop has been unlocked
FRAME_QUEUE = 6  # ~100ms at 60fps; beyond that we drop until the next keyframe


def even(n):
    return max(2, n - (n % 2))


def screen_locked():
    """Is the GNOME desktop locked? GNOME closes and refuses screen-cast
    sessions while it is."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        (locked,) = bus.call_sync("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver",
                                  "org.gnome.ScreenSaver", "GetActive", None,
                                  GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE,
                                  1000, None).unpack()
        return locked
    except GLib.Error:
        return False


class Session:
    rejections = 0  # across sessions: only the first bad token is logged as a warning

    def __init__(self, conn, addr, opts):
        self.conn = conn
        self.addr = addr
        self.opts = opts
        self.frames = queue.Queue(maxsize=FRAME_QUEUE)
        self.stopped = threading.Event()
        self.finished = threading.Event()  # run() has cleaned up
        self.waiting_for_keyframe = True
        self.visible = True  # the app is in the foreground and showing video
        self.monitor_started = False
        self.monitor = None
        self.pipeline = None
        self.send_lock = threading.Lock()
        self.next_ping = 0.0

    # -- lifecycle -------------------------------------------------------

    def run(self, takeover=lambda session: None):
        """Serve this client. `takeover` is called once it has authenticated, to
        end any other session before this one creates its monitor."""
        try:
            hello = self._read_hello()
            takeover(self)
            width = even(round(hello["width"] * self.opts.scale))
            height = even(round(hello["height"] * self.opts.scale))
            log.info("client %s: %dx%d @%ddpi (proto %d) -> monitor %dx%d",
                     self.addr, hello["width"], hello["height"], hello["dpi"],
                     hello["version"], width, height)

            self.size = (width, height)
            self.monitor_started = True
            threading.Thread(target=self._reader, name="reader", daemon=True).start()

            # GNOME closes screen-cast sessions while the desktop is locked (and
            # refuses new ones). Keep the connection, tell the app, and start a
            # new stream once it's unlocked.
            while not self.stopped.is_set():
                self._wait_while_locked()
                if self.stopped.is_set() or not self._start_stream():
                    continue
                self._writer()
                if self.stopped.is_set() or not self.monitor.closed:
                    break
                if self.monitor.closed_by_desktop and not self._locking():
                    log.info("client %s: stopped from the desktop; the tablet waits until asked to reconnect", self.addr)
                    self._send(protocol.frame(protocol.STOPPED))
                    break
                self._stop_stream()  # locked (or about to be): wait and restart
        except protocol.AuthError as e:
            Session.rejections += 1
            log.log(logging.WARNING if Session.rejections == 1 else logging.DEBUG,
                    "client %s rejected: %s", self.addr, e)
        except (OSError, protocol.ProtocolError, RuntimeError) as e:
            log.warning("client %s: %s", self.addr, e)
        except Exception:
            log.exception("client %s: unexpected error", self.addr)
        finally:
            self.close()
            self.finished.set()

    def end(self):
        """Ask a running session to finish (from another thread); run() cleans up."""
        self.stopped.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)  # unblocks reads and sends
        except OSError:
            pass

    def _start_stream(self):
        """Create the virtual monitor and encoder. Returns False if GNOME refused
        because the desktop is locked (the caller waits and retries)."""
        width, height = self.size
        self.monitor = VirtualMonitor(touch=self.opts.touch)
        try:
            node_id = self.monitor.start()
        except MutterError as e:
            self.monitor = None
            if "inhibited" not in str(e):
                raise
            log.debug("client %s: desktop locked, can't start the stream yet", self.addr)
            self._idle(1.0)
            return False
        self.waiting_for_keyframe = True
        self._send(protocol.pack_config(width, height))
        self.pipeline = EncoderPipeline(
            node_id, width, height, self.opts.fps, self.opts.bitrate,
            self.opts.encoder, self._on_frame, self._on_pipeline_error,
        )
        self.pipeline.start()
        if self.opts.position or self.opts.monitor_scale:
            # The monitor only exists once the stream has negotiated, so
            # this polls for it; keep it off the frame-writing thread.
            threading.Thread(target=place_virtual_monitor, args=(self.opts.position, self.opts.monitor_scale),
                             name="position", daemon=True).start()
        return True

    def _stop_stream(self):
        """Tear down the monitor and encoder but keep the connection."""
        pipeline, self.pipeline = self.pipeline, None
        if pipeline:
            pipeline.stop()
        monitor, self.monitor = self.monitor, None
        if monitor:
            monitor.stop()
        self._drain()

    def _wait_while_locked(self):
        """While the desktop is locked: tell the app once, then keep the
        connection alive with pings until it's unlocked."""
        if not screen_locked():
            return
        log.info("client %s: desktop locked; waiting for unlock", self.addr)
        self._send(protocol.frame(protocol.LOCKED))
        while not self.stopped.is_set() and screen_locked():
            self._idle(LOCK_POLL)
        if not self.stopped.is_set():
            log.info("client %s: desktop unlocked; resuming", self.addr)

    def _locking(self, grace=1.0):
        """GNOME closes the session as the lock starts, possibly a moment before
        the screensaver reports it. Watch for a lock briefly before treating the
        close as GNOME's stop button."""
        deadline = time.monotonic() + grace
        while not screen_locked():
            if time.monotonic() >= deadline or self.stopped.is_set():
                return False
            self.stopped.wait(0.1)
        return True

    def _idle(self, seconds):
        """Wait without streaming, still sending keep-alive pings."""
        if time.monotonic() >= self.next_ping:
            self._send(protocol.pack_ping(time.monotonic_ns() // 1000))
            self.next_ping = time.monotonic() + PING_INTERVAL
        self.stopped.wait(seconds)

    def close(self):
        self.stopped.set()
        self._stop_stream()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()
        log.log(logging.INFO if self.monitor_started else logging.DEBUG, "client %s disconnected", self.addr)

    # -- inbound ---------------------------------------------------------

    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.conn.recv(n - len(buf))
            if not chunk:
                raise OSError("connection closed")
            buf += chunk
        return bytes(buf)

    def _read_hello(self):
        self.conn.settimeout(HELLO_TIMEOUT)
        msg_type, length = protocol.HEADER.unpack(self._recv_exact(protocol.HEADER.size))
        if msg_type != protocol.HELLO or length > protocol.MAX_INBOUND_PAYLOAD:
            raise protocol.ProtocolError(f"expected HELLO, got type 0x{msg_type:02x}")
        hello = protocol.unpack_hello(self._recv_exact(length))
        if hello["version"] != protocol.PROTO_VERSION:
            raise protocol.ProtocolError(f"protocol version {hello['version']} unsupported")
        # adb reverse exposes the port to every app on the tablet; only TabLink,
        # launched by host/scripts with the token, may connect.
        if not hmac.compare_digest(hello["token"], self.opts.token):
            raise protocol.AuthError("wrong or missing token (re-plug the tablet so the app gets the current one)")
        if not (64 <= hello["width"] <= 8192 and 64 <= hello["height"] <= 8192):
            raise protocol.ProtocolError(f"bad size {hello['width']}x{hello['height']}")
        self.conn.settimeout(None)
        return hello

    def _reader(self):
        reader = protocol.Reader()
        try:
            while not self.stopped.is_set():
                data = self.conn.recv(4096)
                if not data:
                    break
                for msg_type, payload in reader.feed(data):
                    if msg_type == protocol.TOUCH:
                        self._on_touch(payload)
                    elif msg_type == protocol.VISIBILITY and len(payload) == 1:
                        self._set_visible(bool(payload[0]))
                    elif msg_type == protocol.PING:
                        self._send(protocol.frame(protocol.PONG, payload))
                    elif msg_type == protocol.PONG and len(payload) == 8:
                        (sent,) = struct.unpack(">Q", payload)
                        rtt_ms = (time.monotonic_ns() // 1000 - sent) / 1000
                        log.debug("rtt %.1f ms", rtt_ms)
        except (OSError, protocol.ProtocolError) as e:
            log.debug("reader: %s", e)
        self.stopped.set()

    def _on_touch(self, payload):
        action, slot, x, y = protocol.unpack_touch(payload)
        monitor = self.monitor  # None once the session is closing
        if monitor:
            w, h = self.size
            monitor.touch(action, slot, min(max(x, 0.0), w - 1), min(max(y, 0.0), h - 1))

    def _set_visible(self, visible):
        """The app went to the background (no surface) or came back. Keep the
        virtual monitor either way; only pause the video."""
        if visible == self.visible:
            return
        log.info("client %s: app %s", self.addr, "visible, resuming video" if visible else "in background, pausing video")
        self.visible = visible
        if visible:
            # Its decoder starts from scratch, so restart on a fresh keyframe.
            self.waiting_for_keyframe = True
            if self.pipeline:
                self.pipeline.request_keyframe()
        else:
            self._drain()

    # -- outbound --------------------------------------------------------

    def _send(self, data):
        with self.send_lock:
            self.conn.sendall(data)

    def _on_frame(self, pts_us, data, keyframe):
        if self.stopped.is_set() or not self.visible:
            return
        if self.waiting_for_keyframe:
            if not keyframe:
                return
            self.waiting_for_keyframe = False
        try:
            self.frames.put_nowait(protocol.pack_video(pts_us, data))
        except queue.Full:
            # Client can't keep up: flush and resync on a fresh keyframe so
            # latency stays bounded instead of growing.
            log.debug("send queue full, dropping until keyframe")
            self._drain()
            self.waiting_for_keyframe = True
            self.pipeline.request_keyframe()

    def _drain(self):
        try:
            while True:
                self.frames.get_nowait()
        except queue.Empty:
            pass

    def _on_pipeline_error(self, message):
        log.error("pipeline: %s", message)
        self.stopped.set()

    def _writer(self):
        """Stream until the session is stopped or GNOME closes the monitor."""
        while not self.stopped.is_set() and not self.monitor.closed:
            try:
                msg = self.frames.get(timeout=0.25)
                self._send(msg)
            except queue.Empty:
                pass
            if time.monotonic() >= self.next_ping:
                self._send(protocol.pack_ping(time.monotonic_ns() // 1000))
                self.next_ping = time.monotonic() + PING_INTERVAL


class Server:
    def __init__(self, opts):
        self.opts = opts
        self.loop = GLib.MainLoop()
        self._lock = threading.Lock()
        self._active = None  # the session that passed authentication last
        # Shared secret the app must present. host/scripts generate it and pass it to
        # both the host (TABLINK_TOKEN) and the app (am start --es ...TOKEN).
        opts.token = os.environ.get("TABLINK_TOKEN", "")
        if not opts.token:
            opts.token = secrets.token_hex(16)
            log.warning("TABLINK_TOKEN not set; generated one. Start the app with: "
                        "adb shell am start -n dev.mgade.tablink/.MainActivity "
                        "--es dev.mgade.tablink.extra.TOKEN %s", opts.token)

    def _takeover(self, session):
        """An authenticated client replaces the current session (e.g. the app
        reconnected while the old socket still looked alive)."""
        with self._lock:
            old, self._active = self._active, session
        if old and not old.finished.is_set():
            log.info("client %s replaces %s", session.addr, old.addr)
            old.end()
            old.finished.wait(10)

    def _acceptor(self, sock):
        while True:
            conn, addr = sock.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
            log.debug("connection from %s", addr)
            session = Session(conn, addr, self.opts)
            threading.Thread(target=session.run, args=(self._takeover,),
                             name=f"session-{addr[1]}", daemon=True).start()

    def serve(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.opts.port))
        sock.listen(8)
        log.info("listening on 127.0.0.1:%d (run `adb reverse tcp:%d tcp:%d`)",
                 self.opts.port, self.opts.port, self.opts.port)
        threading.Thread(target=self._acceptor, args=(sock,), name="acceptor", daemon=True).start()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass
