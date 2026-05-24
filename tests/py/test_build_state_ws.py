"""Unit tests for the WebSocket-fanout methods on BuildState.

The methods exercised here (`add_ws`, `drop_ws`, `ws_set_known`,
`push_to_ws`, `broadcast_chunks`, `broadcast_ws_build_failed`,
`broadcast_log_update`, `broadcast_event`) drive the live-preview
push transport that delivers PDF chunks + build events to
connected browsers. They run on the watcher thread; their
correctness gates the live-reload UX for every connected client.

The tests use a `FakeConn` that records `send_text` /
`send_binary` calls rather than involving real sockets — the
RFC 6455 framing layer is already covered by
`test_ws_server.py`, so here we focus on the broadcast logic:
who gets pushed what, in which order, and how the per-
connection `known` set evolves.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from tests.py._template_loader import load_template_module

_M = load_template_module(name="serve_web_ws_test")


# --- Test doubles -----------------------------------------------


class FakeConn:
    """Stand-in for ws_server.WebSocketConnection.

    Captures the sequence of sends; tests assert against
    `.text_frames` and `.binary_frames`. Pass `fail_on=` to
    simulate a closed socket — that send raises and the next
    one in the test should be skipped.
    """

    def __init__(self, fail_on_text_n: Optional[int] = None,
                 fail_on_binary_n: Optional[int] = None):
        self.text_frames: list[str] = []
        self.binary_frames: list[bytes] = []
        self._fail_on_text_n = fail_on_text_n
        self._fail_on_binary_n = fail_on_binary_n

    def send_text(self, payload: str) -> None:
        if (self._fail_on_text_n is not None
                and len(self.text_frames) == self._fail_on_text_n):
            raise BrokenPipeError("simulated peer close (text)")
        self.text_frames.append(payload)

    def send_binary(self, payload: bytes) -> None:
        if (self._fail_on_binary_n is not None
                and len(self.binary_frames) == self._fail_on_binary_n):
            raise BrokenPipeError("simulated peer close (binary)")
        self.binary_frames.append(payload)


@dataclasses.dataclass(frozen=True)
class _Chunk:
    """Mirror of tools.pdf_chunks.Chunk for the broadcast tests.

    We don't import the real Chunk because BuildState only depends
    on duck-typed attributes (object_id, start, end, hash) — keeping
    the test independent of pdf_chunks's import path.
    """
    object_id: int
    start: int
    end: int
    hash: str


@dataclasses.dataclass(frozen=True)
class _Manifest:
    pdf_size: int
    chunks: tuple
    skeleton_ranges: tuple


def _chunk(object_id: int, start: int, end: int, hash: str) -> _Chunk:
    return _Chunk(object_id, start, end, hash)


def _seed_chunks_dir(d: Path, items: dict[str, bytes]) -> None:
    """Write content-addressed chunk files into ``d``."""
    d.mkdir(parents=True, exist_ok=True)
    for hash_, body in items.items():
        (d / hash_).write_bytes(body)


# --- Registry tests ---------------------------------------------


class WsRegistryTest(unittest.TestCase):
    """add_ws / drop_ws / ws_set_known plumb the per-connection
    "known chunk hashes" set that the broadcaster reads from."""

    def setUp(self):
        self.state = _M.BuildState()

    def test_add_ws_then_drop_ws_round_trips(self):
        conn = FakeConn()
        self.state.add_ws(conn)
        # Registry is keyed by id(); we don't expose a public read
        # but can verify via broadcast going nowhere after drop.
        self.state.drop_ws(conn)
        # Drop twice: idempotent — must not raise.
        self.state.drop_ws(conn)

    def test_ws_set_known_pre_add_is_silent_noop(self):
        # Setting known hashes on a conn we never added shouldn't
        # raise; the watcher thread can race the read loop.
        conn = FakeConn()
        self.state.ws_set_known(conn, {"abc"})

    def test_broadcast_to_zero_connections_doesnt_raise(self):
        self.state.broadcast_ws_build_failed("nope")
        self.state.broadcast_log_update(1, success=False)
        # broadcast_chunks needs a manifest to actually push, but
        # with no conns it should short-circuit cleanly.
        with tempfile.TemporaryDirectory() as td:
            self.state.update_manifest(
                _Manifest(pdf_size=0, chunks=(), skeleton_ranges=())
            )
            self.state.broadcast_chunks(Path(td))


# --- broadcast_chunks ---------------------------------------------


class BroadcastChunksTest(unittest.TestCase):
    """The hot-path push: after a successful build, send each
    connected client the manifest text-frame followed by one
    binary frame per chunk it hasn't acked yet."""

    def setUp(self):
        self.state = _M.BuildState()
        self._tmp = tempfile.TemporaryDirectory()
        self.chunks_dir = Path(self._tmp.name) / "chunks"

    def tearDown(self):
        self._tmp.cleanup()

    def test_pushes_manifest_then_each_missing_chunk_binary(self):
        # Three chunks; the client claims to have only the first
        # one cached. Expect manifest + two binary frames.
        chunks = (
            _chunk(1, 0, 100, "aa" * 32),
            _chunk(2, 100, 200, "bb" * 32),
            _chunk(3, 200, 300, "cc" * 32),
        )
        _seed_chunks_dir(self.chunks_dir, {
            "aa" * 32: b"chunk-1-body",
            "bb" * 32: b"chunk-2-body",
            "cc" * 32: b"chunk-3-body",
        })
        self.state.update_manifest(
            _Manifest(pdf_size=300, chunks=chunks,
                      skeleton_ranges=((0, 0), (300, 300))),
        )
        conn = FakeConn()
        self.state.add_ws(conn)
        self.state.ws_set_known(conn, {"aa" * 32})

        self.state.broadcast_chunks(self.chunks_dir)

        # Exactly one manifest text frame.
        self.assertEqual(len(conn.text_frames), 1)
        manifest = json.loads(conn.text_frames[0])
        self.assertEqual(manifest["type"], "manifest")
        self.assertEqual(manifest["pdfSize"], 300)
        # "ranges" key — must match /pdf-manifest's JSON shape so
        # the JS ChunkedTransport doesn't have two code paths.
        self.assertEqual(len(manifest["ranges"]), 3)
        self.assertEqual(
            [r["hash"] for r in manifest["ranges"]],
            ["aa" * 32, "bb" * 32, "cc" * 32],
        )

        # Two binary frames — the chunks the client lacked.
        self.assertEqual(len(conn.binary_frames), 2)
        for frame in conn.binary_frames:
            # Frame layout: <32 bytes raw sha256><body>.
            self.assertGreaterEqual(len(frame), 32)
            hash_hex = frame[:32].hex()
            body = frame[32:]
            # The body in our seed is namespaced under each hash.
            expected_body = {
                "aa" * 32: b"chunk-1-body",
                "bb" * 32: b"chunk-2-body",
                "cc" * 32: b"chunk-3-body",
            }[hash_hex]
            self.assertEqual(body, expected_body)

        # The client-known set is now updated to include both
        # newly-pushed chunks (the broadcast bumps it).
        with self.state._lock:
            _, known = self.state._ws_conns[id(conn)]
        self.assertEqual(known, {"aa" * 32, "bb" * 32, "cc" * 32})

    def test_skips_chunks_already_known(self):
        chunks = (
            _chunk(1, 0, 100, "aa" * 32),
            _chunk(2, 100, 200, "bb" * 32),
        )
        _seed_chunks_dir(self.chunks_dir, {
            "aa" * 32: b"a", "bb" * 32: b"b",
        })
        self.state.update_manifest(
            _Manifest(pdf_size=200, chunks=chunks, skeleton_ranges=()),
        )
        conn = FakeConn()
        self.state.add_ws(conn)
        # Client already has both — no binary frames should follow
        # the manifest.
        self.state.ws_set_known(conn, {"aa" * 32, "bb" * 32})
        self.state.broadcast_chunks(self.chunks_dir)

        self.assertEqual(len(conn.text_frames), 1, "manifest still pushed")
        self.assertEqual(len(conn.binary_frames), 0,
                         "no chunks pushed when client already has them")

    def test_missing_chunk_on_disk_is_skipped_not_fatal(self):
        # Manifest references a hash, but the chunks dir doesn't
        # have the file (GC raced, disk full, etc.). The broadcast
        # should skip the missing chunk and continue with the rest,
        # so a transient FS issue doesn't disconnect the client.
        chunks = (
            _chunk(1, 0, 100, "aa" * 32),
            _chunk(2, 100, 200, "bb" * 32),
        )
        # Note: only seed "aa", leave "bb" missing.
        _seed_chunks_dir(self.chunks_dir, {"aa" * 32: b"a-body"})
        self.state.update_manifest(
            _Manifest(pdf_size=200, chunks=chunks, skeleton_ranges=()),
        )
        conn = FakeConn()
        self.state.add_ws(conn)
        self.state.broadcast_chunks(self.chunks_dir)

        # One chunk pushed (the one that exists); manifest still
        # references both so the client knows to fall back to
        # HTTP /chunk/<hash> for the missing one.
        self.assertEqual(len(conn.binary_frames), 1)
        self.assertEqual(conn.binary_frames[0][:32].hex(), "aa" * 32)

    def test_per_connection_known_set_is_isolated(self):
        # Two clients, different known sets — each gets only what
        # they personally lack. The "known" sets must not leak.
        chunks = (
            _chunk(1, 0, 100, "aa" * 32),
            _chunk(2, 100, 200, "bb" * 32),
        )
        _seed_chunks_dir(self.chunks_dir, {
            "aa" * 32: b"a", "bb" * 32: b"b",
        })
        self.state.update_manifest(
            _Manifest(pdf_size=200, chunks=chunks, skeleton_ranges=()),
        )
        c1, c2 = FakeConn(), FakeConn()
        self.state.add_ws(c1)
        self.state.add_ws(c2)
        self.state.ws_set_known(c1, {"aa" * 32})       # c1 has aa
        self.state.ws_set_known(c2, {"bb" * 32})       # c2 has bb

        self.state.broadcast_chunks(self.chunks_dir)

        # Each client got the manifest + their one missing chunk.
        self.assertEqual(len(c1.binary_frames), 1)
        self.assertEqual(c1.binary_frames[0][:32].hex(), "bb" * 32)
        self.assertEqual(len(c2.binary_frames), 1)
        self.assertEqual(c2.binary_frames[0][:32].hex(), "aa" * 32)

    def test_send_failure_aborts_that_conn_but_not_the_others(self):
        # When one client's socket dies mid-push, the watcher's
        # broadcast loop must keep going for the rest. The dying
        # client's read loop on the handler thread will eventually
        # call drop_ws when its recv returns None.
        chunks = (_chunk(1, 0, 100, "aa" * 32),)
        _seed_chunks_dir(self.chunks_dir, {"aa" * 32: b"body"})
        self.state.update_manifest(
            _Manifest(pdf_size=100, chunks=chunks, skeleton_ranges=()),
        )

        broken = FakeConn(fail_on_text_n=0)   # fails on manifest send
        ok = FakeConn()
        self.state.add_ws(broken)
        self.state.add_ws(ok)

        self.state.broadcast_chunks(self.chunks_dir)

        self.assertEqual(len(broken.text_frames), 0,
                         "broken conn raised before recording the send")
        self.assertEqual(len(ok.text_frames), 1,
                         "second conn must still get the manifest")
        self.assertEqual(len(ok.binary_frames), 1)


# --- broadcast_log_update / broadcast_ws_build_failed -------------


class BroadcastLogAndFailedTest(unittest.TestCase):
    """Both produce a single JSON text frame per connection."""

    def setUp(self):
        self.state = _M.BuildState()

    def test_broadcast_log_update_payload_shape(self):
        conn = FakeConn()
        self.state.add_ws(conn)
        self.state.broadcast_log_update(42, success=False)
        self.assertEqual(len(conn.text_frames), 1)
        msg = json.loads(conn.text_frames[0])
        self.assertEqual(msg, {
            "type": "log-update", "logId": 42, "success": False,
        })

    def test_broadcast_ws_build_failed_payload_shape(self):
        conn = FakeConn()
        self.state.add_ws(conn)
        self.state.broadcast_ws_build_failed("BUILD FAILED (1.4s)")
        self.assertEqual(len(conn.text_frames), 1)
        msg = json.loads(conn.text_frames[0])
        self.assertEqual(msg, {
            "type": "build-failed", "message": "BUILD FAILED (1.4s)",
        })

    def test_broadcast_log_update_with_no_connections_is_noop(self):
        # Watcher thread fires every build; if nobody's connected
        # there's nothing to do but it mustn't raise.
        try:
            self.state.broadcast_log_update(1, success=True)
        except Exception as e:
            self.fail(f"raised with no conns: {e}")

    def test_broken_conn_doesnt_block_others_log_update(self):
        broken = FakeConn(fail_on_text_n=0)
        ok = FakeConn()
        self.state.add_ws(broken)
        self.state.add_ws(ok)
        self.state.broadcast_log_update(7, success=True)
        self.assertEqual(len(broken.text_frames), 0)
        self.assertEqual(len(ok.text_frames), 1)


# --- broadcast_event (SyncTeX forward-sync fan-out) ---------------


class BroadcastEventTest(unittest.TestCase):
    """broadcast_event is the lower-level mechanism that
    /sync/forward calls. It fans out a single JSON string to BOTH
    SSE listeners and WS connections — same payload, two
    transports."""

    def setUp(self):
        self.state = _M.BuildState()

    def test_broadcast_event_pushes_to_ws_connections(self):
        conn = FakeConn()
        self.state.add_ws(conn)
        payload = json.dumps({"type": "jump", "page": 3, "x": 10, "y": 20})
        self.state.broadcast_event(payload)
        # WS gets the raw payload as a text frame.
        self.assertEqual(conn.text_frames, [payload])

    def test_broadcast_event_pushes_to_sse_listeners(self):
        q = self.state.add_listener()
        payload = json.dumps({"type": "jump", "page": 1})
        self.state.broadcast_event(payload)
        # SSE listeners get the same payload string on their queue.
        self.assertEqual(q.get_nowait(), payload)

    def test_broadcast_event_fans_to_both_transports(self):
        # The whole point: a single forward-sync POST should reach
        # both SSE-fallback clients and WS clients without the
        # caller needing to know which transport each client is on.
        sse_q = self.state.add_listener()
        ws_conn = FakeConn()
        self.state.add_ws(ws_conn)
        payload = json.dumps({"type": "jump", "page": 5})
        self.state.broadcast_event(payload)
        self.assertEqual(sse_q.get_nowait(), payload)
        self.assertEqual(ws_conn.text_frames, [payload])


if __name__ == "__main__":
    unittest.main()
