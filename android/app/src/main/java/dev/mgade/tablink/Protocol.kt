package dev.mgade.tablink

import java.io.DataInputStream
import java.io.DataOutputStream

/**
 * Mirror of host/tablink/protocol.py.
 * Every message is framed as [type:u8][len:u32][payload], big-endian.
 */
object Protocol {
    const val VERSION = 1
    const val PORT = 27183

    const val HELLO = 0x01   // app -> host: u16 width, u16 height, u16 dpi, u8 version
    const val CONFIG = 0x02  // host -> app: u16 width, u16 height
    const val VIDEO = 0x10   // host -> app: u64 ptsUs + Annex-B H.264 access unit
    const val PING = 0x30
    const val PONG = 0x31

    const val MAX_PAYLOAD = 16 * 1024 * 1024

    class Message(val type: Int, val payload: ByteArray)

    fun writeHello(out: DataOutputStream, width: Int, height: Int, dpi: Int) {
        out.writeByte(HELLO)
        out.writeInt(7)
        out.writeShort(width)
        out.writeShort(height)
        out.writeShort(dpi)
        out.writeByte(VERSION)
        out.flush()
    }

    fun write(out: DataOutputStream, type: Int, payload: ByteArray) {
        out.writeByte(type)
        out.writeInt(payload.size)
        out.write(payload)
        out.flush()
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
