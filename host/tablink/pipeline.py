"""GStreamer pipeline: Mutter virtual monitor (PipeWire) -> H.264 access units."""

import logging
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)
log = logging.getLogger(__name__)


def have(element):
    return Gst.ElementFactory.find(element) is not None


def pick_encoder(requested):
    if requested in ("auto", "vaapi") and have("vaapih264enc") and have("vaapipostproc"):
        return "vaapi"
    if requested == "vaapi":
        log.warning("vaapih264enc/vaapipostproc not available, falling back to x264")
    if not have("x264enc"):
        raise RuntimeError("no H.264 encoder found (install gstreamer1.0-vaapi or gstreamer1.0-plugins-ugly)")
    return "x264"


def describe(node_id, width, height, fps, bitrate_kbps, encoder, zero_copy=False):
    """zero_copy (VAAPI only): take Mutter's frames as DMA-BUF straight into
    vaapipostproc instead of copying each one into system memory first."""
    gop = fps * 2
    src = f"pipewiresrc path={node_id} do-timestamp=true keepalive-time=1000"
    if zero_copy:
        raw_caps = f"video/x-raw(memory:DMABuf),width={width},height={height},max-framerate={fps}/1"
    else:
        src += " always-copy=true"
        raw_caps = f"video/x-raw,width={width},height={height},max-framerate={fps}/1"
    if encoder == "vaapi":
        enc = (
            "vaapipostproc ! video/x-raw(memory:VASurface),format=NV12 ! "
            f"vaapih264enc rate-control=cbr bitrate={bitrate_kbps} keyframe-period={gop} "
            "max-bframes=0 quality-level=7 cpb-length=100"
        )
    else:
        enc = (
            "videoconvert n-threads=4 ! video/x-raw,format=I420 ! "
            f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate_kbps} "
            # x264 only sends SPS/PPS once unless told to repeat them on every
            # keyframe (VAAPI always does); the app needs them whenever it resumes.
            f"key-int-max={gop} bframes=0 option-string=repeat-headers=1"
        )
    # Constrained baseline tells the decoder there is no frame reordering, so it
    # can output each frame as soon as it is decoded. With High profile the
    # Qualcomm decoder holds frames back, which shows up as lag and as a stuck
    # last frame when the screen goes static.
    # One caps filter right after the encoder (GStreamer can't parse two in a row).
    out_caps = "video/x-h264,profile=constrained-baseline,stream-format=byte-stream,alignment=au"
    # Android decoders want SPS/PPS before every IDR so a late (re)start works.
    # Both encoders already do that; h264parse (gstreamer1.0-plugins-bad, which
    # drags in GTK and more) is only used when it happens to be installed.
    parse = "h264parse config-interval=-1 ! " if have("h264parse") else ""
    sink = "appsink name=sink sync=false emit-signals=true max-buffers=4 drop=false"
    return f"{src} ! {raw_caps} ! queue max-size-buffers=2 leaky=downstream ! {enc} ! {out_caps} ! {parse}{sink}"


class LatencyStats:
    """Logs average/max of a stream of millisecond samples every `period` seconds."""

    def __init__(self, label, period=5.0):
        self.label = label
        self.period = period
        self._reset(time.monotonic())

    def _reset(self, now):
        self.samples = []
        self.since = now

    def add(self, ms):
        self.samples.append(ms)
        now = time.monotonic()
        if now - self.since >= self.period:
            n = len(self.samples)
            log.debug("%s: avg %.1f ms, max %.1f ms (%.0f fps)", self.label,
                     sum(self.samples) / n, max(self.samples), n / (now - self.since))
            self._reset(now)


class EncoderPipeline:
    """Runs the pipeline; on_frame(pts_us, data: bytes, keyframe: bool) is called
    from a GStreamer streaming thread. on_error(message) from the GLib main loop."""

    def __init__(self, node_id, width, height, fps, bitrate_kbps, encoder, on_frame, on_error):
        self.encoder = pick_encoder(encoder)
        self._args = (node_id, width, height, fps, bitrate_kbps, self.encoder)
        # Zero-copy capture when it can work; falls back to copying if the first
        # attempt fails to negotiate (older PipeWire/Mutter, other drivers).
        self.zero_copy = self.encoder == "vaapi"
        self._got_frame = False
        self._lock = threading.Lock()
        self._generation = 0  # bumped per build; bus messages from older builds are ignored
        self._on_frame = on_frame
        self._on_error = on_error
        self._latency = LatencyStats("capture→encoded")
        self._build()

    def _build(self):
        self._generation += 1
        desc = describe(*self._args, zero_copy=self.zero_copy)
        log.debug("pipeline: %s", desc)
        self.pipeline = Gst.parse_launch(desc)
        self._sink = self.pipeline.get_by_name("sink")
        self._sink.connect("new-sample", self._on_sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error, self._generation)
        bus.connect("message::eos", lambda *_: self._on_error("end of stream"))

    def start(self):
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            if not self._fall_back("pipeline failed to start", self._generation):
                raise RuntimeError("pipeline failed to start")
            return
        log.info("encoding with %s (%s)", self.encoder,
                 "zero-copy DMA-BUF capture" if self.zero_copy else "copying frames")

    def _fall_back(self, reason, generation):
        """Switch from zero-copy to copying frames, once and only before the first
        frame. A failed negotiation shows up both as start() failing and as a bus
        error, so both paths come through here. Returns True if the failure is
        handled (now copying, or it came from a pipeline already replaced)."""
        with self._lock:
            if generation != self._generation:
                return True
            if not self.zero_copy or self._got_frame:
                return False
            log.info("zero-copy capture not available (%s); copying frames instead", reason)
            self.stop()
            self.zero_copy = False
            self._build()
        self.start()
        return True

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.pipeline.get_bus().remove_signal_watch()

    def request_keyframe(self):
        # Same event GstVideo.video_event_new_upstream_force_key_unit() builds,
        # without needing the GstVideo typelib.
        s = Gst.Structure.new_from_string("GstForceKeyUnit, all-headers=(boolean)true, count=(uint)0")
        self._sink.send_event(Gst.Event.new_custom(Gst.EventType.CUSTOM_UPSTREAM, s))

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = bytes(info.data)
        finally:
            buf.unmap(info)
        pts = buf.pts
        if pts != Gst.CLOCK_TIME_NONE:
            clock = self.pipeline.get_clock()
            if clock:
                now = clock.get_time() - self.pipeline.get_base_time()
                self._latency.add((now - pts) / 1e6)
        pts_us = pts // 1000 if pts != Gst.CLOCK_TIME_NONE else time.monotonic_ns() // 1000
        keyframe = not buf.has_flags(Gst.BufferFlags.DELTA_UNIT)
        self._got_frame = True
        self._on_frame(pts_us, data, keyframe)
        return Gst.FlowReturn.OK

    def _on_bus_error(self, _bus, msg, generation):
        err, dbg = msg.parse_error()
        log.debug("gstreamer debug: %s", dbg)
        # Typically "not-negotiated": this system can't share DMA-BUFs here.
        if self._fall_back(err.message, generation):
            return
        self._on_error(f"{msg.src.get_name()}: {err.message}")
