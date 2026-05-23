"""Unit tests for the retry/backoff helper in tectonic_populate_cache.

Covers the _retry_urlretrieve helper:
  * Succeeds on first attempt → urlretrieve called once, no sleeps.
  * Transient URLError → retried with exponential backoff.
  * 5xx HTTPError → retried (these are usually transient).
  * 4xx HTTPError → propagated immediately (genuine "not there").
  * Exhausted retries → final exception propagated.

Also covers RULES_LATEX_CTAN_MIRROR — that the env var is read once
at module import and substituted into the URL list.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


_TOOL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "tools"
    / "tectonic_populate_cache.py"
)


def _load_module():
    sys.path.insert(0, str(_TOOL_PATH.parent))
    spec = importlib.util.spec_from_file_location("tpc", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tpc"] = module
    spec.loader.exec_module(module)
    return module


tpc = _load_module()


class FakeSleep:
    """Stub for time.sleep that records delays without actually sleeping."""
    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class RetryUrlretrieveTest(unittest.TestCase):
    def test_success_first_attempt_no_retry(self):
        sleeper = FakeSleep()
        calls = []

        def ok(url, dest):
            calls.append(url)

        with patch.object(tpc.urllib.request, "urlretrieve", ok):
            tpc._retry_urlretrieve(
                "https://example/foo.zip", Path("/tmp/x"), sleep=sleeper
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeper.delays, [])

    def test_transient_urlerror_retries_then_succeeds(self):
        sleeper = FakeSleep()
        attempts = [0]

        def flaky(url, dest):
            attempts[0] += 1
            if attempts[0] < 3:
                raise urllib.error.URLError("connection timed out")
            # third attempt succeeds

        with patch.object(tpc.urllib.request, "urlretrieve", flaky):
            tpc._retry_urlretrieve(
                "https://example/foo.zip",
                Path("/tmp/x"),
                sleep=sleeper,
                base_delay=0.1,
            )
        self.assertEqual(attempts[0], 3)
        # Two backoff sleeps before the third attempt; exponential.
        self.assertEqual(sleeper.delays, [0.1, 0.2])

    def test_exhausted_retries_propagates_last_urlerror(self):
        sleeper = FakeSleep()

        def always_fail(url, dest):
            raise urllib.error.URLError("connection refused")

        with patch.object(tpc.urllib.request, "urlretrieve", always_fail):
            with self.assertRaises(urllib.error.URLError):
                tpc._retry_urlretrieve(
                    "https://example/foo.zip",
                    Path("/tmp/x"),
                    sleep=sleeper,
                    max_attempts=3,
                    base_delay=0.1,
                )
        # Slept between attempts 1->2 and 2->3, then gave up.
        self.assertEqual(sleeper.delays, [0.1, 0.2])

    def test_5xx_http_error_retries(self):
        sleeper = FakeSleep()
        attempts = [0]

        def flaky(url, dest):
            attempts[0] += 1
            if attempts[0] < 2:
                raise urllib.error.HTTPError(
                    url, 503, "Service Unavailable", {}, None
                )

        with patch.object(tpc.urllib.request, "urlretrieve", flaky):
            tpc._retry_urlretrieve(
                "https://example/foo.zip",
                Path("/tmp/x"),
                sleep=sleeper,
                base_delay=0.1,
            )
        self.assertEqual(attempts[0], 2)
        self.assertEqual(sleeper.delays, [0.1])

    def test_4xx_http_error_does_not_retry(self):
        # 404 is "the file really isn't there". Retrying just wastes
        # time and burns CTAN's bandwidth. The caller (download_ctan_package)
        # handles 404 by falling through to the next URL — that's the
        # right layer for that decision.
        sleeper = FakeSleep()
        attempts = [0]

        def four_oh_four(url, dest):
            attempts[0] += 1
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with patch.object(tpc.urllib.request, "urlretrieve", four_oh_four):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                tpc._retry_urlretrieve(
                    "https://example/foo.zip",
                    Path("/tmp/x"),
                    sleep=sleeper,
                )
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(attempts[0], 1)
        self.assertEqual(sleeper.delays, [])

    def test_exponential_backoff_doubles(self):
        # Verify the 1s/2s/4s/8s shape with base_delay=1.
        sleeper = FakeSleep()

        def always_fail(url, dest):
            raise urllib.error.URLError("nope")

        with patch.object(tpc.urllib.request, "urlretrieve", always_fail):
            with self.assertRaises(urllib.error.URLError):
                tpc._retry_urlretrieve(
                    "https://example/foo.zip",
                    Path("/tmp/x"),
                    sleep=sleeper,
                    max_attempts=4,
                    base_delay=1.0,
                )
        self.assertEqual(sleeper.delays, [1.0, 2.0, 4.0])


class CtanMirrorEnvOverrideTest(unittest.TestCase):
    """RULES_LATEX_CTAN_MIRROR is read at import; verify it sets CTAN_MIRROR."""

    def test_default_is_official_mirror(self):
        # Re-import under a cleared env so the assertion is robust to
        # whatever the test harness happens to have set in os.environ.
        # In particular: CI sets RULES_LATEX_CTAN_MIRROR to the local
        # fixture-server URL, so we can't rely on tpc.CTAN_MIRROR
        # being the default in the already-loaded module.
        saved = os.environ.pop("RULES_LATEX_CTAN_MIRROR", None)
        try:
            spec = importlib.util.spec_from_file_location(
                "tpc_default", _TOOL_PATH
            )
            reloaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reloaded)
            self.assertEqual(reloaded.CTAN_MIRROR, "https://mirrors.ctan.org")
        finally:
            if saved is not None:
                os.environ["RULES_LATEX_CTAN_MIRROR"] = saved

    def test_env_override_changes_mirror(self):
        # Reload the module under a patched environment to verify
        # the env var takes effect.
        import importlib
        with patch.dict(
            "os.environ",
            {"RULES_LATEX_CTAN_MIRROR": "http://127.0.0.1:9999"},
        ):
            spec = importlib.util.spec_from_file_location(
                "tpc_reload", _TOOL_PATH
            )
            reloaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reloaded)
        self.assertEqual(reloaded.CTAN_MIRROR, "http://127.0.0.1:9999")

    def test_env_override_strips_trailing_slash(self):
        # Users will copy-paste mirror URLs with or without a trailing
        # slash; we strip it so the resulting URLs don't end up with
        # a duplicated `//`.
        import importlib
        with patch.dict(
            "os.environ",
            {"RULES_LATEX_CTAN_MIRROR": "http://mirror.local/"},
        ):
            spec = importlib.util.spec_from_file_location(
                "tpc_reload2", _TOOL_PATH
            )
            reloaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reloaded)
        self.assertEqual(reloaded.CTAN_MIRROR, "http://mirror.local")


if __name__ == "__main__":
    unittest.main()
