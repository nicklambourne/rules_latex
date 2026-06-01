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
        # A fast failure that is NOT a missing resource -> report it
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

    def test_fast_failure_without_cache_does_not_fall_back(self):
        # No cache context (cache=/bundle doc): a missing resource can't
        # be re-primed, so a fast failure is reported as-is.
        fail = (False, 0.1, "fast rebuild failed", "File `x.sty' not found")
        self._patch(fast_result=fail)
        out = _M.rebuild(Path("/ws"), cache_ctx=None, fast_ctx=object())
        self.assertEqual(self.calls, ["fast"])
        self.assertEqual(out, fail)


if __name__ == "__main__":
    unittest.main()
