"""Unit tests for tools/ws_server.py.

Coverage layout:

* Handshake — accept path generates the correct accept key,
  validates required headers, surfaces missing-header errors.
* Frame parser — round-trips through a real socketpair: bytes
  written by ``encode_frame`` (with a synthetic mask applied to
  simulate a client) parse back to the original payload.
* Control-frame handling — ping replies with pong; peer-initiated
  close surfaces as ``recv() is None`` and tears the connection.
* Fragmentation — multi-fragment data messages reassemble in
  order, and a continuation frame with no prior data frame raises.
* Protocol violations — unmasked client frame, oversize payload,
  control-frame fragmentation all raise ``WebSocketError``.
* Thread safety — concurrent send_text calls don't interleave
  frame bytes on the wire.

The tests use a real ``socket.socketpair()`` rather than a mock so
behaviour under ``recv()``, ``sendall()``, ``shutdown()`` matches
production. They never touch the network.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path
from typing import Optional


# Make tools/ importable without installing the package. Mirrors
# the pattern in test_pdf_chunks.py — tests are stdlib-only and
# don't depend on rules_python.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = _REPO_ROOT / "tools"
sys.path.insert(0, str(_TOOLS))


import ws_server  # noqa: E402  (sys.path mutation above)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _client_encode(
    opcode: int,
    payload: bytes,
    *,
    fin: bool = True,
    mask_key: bytes = b"\x37\xfa\x21\x3d",
) -> bytes:
    """Encode a client→server frame *with* masking, the way a
    browser would send it. Tests use this to feed parse_frame.
    """
    b1 = (0x80 if fin else 0x00) | (opcode & 0x0F)
    n = len(payload)
    if n < 126:
        header = struct.pack("!BB", b1, n | 0x80)
    elif n < (1 << 16):
        header = struct.pack("!BBH", b1, 126 | 0x80, n)
    else:
        header = struct.pack("!BBQ", b1, 127 | 0x80, n)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return header + mask_key + masked


def _socketpair() -> tuple[socket.socket, socket.socket]:
    """A connected (client, server) pair on the loopback. Test
    helpers use raw socket pairs rather than going through any HTTP
    layer because the WS server module operates below HTTP.
    """
    a, b = socket.socketpair()
    return a, b


# ---------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------


class HandshakeTest(unittest.TestCase):
    def test_compute_accept_key_known_vector(self):
        # RFC 6455 §1.3 example: key "dGhlIHNhbXBsZSBub25jZQ==" must
        # produce "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=". Constant-folding-
        # safe golden value used by every WebSocket implementation.
        self.assertEqual(
            ws_server.compute_accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )

    def test_accept_writes_101_response(self):
        sink: list[bytes] = []
        ws_server.accept_websocket(
            method="GET",
            headers={
                "upgrade": "websocket",
                "connection": "Upgrade",
                "sec-websocket-version": "13",
                "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
            write=sink.append,
        )
        out = b"".join(sink)
        self.assertIn(b"HTTP/1.1 101 Switching Protocols\r\n", out)
        self.assertIn(b"Upgrade: websocket\r\n", out)
        self.assertIn(b"Connection: Upgrade\r\n", out)
        self.assertIn(
            b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n",
            out,
        )
        self.assertTrue(out.endswith(b"\r\n\r\n"))

    def test_accept_handles_connection_token_list(self):
        # Real-world Connection headers from Chromium / Firefox
        # are usually "keep-alive, Upgrade". The accept path has to
        # split on commas, not require exact-match.
        sink: list[bytes] = []
        ws_server.accept_websocket(
            method="GET",
            headers={
                "upgrade": "WebSocket",  # case insensitive
                "connection": "keep-alive, Upgrade",
                "sec-websocket-version": "13",
                "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
            write=sink.append,
        )
        self.assertIn(b"101 Switching Protocols", b"".join(sink))

    def test_accept_rejects_wrong_method(self):
        with self.assertRaises(ws_server.WebSocketHandshakeError):
            ws_server.accept_websocket(
                method="POST",
                headers={
                    "upgrade": "websocket",
                    "connection": "Upgrade",
                    "sec-websocket-version": "13",
                    "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
                },
                write=lambda _b: None,
            )

    def test_accept_rejects_missing_key(self):
        with self.assertRaises(ws_server.WebSocketHandshakeError):
            ws_server.accept_websocket(
                method="GET",
                headers={
                    "upgrade": "websocket",
                    "connection": "Upgrade",
                    "sec-websocket-version": "13",
                },
                write=lambda _b: None,
            )

    def test_accept_rejects_old_version(self):
        # Drafts 76 and earlier are long dead but rejecting them
        # cleanly is part of the spec's compliance contract.
        with self.assertRaises(ws_server.WebSocketHandshakeError):
            ws_server.accept_websocket(
                method="GET",
                headers={
                    "upgrade": "websocket",
                    "connection": "Upgrade",
                    "sec-websocket-version": "8",
                    "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
                },
                write=lambda _b: None,
            )


# ---------------------------------------------------------------------
# Frame round-trip
# ---------------------------------------------------------------------


class FrameRoundTripTest(unittest.TestCase):
    def test_short_text_message(self):
        client, server = _socketpair()
        try:
            client.sendall(_client_encode(ws_server.OP_TEXT, b"hello"))
            fin, opcode, payload = ws_server.parse_frame(server)
            self.assertTrue(fin)
            self.assertEqual(opcode, ws_server.OP_TEXT)
            self.assertEqual(payload, b"hello")
        finally:
            client.close()
            server.close()

    def test_extended_16bit_length(self):
        # 200-byte payload triggers the 16-bit extended length path.
        client, server = _socketpair()
        try:
            big = b"A" * 200
            client.sendall(_client_encode(ws_server.OP_BINARY, big))
            fin, opcode, payload = ws_server.parse_frame(server)
            self.assertTrue(fin)
            self.assertEqual(opcode, ws_server.OP_BINARY)
            self.assertEqual(payload, big)
        finally:
            client.close()
            server.close()

    def test_extended_64bit_length(self):
        # 70 KB triggers the 64-bit extended length path. Drive the
        # write from a worker thread so we don't deadlock when the
        # payload exceeds the ~64 KB socketpair buffer — the reader
        # in this thread needs to be draining concurrently.
        client, server = _socketpair()
        try:
            big = os.urandom(70 * 1024)

            def writer():
                client.sendall(_client_encode(ws_server.OP_BINARY, big))

            t = threading.Thread(target=writer)
            t.start()
            fin, opcode, payload = ws_server.parse_frame(server)
            t.join(timeout=5)
            self.assertFalse(t.is_alive(), "writer thread did not exit")
            self.assertTrue(fin)
            self.assertEqual(opcode, ws_server.OP_BINARY)
            self.assertEqual(payload, big)
        finally:
            client.close()
            server.close()

    def test_unmasked_client_frame_rejected(self):
        # Server frames are never masked, so encode_frame's output
        # fed in the client direction must trip the missing-mask
        # check. RFC 6455 §5.1.
        client, server = _socketpair()
        try:
            client.sendall(ws_server.encode_frame(ws_server.OP_TEXT, b"oops"))
            with self.assertRaises(ws_server.WebSocketError):
                ws_server.parse_frame(server)
        finally:
            client.close()
            server.close()

    def test_oversize_frame_rejected(self):
        # We can't actually allocate MAX_MESSAGE_SIZE+1 to send, but
        # we can craft a header that *claims* a payload past the cap
        # and verify the parser rejects it before reading payload
        # bytes (so we don't hang waiting for them).
        oversize = ws_server.MAX_MESSAGE_SIZE + 1
        # opcode=binary, fin=1, mask=1, len=127 → next 8 bytes are
        # the 64-bit length. Then mask key. We don't bother writing
        # the (huge) payload; parser should bail before reading it.
        header = struct.pack(
            "!BBQ",
            0x80 | ws_server.OP_BINARY,
            127 | 0x80,
            oversize,
        )
        header += b"\x00\x00\x00\x00"  # mask key

        client, server = _socketpair()
        try:
            client.sendall(header)
            with self.assertRaises(ws_server.WebSocketError):
                ws_server.parse_frame(server)
        finally:
            client.close()
            server.close()

    def test_control_frame_fragmentation_rejected(self):
        # PING with fin=0 violates §5.5; parser must catch it
        # *before* trying to interpret as a continuation.
        client, server = _socketpair()
        try:
            client.sendall(
                _client_encode(ws_server.OP_PING, b"", fin=False),
            )
            with self.assertRaises(ws_server.WebSocketError):
                ws_server.parse_frame(server)
        finally:
            client.close()
            server.close()

    def test_control_frame_oversize_rejected(self):
        # Control frames cap payload at 125 bytes (§5.5).
        client, server = _socketpair()
        try:
            client.sendall(
                _client_encode(ws_server.OP_PING, b"X" * 126),
            )
            with self.assertRaises(ws_server.WebSocketError):
                ws_server.parse_frame(server)
        finally:
            client.close()
            server.close()


# ---------------------------------------------------------------------
# Connection-level behaviour
# ---------------------------------------------------------------------


class ConnectionTest(unittest.TestCase):
    def test_recv_returns_message_for_text(self):
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            client.sendall(_client_encode(ws_server.OP_TEXT, b"hi"))
            msg = conn.recv()
            self.assertIsNotNone(msg)
            self.assertEqual(msg.opcode, ws_server.OP_TEXT)
            self.assertEqual(msg.payload, b"hi")
        finally:
            conn.close()
            client.close()

    def test_ping_is_answered_with_pong(self):
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            client.sendall(_client_encode(ws_server.OP_PING, b"pingdata"))
            # recv() should swallow the ping and block waiting for
            # the next data frame; drive it on a worker so the test
            # can read the pong reply from the client side.
            thread_msg: list[Optional[ws_server.Message]] = []

            def drive():
                client.sendall(_client_encode(ws_server.OP_TEXT, b"after"))
                thread_msg.append(conn.recv())

            t = threading.Thread(target=drive)
            t.start()

            # Read the server's pong reply off the client socket.
            # The server writes pong unmasked.
            header = client.recv(2)
            self.assertEqual(header[0] & 0x0F, ws_server.OP_PONG)
            payload_len = header[1] & 0x7F
            self.assertEqual(payload_len, len(b"pingdata"))
            pong_payload = client.recv(payload_len)
            self.assertEqual(pong_payload, b"pingdata")

            t.join(timeout=2)
            self.assertFalse(t.is_alive())
            self.assertEqual(thread_msg[0].payload, b"after")
        finally:
            conn.close()
            client.close()

    def test_peer_close_returns_none(self):
        # A clean close frame from the peer should surface as
        # recv() == None and trigger the connection to mirror back
        # its own close frame (verifiable on the client socket).
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            close_payload = struct.pack("!H", int(ws_server.CloseCode.NORMAL))
            client.sendall(_client_encode(ws_server.OP_CLOSE, close_payload))
            msg = conn.recv()
            self.assertIsNone(msg)

            # Server's reply close frame is on the wire — read the
            # header to confirm opcode 0x8.
            reply = client.recv(4)
            self.assertEqual(reply[0] & 0x0F, ws_server.OP_CLOSE)
        finally:
            conn.close()
            client.close()

    def test_send_binary_round_trip(self):
        # Drive a frame *out* of the server back to the client to
        # cover the un-masked send path and confirm the client can
        # parse what the server emits. We hand-decode the frame on
        # the client side rather than spinning up another
        # WebSocketConnection (which expects masked input).
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            payload = b"\x00\x01\x02\x03binary"
            conn.send_binary(payload)
            header = client.recv(2)
            self.assertEqual(header[0] & 0x0F, ws_server.OP_BINARY)
            self.assertFalse(header[1] & 0x80, "server frame must not be masked")
            self.assertEqual(header[1] & 0x7F, len(payload))
            self.assertEqual(client.recv(len(payload)), payload)
        finally:
            conn.close()
            client.close()

    def test_concurrent_sends_dont_interleave(self):
        # Spam two threads sending text concurrently. The bytes on
        # the wire must parse back as exactly the same set of
        # messages we sent; if the write lock leaked, byte streams
        # from the two senders would interleave and parse_frame
        # would either error or hand back garbled payloads.
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            N = 50
            msgs_a = [f"a{i:04d}".encode() for i in range(N)]
            msgs_b = [f"b{i:04d}".encode() for i in range(N)]

            def push(msgs):
                for m in msgs:
                    conn.send_binary(m)

            ta = threading.Thread(target=push, args=(msgs_a,))
            tb = threading.Thread(target=push, args=(msgs_b,))
            ta.start()
            tb.start()
            ta.join(timeout=5)
            tb.join(timeout=5)

            # Read 2N frames off the client side. We don't care
            # about ordering between A and B — we only care that
            # each frame is intact.
            received = []
            for _ in range(2 * N):
                header = client.recv(2)
                self.assertEqual(len(header), 2)
                length = header[1] & 0x7F
                received.append(client.recv(length))

            received_set = set(received)
            self.assertEqual(received_set, set(msgs_a) | set(msgs_b))
        finally:
            conn.close()
            client.close()


# ---------------------------------------------------------------------
# Fragmentation
# ---------------------------------------------------------------------


class FragmentationTest(unittest.TestCase):
    def test_message_reassembles_across_fragments(self):
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            client.sendall(
                _client_encode(ws_server.OP_BINARY, b"hello ", fin=False),
            )
            client.sendall(
                _client_encode(ws_server.OP_CONTINUATION, b"world", fin=True),
            )
            msg = conn.recv()
            self.assertEqual(msg.payload, b"hello world")
            self.assertEqual(msg.opcode, ws_server.OP_BINARY)
        finally:
            conn.close()
            client.close()

    def test_continuation_without_message_rejected(self):
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            client.sendall(
                _client_encode(
                    ws_server.OP_CONTINUATION, b"stray", fin=True
                ),
            )
            with self.assertRaises(ws_server.WebSocketError):
                conn.recv()
        finally:
            conn.close()
            client.close()

    def test_new_data_frame_mid_message_rejected(self):
        # Spec: once a data frame with FIN=0 is open, only
        # CONTINUATION frames (and control frames) may follow until
        # FIN=1. A second data frame with a non-continuation
        # opcode is a protocol error.
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            client.sendall(
                _client_encode(ws_server.OP_BINARY, b"first", fin=False),
            )
            client.sendall(
                _client_encode(ws_server.OP_TEXT, b"interrupt", fin=True),
            )
            with self.assertRaises(ws_server.WebSocketError):
                conn.recv()
        finally:
            conn.close()
            client.close()


# ---------------------------------------------------------------------
# Close path
# ---------------------------------------------------------------------


class CloseTest(unittest.TestCase):
    def test_close_writes_close_frame(self):
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            conn.close(code=ws_server.CloseCode.GOING_AWAY, reason="bye")
            # Server emits opcode=0x8, payload = <code:2 bytes> + reason
            header = client.recv(2)
            self.assertEqual(header[0] & 0x0F, ws_server.OP_CLOSE)
            length = header[1] & 0x7F
            payload = client.recv(length)
            (code,) = struct.unpack("!H", payload[:2])
            self.assertEqual(code, int(ws_server.CloseCode.GOING_AWAY))
            self.assertEqual(payload[2:], b"bye")
        finally:
            client.close()

    def test_close_is_idempotent(self):
        client, server = _socketpair()
        conn = ws_server.WebSocketConnection(server)
        try:
            conn.close()
            # Second call must not raise even though the socket is
            # already shut down.
            conn.close()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
