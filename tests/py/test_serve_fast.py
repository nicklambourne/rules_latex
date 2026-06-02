"""Unit tests for the serve_fast decision logic inside serve_web.py.tpl.

serve_fast lets the watcher recompile a content edit by replaying the
TectonicCompile action directly (via tools/tectonic_compile.py) instead
of shelling out to `bazel build`. The correctness-critical part is the
``rebuild`` dispatcher: it must take the fast path when eligible, fall
back to `bazel build` only when that could actually help (a missing
cached resource the serve-cache can re-prime), and never double-compile
a genuine LaTeX error. These tests drive ``rebuild`` with the heavy
functions (run_fast_build / run_bazel_build) monkeypatched, so they need
neither Bazel nor tectonic.

The default-off path is also pinned: with no fast context, ``rebuild``
must behave exactly like the old `bazel build`-only watcher.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from tests.py._template_loader import load_template_module

_M = load_template_module(name="serve_web_serve_fast_test")


def _fake_cache_ctx(missing_resource: bool):
    """A stand-in ServeCacheContext whose only used surface is
    ``module.looks_like_missing_resource``."""
    module = types.SimpleNamespace(
        looks_like_missing_resource=lambda _b: missing_resource,
    )
    return types.SimpleNamespace(module=module)


class ParamsPathTest(unittest.TestCase):
    def test_params_path_is_bazel_bin_output_plus_suffix(self):
        # PDF_RELPATH defaults to "test/doc.pdf" in the loader, so the
        # action's params file is bazel-bin/test/doc.pdf-0.params.
        ws = Path("/ws")
        self.assertEqual(
            _M._params_path(ws),
            ws / "bazel-bin" / "test/doc.pdf-0.params",
        )


class RebuildDispatchTest(unittest.TestCase):
    def setUp(self):
        # Snapshot + restore the two functions rebuild() dispatches to.
        self._orig_fast = _M.run_fast_build
        self._orig_bazel = _M.run_bazel_build
        self.calls: list[str] = []

    def tearDown(self):
        _M.run_fast_build = self._orig_fast
        _M.run_bazel_build = self._orig_bazel

    def _patch(self, fast_result, bazel_result=(True, 9.0, "bazel", "")):
        def fake_fast(workspace, fast_ctx):
            self.calls.append("fast")
            return fast_result

        def fake_bazel(workspace, cache_ctx=None):
            self.calls.append("bazel")
            return bazel_result

        _M.run_fast_build = fake_fast
        _M.run_bazel_build = fake_bazel

    def test_no_fast_ctx_always_uses_bazel(self):
        # Default off: rebuild must be the plain `bazel build` path.
        self._patch(fast_result=("unused",))
        out = _M.rebuild(Path("/ws"), cache_ctx=None, fast_ctx=None)
        self.assertEqual(self.calls, ["bazel"])
        self.assertEqual(out, (True, 9.0, "bazel", ""))

    def test_fast_success_skips_bazel(self):
        fast = (True, 0.4, "built in 0.40s (fast)", "ok")
        self._patch(fast_result=fast)
        out = _M.rebuild(Path("/ws"), cache_ctx=None, fast_ctx=object())
        self.assertEqual(self.calls, ["fast"])
        self.assertEqual(out, fast)

    def test_fast_none_falls_back_to_bazel(self):
        # No params file yet (first build of the session) -> bazel.
        self._patch(fast_result=None)
        out = _M.rebuild(Path("/ws"), cache_ctx=_fake_cache_ctx(False), fast_ctx=object())
        self.assertEqual(self.calls, ["fast", "bazel"])
        self.assertEqual(out[3], "")

    def test_fast_missing_resource_with_cache_falls_back(self):
        # A fast failure that looks like a missing cached resource, and a
        # serve-cache that can re-prime -> fall back to bazel.
        self._patch(fast_result=(False, 0.1, "fast rebuild failed", "File `x.sty' not found"))
        out = _M.rebuild(
            Path("/ws"),
            cache_ctx=_fake_cache_ctx(missing_resource=True),
            fast_ctx=object(),
        )
        self.assertEqual(self.calls, ["fast", "bazel"])

    def test_fast_genuine_error_does_not_double_compile(self):
        # A fast failure that is NOT a missing file -> report it
        # directly; bazel would fail identically, so don't recompile.
        fail = (False, 0.5, "fast rebuild failed (0.50s)", "! Undefined control sequence")
        self._patch(fast_result=fail)
        out = _M.rebuild(
            Path("/ws"),
            cache_ctx=_fake_cache_ctx(missing_resource=False),
            fast_ctx=object(),
        )
        self.assertEqual(self.calls, ["fast"])
        self.assertEqual(out, fail)

    def test_new_source_file_falls_back_even_without_cache(self):
        # A newly added glob-captured file (or a \input not in the frozen
        # --src list) surfaces as "File `x' not found". Bazel re-globs,
        # so we fall back even for a cache=/bundle doc (cache_ctx=None).
        fail = (False, 0.1, "fast rebuild failed", "! LaTeX Error: File `new.tex' not found.")
        self._patch(fast_result=fail)
        out = _M.rebuild(Path("/ws"), cache_ctx=None, fast_ctx=object())
        self.assertEqual(self.calls, ["fast", "bazel"])


class FastFailureClassifierTest(unittest.TestCase):
    def test_missing_input_file_needs_bazel(self):
        self.assertTrue(
            _M._fast_failure_needs_bazel("! LaTeX Error: File `sections/new.tex' not found.", None)
        )

    def test_missing_package_needs_bazel(self):
        self.assertTrue(
            _M._fast_failure_needs_bazel("File 'biblatex-apa.sty' not found", None)
        )

    def test_plain_latex_error_does_not(self):
        self.assertFalse(
            _M._fast_failure_needs_bazel("! Undefined control sequence.\nl.42 \\frobnicate", None)
        )

    def test_cache_missing_resource_needs_bazel(self):
        # The serve-cache "not found in cache" signature still triggers a
        # re-prime fallback when a cache context is present.
        self.assertTrue(
            _M._fast_failure_needs_bazel("foo.sty not found in cache", _fake_cache_ctx(True))
        )


class WatchSetTest(unittest.TestCase):
    """The directory-listing watcher that picks up newly-added sources."""

    def test_parse_param_srcs(self):
        params = "--tectonic\nt\n--main\na.tex\n--src\na.tex\n--src\nb/c.tex\n--output\no\n"
        self.assertEqual(_M._parse_param_srcs(params), ["a.tex", "b/c.tex"])

    def test_derive_watch_set_skips_generated_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "main.tex").write_text("x")
            (ws / "parts").mkdir()
            (ws / "parts" / "a.tex").write_text("x")
            (ws / "refs.bib").write_text("x")
            files, dirs, exts = _M._derive_watch_set(
                ws,
                ["main.tex", "parts/a.tex", "refs.bib",
                 "bazel-out/k8/bin/gen.tex", "external/x/y.tex", "missing.tex"],
            )
            self.assertEqual(
                files, {ws / "main.tex", ws / "parts/a.tex", ws / "refs.bib"}
            )
            self.assertEqual(dirs, {ws, ws / "parts"})
            self.assertEqual(exts, frozenset({".tex", ".bib"}))

    def test_dir_listing_ignores_atomic_save_scratch_files(self):
        # The key property: a temp+rename save (vim/VS Code) drops scratch
        # files whose suffix isn't a source ext, so the listing is stable
        # and the edit keeps taking the fast path (not a structural build).
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "ch01.tex").write_text("x")
            (dd / "ch02.tex").write_text("x")
            base = _M._dir_source_listing(dd, frozenset({".tex"}))
            self.assertEqual(base, frozenset({"ch01.tex", "ch02.tex"}))
            # vim/VS Code scratch during an atomic save:
            (dd / "4913").write_text("")          # vim writability probe
            (dd / "ch01.tex~").write_text("x")    # vim backup
            (dd / ".ch01.tex.swp").write_text("") # vim swap
            (dd / "ch01.tex.tmp").write_text("x") # VS Code temp
            self.assertEqual(
                _M._dir_source_listing(dd, frozenset({".tex"})), base,
                "scratch files must not change the source listing",
            )

    def test_dir_listing_detects_new_source(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "ch01.tex").write_text("x")
            before = _M._dir_source_listing(dd, frozenset({".tex"}))
            (dd / "ch02.tex").write_text("x")   # a real new source
            after = _M._dir_source_listing(dd, frozenset({".tex"}))
            self.assertNotEqual(after, before)
            self.assertIn("ch02.tex", after)


if __name__ == "__main__":
    unittest.main()
