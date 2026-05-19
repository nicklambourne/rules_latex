"""Unit tests for tools/serve_cache.py.

Covers the parts of the serve-time cache manager that can be tested
without actually invoking tectonic:

  * Cache-path derivation from document labels (stable, collision-
    resistant, filesystem-safe).
  * Sentinel + lock semantics (is_primed, invalidate_for_reprime).
  * .gitignore auto-management (idempotent, non-fatal on failure).
  * Missing-resource heuristic (used to decide whether to auto-
    re-prime on build failure).
  * Cache nonce computation (used as an --action_env to invalidate
    Bazel's action cache when the snapshot is re-primed).

End-to-end behaviour (run_prime invoking tectonic, the override
flag actually changing the action graph, etc.) is covered by the
Starlark analysis test and the example targets in CI.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
_SERVE_CACHE_PATH = _TOOLS_DIR / "serve_cache.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SC = _load_module("serve_cache", _SERVE_CACHE_PATH)


# -----------------------------------------------------------------------------
# Layout / slugify
# -----------------------------------------------------------------------------


class TestSlugify(unittest.TestCase):
    """The cache directory name must be stable and collision-resistant."""

    def test_simple_label(self):
        slug = _SC._slugify_label("//cv:cv")
        # Predictable prefix and a 6-char hex suffix.
        self.assertTrue(slug.startswith("cv_cv_"), slug)
        # Trailing 6 chars are hex.
        suffix = slug.rsplit("_", 1)[-1]
        self.assertEqual(len(suffix), 6)
        int(suffix, 16)  # hex parse should succeed.

    def test_nested_label(self):
        slug = _SC._slugify_label("//path/to:doc")
        self.assertTrue(slug.startswith("path_to_doc_"), slug)

    def test_distinguishes_similar_labels(self):
        # Two labels that sanitise to the same characters must
        # still produce distinct slugs (the hash suffix is the
        # tie-breaker).
        a = _SC._slugify_label("//a-b:c")
        b = _SC._slugify_label("//a_b:c")
        self.assertNotEqual(a, b)

    def test_bzlmod_canonical_label_stripped(self):
        # Bzlmod canonical labels start with @@ and a module name.
        slug = _SC._slugify_label("@@rules_latex_example~//cv:cv")
        # The leading @@ and module marker must not bleed into the
        # slug path.
        self.assertFalse(slug.startswith("@"))
        # The package + name should still be present.
        self.assertIn("cv_cv", slug)

    def test_filesystem_safe(self):
        slug = _SC._slugify_label("//weird:path/with[brackets]+plus")
        # No characters that would confuse a path tokeniser.
        for ch in slug:
            self.assertTrue(
                ch.isalnum() or ch in "_-.",
                f"slug contains forbidden char {ch!r}: {slug}",
            )


class TestDeriveCacheLayout(unittest.TestCase):
    def test_paths_under_workspace_cache_dir(self):
        layout = _SC.derive_cache_layout(Path("/ws"), "//cv:cv")
        self.assertEqual(layout.base.parent.name, "rules_latex")
        self.assertEqual(layout.base.parent.parent.name, ".cache")
        # All paths share a common base.
        self.assertEqual(layout.snapshot.parent, layout.base)
        self.assertEqual(layout.extracted.parent, layout.base)
        self.assertEqual(layout.sentinel.parent, layout.base)
        self.assertEqual(layout.lock.parent, layout.base)
        # The extracted sentinel lives *inside* the extracted dir.
        self.assertEqual(layout.extracted_sentinel.parent, layout.extracted)

    def test_snapshot_filename_is_cache_tar_gz(self):
        layout = _SC.derive_cache_layout(Path("/ws"), "//cv:cv")
        self.assertEqual(layout.snapshot.name, "cache.tar.gz")

    def test_extracted_dir_is_named_cache(self):
        # The extracted directory is what `latex_document`'s
        # serve-cache-override path consumes as TECTONIC_CACHE_DIR.
        # Its name doesn't matter semantically but pinning it
        # documents the convention.
        layout = _SC.derive_cache_layout(Path("/ws"), "//cv:cv")
        self.assertEqual(layout.extracted.name, "cache")

    def test_stable_across_calls(self):
        a = _SC.derive_cache_layout(Path("/ws"), "//cv:cv")
        b = _SC.derive_cache_layout(Path("/ws"), "//cv:cv")
        self.assertEqual(a, b)


# -----------------------------------------------------------------------------
# Sentinel / is_primed
# -----------------------------------------------------------------------------


class TestPrimedSentinel(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="serve_cache_test_")
        self.ws = Path(self._tmp.name)
        self.layout = _SC.derive_cache_layout(self.ws, "//cv:cv")
        self.layout.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unprimed_initially(self):
        self.assertFalse(_SC.is_primed(self.layout))

    def test_snapshot_alone_is_not_primed(self):
        # A bare snapshot without a sentinel could be a half-written
        # prime; we must treat it as missing.
        self.layout.snapshot.write_bytes(b"\x1f\x8b\x08\x00fake")
        self.assertFalse(_SC.is_primed(self.layout))

    def test_sentinel_alone_is_not_primed(self):
        self.layout.sentinel.write_text("stale\n", encoding="utf-8")
        self.assertFalse(_SC.is_primed(self.layout))

    def test_both_present_is_primed(self):
        self.layout.snapshot.write_bytes(b"\x1f\x8b\x08\x00fake")
        self.layout.sentinel.write_text("ok\n", encoding="utf-8")
        self.assertTrue(_SC.is_primed(self.layout))

    def test_invalidate_for_reprime_removes_both_sentinels(self):
        # The prime + extract pair share an invalidation event:
        # if we re-prime because of a missing resource, the
        # extracted tree is also stale.
        self.layout.snapshot.write_bytes(b"\x1f\x8b\x08\x00fake")
        self.layout.sentinel.write_text("ok\n", encoding="utf-8")
        self.layout.extracted.mkdir(parents=True, exist_ok=True)
        self.layout.extracted_sentinel.write_text("ok\n", encoding="utf-8")
        _SC.invalidate_for_reprime(self.layout)
        self.assertFalse(self.layout.sentinel.exists())
        self.assertFalse(self.layout.extracted_sentinel.exists())
        # The snapshot tarball and extracted dir are left intact
        # so any in-flight read doesn't blow up.
        self.assertTrue(self.layout.snapshot.exists())
        self.assertTrue(self.layout.extracted.exists())

    def test_invalidate_is_idempotent_when_sentinel_absent(self):
        # Should not raise even if sentinels are already gone.
        _SC.invalidate_for_reprime(self.layout)
        _SC.invalidate_for_reprime(self.layout)


class TestExtractedSentinel(unittest.TestCase):
    """The extracted cache directory has its own atomicity marker.

    The compile action reads ``TECTONIC_CACHE_DIR`` straight from
    ``layout.extracted`` (skipping the per-action tarball
    decompression). A half-extracted tree must NOT be treated as
    ready-to-use.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="serve_cache_test_")
        self.ws = Path(self._tmp.name)
        self.layout = _SC.derive_cache_layout(self.ws, "//cv:cv")
        self.layout.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unextracted_initially(self):
        self.assertFalse(_SC.is_extracted(self.layout))

    def test_dir_alone_is_not_extracted(self):
        # An extracted/ directory without the sentinel could be a
        # half-extracted tree (interrupted midway).
        self.layout.extracted.mkdir(parents=True, exist_ok=True)
        (self.layout.extracted / "bundles").mkdir()
        self.assertFalse(_SC.is_extracted(self.layout))

    def test_sentinel_without_dir_is_not_extracted(self):
        # Sentinel files are only valid alongside their parent dir.
        # is_extracted checks both, so a stray sentinel doesn't
        # accidentally enable the fast-path.
        # (The sentinel lives inside the extracted dir, so this
        # case is mostly hypothetical; the parent must exist for
        # the sentinel to even be writable.)
        # Skip if the extracted dir somehow exists; we want to
        # test the negative case.
        if self.layout.extracted.exists():
            self.skipTest("test setup invariant: extracted dir absent")
        self.assertFalse(_SC.is_extracted(self.layout))

    def test_both_present_is_extracted(self):
        self.layout.extracted.mkdir(parents=True, exist_ok=True)
        self.layout.extracted_sentinel.write_text("ok\n", encoding="utf-8")
        self.assertTrue(_SC.is_extracted(self.layout))


class TestExtractSnapshot(unittest.TestCase):
    """``_extract_snapshot`` is what makes the pre-extracted cache
    directory exist. It must be atomic: an interrupted extract
    must never produce an ``is_extracted == True`` state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="serve_cache_test_")
        self.ws = Path(self._tmp.name)
        self.layout = _SC.derive_cache_layout(self.ws, "//cv:cv")
        self.layout.base.mkdir(parents=True, exist_ok=True)
        # Build a tiny but real tarball at the snapshot path so
        # _extract_snapshot has something to chew on.
        import tarfile
        import io as _io
        with tarfile.open(self.layout.snapshot, "w:gz") as tar:
            for name in ("a.txt", "sub/b.txt"):
                info = tarfile.TarInfo(name)
                data = name.encode("utf-8")
                info.size = len(data)
                info.mtime = 0
                tar.addfile(info, _io.BytesIO(data))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_extracts_files_at_expected_paths(self):
        _SC._extract_snapshot(self.layout)
        self.assertTrue(_SC.is_extracted(self.layout))
        self.assertEqual(
            (self.layout.extracted / "a.txt").read_bytes(),
            b"a.txt",
        )
        self.assertEqual(
            (self.layout.extracted / "sub" / "b.txt").read_bytes(),
            b"sub/b.txt",
        )

    def test_replaces_existing_extracted_tree(self):
        # A prior extract that touched different content should
        # be cleanly replaced.
        self.layout.extracted.mkdir(parents=True)
        (self.layout.extracted / "stale.txt").write_text("old")
        _SC._extract_snapshot(self.layout)
        self.assertFalse(
            (self.layout.extracted / "stale.txt").exists(),
            "stale entry from prior extract must be cleaned up",
        )

    def test_idempotent_when_re_run(self):
        _SC._extract_snapshot(self.layout)
        first_sentinel = self.layout.extracted_sentinel.read_text(encoding="utf-8")
        _SC._extract_snapshot(self.layout)
        # The sentinel is re-touched (its content includes a
        # timestamp), but extraction itself remains consistent.
        self.assertTrue(_SC.is_extracted(self.layout))
        # File content unchanged.
        self.assertEqual(
            (self.layout.extracted / "a.txt").read_bytes(),
            b"a.txt",
        )

    def test_failure_during_extract_leaves_no_extracted_sentinel(self):
        # If the extract throws midway, the sentinel must not exist
        # so the next invocation re-tries instead of using a
        # half-baked tree.
        import tarfile

        orig_extractall = tarfile.TarFile.extractall

        def _boom(self, *_a, **_k):
            raise RuntimeError("simulated extract failure")

        try:
            tarfile.TarFile.extractall = _boom
            with self.assertRaises(RuntimeError):
                _SC._extract_snapshot(self.layout)
        finally:
            tarfile.TarFile.extractall = orig_extractall

        self.assertFalse(self.layout.extracted_sentinel.exists())
        self.assertFalse(_SC.is_extracted(self.layout))


# -----------------------------------------------------------------------------
# Cache nonce
# -----------------------------------------------------------------------------


class TestCacheNonce(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="serve_cache_test_")
        self.ws = Path(self._tmp.name)
        self.layout = _SC.derive_cache_layout(self.ws, "//cv:cv")
        self.layout.base.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_snapshot_returns_zero(self):
        self.assertEqual(_SC.cache_nonce(self.layout), "0")

    def test_present_snapshot_returns_mtime_ns(self):
        self.layout.snapshot.write_bytes(b"x")
        nonce = _SC.cache_nonce(self.layout)
        self.assertNotEqual(nonce, "0")
        # Sanity: integer-parseable, plausibly recent.
        n = int(nonce)
        self.assertGreater(n, 0)

    def test_nonce_changes_when_snapshot_is_rewritten(self):
        self.layout.snapshot.write_bytes(b"x")
        first = _SC.cache_nonce(self.layout)
        # Sleep enough to bump nanosecond mtime on slow filesystems.
        time.sleep(0.01)
        self.layout.snapshot.write_bytes(b"x2")
        second = _SC.cache_nonce(self.layout)
        self.assertNotEqual(first, second)


# -----------------------------------------------------------------------------
# .gitignore management
# -----------------------------------------------------------------------------


class TestGitignoreManagement(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="serve_cache_test_")
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_gitignore_with_entry_when_missing(self):
        _SC.ensure_gitignore_excludes_cache(self.ws)
        gi = (self.ws / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".cache/rules_latex", gi)

    def test_appends_entry_to_existing_gitignore(self):
        gi_path = self.ws / ".gitignore"
        gi_path.write_text("bazel-*\nnode_modules/\n", encoding="utf-8")
        _SC.ensure_gitignore_excludes_cache(self.ws)
        gi = gi_path.read_text(encoding="utf-8")
        # Existing entries preserved.
        self.assertIn("bazel-*", gi)
        self.assertIn("node_modules/", gi)
        # New entry appended.
        self.assertIn(".cache/rules_latex", gi)

    def test_idempotent_when_entry_already_present(self):
        gi_path = self.ws / ".gitignore"
        gi_path.write_text(".cache/rules_latex/\n", encoding="utf-8")
        before = gi_path.read_text(encoding="utf-8")
        _SC.ensure_gitignore_excludes_cache(self.ws)
        after = gi_path.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_handles_missing_trailing_newline(self):
        gi_path = self.ws / ".gitignore"
        gi_path.write_text("bazel-*", encoding="utf-8")  # no newline
        _SC.ensure_gitignore_excludes_cache(self.ws)
        gi = gi_path.read_text(encoding="utf-8")
        self.assertTrue(gi.startswith("bazel-*\n"))
        self.assertIn(".cache/rules_latex", gi)

    def test_silent_on_read_only_workspace(self):
        if os.name != "posix":
            self.skipTest("chmod-based read-only check is POSIX-only")
        # Make the workspace dir un-writable.
        os.chmod(self.ws, 0o555)
        try:
            # Should not raise -- failure to manage .gitignore is
            # purely a QoL feature.
            _SC.ensure_gitignore_excludes_cache(self.ws)
        finally:
            os.chmod(self.ws, 0o755)


# -----------------------------------------------------------------------------
# Missing-resource heuristic
# -----------------------------------------------------------------------------


class TestMissingResourceHeuristic(unittest.TestCase):
    """The serve loop auto-re-primes on missing-resource failures.

    The heuristic must catch the common tectonic --only-cached
    failure modes without flagging legit user errors (typos in
    \\textbf, undefined references, etc.).
    """

    def test_detects_missing_sty(self):
        msg = (
            b"error: File `mystyle.sty' not found.\n"
            b"error: halted on potentially-recoverable error\n"
        )
        self.assertTrue(_SC.looks_like_missing_resource(msg))

    def test_detects_missing_font_tfm(self):
        # The exact failure we hit when amsmath body uses math
        # symbols that tectonic didn't preload during prime.
        msg = (
            b"error: Font OMX/cmex/m/n/7=cmex7 at 7.0pt not loadable: "
            b"Metric (TFM) file or installed font not found\n"
        )
        self.assertTrue(_SC.looks_like_missing_resource(msg))

    def test_detects_explicit_cache_miss(self):
        msg = b"error: file foobar.tfm not found in cache\n"
        self.assertTrue(_SC.looks_like_missing_resource(msg))

    def test_ignores_user_latex_errors(self):
        # A run-of-the-mill LaTeX user error must NOT trigger a
        # re-prime -- the cache is fine, the user's source is broken.
        msg = (
            b"error: Undefined control sequence \\textfb.\n"
            b"l.42 \\textfb{wrong}\n"
        )
        self.assertFalse(_SC.looks_like_missing_resource(msg))

    def test_ignores_undefined_reference(self):
        msg = (
            b"warning: Reference `fig:missing' on page 1 undefined\n"
        )
        self.assertFalse(_SC.looks_like_missing_resource(msg))

    def test_ignores_empty_input(self):
        self.assertFalse(_SC.looks_like_missing_resource(b""))


# -----------------------------------------------------------------------------
# Exclusive lock
# -----------------------------------------------------------------------------


class TestExclusiveLock(unittest.TestCase):
    """The flock-based mutual-exclusion is correctness-critical when
    two `bazel run //...:serve` invocations race on a fresh checkout.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="serve_cache_test_")
        self.ws = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_lock_file_lazily(self):
        lock_path = self.ws / ".lock"
        self.assertFalse(lock_path.exists())
        with _SC._exclusive_lock(lock_path):
            self.assertTrue(lock_path.exists())

    def test_can_acquire_after_release(self):
        lock_path = self.ws / ".lock"
        with _SC._exclusive_lock(lock_path):
            pass
        # A second acquisition must succeed; lock is properly
        # released on __exit__.
        with _SC._exclusive_lock(lock_path):
            pass

    def test_released_on_exception(self):
        lock_path = self.ws / ".lock"
        with self.assertRaises(RuntimeError):
            with _SC._exclusive_lock(lock_path):
                raise RuntimeError("boom")
        # Lock should still be acquirable.
        with _SC._exclusive_lock(lock_path):
            pass


if __name__ == "__main__":
    unittest.main()
