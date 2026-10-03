import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tablink import protocol  # noqa: E402
from tablink.server import HELLO_TIMEOUT, Server, Session  # noqa: E402


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


class SlotTest(unittest.TestCase):
    """A client without the token can't hold up the real app."""

    def setUp(self):
        self.server = Server(SimpleNamespace(token="s3cret", port=0))
        self.server.opts.token = "s3cret"  # Server() reads TABLINK_TOKEN; pin it for the test
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        threading.Thread(target=self.server._acceptor, args=(self.listener,), daemon=True).start()
        self.addr = self.listener.getsockname()

    def tearDown(self):
        self.listener.close()

    def test_silent_client_does_not_block_others(self):
        silent = socket.create_connection(self.addr)  # connects, never says anything
        other = socket.create_connection(self.addr)
        start = time.monotonic()
        other.sendall(protocol.pack_hello(800, 600, 160, "guess"))
        other.settimeout(HELLO_TIMEOUT / 2)
        self.assertEqual(other.recv(16), b"")  # rejected right away, not after the silent one
        self.assertLess(time.monotonic() - start, HELLO_TIMEOUT / 2)
        silent.settimeout(HELLO_TIMEOUT * 3)
        self.assertEqual(silent.recv(16), b"")  # and the silent one is dropped after the timeout
        silent.close()
        other.close()

    def test_takeover_ends_previous_session(self):
        class Fake:
            def __init__(self, addr):
                self.addr, self.finished, self.ended = addr, threading.Event(), False

            def end(self):
                self.ended = True
                self.finished.set()

        old, new = Fake("old"), Fake("new")
        self.server._takeover(old)
        self.server._takeover(new)
        self.assertTrue(old.ended)
        self.assertFalse(new.ended)
        self.assertIs(self.server._active, new)


if __name__ == "__main__":
    unittest.main()
