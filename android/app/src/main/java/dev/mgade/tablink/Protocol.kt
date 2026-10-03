package dev.mgade.tablink

import java.io.DataInputStream
import java.io.DataOutputStream

/**
 * Mirror of host/tablink/protocol.py.
 * Every message is framed as [type:u8][len:u32][payload], big-endian.
 */
object Protocol {
    const val VERSION = 2
    const val PORT = 27183

    const val HELLO = 0x01   // app -> host: u16 width, u16 height, u16 dpi, u8 version
    const val CONFIG = 0x02  // host -> app: u16 width, u16 height
    const val STOPPED = 0x04  // host -> app: the desktop user ended the session; don't reconnect
    const val VISIBILITY = 0x03  // app -> host: u8 visible (0 pauses video, monitor stays)
    const val VIDEO = 0x10   // host -> app: u64 ptsUs + Annex-B H.264 access unit
    const val TOUCH = 0x20   // app -> host: u8 action, u8 slot, f32 x, f32 y (monitor pixels)

    const val TOUCH_DOWN = 0
    const val TOUCH_MOVE = 1
    const val TOUCH_UP = 2
    const val PING = 0x30
    const val PONG = 0x31

    const val MAX_PAYLOAD = 16 * 1024 * 1024

    class Message(val type: Int, val payload: ByteArray)

    /** HELLO carries the session token the desktop launched us with (see TabLinkService). */
    fun writeHello(out: DataOutputStream, width: Int, height: Int, dpi: Int, token: String) {
        val tokenBytes = token.toByteArray(Charsets.US_ASCII)
        out.writeByte(HELLO)
        out.writeInt(7 + tokenBytes.size)
        out.writeShort(width)
        out.writeShort(height)
        out.writeShort(dpi)
        out.writeByte(VERSION)
        out.write(tokenBytes)
        out.flush()
    }

    fun write(out: DataOutputStream, type: Int, payload: ByteArray) {
        out.writeByte(type)
        out.writeInt(payload.size)
        out.write(payload)
        out.flush()
    }

    /** Appends one TOUCH message to [out]. */
    fun writeTouch(out: DataOutputStream, action: Int, slot: Int, x: Float, y: Float) {
        out.writeByte(TOUCH)
        out.writeInt(10)
        out.writeByte(action)
        out.writeByte(slot)
        out.writeFloat(x)
        out.writeFloat(y)
    }

    fun read(input: DataInputStream): Message {
        val type = input.readUnsignedByte()
        val len = input.readInt()
        if (len < 0 || len > MAX_PAYLOAD) throw java.io.IOException("bad payload length $len")
        val payload = ByteArray(len)
        input.readFully(payload)
        return Message(type, payload)
    }
}
