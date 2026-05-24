#!/usr/bin/env python3
"""Minimal stdlib WebSocket server for ``latex_serve_web``.

This module implements the slice of RFC 6455 that ``latex_serve_web``
actually needs to push PDF-chunk deltas to the browser:

* HTTP Upgrade handshake (server-side).
* Frame parsing for client→server traffic (always masked per spec).
* Frame writing for server→client traffic (never masked).
* Control frames: ping (we reply pong), pong (we ignore), close
  (we mirror and tear down).
* Text + binary data frames.
* Fragmentation across multiple frames into one message.

Things we intentionally don't implement:

* permessage-deflate. The PDF chunks are already raw bytes from
  FlateDecode'd PDF objects — compressing them again costs CPU for
  no win. The handshake therefore declines the extension if the
  client offers it.
* Sub-protocols. We don't negotiate `Sec-WebSocket-Protocol`.
* Client mode. We're always the server. RFC 6455 mandates that
  server→client frames are *not* masked and client→server frames
  *are* masked; this module asserts both directions.
* Continuation-frame validation across messages. We support
  fragmented messages (FIN=0 continuation chain) but trust the
  client not to interleave fragments from different messages — a
  spec violation that no real browser produces.

The design assumes the caller has already accepted the TCP
connection (via ``socketserver.BaseHTTPRequestHandler`` or similar)
and is willing to hand the raw socket over for the lifetime of the
WS session. The handler then either spawns a thread per connection
or drives the connection synchronously from the calling thread.

Usage::

    # Inside an HTTP request handler that has parsed an
    # Upgrade: websocket request:
    accept_websocket(handler)               # writes 101 response
    conn = WebSocketConnection(handler.connection)
    try:
        while True:
            msg = conn.recv()               # blocks; returns Message
            if msg is None:                  # client closed
                break
            if msg.opcode == OP_TEXT:
                handle_text(msg.payload.decode("utf-8"))
            elif msg.opcode == OP_BINARY:
                handle_binary(msg.payload)
    finally:
        conn.close()

Thread-safety: ``WebSocketConnection.send_*`` methods are
synchronized via an internal lock so multiple threads can push to
the same socket without interleaving frames. ``recv`` must be
driven from a single thread.
"""

from __future__ import annotations

import base64
import enum
import errno
import hashlib
import io
import os
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Optional

# RFC 6455 §1.3 — magic GUID concatenated with Sec-WebSocket-Key
# before SHA-1 hashing for the Sec-WebSocket-Accept response.
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Opcodes — RFC 6455 §5.2.
OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# Hard cap on a single message after reassembly. The PDF chunks
# we push top out around a few hundred KB per object. 16 MiB is
# more headroom than any sane payload needs; rejecting larger ones
# protects against an unbounded memory hold by a misbehaving peer.
MAX_MESSAGE_SIZE = 16 * 1024 * 1024


class WebSocketError(Exception):
    """Base for protocol-level failures."""


class WebSocketHandshakeError(WebSocketError):
    """The HTTP upgrade request was malformed."""


class WebSocketClosed(WebSocketError):
    """Peer closed the connection or we did. ``recv()`` returns ``None``
    instead of raising; callers handle that and the connection-level
    teardown happens automatically. This exception is reserved for
    cases where a callee specifically needs to signal "the wire is
    gone, don't try to send anything more".
    """


class CloseCode(enum.IntEnum):
    """Subset of RFC 6455 §7.4 status codes we emit."""

    NORMAL = 1000
    GOING_AWAY = 1001
    PROTOCOL_ERROR = 1002
    UNSUPPORTED_DATA = 1003
    POLICY_VIOLATION = 1008
    MESSAGE_TOO_BIG = 1009
    INTERNAL_ERROR = 1011


@dataclass(frozen=True)
class Message:
    """One application message reassembled from one or more frames.

    ``opcode`` is the opcode of the *first* frame in the chain
    (continuation frames inherit it). Always either ``OP_TEXT`` or
    ``OP_BINARY`` — control frames don't surface here.
    """

    opcode: int
    payload: bytes


def compute_accept_key(client_key: str) -> str:
    """Return the value for the ``Sec-WebSocket-Accept`` response
    header, given the client's ``Sec-WebSocket-Key`` request header
    value.

    Algorithm: base64(sha1(client_key + GUID)). Per RFC 6455 §4.2.2.
    """
    sha1 = hashlib.sha1((client_key + _GUID).encode("ascii")).digest()
    return base64.b64encode(sha1).decode("ascii")


def _read_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``sock`` or raise
    ``WebSocketClosed`` if the peer closes mid-read.

    Wraps the recv loop in one place so frame parsing can rely on
    "got it all" semantics. ``EINTR`` (signal mid-recv) retries
    transparently.
    """
    chunks = []
    remaining = n
    while remaining:
        try:
            buf = sock.recv(remaining)
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise WebSocketClosed(f"socket error during read: {exc}") from exc
        if not buf:
            raise WebSocketClosed("peer closed mid-frame")
        chunks.append(buf)
        remaining -= len(buf)
    return b"".join(chunks)


def _mask(payload: bytes, key: bytes) -> bytes:
    """XOR ``payload`` against the repeating 4-byte ``key``.

    Used both for unmasking client→server frames and (in tests) for
    masking client→server frames in fixtures. Server→client traffic
    is never masked so we don't call this on the write path.

    The native bytes ``zip`` is faster than int-iter on CPython but
    we prefer correctness over micro-optimization here; payloads are
    bounded by MAX_MESSAGE_SIZE and chunked PDFs aren't latency-
    sensitive at the masking layer.
    """
    if len(key) != 4:
        raise WebSocketError("mask key must be 4 bytes")
    return bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def accept_websocket(
    *,
    headers: dict[str, str],
    method: str,
    write: "callable[[bytes], None]",
) -> None:
    """Validate the upgrade request and write the 101 response.

    ``headers`` is a case-insensitive mapping (any dict with
    lower-cased keys); ``method`` is the request verb ("GET" only);
    ``write`` writes raw bytes to the underlying socket (typically
    ``BaseHTTPRequestHandler.wfile.write``, or just ``sock.sendall``).

    Raises ``WebSocketHandshakeError`` for any RFC 6455 §4.2.1
    violation — caller is expected to translate that into an
    HTTP 400 response.
    """
    if method.upper() != "GET":
        raise WebSocketHandshakeError("websocket upgrade requires GET")

    # Required headers per RFC 6455 §4.2.1.
    upgrade = headers.get("upgrade", "").strip().lower()
    if upgrade != "websocket":
        raise WebSocketHandshakeError(
            f"Upgrade header must be 'websocket', got {upgrade!r}"
        )

    connection = headers.get("connection", "").lower()
    # The Connection header can be a comma-separated list (e.g.
    # "keep-alive, Upgrade"). RFC mandates that one of its tokens
    # be "upgrade".
    if "upgrade" not in (tok.strip() for tok in connection.split(",")):
        raise WebSocketHandshakeError(
            f"Connection header must include 'upgrade', got {connection!r}"
        )

    version = headers.get("sec-websocket-version", "").strip()
    if version != "13":
        raise WebSocketHandshakeError(
            f"only Sec-WebSocket-Version: 13 is supported, got {version!r}"
        )

    key = headers.get("sec-websocket-key", "").strip()
    if not key:
        raise WebSocketHandshakeError("missing Sec-WebSocket-Key header")
    # Per spec the key is base64 of a 16-byte random value, hence
    # 24 chars including padding. We don't enforce the length —
    # browsers all do the right thing here and pedantic enforcement
    # buys us nothing.

    accept = compute_accept_key(key)

    response = (
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n"
        b"\r\n"
    )
    write(response)


def parse_frame(sock: socket.socket) -> tuple[bool, int, bytes]:
    """Read one frame from ``sock``. Returns ``(fin, opcode, payload)``.

    ``payload`` is already unmasked. The caller handles control-frame
    semantics and continuation chaining (see ``WebSocketConnection.recv``
    for the canonical loop).

    Raises ``WebSocketClosed`` if the peer disconnects mid-frame,
    ``WebSocketError`` for protocol violations (bad reserved bits,
    unmasked client frame, oversize payload, etc.).
    """
    header = _read_exact(sock, 2)
    b1, b2 = header[0], header[1]

    fin = (b1 & 0x80) != 0
    # RFC 6455 §5.2: RSV1-3 must be 0 unless an extension was
    # negotiated. We never negotiate extensions, so any nonzero
    # RSV bit is a protocol error.
    if b1 & 0x70:
        raise WebSocketError("RSV bits set without extension")
    opcode = b1 & 0x0F

    mask = (b2 & 0x80) != 0
    if not mask:
        # Client→server frames MUST be masked. RFC 6455 §5.1.
        raise WebSocketError("client frame missing mask")

    payload_len = b2 & 0x7F
    if payload_len == 126:
        payload_len = struct.unpack("!H", _read_exact(sock, 2))[0]
    elif payload_len == 127:
        payload_len = struct.unpack("!Q", _read_exact(sock, 8))[0]
        # The high bit MUST be 0 (RFC 6455 §5.2). 8 EiB is plenty
        # but we cap well below that.
        if payload_len & (1 << 63):
            raise WebSocketError("payload length high bit set")

    if payload_len > MAX_MESSAGE_SIZE:
        raise WebSocketError(
            f"frame payload {payload_len} exceeds MAX_MESSAGE_SIZE"
        )

    # Control frames (opcode >= 8) have additional constraints
    # per RFC 6455 §5.5: payload must be <= 125 bytes, must not be
    # fragmented.
    if opcode >= 0x8:
        if payload_len > 125:
            raise WebSocketError("control frame payload > 125 bytes")
        if not fin:
            raise WebSocketError("control frame must not be fragmented")

    mask_key = _read_exact(sock, 4)
    payload = _read_exact(sock, payload_len) if payload_len else b""
    payload = _mask(payload, mask_key)

    return fin, opcode, payload


def encode_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    """Encode one server→client frame. No masking (server frames
    MUST NOT be masked, RFC 6455 §5.1).

    ``opcode`` is one of the ``OP_*`` constants. ``fin`` lets the
    caller emit fragmented frames; in practice we always send
    whole messages in a single frame because PDF chunks are well
    below the practical fragmentation threshold (and TCP segmenting
    is fine).
    """
    b1 = (0x80 if fin else 0x00) | (opcode & 0x0F)

    n = len(payload)
    if n < 126:
        header = struct.pack("!BB", b1, n)
    elif n < (1 << 16):
        header = struct.pack("!BBH", b1, 126, n)
    else:
        header = struct.pack("!BBQ", b1, 127, n)

    return header + payload


class WebSocketConnection:
    """A live WebSocket session, sitting on top of an accepted TCP socket.

    The constructor does NOT perform the handshake. Call
    ``accept_websocket`` against the underlying HTTP request first,
    then wrap the now-upgraded socket in this class.

    Lifecycle::

        recv()          → returns Message, or None when the peer
                          has cleanly closed (we've already sent
                          our close frame).
        send_text(s)    → fragmentable in principle, but we always
        send_binary(b)    push one frame per message.
        ping(payload=…) → solicited ping; pong replies are handled
                          internally and bypass recv().
        close(code=…)   → send our close frame, drain, then close
                          the socket.

    The internal write lock makes send_text / send_binary / ping /
    close safe to call from multiple threads at once. recv() must
    be driven from a single thread.
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._write_lock = threading.Lock()
        self._closed = False
        # When the peer sends a close frame to us, ``recv`` returns
        # None and we set this so subsequent send_* calls become
        # no-ops rather than EPIPE. The socket itself is closed in
        # ``close``.
        self._peer_closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def recv(self) -> Optional[Message]:
        """Block until a complete application message arrives.

        Returns ``None`` if the peer closed cleanly. Control frames
        (ping/pong/close) are handled internally and don't surface
        — recv loops past them until a text or binary message is
        ready.

        Reassembles fragmented messages. The pre-reassembly per-
        fragment payload is still subject to MAX_MESSAGE_SIZE
        individually, and the reassembled total is checked again
        before return.
        """
        fragments: list[bytes] = []
        message_opcode: Optional[int] = None
        total_size = 0

        while True:
            try:
                fin, opcode, payload = parse_frame(self._sock)
            except WebSocketClosed:
                return None

            if opcode == OP_CLOSE:
                # Peer initiated close. Mirror back the status (if
                # provided) and tear down. We don't try to deliver
                # any pending application data — that's a "graceful"
                # close, RFC 6455 §1.4.
                code = CloseCode.NORMAL
                if len(payload) >= 2:
                    code = struct.unpack("!H", payload[:2])[0]
                self._peer_closed = True
                # Try to send our close frame, but don't surface
                # errors — peer may have already half-closed.
                try:
                    self._send_frame(
                        OP_CLOSE,
                        struct.pack("!H", code),
                    )
                except (OSError, WebSocketError):
                    pass
                return None

            if opcode == OP_PING:
                # Reply pong with the same payload (RFC 6455 §5.5.2).
                self._send_frame(OP_PONG, payload)
                continue

            if opcode == OP_PONG:
                # Unsolicited or response to our ping. We don't
                # currently use pong content for anything; the
                # mere arrival proves the connection is alive,
                # which the OS-level TCP keepalive plus our read
                # timeout already cover.
                continue

            if opcode == OP_CONTINUATION:
                if message_opcode is None:
                    raise WebSocketError(
                        "continuation frame with no message in progress"
                    )
            elif opcode in (OP_TEXT, OP_BINARY):
                if message_opcode is not None:
                    raise WebSocketError(
                        "new data frame interrupts in-progress message"
                    )
                message_opcode = opcode
            else:
                raise WebSocketError(f"unknown opcode 0x{opcode:x}")

            fragments.append(payload)
            total_size += len(payload)
            if total_size > MAX_MESSAGE_SIZE:
                raise WebSocketError(
                    f"reassembled message exceeds MAX_MESSAGE_SIZE"
                )

            if fin:
                assert message_opcode is not None  # narrowed above
                return Message(
                    opcode=message_opcode,
                    payload=b"".join(fragments),
                )

    def send_text(self, text: str) -> None:
        """Send a UTF-8 text message in a single frame."""
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        """Send a binary message in a single frame."""
        self._send_frame(OP_BINARY, data)

    def ping(self, payload: bytes = b"") -> None:
        """Send a ping frame. The peer's pong reply is consumed
        silently inside ``recv``.

        ``payload`` is bounded at 125 bytes per RFC; longer payloads
        raise ``WebSocketError``.
        """
        if len(payload) > 125:
            raise WebSocketError("ping payload > 125 bytes")
        self._send_frame(OP_PING, payload)

    def close(
        self,
        code: int = CloseCode.NORMAL,
        reason: str = "",
    ) -> None:
        """Send a close frame, then tear down the socket. Idempotent.

        We don't wait for the peer's close frame back — the network
        stack will deliver any in-flight bytes the peer sent before
        we kicked the socket, but RFC 6455 §1.4 says the underlying
        TCP close is acceptable once we've sent our close. The
        watcher thread will drop the connection from its registry
        when recv() returns None.
        """
        if self._closed:
            return

        if not self._peer_closed:
            reason_bytes = reason.encode("utf-8")
            if len(reason_bytes) > 123:  # 125 - 2 bytes for code
                reason_bytes = reason_bytes[:123]
            payload = struct.pack("!H", int(code)) + reason_bytes
            try:
                # Send before flipping _closed — _send_frame
                # short-circuits when _closed is set.
                self._send_frame(OP_CLOSE, payload)
            except (OSError, WebSocketError):
                # Socket already gone — proceed to teardown.
                pass

        self._closed = True

        try:
            # Half-shutdown: signal we won't write any more, but
            # let the kernel drain any in-flight peer bytes.
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            # No-op rather than raise. Callers churn through close
            # paths where a stray late write is expected.
            return
        frame = encode_frame(opcode, payload)
        with self._write_lock:
            try:
                self._sock.sendall(frame)
            except (BrokenPipeError, ConnectionResetError) as exc:
                # Peer went away mid-write. Mark closed so future
                # callers don't keep trying.
                self._closed = True
                raise WebSocketClosed(f"peer gone: {exc}") from exc
