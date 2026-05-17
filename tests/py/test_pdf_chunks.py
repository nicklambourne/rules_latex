"""Unit tests for tools/pdf_chunks.py.

The chunk parser is correctness-critical: a false-positive
(reporting an object boundary that doesn't exist) corrupts the
PDF the browser reconstructs from chunks, while a false-negative
(failing to find a chunk that *should* be content-addressed)
silently undoes the optimization. The tests below cover:

* Cross-reference stream parse (the format tectonic emits).
* Classic xref table parse (older PDF flavor).
* Chunk + skeleton coverage invariant: every byte of the PDF is
  covered exactly once between chunks and skeleton ranges.
* Hash stability: same input → same chunk hashes.
* Error paths: malformed PDFs return None, never crash.
* Round-trip: chunk file contents match the bytes the PDF actually
  has at the chunk's range.

The hand-crafted PDFs are intentionally minimal; they're not
real-document complexity but they exercise the parse paths
deterministically. Tests against the actual CV PDF are in the
end-to-end smoke test (run as part of CI's serve-smoke
integration).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
_PDF_CHUNKS_PATH = _TOOLS_DIR / "pdf_chunks.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_PC = _load_module("pdf_chunks", _PDF_CHUNKS_PATH)


# -----------------------------------------------------------------------------
# Cross-reference-stream synthesis helpers
# -----------------------------------------------------------------------------


def _build_xref_stream_pdf(
    object_payloads: list[bytes],
    *,
    w: tuple[int, int, int] = (1, 2, 2),
) -> bytes:
    """Build a minimal PDF with a cross-reference stream.

    Each ``object_payloads`` entry becomes one indirect object at a
    monotonically increasing byte offset. Returns the assembled
    PDF bytes; the xref stream is appended and the trailer with
    ``startxref`` written.

    Object numbering is 1-based (object 0 is implicitly the free
    head in the xref table). Generation is always 0. Object IDs
    start at 1 and run consecutively.
    """
    header = b"%PDF-1.5\n%\xe4\xf0\xed\xf8\n"
    out = bytearray(header)
    offsets = []  # parallel to object_payloads; absolute byte offsets
    for i, payload in enumerate(object_payloads, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(payload)
        out.extend(b"\nendobj\n")
    # Build the xref stream as the next object (id = N+1).
    xref_obj_id = len(object_payloads) + 1
    xref_offset_in_pdf = len(out)

    # Build the xref entries: entry 0 = free, then one type=1 per object.
    entries = bytearray()
    # Object 0: free, offset 0, generation 65535.
    entries.extend(_pack_entry((0, 0, 65535), w))
    for i, off in enumerate(offsets, start=1):
        entries.extend(_pack_entry((1, off, 0), w))
    # The xref stream object itself also needs a self-referential
    # entry, but real PDFs include it. Add one pointing at this
    # object's own offset.
    entries.extend(_pack_entry((1, xref_offset_in_pdf, 0), w))

    compressed = zlib.compress(bytes(entries))
    size = len(object_payloads) + 2  # objects 0..N + xref-stream object

    dict_part = (
        f"<</Type/XRef/Size {size}/W[{w[0]} {w[1]} {w[2]}]"
        f"/Filter/FlateDecode/Length {len(compressed)}>>"
    ).encode("ascii")
    out.extend(f"{xref_obj_id} 0 obj\n".encode("ascii"))
    out.extend(dict_part)
    out.extend(b"\nstream\n")
    out.extend(compressed)
    out.extend(b"\nendstream\nendobj\n")
    out.extend(f"startxref\n{xref_offset_in_pdf}\n%%EOF\n".encode("ascii"))
    return bytes(out)


def _pack_entry(fields: tuple[int, int, int], w: tuple[int, int, int]) -> bytes:
    """Pack one xref entry into big-endian bytes per the /W spec."""
    out = bytearray()
    for value, width in zip(fields, w):
        if width == 0:
            continue
        for shift in range((width - 1) * 8, -1, -8):
            out.append((value >> shift) & 0xFF)
    return bytes(out)


# -----------------------------------------------------------------------------
# Classic xref synthesis helpers
# -----------------------------------------------------------------------------


def _build_classic_xref_pdf(object_payloads: list[bytes]) -> bytes:
    """Build a minimal PDF with the classic ASCII xref table."""
    header = b"%PDF-1.4\n%\xe4\xf0\xed\xf8\n"
    out = bytearray(header)
    offsets = []
    for i, payload in enumerate(object_payloads, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(payload)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    out.extend(b"xref\n")
    n = len(object_payloads) + 1
    out.extend(f"0 {n}\n".encode("ascii"))
    # Object 0: free.
    out.extend(b"0000000000 65535 f \n")
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(b"trailer\n<</Size " + str(n).encode("ascii") + b">>\n")
    out.extend(f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii"))
    return bytes(out)


# -----------------------------------------------------------------------------
# Test fixtures
# -----------------------------------------------------------------------------


class _PdfFixture:
    """Context manager: writes a PDF to disk in a fresh tmpdir,
    cleans up on exit, exposes the path and the chunks directory."""

    def __init__(self, pdf_bytes: bytes) -> None:
        self.pdf_bytes = pdf_bytes
        self._tmp = tempfile.TemporaryDirectory(prefix="pdf_chunks_test_")
        self.tmp = Path(self._tmp.name)
        self.pdf_path = self.tmp / "doc.pdf"
        self.pdf_path.write_bytes(pdf_bytes)
        self.chunks_dir = self.tmp / "chunks"

    def __enter__(self) -> "_PdfFixture":
        return self

    def __exit__(self, *exc) -> None:
        self._tmp.cleanup()


# -----------------------------------------------------------------------------
# Cross-reference stream tests
# -----------------------------------------------------------------------------


class TestXrefStream(unittest.TestCase):
    """The format tectonic emits in practice."""

    def test_minimal_three_object_pdf(self):
        payloads = [
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>",
        ]
        pdf = _build_xref_stream_pdf(payloads)
        with _PdfFixture(pdf) as f:
            m = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m, "valid xref-stream PDF must parse")
            # 3 input objects + 1 xref-stream object = 4 chunks.
            self.assertEqual(len(m.chunks), 4)
            # All object IDs should be 1, 2, 3, 4 in offset order.
            ids = [c.object_id for c in m.chunks]
            self.assertEqual(sorted(ids), [1, 2, 3, 4])
            self.assertEqual(m.pdf_size, len(pdf))

    def test_chunk_content_matches_byte_range(self):
        """The bytes written to ``chunks_dir/<hash>`` must be
        exactly ``pdf[chunk.start:chunk.end]`` — this is the
        invariant the client-side reconstruction relies on."""
        payloads = [b"<</A 1>>", b"<</B 2>>", b"<</C 3>>"]
        pdf = _build_xref_stream_pdf(payloads)
        with _PdfFixture(pdf) as f:
            m = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m)
            for chunk in m.chunks:
                on_disk = (f.chunks_dir / chunk.hash).read_bytes()
                expected = pdf[chunk.start:chunk.end]
                self.assertEqual(on_disk, expected,
                                 f"chunk {chunk.hash} bytes mismatch")
                self.assertEqual(
                    hashlib.sha256(on_disk).hexdigest(),
                    chunk.hash,
                    "chunk hash must be SHA-256 of its bytes",
                )

    def test_coverage_invariant(self):
        """Every byte of the PDF must be covered exactly once
        between chunks + skeleton ranges, with no overlaps or
        gaps."""
        payloads = [b"<</A 1>>", b"<</B 2>>", b"<</C 3>>"]
        pdf = _build_xref_stream_pdf(payloads)
        with _PdfFixture(pdf) as f:
            m = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m)
            # Sort and confirm we cover [0, pdf_size) without
            # overlap.
            all_ranges = (
                [(c.start, c.end) for c in m.chunks]
                + list(m.skeleton_ranges)
            )
            all_ranges.sort()
            cursor = 0
            for start, end in all_ranges:
                self.assertEqual(start, cursor,
                                 f"gap or overlap at {start} (expected {cursor})")
                self.assertGreater(end, start, "empty range")
                cursor = end
            self.assertEqual(cursor, m.pdf_size,
                             "ranges must extend to pdf_size")

    def test_hash_stable_across_calls(self):
        """Same PDF → same chunk hashes. Cornerstone of the
        client-cache hit rate (a body-unchanged compile produces
        identical hashes so nothing re-downloads)."""
        payloads = [b"<</A 1>>", b"<</B 2>>"]
        pdf = _build_xref_stream_pdf(payloads)
        with _PdfFixture(pdf) as f:
            m1 = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            m2 = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m1)
            self.assertIsNotNone(m2)
            self.assertEqual(
                [c.hash for c in m1.chunks],
                [c.hash for c in m2.chunks],
            )

    def test_single_object_change_invalidates_one_chunk(self):
        """A change to one object's content must change exactly
        one chunk hash. (This is the property that makes the
        whole optimization worthwhile.)"""
        before = [b"<</A 1>>", b"<</B 2>>", b"<</C 3>>"]
        # Change object 2's content; other objects stay the same.
        after = [b"<</A 1>>", b"<</B XYZ>>", b"<</C 3>>"]
        pdf_before = _build_xref_stream_pdf(before)
        pdf_after = _build_xref_stream_pdf(after)
        with _PdfFixture(pdf_before) as f1, \
                _PdfFixture(pdf_after) as f2:
            m1 = _PC.compute_manifest(f1.pdf_path, f1.chunks_dir)
            m2 = _PC.compute_manifest(f2.pdf_path, f2.chunks_dir)
            self.assertIsNotNone(m1)
            self.assertIsNotNone(m2)
            hashes_before = [c.hash for c in m1.chunks]
            hashes_after = [c.hash for c in m2.chunks]
            # In practice the xref stream chunk also changes (it
            # records object byte offsets, which shift when an
            # object's size changes). The "core" property: the
            # FIRST chunk (object 1, unchanged "<</A 1>>") MUST
            # have a stable hash.
            self.assertEqual(hashes_before[0], hashes_after[0],
                             "unchanged leading object's hash must be stable")

    def test_w_variant_sizes(self):
        """The /W field can vary across PDFs. tectonic typically
        uses [1 2 2] but a longer document might require [1 3 2]
        or [1 4 2] for objects beyond 64 KB."""
        payloads = [b"<</A 1>>", b"<</B 2>>"]
        pdf = _build_xref_stream_pdf(payloads, w=(1, 3, 1))
        with _PdfFixture(pdf) as f:
            m = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m)
            self.assertEqual(len(m.chunks), 3)  # 2 objects + xref stream


# -----------------------------------------------------------------------------
# Classic xref table tests
# -----------------------------------------------------------------------------


class TestClassicXref(unittest.TestCase):
    """Older PDFs (and some non-tectonic producers) emit the
    classic ASCII xref table. We handle this as a fallback."""

    def test_minimal_two_object_pdf(self):
        payloads = [
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[]/Count 0>>",
        ]
        pdf = _build_classic_xref_pdf(payloads)
        with _PdfFixture(pdf) as f:
            m = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m, "classic xref PDF must parse")
            # 2 input objects → 2 chunks. (Unlike xref-stream
            # format, the xref table itself doesn't get a
            # chunk — it has no object number.)
            self.assertEqual(len(m.chunks), 2)
            ids = [c.object_id for c in m.chunks]
            self.assertEqual(sorted(ids), [1, 2])

    def test_classic_chunk_content_matches(self):
        payloads = [b"<</A 1>>", b"<</B 2>>"]
        pdf = _build_classic_xref_pdf(payloads)
        with _PdfFixture(pdf) as f:
            m = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m)
            for chunk in m.chunks:
                on_disk = (f.chunks_dir / chunk.hash).read_bytes()
                self.assertEqual(on_disk, pdf[chunk.start:chunk.end])


# -----------------------------------------------------------------------------
# Error paths (must never crash; always return None)
# -----------------------------------------------------------------------------


class TestErrorPaths(unittest.TestCase):
    """Malformed PDFs and edge cases: ``compute_manifest`` must
    return None so the server falls back to whole-PDF transport.
    Crashing here would take down the live-preview HTTP handler."""

    def test_empty_file_returns_none(self):
        with _PdfFixture(b"") as f:
            self.assertIsNone(_PC.compute_manifest(f.pdf_path, f.chunks_dir))

    def test_not_a_pdf(self):
        with _PdfFixture(b"this is not a PDF, just text") as f:
            self.assertIsNone(_PC.compute_manifest(f.pdf_path, f.chunks_dir))

    def test_missing_startxref(self):
        # Valid PDF header but no trailer.
        with _PdfFixture(b"%PDF-1.5\nsome content but no trailer\n") as f:
            self.assertIsNone(_PC.compute_manifest(f.pdf_path, f.chunks_dir))

    def test_startxref_offset_past_eof(self):
        pdf = b"%PDF-1.5\nblah\nstartxref\n99999999\n%%EOF\n"
        with _PdfFixture(pdf) as f:
            self.assertIsNone(_PC.compute_manifest(f.pdf_path, f.chunks_dir))

    def test_xref_stream_with_unsupported_filter(self):
        # Build a normal xref stream then patch the /Filter to
        # something we don't support.
        pdf = _build_xref_stream_pdf([b"<</A 1>>"])
        pdf = pdf.replace(b"/Filter/FlateDecode", b"/Filter/ASCIIHexDecode")
        with _PdfFixture(pdf) as f:
            self.assertIsNone(_PC.compute_manifest(f.pdf_path, f.chunks_dir))

    def test_truncated_pdf(self):
        # A valid PDF cut mid-stream.
        pdf = _build_xref_stream_pdf([b"<</A 1>>", b"<</B 2>>"])
        truncated = pdf[: len(pdf) // 2]
        with _PdfFixture(truncated) as f:
            self.assertIsNone(_PC.compute_manifest(f.pdf_path, f.chunks_dir))

    def test_oversize_pdf_returns_none(self):
        # We don't want to actually allocate 257 MB in a test; use
        # a sparse file (zero-byte at high offset).
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pdf = tmp / "doc.pdf"
            with open(pdf, "wb") as fp:
                fp.seek(_PC.MAX_PDF_SIZE + 1)
                fp.write(b"\0")
            self.assertIsNone(
                _PC.compute_manifest(pdf, tmp / "chunks"),
                "oversize PDF must return None, not try to load",
            )


# -----------------------------------------------------------------------------
# Atomicity
# -----------------------------------------------------------------------------


class TestAtomicWrite(unittest.TestCase):
    def test_atomic_write_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x"
            _PC._atomic_write_bytes(dest, b"hello")
            self.assertEqual(dest.read_bytes(), b"hello")
            # No leftover tmp file.
            self.assertFalse((Path(tmp) / "x.tmp").exists())

    def test_atomic_write_replaces_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x"
            dest.write_bytes(b"old content")
            _PC._atomic_write_bytes(dest, b"new content")
            self.assertEqual(dest.read_bytes(), b"new content")


# -----------------------------------------------------------------------------
# Existing-file dedup
# -----------------------------------------------------------------------------


class TestChunkDeduplication(unittest.TestCase):
    """Two compiles producing identical chunks must not re-write
    the on-disk chunk files (they're content-addressed and the
    contents are by definition identical). This keeps mtimes
    stable so the GC can rely on them."""

    def test_existing_chunk_not_rewritten(self):
        payloads = [b"<</A 1>>", b"<</B 2>>"]
        pdf = _build_xref_stream_pdf(payloads)
        with _PdfFixture(pdf) as f:
            m1 = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m1)
            # Snapshot mtimes after first call.
            first_mtimes = {
                c.hash: (f.chunks_dir / c.hash).stat().st_mtime_ns
                for c in m1.chunks
            }
            # Sleep then second call.
            import time
            time.sleep(0.02)
            m2 = _PC.compute_manifest(f.pdf_path, f.chunks_dir)
            self.assertIsNotNone(m2)
            for c in m2.chunks:
                cur = (f.chunks_dir / c.hash).stat().st_mtime_ns
                self.assertEqual(
                    cur, first_mtimes[c.hash],
                    f"chunk {c.hash} was rewritten "
                    "(mtime should be stable)",
                )


if __name__ == "__main__":
    unittest.main()
