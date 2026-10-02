"""Create a GNOME virtual monitor through Mutter's private ScreenCast D-Bus API.

RecordVirtual makes Mutter add a monitor to the desktop layout whose size is
decided by the caps the PipeWire consumer negotiates. Stopping the session (or
our process exiting / dropping off the bus) removes the monitor again.
"""

import logging
import threading

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BUS_NAME = "org.gnome.Mutter.ScreenCast"
OBJECT_PATH = "/org/gnome/Mutter/ScreenCast"
IFACE = "org.gnome.Mutter.ScreenCast"
SESSION_IFACE = "org.gnome.Mutter.ScreenCast.Session"
STREAM_IFACE = "org.gnome.Mutter.ScreenCast.Stream"

CURSOR_MODE_EMBEDDED = 1


class MutterError(RuntimeError):
    pass


class VirtualMonitor:
    def __init__(self):
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._session_path = None
        self._stream_path = None
        self._subscriptions = []
        self._node_ready = threading.Event()
        self._closed = threading.Event()
        self.node_id = None

    def _call(self, path, iface, method, args, reply_type):
        try:
            return self._bus.call_sync(
                BUS_NAME, path, iface, method, args,
                GLib.VariantType.new(reply_type) if reply_type else None,
                Gio.DBusCallFlags.NONE, 5000, None,
            )
        except GLib.Error as e:
            hint = " (is the screen locked?)" if "inhibited" in e.message else ""
            raise MutterError(f"{iface}.{method} failed: {e.message}{hint}") from e

    def _subscribe(self, path, iface, signal, callback):
        sub = self._bus.signal_subscribe(
            BUS_NAME, iface, signal, path, None, Gio.DBusSignalFlags.NONE, callback,
        )
        self._subscriptions.append(sub)

    def start(self, timeout=5.0):
        """Create the session + virtual stream and wait for its PipeWire node id."""
        (self._session_path,) = self._call(
            OBJECT_PATH, IFACE, "CreateSession",
            GLib.Variant("(a{sv})", ({},)), "(o)",
        ).unpack()
        log.debug("screencast session %s", self._session_path)

        props = {
            "cursor-mode": GLib.Variant("u", CURSOR_MODE_EMBEDDED),
            # Treat it like a physical monitor (shows up in Settings → Displays,
            # windows can be placed on it, layout is remembered).
            "is-platform": GLib.Variant("b", True),
        }
        (self._stream_path,) = self._call(
            self._session_path, SESSION_IFACE, "RecordVirtual",
            GLib.Variant("(a{sv})", (props,)), "(o)",
        ).unpack()
        log.debug("virtual stream %s", self._stream_path)

        self._subscribe(self._stream_path, STREAM_IFACE, "PipeWireStreamAdded", self._on_stream_added)
        self._subscribe(self._session_path, SESSION_IFACE, "Closed", self._on_closed)

        self._call(self._session_path, SESSION_IFACE, "Start", None, None)
        if not self._node_ready.wait(timeout):
            self.stop()
            raise MutterError("timed out waiting for PipeWireStreamAdded")
        log.info("virtual monitor PipeWire node %d", self.node_id)
        return self.node_id

    def _on_stream_added(self, _conn, _sender, _path, _iface, _signal, params):
        (self.node_id,) = params.unpack()
        self._node_ready.set()

    def _on_closed(self, *_args):
        log.info("mutter closed the screencast session")
        self._closed.set()

    @property
    def closed(self):
        return self._closed.is_set()

    def stop(self):
        for sub in self._subscriptions:
            self._bus.signal_unsubscribe(sub)
        self._subscriptions.clear()
        if self._session_path and not self._closed.is_set():
            try:
                self._call(self._session_path, SESSION_IFACE, "Stop", None, None)
            except MutterError as e:
                log.debug("%s", e)
        self._session_path = None
        self._closed.set()
