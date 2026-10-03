package dev.mgade.tablink

import android.util.Log
import android.view.MotionEvent
import android.view.Surface
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer
import java.util.concurrent.Executors

/**
 * Connects to the host through `adb reverse` (127.0.0.1 on the tablet is the
 * desktop), announces the screen size and decodes the video it gets back.
 * Reconnects forever until stop() is called.
 *
 * The connection outlives the screen: attach()/detach() a Surface as the app
 * comes and goes. While detached the host keeps the virtual monitor but sends
 * no video (VISIBILITY 0).
 */
class Connection(
    val width: Int,
    val height: Int,
    private val dpi: Int,
    private val onStatus: (String?) -> Unit,  // null: video is showing
) {
    @Volatile private var running = false
    @Volatile private var socket: Socket? = null
    @Volatile private var output: DataOutputStream? = null
    @Volatile var connected = false
        private set
    private var thread: Thread? = null
    // Network writes from attach()/detach() (main thread) go through here.
    private val sender = Executors.newSingleThreadExecutor()

    // Guarded by lock: shared between the main thread (attach/detach) and the
    // connection thread (CONFIG/VIDEO).
    private val lock = Any()
    private var surface: Surface? = null
    private var decoder: Decoder? = null
    private var videoWidth = 0
    private var videoHeight = 0

    fun start() {
        running = true
        thread = Thread(::loop, "connection").also { it.start() }
    }

    fun stop() {
        running = false
        try { socket?.close() } catch (_: IOException) {}
        thread?.join(1000)
        thread = null
        sender.shutdownNow()
        synchronized(lock) { replaceDecoder(null) }
    }

    /** The app is showing: render into [s]. Repeat calls with the same surface are ignored. */
    fun attach(s: Surface) {
        val changed = synchronized(lock) {
            if (surface === s) return@synchronized false
            surface = s
            replaceDecoder(s)
            true
        }
        if (changed) sendVisibility(true)
    }

    /** The app went to the background and its surface is going away. Idempotent. */
    fun detach() {
        val changed = synchronized(lock) {
            if (surface == null) return@synchronized false
            surface = null
            replaceDecoder(null)
            true
        }
        if (changed) sendVisibility(false)
    }

    /** Must hold [lock]. A fresh decoder waits for the next keyframe's SPS/PPS. */
    private fun replaceDecoder(s: Surface?) {
        decoder?.release()
        decoder = if (s != null && videoWidth > 0) {
            Decoder(s, videoWidth, videoHeight) { onStatus(null) }
        } else {
            null
        }
    }

    /**
     * Forward a touch event on a view of [viewWidth]x[viewHeight] pixels, scaled to
     * the host's virtual monitor. Each finger is a slot (its pointer id).
     */
    fun sendTouch(event: MotionEvent, viewWidth: Int, viewHeight: Int) {
        val out = output ?: return
        val (sx, sy) = synchronized(lock) {
            if (videoWidth == 0) return
            Pair(videoWidth.toFloat() / viewWidth, videoHeight.toFloat() / viewHeight)
        }
        val bytes = ByteArrayOutputStream(64)
        val data = DataOutputStream(bytes)
        fun touch(action: Int, index: Int) = Protocol.writeTouch(
            data, action, event.getPointerId(index) and 0xff, event.getX(index) * sx, event.getY(index) * sy)
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> touch(Protocol.TOUCH_DOWN, event.actionIndex)
            MotionEvent.ACTION_MOVE -> for (i in 0 until event.pointerCount) touch(Protocol.TOUCH_MOVE, i)
            MotionEvent.ACTION_UP, MotionEvent.ACTION_POINTER_UP -> touch(Protocol.TOUCH_UP, event.actionIndex)
            MotionEvent.ACTION_CANCEL -> for (i in 0 until event.pointerCount) touch(Protocol.TOUCH_UP, i)
            else -> return
        }
        val payload = bytes.toByteArray()
        try {
            sender.execute {
                try {
                    synchronized(out) { out.write(payload); out.flush() }
                } catch (e: IOException) {
                    Log.d(TAG, "touch: ${e.message}")
                }
            }
        } catch (_: java.util.concurrent.RejectedExecutionException) {
            // stopped
        }
    }

    private fun sendVisibility(visible: Boolean) {
        val out = output ?: return  // sent with the HELLO on the next connect
        try {
            sender.execute {
                try {
                    synchronized(out) { Protocol.write(out, Protocol.VISIBILITY, byteArrayOf(if (visible) 1 else 0)) }
                } catch (e: IOException) {
                    Log.d(TAG, "visibility: ${e.message}")
                }
            }
        } catch (_: java.util.concurrent.RejectedExecutionException) {
            // stopped
        }
    }

    private fun loop() {
        var backoffMs = 500L
        while (running) {
            onStatus("Waiting for host…\nStart TabLink on the desktop\n(${width}x$height)")
            try {
                session()
                backoffMs = 500L
            } catch (e: IOException) {
                Log.d(TAG, "connection: ${e.message}")
            }
            if (!running) break
            try { Thread.sleep(backoffMs) } catch (_: InterruptedException) { break }
            backoffMs = (backoffMs * 2).coerceAtMost(2000L)
        }
    }

    private fun session() {
        val s = Socket()
        socket = s
        try {
            s.tcpNoDelay = true
            s.receiveBufferSize = 1 shl 20
            s.connect(InetSocketAddress("127.0.0.1", Protocol.PORT), 1000)
            val input = DataInputStream(BufferedInputStream(s.getInputStream(), 1 shl 16))
            val out = DataOutputStream(BufferedOutputStream(s.getOutputStream()))
            synchronized(out) {
                Protocol.writeHello(out, width, height, dpi)
                val visible = synchronized(lock) { surface != null }
                Protocol.write(out, Protocol.VISIBILITY, byteArrayOf(if (visible) 1 else 0))
            }
            output = out
            connected = true

            while (running) {
                val msg = Protocol.read(input)
                when (msg.type) {
                    Protocol.CONFIG -> {
                        val b = ByteBuffer.wrap(msg.payload)
                        val w = b.short.toInt() and 0xffff
                        val h = b.short.toInt() and 0xffff
                        Log.i(TAG, "host monitor ${w}x$h")
                        onStatus("Connected — starting video…")
                        synchronized(lock) {
                            videoWidth = w
                            videoHeight = h
                            replaceDecoder(surface)
                        }
                    }
                    Protocol.VIDEO -> {
                        val b = ByteBuffer.wrap(msg.payload)
                        val pts = b.long
                        val au = ByteArray(b.remaining()).also { b.get(it) }
                        synchronized(lock) { decoder?.feed(pts, au) }
                    }
                    Protocol.PING -> synchronized(out) {
                        Protocol.write(out, Protocol.PONG, msg.payload)
                    }
                    else -> Log.w(TAG, "unknown message 0x${msg.type.toString(16)}")
                }
            }
        } finally {
            connected = false
            output = null
            synchronized(lock) {
                videoWidth = 0
                replaceDecoder(null)
            }
            try { s.close() } catch (_: IOException) {}
            socket = null
        }
    }

    companion object {
        private const val TAG = "Connection"
    }
}
