"""Place the virtual monitor relative to the existing ones (left/right/above/below)
and set its GNOME scale, through org.gnome.Mutter.DisplayConfig, the API
GNOME Settings → Displays uses.
"""

import logging
import time

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BUS_NAME = "org.gnome.Mutter.DisplayConfig"
OBJECT_PATH = "/org/gnome/Mutter/DisplayConfig"
IFACE = "org.gnome.Mutter.DisplayConfig"

VIRTUAL_VENDOR = "MetaVendor"  # what Mutter reports for RecordVirtual monitors
METHOD_TEMPORARY = 1           # apply without writing monitors.xml
LAYOUT_PHYSICAL = 2
POSITIONS = ("left", "right", "above", "below")


class Box:
    def __init__(self, logical, monitors, layout_mode):
        x, y, scale, transform, primary, specs, _props = logical
        self.x, self.y, self.scale, self.transform, self.primary = x, y, scale, transform, primary
        self.specs = specs
        self.virtual = any(spec[1] == VIRTUAL_VENDOR for spec in specs)
        # Size comes from the current mode of its (first) monitor.
        mon = monitors[specs[0]]
        w, h = mon["size"]
        if transform % 2:  # 90°/270° rotations swap width and height
            w, h = h, w
        self.mode_w, self.mode_h = w, h
        self.layout_mode = layout_mode
        self.supported_scales = mon.get("scales", [])

    # Layout size: in the default logical layout mode, the mode size divided by the scale.
    @property
    def w(self):
        return self.mode_w if self.layout_mode == LAYOUT_PHYSICAL else round(self.mode_w / self.scale)

    @property
    def h(self):
        return self.mode_h if self.layout_mode == LAYOUT_PHYSICAL else round(self.mode_h / self.scale)

    def to_variant_tuple(self, monitors):
        return (self.x, self.y, self.scale, self.transform, self.primary,
                [(spec[0], monitors[spec]["mode"], {}) for spec in self.specs])


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _state(bus):
    serial, monitors, logical, props = bus.call_sync(
        BUS_NAME, OBJECT_PATH, IFACE, "GetCurrentState", None,
        GLib.VariantType.new("(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})"),
        Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()
    mons = {}
    for spec, modes, _mprops in monitors:
        for mode_id, w, h, _refresh, _preferred, scales, mode_props in modes:
            if mode_props.get("is-current"):
                mons[tuple(spec)] = {"mode": mode_id, "size": (w, h), "scales": list(scales)}
    layout_mode = props.get("layout-mode", 1)
    boxes = [Box(lm, mons, layout_mode) for lm in logical
             if all(tuple(s) in mons for s in lm[5])]
    return serial, mons, boxes


def _anchor(others, position):
    """The existing monitor the virtual one should sit next to."""
    if position == "left":
        edge = min(b.x for b in others)
        candidates = [b for b in others if b.x == edge]
    elif position == "right":
        edge = max(b.x + b.w for b in others)
        candidates = [b for b in others if b.x + b.w == edge]
    elif position == "above":
        edge = min(b.y for b in others)
        candidates = [b for b in others if b.y == edge]
    else:
        edge = max(b.y + b.h for b in others)
        candidates = [b for b in others if b.y + b.h == edge]
    return next((b for b in candidates if b.primary), candidates[0])


def _side(virtual, others):
    """Which side of the other monitors the virtual one is on now."""
    if virtual.x + virtual.w <= min(b.x for b in others):
        return "left"
    if virtual.x >= max(b.x + b.w for b in others):
        return "right"
    if virtual.y + virtual.h <= min(b.y for b in others):
        return "above"
    return "below"


def _supported(box, scale):
    """The supported scale closest to `scale` (Mutter rejects others)."""
    if not box.supported_scales:
        return scale
    best = min(box.supported_scales, key=lambda s: abs(s - scale))
    if abs(best - scale) > 0.01:
        log.warning("scale %g isn't supported for a %dx%d monitor; using %g (supported: %s)",
                    scale, box.mode_w, box.mode_h, best,
                    ", ".join(f"{s:g}" for s in box.supported_scales))
    return best


def _layout(boxes, position=None, scale=None):
    """Set the virtual box's scale and move it to `position` (default: the side
    it's on now, so a scale change keeps it there). Returns False if nothing changed."""
    virtual = next(b for b in boxes if b.virtual)
    others = [b for b in boxes if not b.virtual]
    position = position or _side(virtual, others)
    changed = False
    if scale:
        scale = _supported(virtual, scale)
        if abs(virtual.scale - scale) > 1e-6:
            virtual.scale = scale
            changed = True
    a = _anchor(others, position)
    target = {
        "left": (a.x - virtual.w, a.y),
        "right": (a.x + a.w, a.y),
        "above": (a.x, a.y - virtual.h),
        "below": (a.x, a.y + a.h),
    }[position]
    if (virtual.x, virtual.y) == target and not changed:
        return False
    virtual.x, virtual.y = target
    # Mutter wants the layout to start at (0, 0).
    dx, dy = min(b.x for b in boxes), min(b.y for b in boxes)
    for b in boxes:
        b.x -= dx
        b.y -= dy
    return True


def place_virtual_monitor(position=None, scale=None, timeout=5.0):
    """Wait for the virtual monitor to appear, then move it to `position` and/or
    set its GNOME `scale` (e.g. 2 for sharp text at full tablet resolution)."""
    bus = _bus()
    deadline = time.monotonic() + timeout
    while True:
        serial, mons, boxes = _state(bus)
        if any(b.virtual for b in boxes) and len(boxes) > 1:
            break
        if time.monotonic() > deadline:
            log.warning("virtual monitor did not show up; not positioning it")
            return
        time.sleep(0.1)

    virtual = next(b for b in boxes if b.virtual)
    if not _layout(boxes, position, scale):
        log.info("virtual monitor already %s of the main display at %g%%",
                 _side(virtual, [b for b in boxes if not b.virtual]), virtual.scale * 100)
        return
    config = [b.to_variant_tuple(mons) for b in boxes]
    try:
        bus.call_sync(
            BUS_NAME, OBJECT_PATH, IFACE, "ApplyMonitorsConfig",
            GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, METHOD_TEMPORARY, config, {})),
            None, Gio.DBusCallFlags.NONE, 5000, None,
        )
        log.info("placed virtual monitor %s of the main display at %g%%",
                 position or _side(virtual, [b for b in boxes if not b.virtual]), virtual.scale * 100)
    except GLib.Error as e:
        log.warning("could not position virtual monitor: %s", e.message)
