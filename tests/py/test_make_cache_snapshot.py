"""Unit tests for tools/make_cache_snapshot.py.

Covers the parts of the tool that are pure functions of the inputs:
the tarball-packing reproducibility, source-root auto-computation,
and the staging-layout logic. The actual tectonic invocation is out
of scope for unit tests (covered by example-target end-to-end builds
in CI).
"""

from __future__ import annotations

import gzip
import importlib.util
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


_TOOL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "tools"
    / "make_cache_snapshot.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("make_cache_snapshot", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_cache_snapshot"] = module
    spec.loader.exec_module(module)
    return module


_M = _load_tool()


class TestComputeSrcRoot(unittest.TestCase):
    """Auto-detection of the deepest common ancestor."""

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        # Layout:
        #   workspace/letter/letter.tex
        #   workspace/letter/preamble.tex
        #   workspace/_shared/logo/logo.png
        (self.workspace / "letter").mkdir()
        (self.workspace / "_shared" / "logo").mkdir(parents=True)
        for rel in [
            "letter/letter.tex",
            "letter/preamble.tex",
            "_shared/logo/logo.png",
        ]:
            (self.workspace / rel).write_text("x")

    def tearDown(self):
        shutil.rmtree(self.workspace)

    def test_single_package_uses_package_dir(self):
        main = self.workspace / "letter" / "letter.tex"
        srcs = [
            self.workspace / "letter" / "letter.tex",
            self.workspace / "letter" / "preamble.tex",
        ]
        root = _M._compute_src_root(main, srcs, requested=None)
        self.assertEqual(root, (self.workspace / "letter").resolve())

    def test_cross_package_uses_workspace_root(self):
        main = self.workspace / "letter" / "letter.tex"
        srcs = [
            self.workspace / "letter" / "letter.tex",
            self.workspace / "_shared" / "logo" / "logo.png",
        ]
        root = _M._compute_src_root(main, srcs, requested=None)
        self.assertEqual(root, self.workspace.resolve())

    def test_explicit_src_root_respected(self):
        main = self.workspace / "letter" / "letter.tex"
        srcs = [self.workspace / "letter" / "letter.tex"]
        root = _M._compute_src_root(main, srcs, requested=self.workspace)
        self.assertEqual(root, self.workspace)

    def test_explicit_src_root_rejects_outside_files(self):
        # If the user pins --src-root but a source escapes it, we
        # fail loudly rather than silently mis-staging the file.
        main = self.workspace / "letter" / "letter.tex"
        srcs = [self.workspace / "_shared" / "logo" / "logo.png"]
        with self.assertRaises(SystemExit) as exc:
            _M._compute_src_root(main, srcs, requested=self.workspace / "letter")
        self.assertIn("not under --src-root", str(exc.exception))


class TestStageSources(unittest.TestCase):
    """Stage sources into a work directory preserving the layout."""

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        (self.workspace / "pkg").mkdir()
        (self.workspace / "pkg" / "main.tex").write_text("hello")
        (self.workspace / "pkg" / "preamble.tex").write_text("preamble")
        (self.workspace / "extra").mkdir()
        (self.workspace / "extra" / "logo.png").write_text("png")
        self.work_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.workspace)
        shutil.rmtree(self.work_dir)

    def test_stages_with_src_root_preserves_layout(self):
        main = self.workspace / "pkg" / "main.tex"
        srcs = [
            self.workspace / "pkg" / "main.tex",
            self.workspace / "pkg" / "preamble.tex",
            self.workspace / "extra" / "logo.png",
        ]
        result = _M.stage_sources(
            main, srcs, self.workspace, self.work_dir
        )
        # Expected layout:
        #   work_dir/pkg/main.tex
        #   work_dir/pkg/preamble.tex
        #   work_dir/extra/logo.png
        self.assertTrue((self.work_dir / "pkg" / "main.tex").is_file())
        self.assertTrue((self.work_dir / "pkg" / "preamble.tex").is_file())
        self.assertTrue((self.work_dir / "extra" / "logo.png").is_file())
        # And the function returns the staged main.
        self.assertEqual(result, self.work_dir / "pkg" / "main.tex")

    def test_stages_flat_when_no_src_root(self):
        main = self.workspace / "pkg" / "main.tex"
        srcs = [
            self.workspace / "pkg" / "main.tex",
            self.workspace / "pkg" / "preamble.tex",
        ]
        result = _M.stage_sources(main, srcs, None, self.work_dir)
        # All files at the top of work_dir.
        self.assertTrue((self.work_dir / "main.tex").is_file())
        self.assertTrue((self.work_dir / "preamble.tex").is_file())
        self.assertEqual(result, self.work_dir / "main.tex")


def _make_fake_cache(parent: Path, files: dict[str, bytes]) -> Path:
    """Materialise a fake tectonic cache directory for pack_cache tests."""
    cache = parent / "cache"
    cache.mkdir()
    for rel, contents in files.items():
        path = cache / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    return cache


class TestPackCache(unittest.TestCase):
    """Determinism and structure of the produced cache tarball."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_pack_is_byte_identical_across_runs(self):
        # The whole reason we hand-roll the tar packing instead of
        # using `tar -cf` is to get this property. Verify it holds.
        cache = _make_fake_cache(self.tmp, {
            "amsmath.sty": b"% pretend amsmath",
            "fonts/cmr10.tfm": b"\x00\x01\x02",
            "bundles/hashes/foo": b"hash",
        })
        out_a = self.tmp / "a.tar.gz"
        out_b = self.tmp / "b.tar.gz"
        _M.pack_cache(cache, out_a)
        _M.pack_cache(cache, out_b)
        self.assertEqual(out_a.read_bytes(), out_b.read_bytes())

    def test_pack_entries_are_sorted_and_have_zero_mtime(self):
        cache = _make_fake_cache(self.tmp, {
            "b.sty": b"second alphabetically",
            "a.sty": b"first alphabetically",
            "c.sty": b"third alphabetically",
        })
        out = self.tmp / "out.tar.gz"
        _M.pack_cache(cache, out)
        with tarfile.open(out, "r:gz") as tar:
            members = tar.getmembers()
        # Sorted by name.
        self.assertEqual(
            [m.name for m in members],
            ["a.sty", "b.sty", "c.sty"],
        )
        # Fixed mtime / uid / gid for reproducibility.
        for m in members:
            self.assertEqual(m.mtime, 0)
            self.assertEqual(m.uid, 0)
            self.assertEqual(m.gid, 0)
            self.assertEqual(m.uname, "")
            self.assertEqual(m.gname, "")

    def test_pack_gzip_header_has_zero_mtime(self):
        # Gzip's outer mtime field, separate from the tar's per-entry
        # mtime. Bytes 4..8 of a gzip stream encode the mtime as
        # little-endian uint32.
        cache = _make_fake_cache(self.tmp, {"a.sty": b"contents"})
        out = self.tmp / "out.tar.gz"
        _M.pack_cache(cache, out)
        header = out.read_bytes()[:10]
        # Magic + method.
        self.assertEqual(header[:2], b"\x1f\x8b")
        self.assertEqual(header[2], 8)  # deflate
        mtime_field = int.from_bytes(header[4:8], "little")
        self.assertEqual(mtime_field, 0)


if __name__ == "__main__":
    unittest.main()
