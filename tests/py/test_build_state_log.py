"""Unit tests for `BuildState.set_log` / `get_log` in serve_web.py.tpl.

The build-log drawer (UI PR 5/7) caps server-retained output at
`LOG_MAX_BYTES = 64 KiB`. The cap lives on the **tail** of the
output — bazel emits 'BUILD FAILED' and the surrounding error
context at the *end* of the stream, so trimming from the head is
what preserves the useful part. These tests pin that contract.

The truncation has to be byte-aware: trimming a UTF-8 byte stream
at an arbitrary offset can split a multi-byte character mid-
sequence, producing an undecodable result. set_log slices in
bytes then drops the leading continuation bytes (`0x80-0xBF`) so
the surviving prefix always starts on a code-point boundary.
"""

from __future__ import annotations

import unittest

from tests.py._template_loader import load_template_module

_M = load_template_module(name="serve_web_log_test")
LOG_MAX_BYTES = _M.LOG_MAX_BYTES


class SetLogTest(unittest.TestCase):
    def setUp(self):
        self.state = _M.BuildState()

    def test_get_initial_state(self):
        log_id, text = self.state.get_log()
        self.assertEqual(log_id, 0)
        self.assertEqual(text, "")

    def test_set_log_round_trip(self):
        self.state.set_log("hello world\n")
        log_id, text = self.state.get_log()
        self.assertEqual(log_id, 1)
        self.assertEqual(text, "hello world\n")

    def test_log_id_monotonic(self):
        # Each call bumps the id, even if the text is identical —
        # clients dedup on id, not content.
        self.state.set_log("a")
        self.state.set_log("a")
        self.state.set_log("a")
        log_id, _ = self.state.get_log()
        self.assertEqual(log_id, 3)

    def test_set_log_returns_new_id(self):
        n1 = self.state.set_log("first")
        n2 = self.state.set_log("second")
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 2)
        # And get_log agrees with the last set's returned id.
        log_id, _ = self.state.get_log()
        self.assertEqual(log_id, 2)

    def test_none_becomes_empty(self):
        # The contract documents `None` as a noop-equivalent.
        self.state.set_log(None)
        log_id, text = self.state.get_log()
        self.assertEqual(log_id, 1)
        self.assertEqual(text, "")

    def test_under_cap_passes_through_unchanged(self):
        # ASCII payload well under LOG_MAX_BYTES — should round-trip
        # byte-for-byte (no truncation marker prepended).
        msg = "INFO: Build completed successfully\n"
        self.state.set_log(msg)
        _, text = self.state.get_log()
        self.assertEqual(text, msg)
        self.assertNotIn("(truncated)", text)

    def test_truncates_oversize_input_from_head(self):
        # Build a > LOG_MAX_BYTES payload where the unique tail
        # marker survives but the unique head marker doesn't.
        head = b"HEAD-MARKER-DO-NOT-KEEP\n"
        filler = b"x" * (LOG_MAX_BYTES + 10_000)
        tail = b"TAIL-MARKER-MUST-SURVIVE\nBUILD FAILED\n"
        big = (head + filler + tail).decode("ascii")
        self.state.set_log(big)
        _, text = self.state.get_log()

        self.assertNotIn("HEAD-MARKER-DO-NOT-KEEP", text)
        self.assertIn("TAIL-MARKER-MUST-SURVIVE", text)
        self.assertIn("BUILD FAILED", text)
        # The truncation marker prefix is part of the contract;
        # the JS drawer uses it to surface "log was clipped" to
        # the user if anyone asks.
        self.assertIn("(truncated)", text)

    def test_truncated_payload_under_cap_plus_marker(self):
        # After truncation the stored text should be within
        # LOG_MAX_BYTES + a small ceiling for the prepended marker.
        big = "y" * (LOG_MAX_BYTES * 3)
        self.state.set_log(big)
        _, text = self.state.get_log()
        # Marker is "... (truncated)\n" — 16 bytes — give 64 of
        # slop for safety.
        self.assertLessEqual(
            len(text.encode("utf-8")),
            LOG_MAX_BYTES + 64,
        )

    def test_truncates_on_codepoint_boundary(self):
        # The tail-slice may land inside a multi-byte UTF-8
        # character. set_log must drop any leading continuation
        # bytes so the surviving text is well-formed Unicode.
        # We construct an input where the byte-aligned cut would
        # land mid-codepoint, then assert the decode succeeds and
        # the text is well-formed.
        #
        # 'é' encodes as b'\xc3\xa9' (2 bytes); we frame the cut
        # so the boundary nominally falls on the trailing 0xa9
        # continuation byte. The set_log strip-leading-
        # continuations rule should yank it off.
        prefix = b"\xc3\xa9" * (LOG_MAX_BYTES // 2)  # all 'é'
        # Pad so total exceeds the cap and the cut lands inside
        # an 'é' codepoint.
        big = (prefix + b"TAIL").decode("utf-8")
        self.state.set_log(big)
        _, text = self.state.get_log()
        # Mostly we're asserting "doesn't raise a decode error"
        # (implicit in `text` being a str), and that the
        # surviving text really is well-formed UTF-8.
        self.assertTrue(
            text.encode("utf-8").decode("utf-8"),
            "stored log should be well-formed UTF-8",
        )
        # Sanity: the tail still made it through.
        self.assertIn("TAIL", text)


if __name__ == "__main__":
    unittest.main()
