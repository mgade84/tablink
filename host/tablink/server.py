"""TCP server (loopback only, reached from the tablet via `adb reverse`).

Threads:
  main      GLib main loop (D-Bus signals, GStreamer bus messages)
  acceptor  accepts one client at a time and runs a Session for it
  per client: a reader thread (PING/PONG, EOF detection); the session's own
            thread drains the frame queue into the socket.
"""

import logging
import queue
import socket
import struct
import threading
import time

from gi.repository import GLib

from . import protocol
from .display import place_virtual_monitor
from .mutter import VirtualMonitor
from .pipeline import EncoderPipeline

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 5.0
PING_INTERVAL = 2.0
FRAME_QUEUE = 6  # ~100ms at 60fps; beyond that we drop until the next keyframe


def even(n):
    return max(2, n - (n % 2))


class Session:
    def __init__(self, conn, addr, opts):
        self.conn = conn
        self.addr = addr
        self.opts = opts
        self.frames = queue.Queue(maxsize=FRAME_QUEUE)
        self.stopped = threading.Event()
        self.waiting_for_keyframe = True
        self.monitor = None
        self.pipeline = None
        self.send_lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------

    def run(self):
        try:
            hello = self._read_hello()
            width = even(round(hello["width"] * self.opts.scale))
            height = even(round(hello["height"] * self.opts.scale))
            log.info("client %s: %dx%d @%ddpi (proto %d) -> monitor %dx%d",
                     self.addr, hello["width"], hello["height"], hello["dpi"],
                     hello["version"], width, height)

            self.monitor = VirtualMonitor()
            node_id = self.monitor.start()
            self._send(protocol.pack_config(width, height))

            self.pipeline = EncoderPipeline(
                node_id, width, height, self.opts.fps, self.opts.bitrate,
                self.opts.encoder, self._on_frame, self._on_pipeline_error,
            )
            self.pipeline.start()
            if self.opts.position:
                # The monitor only exists once the stream has negotiated, so
                # this polls for it; keep it off the frame-writing thread.
                threading.Thread(target=place_virtual_monitor, args=(self.opts.position,),
                                 name="position", daemon=True).start()

            threading.Thread(target=self._reader, name="reader", daemon=True).start()
            self._writer()
        except (OSError, protocol.ProtocolError, RuntimeError) as e:
            log.warning("client %s: %s", self.addr, e)
        except Exception:
            log.exception("client %s: unexpected error", self.addr)
        finally:
            self.close()

    def close(self):
        self.stopped.set()
        if self.pipeline:
            self.pipeline.stop()
            self.pipeline = None
        if self.monitor:
            self.monitor.stop()
            self.monitor = None
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()
        log.info("client %s disconnected", self.addr)

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
                    if msg_type == protocol.PING:
                        self._send(protocol.frame(protocol.PONG, payload))
                    elif msg_type == protocol.PONG and len(payload) == 8:
                        (sent,) = struct.unpack(">Q", payload)
                        rtt_ms = (time.monotonic_ns() // 1000 - sent) / 1000
                        log.info("rtt %.1f ms", rtt_ms)
        except (OSError, protocol.ProtocolError) as e:
            log.debug("reader: %s", e)
        self.stopped.set()

    # -- outbound --------------------------------------------------------

    def _send(self, data):
        with self.send_lock:
            self.conn.sendall(data)

    def _on_frame(self, pts_us, data, keyframe):
        if self.stopped.is_set():
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
        next_ping = time.monotonic() + PING_INTERVAL
        while not self.stopped.is_set() and not self.monitor.closed:
            try:
                msg = self.frames.get(timeout=0.25)
                self._send(msg)
            except queue.Empty:
                pass
            if time.monotonic() >= next_ping:
                self._send(protocol.pack_ping(time.monotonic_ns() // 1000))
                next_ping = time.monotonic() + PING_INTERVAL


class Server:
    def __init__(self, opts):
        self.opts = opts
        self.loop = GLib.MainLoop()

    def _acceptor(self, sock):
        while True:
            conn, addr = sock.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
            log.info("connection from %s", addr)
            # One tablet at a time: handle this client to completion.
            Session(conn, addr, self.opts).run()

    def serve(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.opts.port))
        sock.listen(1)
        log.info("listening on 127.0.0.1:%d (run `adb reverse tcp:%d tcp:%d`)",
                 self.opts.port, self.opts.port, self.opts.port)
        threading.Thread(target=self._acceptor, args=(sock,), name="acceptor", daemon=True).start()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass
