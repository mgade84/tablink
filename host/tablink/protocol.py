"""Wire protocol shared with the Android app (see android/.../Protocol.kt).

Every message is framed as ``[type:u8][len:u32][payload]``, big-endian.
"""

import struct

PROTO_VERSION = 1
DEFAULT_PORT = 27183

HELLO = 0x01   # app -> host: u16 width, u16 height, u16 dpi, u8 protoVersion
CONFIG = 0x02  # host -> app: u16 width, u16 height
VIDEO = 0x10   # host -> app: u64 ptsUs + Annex-B H.264 access unit
PING = 0x30    # either way: u64 timestamp (opaque to the receiver)
PONG = 0x31    # echo of PING payload

HEADER = struct.Struct(">BI")
HELLO_BODY = struct.Struct(">HHHB")
CONFIG_BODY = struct.Struct(">HH")
PTS = struct.Struct(">Q")

MAX_INBOUND_PAYLOAD = 64 * 1024  # the app only ever sends tiny messages


class ProtocolError(Exception):
    pass


def frame(msg_type, payload=b""):
    return HEADER.pack(msg_type, len(payload)) + payload


def pack_hello(width, height, dpi, version=PROTO_VERSION):
    return frame(HELLO, HELLO_BODY.pack(width, height, dpi, version))


def unpack_hello(payload):
    if len(payload) != HELLO_BODY.size:
        raise ProtocolError(f"bad HELLO length {len(payload)}")
    width, height, dpi, version = HELLO_BODY.unpack(payload)
    return {"width": width, "height": height, "dpi": dpi, "version": version}


def pack_config(width, height):
    return frame(CONFIG, CONFIG_BODY.pack(width, height))


def pack_video(pts_us, access_unit):
    return frame(VIDEO, PTS.pack(pts_us) + access_unit)


def pack_ping(stamp):
    return frame(PING, PTS.pack(stamp))


class Reader:
    """Incremental decoder: feed() bytes, iterate complete (type, payload) messages."""

    def __init__(self, max_payload=MAX_INBOUND_PAYLOAD):
        self._buf = bytearray()
        self._max = max_payload

    def feed(self, data):
        self._buf += data
        out = []
        while len(self._buf) >= HEADER.size:
            msg_type, length = HEADER.unpack_from(self._buf)
            if length > self._max:
                raise ProtocolError(f"payload too large: {length}")
            end = HEADER.size + length
            if len(self._buf) < end:
                break
            out.append((msg_type, bytes(self._buf[HEADER.size:end])))
            del self._buf[:end]
        return out
