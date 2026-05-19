"""Unit tests for the watcher post-build hook in serve_web.py.tpl.

After every successful build the watcher thread calls
``_compute_manifest_post_build`` to (1) parse the PDF into
content-addressed chunks, (2) write the chunk files into the
per-document chunks directory, and (3) GC chunks no longer in the
current manifest after a min-age guard.

The tests below exercise the integration of ``pdf_chunks`` (the
real chunker) with the serve script's state plumbing. We
deliberately load the template module rather than mocking out
``pdf_chunks``, because the contract under test is exactly that
the wiring matches: any drift between ``pdf_chunks.Manifest`` and
what ``BuildState.update_manifest`` expects would only show up at
integration time.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
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
    "{{PORT}}": "8765",
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
            "serve_web_test_module_chunks", tmp.name,
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["serve_web_test_module_chunks"] = module
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


def _build_minimal_pdf() -> bytes:
    """Synthesise a minimal valid PDF with a cross-reference
    stream. Reused from test_pdf_chunks's synthesiser via a
    direct copy to keep the test self-contained."""
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
    w = (1, 2, 2)
    entries = bytearray()
    entries.extend(b"\x00\x00\x00\xff\xff")  # free, gen 65535
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


class TestPostBuildHookIntegration(unittest.TestCase):
    """``_compute_manifest_post_build`` is the glue between
    pdf_chunks (parser) and BuildState (consumer). It must:

    * Produce a manifest installed via update_manifest() when the
      PDF is parseable.
    * Install None when the PDF is missing (e.g. outfmt != pdf).
    * Never raise — failures must degrade gracefully so the watcher
      thread doesn't die.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="post_build_hook_test_")
        self.workspace = Path(self._tmp.name)
        # Mimic the real layout: PDF lives at
        # workspace/bazel-bin/<PDF_RELPATH>, chunks under
        # workspace/.cache/rules_latex/<slug>/chunks/.
        self.pdf_dir = self.workspace / "bazel-bin" / "test"
        self.pdf_dir.mkdir(parents=True)
        self.pdf_path = self.pdf_dir / "doc.pdf"
        self.chunks_dir = self.workspace / "chunks"
        self.chunks_dir.mkdir()
        self.ctx = _M.PdfChunksContext(
            module=_PC,
            chunks_dir=self.chunks_dir,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_manifest_installed_when_pdf_parseable(self):
        self.pdf_path.write_bytes(_build_minimal_pdf())
        state = _M.BuildState()
        _M._compute_manifest_post_build(state, self.workspace, self.ctx)
        manifest = state.get_manifest()
        self.assertIsNotNone(
            manifest,
            "minimal valid PDF must produce a manifest",
        )
        # 3 input objects + 1 xref-stream object = 4 chunks.
        self.assertEqual(len(manifest.chunks), 4)
        # Chunk files were written to disk.
        for chunk in manifest.chunks:
            self.assertTrue(
                (self.chunks_dir / chunk.hash).is_file(),
                f"chunk {chunk.hash} not written",
            )

    def test_manifest_none_when_pdf_missing(self):
        # No PDF on disk — e.g. outfmt = "xdv".
        state = _M.BuildState()
        _M._compute_manifest_post_build(state, self.workspace, self.ctx)
        self.assertIsNone(state.get_manifest())

    def test_manifest_none_when_pdf_unparseable(self):
        # Garbage data: chunker returns None; hook installs None.
        self.pdf_path.write_bytes(b"not a PDF at all")
        state = _M.BuildState()
        _M._compute_manifest_post_build(state, self.workspace, self.ctx)
        self.assertIsNone(state.get_manifest())

    def test_no_chunks_ctx_is_noop(self):
        # When the chunker isn't loaded (e.g. runfiles glitch),
        # the hook must do nothing rather than raise.
        state = _M.BuildState()
        # No exception, no state change.
        _M._compute_manifest_post_build(state, self.workspace, None)
        self.assertIsNone(state.get_manifest())

    def test_two_builds_keep_stable_chunk_hashes(self):
        """Same PDF compiled twice → same chunk hashes → no
        re-writes (the dedup test on pdf_chunks already covers
        rewriting, but here we exercise the BuildState round-
        trip)."""
        self.pdf_path.write_bytes(_build_minimal_pdf())
        state = _M.BuildState()
        _M._compute_manifest_post_build(state, self.workspace, self.ctx)
        first = [c.hash for c in state.get_manifest().chunks]
        _M._compute_manifest_post_build(state, self.workspace, self.ctx)
        second = [c.hash for c in state.get_manifest().chunks]
        self.assertEqual(first, second)


class TestChunkGC(unittest.TestCase):
    """``_gc_chunks`` must delete only chunk files (a) not in the
    keep set AND (b) older than ``min_age_seconds``. The age
    guard prevents thrashing during back-and-forth edits."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gc_test_")
        self.chunks_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_chunk(self, hash_hex: str, content: bytes = b"x",
                     age_seconds: float = 0.0) -> Path:
        p = self.chunks_dir / hash_hex
        p.write_bytes(content)
        if age_seconds > 0:
            # Backdate the mtime so the GC's age check sees it as
            # old.
            past = time.time() - age_seconds
            import os
            os.utime(p, (past, past))
        return p

    def test_deletes_old_chunk_not_in_keep(self):
        h_old = "a" * 64
        h_keep = "b" * 64
        self._write_chunk(h_old, age_seconds=600)  # 10 min old
        self._write_chunk(h_keep)
        deleted = _M._gc_chunks(
            self.chunks_dir, {h_keep}, min_age_seconds=300,
        )
        self.assertEqual(deleted, 1)
        self.assertFalse((self.chunks_dir / h_old).exists())
        self.assertTrue((self.chunks_dir / h_keep).exists())

    def test_preserves_recent_chunk_not_in_keep(self):
        # Recently-created chunks must be kept even if absent from
        # the current manifest, so that fast edit-undo doesn't
        # cause a re-fetch.
        h_recent = "c" * 64
        self._write_chunk(h_recent)
        deleted = _M._gc_chunks(
            self.chunks_dir, set(), min_age_seconds=300,
        )
        self.assertEqual(deleted, 0)
        self.assertTrue((self.chunks_dir / h_recent).exists())

    def test_ignores_non_hash_filenames(self):
        # The chunks dir may contain other things — atomic-write
        # tmp files, .gitignore, accidental cruft. GC must only
        # touch 64-hex-char files.
        (self.chunks_dir / "junk.txt").write_text("not a chunk")
        (self.chunks_dir / "abc123.tmp").write_text("atomic-write sidecar")
        # 65-char file (not a SHA-256).
        too_long = "d" * 65
        (self.chunks_dir / too_long).write_text("not a hash")
        deleted = _M._gc_chunks(
            self.chunks_dir, set(), min_age_seconds=0,
        )
        # Zero because none of these match the hash regex.
        self.assertEqual(deleted, 0)
        self.assertTrue((self.chunks_dir / "junk.txt").exists())
        self.assertTrue((self.chunks_dir / "abc123.tmp").exists())
        self.assertTrue((self.chunks_dir / too_long).exists())

    def test_keeps_chunks_in_keep_set_regardless_of_age(self):
        # Even a chunk older than min_age stays if it's still in
        # the current manifest.
        h_old_kept = "e" * 64
        self._write_chunk(h_old_kept, age_seconds=999)
        deleted = _M._gc_chunks(
            self.chunks_dir, {h_old_kept}, min_age_seconds=300,
        )
        self.assertEqual(deleted, 0)
        self.assertTrue((self.chunks_dir / h_old_kept).exists())

    def test_missing_chunks_dir_no_error(self):
        # If the chunks dir vanished between calls (filesystem
        # surprise), GC must not crash — the watcher thread keeps
        # running.
        missing = self.chunks_dir / "does_not_exist"
        deleted = _M._gc_chunks(missing, set(), min_age_seconds=0)
        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()
