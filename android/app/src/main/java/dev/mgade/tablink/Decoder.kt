package dev.mgade.tablink

import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Build
import android.util.Log
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.util.concurrent.ConcurrentHashMap

/**
 * Low-latency H.264 decoder rendering straight to a Surface.
 * feed() is called from the network thread; a second thread drains output
 * and renders every frame as soon as it is decoded.
 */
class Decoder(
    private val surface: Surface,
    private val width: Int,
    private val height: Int,
    private val onFirstFrame: () -> Unit,
) {
    private var codec: MediaCodec? = null
    private var drainThread: Thread? = null
    @Volatile private var running = false
    private var lastConfig: ByteArray? = null

    // pts -> System.nanoTime() when the frame was queued, for latency logging
    private val queuedAt = ConcurrentHashMap<Long, Long>()
    private var statCount = 0
    private var statSumMs = 0.0
    private var statMaxMs = 0.0
    private var statSince = System.nanoTime()

    fun feed(ptsUs: Long, accessUnit: ByteArray) {
        val (config, frame) = splitConfig(accessUnit)
        if (config != null && !config.contentEquals(lastConfig)) {
            if (codec != null) release()  // stream parameters changed
            start()
            queue(config, 0, MediaCodec.BUFFER_FLAG_CODEC_CONFIG)
            lastConfig = config
        }
        if (codec == null) return  // waiting for SPS/PPS
        if (frame.isNotEmpty()) queue(frame, ptsUs, 0)
    }

    private fun start() {
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
        }
        format.setInteger(MediaFormat.KEY_PRIORITY, 0)  // realtime
        // Qualcomm (c2.qti.*) vendor switches: low-latency mode and output in
        // decode order instead of buffering frames for display reordering.
        // Ignored by other vendors' decoders.
        format.setInteger("vendor.qti-ext-dec-low-latency.enable", 1)
        format.setInteger("vendor.qti-ext-dec-picture-order.enable", 1)
        val c = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        c.configure(format, surface, null, 0)
        c.start()
        codec = c
        running = true
        drainThread = Thread({ drain(c) }, "decoder-drain").also { it.start() }
        Log.i(TAG, "decoder ${c.name} started ${width}x$height")
    }

    private fun queue(data: ByteArray, ptsUs: Long, flags: Int) {
        val c = codec ?: return
        val index = c.dequeueInputBuffer(100_000)
        if (index < 0) {
            Log.w(TAG, "no input buffer, dropping frame")
            return
        }
        val buf = c.getInputBuffer(index)!!
        buf.clear()
        buf.put(data)
        if (flags == 0) queuedAt[ptsUs] = System.nanoTime()
        c.queueInputBuffer(index, 0, data.size, ptsUs, flags)
    }

    private fun drain(c: MediaCodec) {
        val info = MediaCodec.BufferInfo()
        var first = true
        try {
            while (running) {
                val index = c.dequeueOutputBuffer(info, 10_000)
                if (index >= 0) {
                    c.releaseOutputBuffer(index, true)
                    queuedAt.remove(info.presentationTimeUs)?.let { recordLatency(it) }
                    if (first) {
                        first = false
                        onFirstFrame()
                    }
                }
            }
        } catch (e: IllegalStateException) {
            // codec released underneath us
        }
    }

    private fun recordLatency(queuedNs: Long) {
        val now = System.nanoTime()
        val ms = (now - queuedNs) / 1e6
        statCount++
        statSumMs += ms
        if (ms > statMaxMs) statMaxMs = ms
        val elapsed = (now - statSince) / 1e9
        if (elapsed >= 5.0) {
            Log.i(TAG, "decode: avg %.1f ms, max %.1f ms (%.0f fps)".format(
                statSumMs / statCount, statMaxMs, statCount / elapsed))
            statCount = 0; statSumMs = 0.0; statMaxMs = 0.0; statSince = now
        }
    }

    fun release() {
        running = false
        drainThread?.join(500)
        drainThread = null
        codec?.let {
            try { it.stop() } catch (_: Exception) {}
            it.release()
        }
        codec = null
        lastConfig = null
        queuedAt.clear()
    }

    companion object {
        private const val TAG = "Decoder"

        /**
         * Split an Annex-B access unit into (SPS+PPS, remaining NALs).
         * Config is null when the unit carries no SPS/PPS.
         */
        fun splitConfig(au: ByteArray): Pair<ByteArray?, ByteArray> {
            val starts = ArrayList<Int>()
            var i = 0
            while (i + 3 <= au.size) {
                if (au[i].toInt() == 0 && au[i + 1].toInt() == 0 && au[i + 2].toInt() == 1) {
                    // include a leading zero of a 4-byte start code
                    starts.add(if (i > 0 && au[i - 1].toInt() == 0) i - 1 else i)
                    i += 3
                } else {
                    i++
                }
            }
            if (starts.isEmpty()) return Pair(null, au)
            val config = ByteArrayOutputStream()
            val rest = ByteArrayOutputStream()
            for ((n, start) in starts.withIndex()) {
                val end = if (n + 1 < starts.size) starts[n + 1] else au.size
                var hdr = start
                while (hdr < end && au[hdr].toInt() == 0) hdr++
                if (hdr + 1 >= end) continue
                val nalType = au[hdr + 1].toInt() and 0x1f
                val target = if (nalType == 7 || nalType == 8) config else rest
                target.write(au, start, end - start)
            }
            return Pair(if (config.size() > 0) config.toByteArray() else null, rest.toByteArray())
        }
    }
}
