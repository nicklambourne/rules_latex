"""HTTP-level tests for the HEAD-vs-GET parity in serve_web.py.tpl.

HTTP/1.1 (RFC 7231 §4.3.2) requires that HEAD returns the same
status code and headers as GET would, but with an empty body. We
implement HEAD by re-running do_GET with a flag set, then swapping
the underlying wfile to a sink after end_headers() flushes the
buffered header bytes — so the response writer sites don't need
to know about HEAD individually. These tests pin down that the
property actually holds for every public endpoint.

The tests spin up a real ``ThreadingHTTPServer`` on an ephemeral
port so we exercise the entire request lifecycle: socket → parse
→ dispatch → response writer → end_headers override → sink. Going
through the actual HTTP machinery (rather than calling methods
directly) is what catches the class of bug we're guarding against,
where a future writer site forgets to honour HEAD mode.

We deliberately don't depend on a real PDF or a real PDF.js: the
endpoints under test serve in-memory bytes or read from a tmp
workspace we control. Each test seeds just enough state to make
the corresponding GET succeed, then asserts GET vs HEAD parity.
"""

from __future__ import annotations

import http.client
import importlib.util
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "latex" / "private" / "serve_web.py.tpl"
_PDF_CHUNKS_PATH = _REPO_ROOT / "tools" / "pdf_chunks.py"


_PLACEHOLDERS = {
    "{{DOCUMENT_LABEL}}": "//test:doc",
    "{{PDF_RELPATH}}": "test/doc.pdf",
    "{{SYNCTEX_RELPATH}}": "",
    "{{WATCHED_PATHS}}": "test/doc.tex",
    "{{POLL_INTERVAL}}": "80",
    "{{DEBOUNCE_MS}}": "250",
    "{{DEBOUNCE_MAX_MS}}": "1500",
    "{{PORT}}": "0",  # we override at construction time
    "{{DOCUMENT_NAME}}": "doc",
    "{{PDFJS_LIB_RUNFILE}}": "_pdfjs/pdf.mjs",
    "{{PDFJS_WORKER_RUNFILE}}": "_pdfjs/pdf.worker.mjs",
    "{{OPEN_ON_START}}": "0",
    "{{PDF_CHUNKS_RUNFILE}}": "_tools/pdf_chunks.py",
    "{{ENABLE_SERVE_CACHE}}": "",
    "{{SERVE_CACHE_RUNFILE}}": "",
    "{{PRIME_MAIN_RUNFILE}}": "",
    "{{PRIME_TECTONIC_RUNFILE}}": "",
    "{{PRIME_POPULATE_TOOL_RUNFILE}}": "",
    "{{PRIME_STAGING_LIB_RUNFILE}}": "",
    "{{PRIME_BIBER_RUNFILE}}": "",
    "{{PRIME_USE_SYSTEM_BIBER}}": "",
    "{{PRIME_SRCS}}": "",
    "{{PRIME_PKG_FILES}}": "",
}


def _load_template_module():
    source = _TEMPLATE_PATH.read_text()
    for placeholder, replacement in _PLACEHOLDERS.items():
        source = source.replace(placeholder, replacement)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8",
    )
    try:
        tmp.write(source)
        tmp.close()
        spec = importlib.util.spec_from_file_location(
            "serve_web_test_module_head", tmp.name,
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["serve_web_test_module_head"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        Path(tmp.name).unlink()


def _load_pdf_chunks():
    spec = importlib.util.spec_from_file_location(
        "pdf_chunks", _PDF_CHUNKS_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pdf_chunks"] = module
    spec.loader.exec_module(module)
    return module


_M = _load_template_module()
_PC = _load_pdf_chunks()


def _free_port() -> int:
    """Pick an ephemeral local TCP port. There's an inherent race
    between binding-here and binding-in-the-handler, but it's the
    same race http.server itself runs whenever you give it port
    0; we close the probe socket immediately so the window is
    tiny."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_minimal_pdf() -> bytes:
    """Synthesise a minimal valid PDF with a cross-reference
    stream. Adapted from tests/py/test_pdf_chunks.py; kept inline
    to avoid an inter-test-file import."""
    import zlib
    header = b"%PDF-1.5\n%\xe4\xf0\xed\xf8\n"
    out = bytearray(header)
    payloads = [b"<</Type/Catalog/Pages 2 0 R>>",
                b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
                b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>"]
    offsets = []
    for i, payload in enumerate(payloads, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(payload)
        out.extend(b"\nendobj\n")
    xref_offset = len(out)
    entries = bytearray()
    entries.extend(b"\x00\x00\x00\xff\xff")
    for off in offsets:
        entries.extend(bytes([1, (off >> 8) & 0xff, off & 0xff, 0, 0]))
    entries.extend(bytes([1, (xref_offset >> 8) & 0xff,
                          xref_offset & 0xff, 0, 0]))
    compressed = zlib.compress(bytes(entries))
    size = len(payloads) + 2
    dict_part = (
        f"<</Type/XRef/Size {size}/W[1 2 2]"
        f"/Filter/FlateDecode/Length {len(compressed)}>>"
    ).encode("ascii")
    out.extend(f"{len(payloads) + 1} 0 obj\n".encode("ascii"))
    out.extend(dict_part)
    out.extend(b"\nstream\n")
    out.extend(compressed)
    out.extend(b"\nendstream\nendobj\n")
    out.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(out)


class _ServerFixture:
    """Stand up a real ``ThreadingHTTPServer`` running the
    template's ``Handler``, with the per-class state attributes
    seeded so each endpoint has something plausible to serve.

    Use as a context manager. Yields a ``(host, port)`` tuple
    for client requests.
    """

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="head_test_")
        self.workspace = Path(self._tmp.name)
        # Lay out a fake bazel-bin/<pdf_relpath>.
        pdf_dir = self.workspace / "bazel-bin" / "test"
        pdf_dir.mkdir(parents=True)
        self.pdf_path = pdf_dir / "doc.pdf"
        self.pdf_path.write_bytes(_build_minimal_pdf())
        # Chunks dir for the manifest endpoints.
        self.chunks_dir = self.workspace / ".cache" / "chunks"
        self.chunks_dir.mkdir(parents=True)
        # Seed BuildState + post-build hook so /pdf-manifest and
        # /chunk/<hash> have real data.
        self.state = _M.BuildState()
        self.state.record_build(True, 0.1, "test build")
        self.pdf_chunks_ctx = _M.PdfChunksContext(
            module=_PC,
            chunks_dir=self.chunks_dir,
        )
        _M._compute_manifest_post_build(
            self.state, self.workspace, self.pdf_chunks_ctx,
        )
        # Class-level attribute injection (mirrors what main()
        # does on real startup).
        _M.Handler.state = self.state
        _M.Handler.workspace = self.workspace
        _M.Handler.pdfjs_lib_bytes = b"// pdf.mjs stub"
        _M.Handler.pdfjs_worker_bytes = b"// pdf.worker stub"
        _M.Handler.pdf_chunks_ctx = self.pdf_chunks_ctx

        self.port = _free_port()
        self._server = _M.ThreadingHTTPServer(
            ("127.0.0.1", self.port), _M.Handler,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="head-test-server",
        )

    def __enter__(self) -> "_ServerFixture":
        self._thread.start()
        # The server is in serve_forever the moment the thread is
        # scheduled; no separate ready signal needed because
        # http.client connect() will retry-on-refused.
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)
        self._tmp.cleanup()


def _request(
    port: int, method: str, path: str,
    *, headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], bytes]:
    """Issue one request and return (status, response_headers, body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        # Lowercase keys for case-insensitive comparison.
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, hdrs, body
    finally:
        conn.close()


class TestHeadParity(unittest.TestCase):
    """For every GET endpoint, HEAD must return the same status
    and the same Content-Length, with an empty body."""

    def setUp(self) -> None:
        self._fixture_ctx = _ServerFixture()
        self.fixture = self._fixture_ctx.__enter__()

    def tearDown(self) -> None:
        self._fixture_ctx.__exit__(None, None, None)

    def _check_parity(self, path: str, expected_status: int = 200) -> None:
        port = self.fixture.port
        g_status, g_hdrs, g_body = _request(port, "GET", path)
        h_status, h_hdrs, h_body = _request(port, "HEAD", path)
        self.assertEqual(g_status, expected_status,
                         f"GET {path}: unexpected status {g_status}")
        self.assertEqual(h_status, g_status,
                         f"HEAD {path}: status {h_status} != GET {g_status}")
        self.assertEqual(h_hdrs.get("content-length"),
                         g_hdrs.get("content-length"),
                         f"HEAD {path}: Content-Length mismatch")
        self.assertEqual(h_hdrs.get("content-type"),
                         g_hdrs.get("content-type"),
                         f"HEAD {path}: Content-Type mismatch")
        self.assertEqual(h_body, b"",
                         f"HEAD {path}: body must be empty, got {len(h_body)} bytes")

    def test_head_index_html(self):
        self._check_parity("/")

    def test_head_pdf(self):
        self._check_parity("/pdf")

    def test_head_status(self):
        self._check_parity("/status")

    def test_head_pdf_manifest(self):
        self._check_parity("/pdf-manifest")

    def test_head_chunk(self):
        # Pick the first chunk hash from the seeded manifest.
        manifest = self.fixture.state.get_manifest()
        self.assertIsNotNone(manifest, "test fixture should have a manifest")
        first_hash = manifest.chunks[0].hash
        self._check_parity(f"/chunk/{first_hash}")

    def test_head_pdfjs_lib(self):
        self._check_parity("/_pdfjs/pdf.mjs")

    def test_head_pdfjs_worker(self):
        self._check_parity("/_pdfjs/pdf.worker.mjs")

    def test_head_unknown_path_returns_404(self):
        # Unknown paths return 404 + plain-text body on GET; HEAD
        # must mirror that.
        self._check_parity("/nonexistent", expected_status=404)

    def test_head_chunk_invalid_hash(self):
        # 64-char-but-non-hex hash → 400 from the validator.
        self._check_parity(
            "/chunk/" + "z" * 64,
            expected_status=400,
        )

    def test_head_chunk_unknown_hash(self):
        # Well-formed hash that doesn't exist on disk → 404.
        self._check_parity(
            "/chunk/" + "a" * 64,
            expected_status=404,
        )


class TestHeadRangeRequests(unittest.TestCase):
    """RFC 7233 §3.1 allows HEAD to honour Range. Our server
    should return 206 + Content-Range + the right Content-Length
    for HEAD just like it does for GET, just without the body."""

    def setUp(self) -> None:
        self._fixture_ctx = _ServerFixture()
        self.fixture = self._fixture_ctx.__enter__()

    def tearDown(self) -> None:
        self._fixture_ctx.__exit__(None, None, None)

    def test_head_pdf_with_range_returns_206_no_body(self):
        port = self.fixture.port
        headers = {"Range": "bytes=0-99"}
        g_status, g_hdrs, g_body = _request(
            port, "GET", "/pdf", headers=headers,
        )
        h_status, h_hdrs, h_body = _request(
            port, "HEAD", "/pdf", headers=headers,
        )
        self.assertEqual(g_status, 206)
        self.assertEqual(h_status, 206)
        self.assertEqual(g_hdrs.get("content-range"),
                         h_hdrs.get("content-range"))
        self.assertEqual(g_hdrs.get("content-length"),
                         h_hdrs.get("content-length"))
        # GET delivers 100 bytes; HEAD delivers zero.
        self.assertEqual(len(g_body), 100)
        self.assertEqual(h_body, b"")

    def test_head_pdf_with_bad_range(self):
        # 416 Range Not Satisfiable on both GET and HEAD.
        port = self.fixture.port
        headers = {"Range": "bytes=999999999-"}
        g_status, _, _ = _request(port, "GET", "/pdf", headers=headers)
        h_status, _, h_body = _request(port, "HEAD", "/pdf", headers=headers)
        self.assertEqual(g_status, 416)
        self.assertEqual(h_status, 416)
        self.assertEqual(h_body, b"")


class TestHeadDoesNotLeakSseListener(unittest.TestCase):
    """HEAD on /events must NOT enter the listener loop, because
    that would leak a thread writing keepalives to a sink forever
    after the HEAD client disconnects. We assert via the listener
    count on the shared BuildState."""

    def setUp(self) -> None:
        self._fixture_ctx = _ServerFixture()
        self.fixture = self._fixture_ctx.__enter__()

    def tearDown(self) -> None:
        self._fixture_ctx.__exit__(None, None, None)

    def test_head_events_returns_headers_only(self):
        port = self.fixture.port
        status, hdrs, body = _request(port, "HEAD", "/events", timeout=2.0)
        self.assertEqual(status, 200)
        # SSE content type still advertised, even though no
        # events flow.
        self.assertEqual(hdrs.get("content-type"), "text/event-stream")
        self.assertEqual(body, b"")

    def test_head_events_does_not_register_listener(self):
        port = self.fixture.port
        # The SSE handler calls state.add_listener() in the body
        # path. We can probe the resulting listener count by
        # examining the BuildState directly. add_listener() is
        # the only path that grows the listener list.
        before = len(self.fixture.state._listeners)  # noqa: SLF001
        _request(port, "HEAD", "/events", timeout=2.0)
        # Give the handler a moment to settle (it shouldn't have
        # registered, but if it had, the listener would still be
        # there until the connection actually closes).
        time.sleep(0.05)
        after = len(self.fixture.state._listeners)  # noqa: SLF001
        self.assertEqual(
            after, before,
            "HEAD /events must not enter the listener loop",
        )


class TestPostUnchanged(unittest.TestCase):
    """Sanity: introducing do_HEAD must not affect POST handling.
    /sync/reverse is the only POST endpoint."""

    def setUp(self) -> None:
        self._fixture_ctx = _ServerFixture()
        self.fixture = self._fixture_ctx.__enter__()

    def tearDown(self) -> None:
        self._fixture_ctx.__exit__(None, None, None)

    def test_post_sync_reverse_still_works(self):
        # SYNCTEX_ENABLED is False in this fixture (we set
        # SYNCTEX_RELPATH to empty in the placeholders), so the
        # endpoint returns 404 with {"ok": False}. That's not
        # the success path but it does prove the POST dispatch
        # is intact.
        port = self.fixture.port
        status, _, body = _request(
            port, "POST", "/sync/reverse",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 404)
        # Sanity: the body is the JSON error, not an empty
        # response.
        self.assertIn(b"synctex", body)

    def test_head_on_post_only_endpoint_returns_404(self):
        # HEAD /sync/reverse re-dispatches into do_GET, which
        # doesn't recognise the path → 404. That's the same
        # behaviour GET would have on this URL, which is what
        # HEAD-parity requires.
        port = self.fixture.port
        h_status, _, h_body = _request(port, "HEAD", "/sync/reverse")
        g_status, _, _ = _request(port, "GET", "/sync/reverse")
        self.assertEqual(h_status, 404)
        self.assertEqual(g_status, 404)
        self.assertEqual(h_body, b"")


if __name__ == "__main__":
    unittest.main()
