"""Unit tests for the auto-resolver in tectonic_populate_cache.

Covers:
  * `_load_bundle_manifest` — file format, comment handling, missing files.
  * `_head_probe_ctan` — 200 / 404 / 5xx / URLError outcomes against the
    4-URL fallback chain.
  * `resolve_transitive_closure` — the main resolver loop: bundle
    filtering, HEAD-probe filtering of false positives, recursion
    on transitive references, cycle handling, max-iteration bound.

End-to-end coverage (real subprocess, real fixture mirror) is at
tests/ctan/auto_resolve_test in the bazel test suite.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
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


class LoadBundleManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rules_latex_test_")
        self.path = Path(self.tmp.name) / "manifest.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_simple_list(self):
        self.path.write_text("etoolbox\nbiblatex\nxcolor\n")
        self.assertEqual(
            tpc._load_bundle_manifest(self.path),
            {"etoolbox", "biblatex", "xcolor"},
        )

    def test_comments_and_blank_lines_ignored(self):
        self.path.write_text(
            "# A header comment.\n"
            "# Another comment.\n"
            "\n"
            "etoolbox\n"
            "\n"
            "biblatex\n"
            "# Trailing comment\n"
        )
        self.assertEqual(
            tpc._load_bundle_manifest(self.path),
            {"etoolbox", "biblatex"},
        )

    def test_whitespace_stripped(self):
        # Real-world files can sneak in trailing whitespace from
        # editors; strip it.
        self.path.write_text("  etoolbox  \n\tbiblatex\t\n")
        self.assertEqual(
            tpc._load_bundle_manifest(self.path),
            {"etoolbox", "biblatex"},
        )

    def test_missing_file_raises_systemexit(self):
        missing = Path(self.tmp.name) / "does-not-exist.txt"
        with self.assertRaises(SystemExit) as cm:
            tpc._load_bundle_manifest(missing)
        # Error message should point at the offending path so it's
        # debuggable.
        self.assertIn(str(missing), str(cm.exception))


def _make_http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "", {}, None)


class HeadProbeCtanTest(unittest.TestCase):
    def test_returns_true_on_first_200(self):
        # The .tds.zip URL succeeds; we shouldn't probe further URLs.
        attempted: list[str] = []

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            attempted.append(req.full_url)
            return FakeResponse()

        with patch.object(tpc.urllib.request, "urlopen", fake_urlopen):
            result = tpc._head_probe_ctan("lipsum")
        self.assertTrue(result)
        # Hit only the first URL.
        self.assertEqual(len(attempted), 1)
        self.assertIn(".tds.zip", attempted[0])

    def test_404_falls_through_to_next_url(self):
        # First two URLs 404; third succeeds. Common shape for
        # biblatex-* packages that live under biblatex-contrib/.
        responses = [
            _make_http_error("u1", 404),
            _make_http_error("u2", 404),
            _make_response(200),
        ]

        def fake_urlopen(req, timeout=None):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with patch.object(tpc.urllib.request, "urlopen", fake_urlopen):
            result = tpc._head_probe_ctan("biblatex-apa")
        self.assertTrue(result)

    def test_all_404_returns_false(self):
        def fake_urlopen(req, timeout=None):
            raise _make_http_error(req.full_url, 404)

        with patch.object(tpc.urllib.request, "urlopen", fake_urlopen):
            self.assertFalse(tpc._head_probe_ctan("not-a-real-pkg"))

    def test_urlerror_treated_as_not_found(self):
        # Network errors should NOT be reported as "package exists" —
        # we'd then attempt to fetch and the retry-budget would still
        # eventually fail. Better: skip the candidate this run, the
        # user will see the missing-file hint if it really mattered.
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("connection refused")

        with patch.object(tpc.urllib.request, "urlopen", fake_urlopen):
            self.assertFalse(tpc._head_probe_ctan("anything"))

    def test_5xx_treated_as_not_found(self):
        # Same rationale as URLError: we don't know, so we're
        # conservative.
        def fake_urlopen(req, timeout=None):
            raise _make_http_error(req.full_url, 503)

        with patch.object(tpc.urllib.request, "urlopen", fake_urlopen):
            self.assertFalse(tpc._head_probe_ctan("anything"))


def _make_response(status):
    """Helper: a context-manager-compatible HTTP response stub."""
    class R:
        def __init__(self, s):
            self.status = s

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    return R(status)


class ResolveTransitiveClosureTest(unittest.TestCase):
    """The resolver loop's behaviour with mocked download + probe.

    We patch `download_ctan_package` so each call returns a canned
    "what this package references" set, and patch `_head_probe_ctan`
    to control which names look CTAN-resident.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rules_latex_test_")
        self.dest = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_download(self, dep_map: dict[str, set[str]]):
        """Patch download_ctan_package to return canned dep sets."""
        def fake_download(pkg, dest):
            return dep_map.get(pkg, set())
        return patch.object(tpc, "download_ctan_package", fake_download)

    def _patch_probe(self, available: set[str]):
        """Patch _head_probe_ctan to return True only for ``available``."""
        def fake_probe(pkg, timeout=10.0):
            return pkg in available
        return patch.object(tpc, "_head_probe_ctan", fake_probe)

    def test_single_seed_no_transitive(self):
        # User listed lipsum; it doesn't reference anything outside
        # the bundle. Resolver fetches lipsum, scans, sees no
        # interesting refs, stops.
        with self._patch_download({"lipsum": {"etoolbox"}}):
            result = tpc.resolve_transitive_closure(
                ["lipsum"],
                self.dest,
                bundle_manifest={"etoolbox"},
            )
        self.assertEqual(set(result), {"lipsum"})
        self.assertEqual(result["lipsum"], {"etoolbox"})

    def test_two_level_transitive_chain(self):
        # User lists pkg_a. pkg_a references pkg_b (not in bundle,
        # CTAN-resident). pkg_b references etoolbox (bundle).
        # Resolver should fetch both pkg_a and pkg_b in one populate.
        deps = {
            "pkg_a": {"pkg_b", "etoolbox"},
            "pkg_b": {"etoolbox", "xcolor"},
        }
        with self._patch_download(deps), self._patch_probe({"pkg_b"}):
            result = tpc.resolve_transitive_closure(
                ["pkg_a"],
                self.dest,
                bundle_manifest={"etoolbox", "xcolor"},
            )
        self.assertEqual(set(result), {"pkg_a", "pkg_b"})

    def test_three_level_chain(self):
        # pkg_a → pkg_b → pkg_c → nothing-new.
        deps = {
            "pkg_a": {"pkg_b"},
            "pkg_b": {"pkg_c"},
            "pkg_c": {"etoolbox"},
        }
        with self._patch_download(deps), self._patch_probe({"pkg_b", "pkg_c"}):
            result = tpc.resolve_transitive_closure(
                ["pkg_a"],
                self.dest,
                bundle_manifest={"etoolbox"},
            )
        self.assertEqual(set(result), {"pkg_a", "pkg_b", "pkg_c"})

    def test_bundle_resident_ref_not_fetched(self):
        # pkg_a references biblatex, which is in the bundle.
        # Don't fetch biblatex — shadowing risk (see DESIGN.md §4.10).
        deps = {"pkg_a": {"biblatex", "etoolbox"}}
        with self._patch_download(deps), self._patch_probe(set()):
            # Probe returns False for everything; we should still not
            # have tried to probe biblatex (filter first).
            result = tpc.resolve_transitive_closure(
                ["pkg_a"],
                self.dest,
                bundle_manifest={"biblatex", "etoolbox"},
            )
        self.assertEqual(set(result), {"pkg_a"})

    def test_unknown_ref_filtered_by_head_probe(self):
        # pkg_a references some-internal-thing which isn't in the
        # bundle and isn't on CTAN (probe returns False). Don't
        # fetch — the compile will fail with the missing-file
        # hint if it really mattered.
        deps = {"pkg_a": {"some-internal-thing"}}
        with self._patch_download(deps), self._patch_probe(set()):
            result = tpc.resolve_transitive_closure(
                ["pkg_a"],
                self.dest,
                bundle_manifest=set(),
            )
        self.assertEqual(set(result), {"pkg_a"})

    def test_seed_package_in_bundle_still_fetched(self):
        # Even if the user lists a package that happens to be in
        # the bundle (rare, but possible — overriding a stale
        # bundle version intentionally), we honour the explicit
        # request and fetch it. The bundle filter only applies to
        # *transitive* refs.
        with self._patch_download({"biblatex": set()}), self._patch_probe(set()):
            result = tpc.resolve_transitive_closure(
                ["biblatex"],
                self.dest,
                bundle_manifest={"biblatex"},
            )
        self.assertEqual(set(result), {"biblatex"})

    def test_cycle_does_not_infinite_loop(self):
        # pkg_a references pkg_b; pkg_b references pkg_a. The
        # resolver should fetch each exactly once and terminate.
        deps = {
            "pkg_a": {"pkg_b"},
            "pkg_b": {"pkg_a"},
        }
        with self._patch_download(deps), self._patch_probe({"pkg_b"}):
            result = tpc.resolve_transitive_closure(
                ["pkg_a"],
                self.dest,
                bundle_manifest=set(),
            )
        self.assertEqual(set(result), {"pkg_a", "pkg_b"})

    def test_max_iterations_bounded(self):
        # Pathological: each fetched package references a new
        # unique name. Without the iteration cap this would never
        # terminate (the resolver would keep fetching). With the
        # cap, it stops gracefully.
        counter = [0]

        def fake_download(pkg, dest):
            counter[0] += 1
            return {f"pkg_{counter[0]}"}

        with patch.object(tpc, "download_ctan_package", fake_download), \
                self._patch_probe({f"pkg_{i}" for i in range(1000)}):
            # We expect the resolver to stop at the iteration cap.
            result = tpc.resolve_transitive_closure(
                ["seed"],
                self.dest,
                bundle_manifest=set(),
                max_iterations=5,
            )
        # We can't predict the exact set, only that it's bounded.
        self.assertLessEqual(len(result), 5)

    def test_head_probe_only_called_for_transitive_refs(self):
        # Seeds always fetch; bundle-resident transitive refs are
        # filtered before probing. Probe should only be called for
        # transitive refs that aren't in the bundle.
        deps = {
            "pkg_a": {"biblatex", "novel_pkg"},
        }
        probed: list[str] = []

        def fake_probe(pkg, timeout=10.0):
            probed.append(pkg)
            return False

        with self._patch_download(deps), patch.object(tpc, "_head_probe_ctan", fake_probe):
            tpc.resolve_transitive_closure(
                ["pkg_a"],
                self.dest,
                bundle_manifest={"biblatex"},
            )
        # biblatex shouldn't be probed (filtered by manifest); only
        # novel_pkg should.
        self.assertEqual(probed, ["novel_pkg"])


if __name__ == "__main__":
    unittest.main()
