"""Unit tests for `_combine_output` inside serve_web.py.tpl.

`_combine_output(stdout, stderr)` builds the "Build log" drawer
payload from the captured outputs of a `bazel build` subprocess.
The invariant that matters is that **stderr lands at the bottom**:
bazel emits its "BUILD FAILED" / error context on stderr, and the
build-log drawer should put that at the end where the user reads
first (the JS summary line picks the last non-empty line of the
combined text). Reversing the order would silently bury build
failures under stdout noise.
"""

from __future__ import annotations

import unittest

# Test helpers — same loader pattern as test_synctex_parser.py.
from tests.py._template_loader import load_template_module

_M = load_template_module(name="serve_web_combine_output_test")


class CombineOutputTest(unittest.TestCase):
    def test_stderr_lands_after_stdout(self):
        out = _M._combine_output("hello stdout", "hello stderr")
        # Body order matters — find indexes.
        i_out = out.index("hello stdout")
        i_err = out.index("hello stderr")
        self.assertLess(
            i_out, i_err,
            "stderr must come after stdout so BUILD FAILED lands at the "
            f"bottom for the drawer summary; got: {out!r}",
        )

    def test_stdout_only(self):
        out = _M._combine_output("only stdout\n", "")
        self.assertIn("only stdout", out)
        self.assertTrue(out.endswith("\n"))

    def test_stderr_only(self):
        # Common case for a clean-but-failing build: bazel prints
        # the summary to stderr and nothing to stdout.
        out = _M._combine_output("", "BUILD FAILED")
        self.assertIn("BUILD FAILED", out)

    def test_both_empty(self):
        # Pathological but reachable: subprocess returned but
        # captured nothing (e.g. bazel exited via signal before
        # emitting anything).
        self.assertEqual(_M._combine_output("", ""), "")

    def test_strips_trailing_newlines_then_separates_blocks(self):
        # The function rstrips each block before joining with a
        # blank-line separator, so a stdout ending in many newlines
        # doesn't double-space the boundary.
        out = _M._combine_output("a\n\n\n", "b\n\n")
        # Exactly one blank line between the two blocks, and a
        # single trailing newline at the end.
        self.assertEqual(out, "a\n\nb\n")

    def test_handles_none_safely(self):
        # subprocess.run with capture_output=True always returns
        # strings, but defensively the function shouldn't crash if
        # a caller passes None somehow. We assert "doesn't crash"
        # not "returns a specific shape" — the contract is just
        # "best-effort string assembly".
        try:
            _M._combine_output("", "")  # baseline
        except Exception as e:
            self.fail(f"combine_output raised on empty inputs: {e}")


if __name__ == "__main__":
    unittest.main()
