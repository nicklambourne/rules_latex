"""End-to-end coverage of the populate-cache failure-hint plumbing.

The unit tests in test_populate_cache_hints.py cover each pure
function in isolation. This module exercises the actual wire-up
inside `run_tectonic`: that the failure path locates the right log
file, greps it correctly, threads `package_deps` through to the hint
formatter, and emits the result to stderr before raising SystemExit.

We don't want to depend on a real tectonic binary or real CTAN
mirrors for this test — those are exercised by //tests/ctan:* — so
the test fakes `tectonic` with a tiny shell script that writes a
controlled `.log` and exits non-zero. The rest of the flow runs for
real.
"""

from __future__ import annotations

import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
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


def _write_fake_tectonic(path: Path, log_body: str, exit_code: int) -> None:
    """Write a shell script that mimics ``tectonic -X compile``.

    Parses --outdir from the args, writes a `.log` next to the main
    .tex (matching --keep-logs behaviour), then exits with the
    requested code. The actual .tex is ignored — we control what
    goes into the log directly.
    """
    script = f"""#!/usr/bin/env bash
set -euo pipefail
outdir=""
prev=""
for arg in "$@"; do
    if [[ "$prev" == "--outdir" ]]; then
        outdir="$arg"
    fi
    prev="$arg"
done

# Find the .tex arg (always the last positional) to derive log name
last_arg=""
for arg in "$@"; do
    last_arg="$arg"
done
stem="${{last_arg%.tex}}"
stem="$(basename "$stem")"

mkdir -p "$outdir"
cat > "$outdir/$stem.log" <<'EOF'
{log_body}
EOF
exit {exit_code}
"""
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RunTectonicFailurePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rules_latex_e2e_")
        root = Path(self.tmp.name)
        self.work_dir = root / "work"
        self.work_dir.mkdir()
        self.cache_dir = root / "cache"
        self.cache_dir.mkdir()
        # The "main" file. run_tectonic uses its name to derive the
        # log path (<stem>.log) and to invoke tectonic with the
        # basename as the positional arg.
        self.main = self.work_dir / "thesis.tex"
        self.main.write_text(r"\documentclass{article}\begin{document}\end{document}")
        self.tectonic = root / "fake_tectonic"

    def tearDown(self):
        self.tmp.cleanup()

    def test_failure_with_missing_file_in_log_emits_hint(self):
        # Fake tectonic fails with a classic missing-file error and
        # the .log it writes will be greppable by _extract_missing_file.
        _write_fake_tectonic(
            self.tectonic,
            log_body="! LaTeX Error: File `apa7.sty' not found.\n",
            exit_code=1,
        )
        with self.assertRaises(SystemExit) as cm:
            tpc.run_tectonic(
                tectonic=self.tectonic,
                main_in_workdir=self.main,
                cache_dir=self.cache_dir,
                ctan_packages=["biblatex-apa"],
                package_deps={"biblatex-apa": {"apa7", "biblatex"}},
            )
        msg = str(cm.exception)
        self.assertIn("tectonic exited with code 1", msg)
        # The targeted hint should name the missing file, the
        # referencing package, and prompt the user to add it.
        self.assertIn("apa7", msg)
        self.assertIn("biblatex-apa", msg)
        self.assertIn("ctan_packages", msg)

    def test_failure_without_missing_file_omits_hint(self):
        # A non-file failure (e.g. an OOM, or a transient tectonic
        # crash) shouldn't trigger the hint path — there's nothing
        # to suggest. We should still raise SystemExit with the
        # generic exit-code message.
        _write_fake_tectonic(
            self.tectonic,
            log_body="(no LaTeX file-not-found error here)\n",
            exit_code=42,
        )
        with self.assertRaises(SystemExit) as cm:
            tpc.run_tectonic(
                tectonic=self.tectonic,
                main_in_workdir=self.main,
                cache_dir=self.cache_dir,
                ctan_packages=["biblatex-apa"],
                package_deps={"biblatex-apa": {"biblatex"}},
            )
        msg = str(cm.exception)
        self.assertIn("tectonic exited with code 42", msg)
        # No hint paragraph should appear.
        self.assertNotIn("hint:", msg)

    def test_failure_with_missing_file_and_no_ctan_packages(self):
        # No ctan_packages at all — the missing file is presumably
        # something the user typo'd in their .tex. We should still
        # emit a hint, but the "unknown" case rather than
        # "referenced".
        _write_fake_tectonic(
            self.tectonic,
            log_body="! LaTeX Error: File `mystery.sty' not found.\n",
            exit_code=1,
        )
        with self.assertRaises(SystemExit) as cm:
            tpc.run_tectonic(
                tectonic=self.tectonic,
                main_in_workdir=self.main,
                cache_dir=self.cache_dir,
                ctan_packages=[],
                package_deps={},
            )
        msg = str(cm.exception)
        self.assertIn("mystery", msg)
        # "Unknown" case mentions typo + CTAN-package possibility.
        self.assertIn("typo", msg)

    def test_success_path_does_not_raise(self):
        # When tectonic exits 0, no SystemExit, no hint, no fuss.
        _write_fake_tectonic(
            self.tectonic,
            log_body="this is a successful compile log\n",
            exit_code=0,
        )
        tpc.run_tectonic(
            tectonic=self.tectonic,
            main_in_workdir=self.main,
            cache_dir=self.cache_dir,
            ctan_packages=["biblatex-apa"],
            package_deps={"biblatex-apa": {"biblatex"}},
        )

    def test_listed_package_failure_steers_to_issue(self):
        # The package itself is listed in ctan_packages — download
        # succeeded but tectonic still didn't see it. The hint
        # should warn about a TDS-layout mismatch.
        _write_fake_tectonic(
            self.tectonic,
            log_body="! LaTeX Error: File `weird-pkg.sty' not found.\n",
            exit_code=1,
        )
        with self.assertRaises(SystemExit) as cm:
            tpc.run_tectonic(
                tectonic=self.tectonic,
                main_in_workdir=self.main,
                cache_dir=self.cache_dir,
                ctan_packages=["weird-pkg"],
                package_deps={"weird-pkg": set()},
            )
        self.assertIn("already listed", str(cm.exception))
        self.assertIn("weird-pkg", str(cm.exception))


class DownloadAndScanFlowTest(unittest.TestCase):
    """Verifies download_ctan_package + _scan_package_dependencies wire up.

    Cant hit CTAN from tests, so we monkeypatch urlretrieve to drop a
    pre-built fake .zip on disk. The rest of download_ctan_package
    (extract, scan, normalise) runs for real, exercising the
    "per-package dep tracking" path that the proactive summary and
    the failure-hint formatter both depend on.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rules_latex_e2e_")
        self.dest = Path(self.tmp.name) / "ctan_pkgs"
        self.dest.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_fake_zip(self, name: str, contents: dict[str, str]) -> Path:
        """Build a .zip with the given relpath -> body contents."""
        import zipfile
        path = Path(self.tmp.name) / f"{name}.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for rel, body in contents.items():
                zf.writestr(rel, body)
        return path

    def test_download_returns_per_package_dep_set(self):
        # Synthetic package whose .sty requires three upstreams.
        fake_zip = self._make_fake_zip(
            "fake-pkg",
            {
                "fake-pkg/fake-pkg.sty": (
                    r"\RequirePackage{etoolbox}"
                    + "\n"
                    + r"\usepackage{xcolor}"
                    + "\n"
                    + r"\RequirePackage{tikz}"
                    + "\n"
                ),
            },
        )

        def fake_urlretrieve(url, archive):
            import shutil as _sh
            _sh.copyfile(fake_zip, archive)

        with patch.object(tpc.urllib.request, "urlretrieve", fake_urlretrieve):
            deps = tpc.download_ctan_package("fake-pkg", self.dest)

        self.assertEqual(deps, {"etoolbox", "xcolor", "tikz"})

    def test_download_handles_package_with_no_deps(self):
        fake_zip = self._make_fake_zip(
            "lonely",
            {"lonely/lonely.sty": "% no dependencies here\n"},
        )

        def fake_urlretrieve(url, archive):
            import shutil as _sh
            _sh.copyfile(fake_zip, archive)

        with patch.object(tpc.urllib.request, "urlretrieve", fake_urlretrieve):
            deps = tpc.download_ctan_package("lonely", self.dest)

        self.assertEqual(deps, set())


if __name__ == "__main__":
    unittest.main()
