import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tablink import protocol  # noqa: E402
from tablink.server import Session  # noqa: E402


class TokenTest(unittest.TestCase):
    def hello(self, data):
        host, app = socket.socketpair()
        with host, app:
            app.sendall(data)
            return Session(host, "test", SimpleNamespace(token="s3cret"))._read_hello()

    def test_right_token_accepted(self):
        self.assertEqual(self.hello(protocol.pack_hello(800, 600, 160, "s3cret"))["width"], 800)

    def test_wrong_token_rejected(self):
        with self.assertRaises(protocol.AuthError):
            self.hello(protocol.pack_hello(800, 600, 160, "guess"))

    def test_missing_token_rejected(self):
        with self.assertRaises(protocol.AuthError):
            self.hello(protocol.pack_hello(800, 600, 160, ""))

    def test_old_protocol_rejected(self):
        old = protocol.frame(protocol.HELLO, protocol.HELLO_BODY.pack(800, 600, 160, 1))
        with self.assertRaises(protocol.ProtocolError):
            self.hello(old)


if __name__ == "__main__":
    unittest.main()
