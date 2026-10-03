import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tablink import protocol  # noqa: E402


class ProtocolTest(unittest.TestCase):
    def test_hello_roundtrip(self):
        msgs = protocol.Reader().feed(protocol.pack_hello(2560, 1600, 320, "abc123"))
        self.assertEqual(len(msgs), 1)
        msg_type, payload = msgs[0]
        self.assertEqual(msg_type, protocol.HELLO)
        self.assertEqual(protocol.unpack_hello(payload),
                         {"width": 2560, "height": 1600, "dpi": 320, "version": protocol.PROTO_VERSION,
                          "token": "abc123"})

    def test_video_layout(self):
        au = b"\x00\x00\x00\x01\x65" + b"x" * 100
        data = protocol.pack_video(123456789, au)
        msg_type, length = struct.unpack(">BI", data[:5])
        self.assertEqual(msg_type, protocol.VIDEO)
        self.assertEqual(length, 8 + len(au))
        self.assertEqual(struct.unpack(">Q", data[5:13])[0], 123456789)
        self.assertEqual(data[13:], au)

    def test_reader_handles_fragmentation(self):
        stream = protocol.pack_ping(1) + protocol.pack_hello(800, 600, 160, "t") + protocol.pack_ping(2)
        reader = protocol.Reader()
        msgs = []
        for i in range(len(stream)):
            msgs += reader.feed(stream[i:i + 1])
        self.assertEqual([t for t, _ in msgs], [protocol.PING, protocol.HELLO, protocol.PING])

    def test_visibility(self):
        msgs = protocol.Reader().feed(protocol.pack_visibility(False) + protocol.pack_visibility(True))
        self.assertEqual(msgs, [(protocol.VISIBILITY, b"\x00"), (protocol.VISIBILITY, b"\x01")])

    def test_touch_roundtrip(self):
        ((msg_type, payload),) = protocol.Reader().feed(protocol.pack_touch(1, 3, 740.5, 12.25))
        self.assertEqual(msg_type, protocol.TOUCH)
        self.assertEqual(protocol.unpack_touch(payload), (1, 3, 740.5, 12.25))

    def test_reader_rejects_oversized(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.Reader().feed(struct.pack(">BI", protocol.PING, 10_000_000))

    def test_bad_hello(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.unpack_hello(b"\x00\x01")


if __name__ == "__main__":
    unittest.main()
