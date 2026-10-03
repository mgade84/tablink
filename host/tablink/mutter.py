"""Create a GNOME virtual monitor through Mutter's private ScreenCast D-Bus API,
optionally with touch input through the linked RemoteDesktop API.

RecordVirtual makes Mutter add a monitor to the desktop layout whose size is
decided by the caps the PipeWire consumer negotiates. Stopping the session (or
our process exiting / dropping off the bus) removes the monitor again.

With touch enabled, the ScreenCast session is created inside a RemoteDesktop
session (Mutter then expects Start/Stop on the RemoteDesktop session), and
touch events are sent relative to the virtual monitor's stream.
"""

import logging
import threading

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

SC_BUS = "org.gnome.Mutter.ScreenCast"
SC_PATH = "/org/gnome/Mutter/ScreenCast"
SC_IFACE = "org.gnome.Mutter.ScreenCast"
SC_SESSION_IFACE = "org.gnome.Mutter.ScreenCast.Session"
SC_STREAM_IFACE = "org.gnome.Mutter.ScreenCast.Stream"

RD_BUS = "org.gnome.Mutter.RemoteDesktop"
RD_PATH = "/org/gnome/Mutter/RemoteDesktop"
RD_IFACE = "org.gnome.Mutter.RemoteDesktop"
RD_SESSION_IFACE = "org.gnome.Mutter.RemoteDesktop.Session"

CURSOR_MODE_EMBEDDED = 1

# protocol.TOUCH actions
TOUCH_DOWN, TOUCH_MOVE, TOUCH_UP = 0, 1, 2


class MutterError(RuntimeError):
    pass


class VirtualMonitor:
    def __init__(self, touch=False):
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._touch = touch
        self._rd_session = None   # RemoteDesktop session path (touch only)
        self._sc_session = None   # ScreenCast session path
        self._stream_path = None
        self._subscriptions = []
        self._node_ready = threading.Event()
        self._closed = threading.Event()
        self.node_id = None
        self._touch_failed = False
        self.closed_by_desktop = False

    def _call(self, bus, path, iface, method, args=None, reply_type=None):
        try:
            return self._bus.call_sync(
                bus, path, iface, method, args,
                GLib.VariantType.new(reply_type) if reply_type else None,
                Gio.DBusCallFlags.NONE, 5000, None,
            )
        except GLib.Error as e:
            hint = " (is the screen locked?)" if "inhibited" in e.message else ""
            raise MutterError(f"{iface}.{method} failed: {e.message}{hint}") from e

    def _subscribe(self, bus, path, iface, signal, callback):
        sub = self._bus.signal_subscribe(
            bus, iface, signal, path, None, Gio.DBusSignalFlags.NONE, callback,
        )
        self._subscriptions.append(sub)

    @property
    def _control(self):
        """(bus, path, iface) of the session that is started/stopped."""
        if self._rd_session:
            return RD_BUS, self._rd_session, RD_SESSION_IFACE
        return SC_BUS, self._sc_session, SC_SESSION_IFACE

    def start(self, timeout=5.0):
        """Create the session(s) + virtual stream and wait for its PipeWire node id."""
        sc_options = {}
        if self._touch:
            (self._rd_session,) = self._call(RD_BUS, RD_PATH, RD_IFACE, "CreateSession",
                                             reply_type="(o)").unpack()
            (session_id,) = self._call(
                RD_BUS, self._rd_session, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (RD_SESSION_IFACE, "SessionId")), "(v)",
            ).unpack()
            sc_options["remote-desktop-session-id"] = GLib.Variant("s", session_id)
            log.debug("remote desktop session %s", self._rd_session)

        (self._sc_session,) = self._call(
            SC_BUS, SC_PATH, SC_IFACE, "CreateSession",
            GLib.Variant("(a{sv})", (sc_options,)), "(o)",
        ).unpack()
        log.debug("screencast session %s", self._sc_session)

        props = {
            "cursor-mode": GLib.Variant("u", CURSOR_MODE_EMBEDDED),
            # Treat it like a physical monitor (shows up in Settings → Displays,
            # windows can be placed on it, layout is remembered).
            "is-platform": GLib.Variant("b", True),
        }
        (self._stream_path,) = self._call(
            SC_BUS, self._sc_session, SC_SESSION_IFACE, "RecordVirtual",
            GLib.Variant("(a{sv})", (props,)), "(o)",
        ).unpack()
        log.debug("virtual stream %s", self._stream_path)

        self._subscribe(SC_BUS, self._stream_path, SC_STREAM_IFACE, "PipeWireStreamAdded", self._on_stream_added)
        bus, path, iface = self._control
        self._subscribe(bus, path, iface, "Closed", self._on_closed)

        self._call(bus, path, iface, "Start")
        if not self._node_ready.wait(timeout):
            self.stop()
            raise MutterError("timed out waiting for PipeWireStreamAdded")
        log.info("virtual monitor PipeWire node %d%s", self.node_id, " (touch enabled)" if self._touch else "")
        return self.node_id

    def _on_stream_added(self, _conn, _sender, _path, _iface, _signal, params):
        (self.node_id,) = params.unpack()
        self._node_ready.set()

    def _on_closed(self, *_args):
        # Only reached when Mutter closes the session on its own: stop()
        # unsubscribes before it closes the session itself.
        self.closed_by_desktop = True
        log.info("mutter closed the session")
        self._closed.set()

    @property
    def closed(self):
        return self._closed.is_set()

    def touch(self, action, slot, x, y):
        """Inject a touch event at (x, y) in virtual-monitor pixels. Fire-and-forget:
        D-Bus keeps the calls in order, and waiting for replies would add latency."""
        if not self._rd_session or self._closed.is_set():
            return
        if action == TOUCH_UP:
            method, args = "NotifyTouchUp", GLib.Variant("(u)", (slot,))
        else:
            method = "NotifyTouchDown" if action == TOUCH_DOWN else "NotifyTouchMotion"
            args = GLib.Variant("(sudd)", (self._stream_path, slot, x, y))
        self._bus.call(RD_BUS, self._rd_session, RD_SESSION_IFACE, method, args,
                       None, Gio.DBusCallFlags.NONE, 1000, None, self._on_touch_reply, method)

    def _on_touch_reply(self, bus, result, method):
        try:
            bus.call_finish(result)
        except GLib.Error as e:
            # Warn once per session; touch streams are too chatty to log every failure.
            level = logging.DEBUG if self._touch_failed else logging.WARNING
            self._touch_failed = True
            log.log(level, "touch input failed: %s: %s", method, e.message)

    def stop(self):
        for sub in self._subscriptions:
            self._bus.signal_unsubscribe(sub)
        self._subscriptions.clear()
        if self._sc_session and not self._closed.is_set():
            bus, path, iface = self._control
            try:
                self._call(bus, path, iface, "Stop")
            except MutterError as e:
                log.debug("%s", e)
        self._rd_session = self._sc_session = None
        self._closed.set()
