"""Unit tests for the watcher debouncer FSM embedded in serve_web.py.tpl.

The watcher's job is to bridge between filesystem mtime polling and
the ``bazel build`` invocation. The debouncer FSM in
``_debouncer_step`` decides *when* a detected change should
trigger a rebuild. The hot-path correctness properties we want:

* Coalesce bursts (write-then-rename, format-on-save, fast typing)
  into a single rebuild.
* Never let the debounce window stretch unboundedly under
  continuous activity — the hard cap fires the build after at most
  ``debounce_max`` of total wait.
* No-op when ``fresh_changes`` is empty and the FSM is idle.
* Be deterministic and side-effect-free in the "no fire" path so
  the watcher loop can step it once per poll without surprises.

The FSM is exercised here as a pure function (clock supplied as
``now``), independent of the watcher loop's ``time.sleep`` and
``time.monotonic`` machinery. The end-to-end behaviour (poll
detection × FSM × build dispatch) is integration-tested by the
serve smoke target.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "latex"
    / "private"
    / "serve_web.py.tpl"
)

# Identical to the test_synctex_parser placeholder set. Kept
# separate (rather than imported) so this test stays runnable
# in isolation without an import-order dependency on the sibling
# test file.
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
            "serve_web_test_module_debouncer", tmp.name,
        )
        module = importlib.util.module_from_spec(spec)
        # Install in sys.modules before exec so the @dataclass
        # decorator's `sys.modules.get(cls.__module__).__dict__`
        # lookup works under Python 3.12+.
        sys.modules["serve_web_test_module_debouncer"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        Path(tmp.name).unlink()


_M = _load_template_module()


# Convenience: short aliases for the symbols under test.
_DebouncerState = _M._DebouncerState
_step = _M._debouncer_step


# Realistic timing parameters (seconds) matching the defaults the
# rule passes through. Held in module-scope constants so individual
# tests don't drift.
WINDOW = 0.25  # debounce_ms = 250 ms
HARD = 1.5     # debounce_max_ms = 1500 ms


class TestIdleState(unittest.TestCase):
    """When no changes have been seen, the FSM stays idle and the
    step is a pure no-op."""

    def test_no_changes_no_fire(self):
        fsm = _DebouncerState()
        fire, hard = _step(fsm, [], now=0.0,
                           debounce_window=WINDOW, debounce_max=HARD)
        self.assertFalse(fire)
        self.assertFalse(hard)
        self.assertIsNone(fsm.idle_deadline)
        self.assertIsNone(fsm.deadline_max)
        self.assertEqual(fsm.pending_changes, [])

    def test_repeated_no_change_polls_remain_idle(self):
        fsm = _DebouncerState()
        for t in (0.0, 0.08, 0.16, 0.24, 0.32):
            fire, _ = _step(fsm, [], now=t,
                            debounce_window=WINDOW, debounce_max=HARD)
            self.assertFalse(fire)
        self.assertIsNone(fsm.idle_deadline)


class TestSingleChange(unittest.TestCase):
    """One change at t=0 should fire exactly once, after the idle
    window elapses."""

    def test_first_change_arms_debounce(self):
        fsm = _DebouncerState()
        fire, _ = _step(fsm, [Path("/foo")], now=0.0,
                        debounce_window=WINDOW, debounce_max=HARD)
        self.assertFalse(fire, "first change must not fire immediately")
        self.assertEqual(fsm.idle_deadline, WINDOW)
        self.assertEqual(fsm.deadline_max, HARD)
        self.assertEqual(fsm.pending_changes, [Path("/foo")])

    def test_fires_at_deadline(self):
        fsm = _DebouncerState()
        _step(fsm, [Path("/foo")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        # Just before the deadline: no fire.
        fire, hard = _step(fsm, [], now=WINDOW - 0.001,
                           debounce_window=WINDOW, debounce_max=HARD)
        self.assertFalse(fire)
        # At the deadline: fire (and not via hard cap).
        fire, hard = _step(fsm, [], now=WINDOW,
                           debounce_window=WINDOW, debounce_max=HARD)
        self.assertTrue(fire)
        self.assertFalse(hard)

    def test_pending_changes_carries_path(self):
        fsm = _DebouncerState()
        _step(fsm, [Path("/foo.tex")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        _step(fsm, [], now=WINDOW,
              debounce_window=WINDOW, debounce_max=HARD)
        self.assertEqual(fsm.pending_changes, [Path("/foo.tex")])


class TestBurstCoalescing(unittest.TestCase):
    """Two changes inside the debounce window should produce one
    fire, not two."""

    def test_two_changes_in_window_one_fire(self):
        fsm = _DebouncerState()
        # First change at t=0.
        _step(fsm, [Path("/a")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        # Second change at t=0.1 (still inside the 0.25 window).
        fire, _ = _step(fsm, [Path("/b")], now=0.1,
                        debounce_window=WINDOW, debounce_max=HARD)
        self.assertFalse(fire,
                         "second change inside window must not fire")
        # Deadline was pushed out: 0.1 + 0.25 = 0.35.
        self.assertAlmostEqual(fsm.idle_deadline, 0.35)
        # Old deadline (0.25) should NOT fire.
        fire, _ = _step(fsm, [], now=0.26,
                        debounce_window=WINDOW, debounce_max=HARD)
        self.assertFalse(fire,
                         "deadline must have been pushed by second change")
        # New deadline DOES fire.
        fire, _ = _step(fsm, [], now=0.36,
                        debounce_window=WINDOW, debounce_max=HARD)
        self.assertTrue(fire)

    def test_pending_changes_dedupes(self):
        # Two polls reporting the same path should not duplicate
        # it in pending_changes (the operator log would otherwise
        # repeat).
        fsm = _DebouncerState()
        _step(fsm, [Path("/foo")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        _step(fsm, [Path("/foo")], now=0.05,
              debounce_window=WINDOW, debounce_max=HARD)
        self.assertEqual(fsm.pending_changes, [Path("/foo")])

    def test_two_distinct_files_both_recorded(self):
        fsm = _DebouncerState()
        _step(fsm, [Path("/a")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        _step(fsm, [Path("/b")], now=0.05,
              debounce_window=WINDOW, debounce_max=HARD)
        self.assertEqual(fsm.pending_changes, [Path("/a"), Path("/b")])


class TestHardCap(unittest.TestCase):
    """Continuous activity must not extend the debounce window
    beyond ``debounce_max``."""

    def test_continuous_activity_fires_at_hard_cap(self):
        fsm = _DebouncerState()
        # Initial change at t=0.
        _step(fsm, [Path("/foo")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        # Every 0.1s a new change arrives — the idle window would
        # never elapse on its own.
        for t in [0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 1.4]:
            fire, hard = _step(fsm, [Path("/foo")], now=t,
                               debounce_window=WINDOW, debounce_max=HARD)
            self.assertFalse(fire,
                             f"hard cap (1.5) not yet reached at t={t}")
        # At t=1.5 the hard cap fires.
        fire, hard = _step(fsm, [Path("/foo")], now=HARD,
                           debounce_window=WINDOW, debounce_max=HARD)
        self.assertTrue(fire, "hard cap must fire at deadline_max")
        self.assertTrue(hard,
                        "hit_hard_cap signal must be set on hard-cap fire")

    def test_hard_cap_anchored_to_first_change(self):
        # The hard cap is set once on entry to DEBOUNCING and does
        # NOT get pushed by subsequent changes. This is the whole
        # point — it bounds total wait time.
        fsm = _DebouncerState()
        _step(fsm, [Path("/foo")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        first_cap = fsm.deadline_max
        _step(fsm, [Path("/foo")], now=0.5,
              debounce_window=WINDOW, debounce_max=HARD)
        self.assertEqual(fsm.deadline_max, first_cap,
                         "deadline_max must not change after entry")

    def test_idle_window_fires_first_if_short_burst(self):
        # If the user makes one save then goes idle, the idle
        # window fires well before the hard cap. The hard cap
        # signal must NOT be set in this case.
        fsm = _DebouncerState()
        _step(fsm, [Path("/foo")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        fire, hard = _step(fsm, [], now=WINDOW,
                           debounce_window=WINDOW, debounce_max=HARD)
        self.assertTrue(fire)
        self.assertFalse(hard,
                         "short-burst fire must not flag the hard cap")


class TestZeroDebounce(unittest.TestCase):
    """``debounce_ms = 0`` should reproduce the legacy
    fire-on-every-poll behaviour for users who opted out of the
    debouncer entirely."""

    def test_zero_window_fires_on_first_change(self):
        fsm = _DebouncerState()
        fire, _ = _step(fsm, [Path("/foo")], now=0.0,
                        debounce_window=0.0, debounce_max=HARD)
        self.assertTrue(fire,
                        "zero-debounce: change must fire same tick")


class TestStateReuse(unittest.TestCase):
    """The watcher loop reuses the same ``_DebouncerState`` instance
    across many ticks. Confirm a manual reset (the loop does this
    after a fire) lets the FSM start fresh."""

    def test_manual_reset_after_fire(self):
        fsm = _DebouncerState()
        _step(fsm, [Path("/foo")], now=0.0,
              debounce_window=WINDOW, debounce_max=HARD)
        fire, _ = _step(fsm, [], now=WINDOW,
                        debounce_window=WINDOW, debounce_max=HARD)
        self.assertTrue(fire)

        # The watcher loop performs this reset after dispatching
        # the build. Reproduce it here.
        fsm.idle_deadline = None
        fsm.deadline_max = None
        fsm.pending_changes.clear()

        # Subsequent ticks see no fresh changes → idle.
        fire, _ = _step(fsm, [], now=WINDOW + 0.1,
                        debounce_window=WINDOW, debounce_max=HARD)
        self.assertFalse(fire)
        # A new burst arms a fresh window.
        fire, _ = _step(fsm, [Path("/bar")], now=WINDOW + 1.0,
                        debounce_window=WINDOW, debounce_max=HARD)
        self.assertFalse(fire)
        self.assertEqual(fsm.idle_deadline, WINDOW + 1.0 + WINDOW)


if __name__ == "__main__":
    unittest.main()
