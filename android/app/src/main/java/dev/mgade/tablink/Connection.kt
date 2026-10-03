package dev.mgade.tablink

import android.util.Log
import android.view.Surface
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.ByteBuffer

/**
 * Connects to the host through `adb reverse` (127.0.0.1 on the tablet is the
 * desktop), announces the screen size and decodes the video it gets back.
 * Reconnects forever until stop() is called.
 */
class Connection(
    private val surface: Surface,
    private val width: Int,
    private val height: Int,
    private val dpi: Int,
    private val onStatus: (String?) -> Unit,  // null hides the overlay
) {
    @Volatile private var running = false
    @Volatile private var socket: Socket? = null
    private var thread: Thread? = null

    fun start() {
        running = true
        thread = Thread(::loop, "connection").also { it.start() }
    }

    fun stop() {
        running = false
        try { socket?.close() } catch (_: IOException) {}
        thread?.join(1000)
        thread = null
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
        var decoder: Decoder? = null
        try {
            s.tcpNoDelay = true
            s.receiveBufferSize = 1 shl 20
            s.connect(InetSocketAddress("127.0.0.1", Protocol.PORT), 1000)
            val input = DataInputStream(BufferedInputStream(s.getInputStream(), 1 shl 16))
            val output = DataOutputStream(BufferedOutputStream(s.getOutputStream()))
            Protocol.writeHello(output, width, height, dpi)

            while (running) {
                val msg = Protocol.read(input)
                when (msg.type) {
                    Protocol.CONFIG -> {
                        val b = ByteBuffer.wrap(msg.payload)
                        val w = b.short.toInt() and 0xffff
                        val h = b.short.toInt() and 0xffff
                        Log.i(TAG, "host monitor ${w}x$h")
                        onStatus("Connected — starting video…")
                        decoder?.release()
                        decoder = Decoder(surface, w, h) { onStatus(null) }
                    }
                    Protocol.VIDEO -> {
                        val b = ByteBuffer.wrap(msg.payload)
                        val pts = b.long
                        val au = ByteArray(b.remaining()).also { b.get(it) }
                        decoder?.feed(pts, au)
                    }
                    Protocol.PING -> synchronized(output) {
                        Protocol.write(output, Protocol.PONG, msg.payload)
                    }
                    else -> Log.w(TAG, "unknown message 0x${msg.type.toString(16)}")
                }
            }
        } finally {
            decoder?.release()
            try { s.close() } catch (_: IOException) {}
            socket = null
        }
    }

    companion object {
        private const val TAG = "Connection"
    }
}
