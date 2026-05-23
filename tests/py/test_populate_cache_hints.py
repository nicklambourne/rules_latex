"""Unit tests for the missing-file hint logic in tectonic_populate_cache.

Covers:
  * `_scan_ctan_dependencies` — which package-file references the
    scanner detects across .sty/.cls/.bbx/.cbx layouts.
  * `_extract_missing_file` — the LaTeX-error grep over a tectonic
    .log file.
  * `_format_missing_file_hint` — the three-case hint formatter
    (listed / referenced / unknown).

These are the failure-path ergonomics for the ctan_packages workflow.
The actual tectonic invocation is exercised end-to-end by
//tests/ctan:*.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


_TOOL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "tools"
    / "tectonic_populate_cache.py"
)


def _load_module():
    # The module imports `staging` from its sibling path at top-level,
    # so we need that path on sys.path before exec.
    sys.path.insert(0, str(_TOOL_PATH.parent))
    spec = importlib.util.spec_from_file_location("tpc", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tpc"] = module
    spec.loader.exec_module(module)
    return module


tpc = _load_module()


class ScanCtanDependenciesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rules_latex_test_")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rel: str, body: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_returns_empty_for_missing_directory(self):
        result = tpc._scan_ctan_dependencies(self.root / "does-not-exist")
        self.assertEqual(result, {})

    def test_finds_RequirePackage(self):
        self._write(
            "tex/latex/contrib/foo/foo.sty",
            r"\RequirePackage{etoolbox}" + "\n",
        )
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertEqual(set(refs.keys()), {"etoolbox"})
        self.assertEqual(refs["etoolbox"], {"foo.sty"})

    def test_finds_usepackage_with_options(self):
        self._write(
            "tex/latex/contrib/foo/foo.sty",
            r"\usepackage[utf8]{inputenc}" + "\n",
        )
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertIn("inputenc", refs)

    def test_finds_LoadClass(self):
        self._write(
            "tex/latex/contrib/bar/bar.cls",
            r"\LoadClass[10pt]{article}" + "\n",
        )
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertIn("article", refs)

    def test_handles_comma_separated_package_list(self):
        # `\usepackage{a,b,c}` is the multi-package form. We want all
        # three names so the hint can be precise about which one was
        # the culprit.
        self._write(
            "tex/latex/contrib/multi/multi.sty",
            r"\usepackage{amsmath, amssymb,amsfonts}" + "\n",
        )
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertEqual(
            {k for k in refs}, {"amsmath", "amssymb", "amsfonts"}
        )

    def test_RequirePackageWithOptions_variant(self):
        self._write(
            "tex/latex/contrib/x/x.sty",
            r"\RequirePackageWithOptions{geometry}" + "\n",
        )
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertIn("geometry", refs)

    def test_biblatex_style_files_scanned(self):
        # APA-style citation styles live as .bbx/.cbx/.lbx under
        # tex/latex/biblatex/. The scanner needs to read them too,
        # since they're a major source of transitive ctan_packages
        # demand.
        self._write(
            "tex/latex/biblatex/bbx/apa.bbx",
            r"\RequirePackage{biblatex}" + "\n",
        )
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertEqual(refs.get("biblatex"), {"apa.bbx"})

    def test_ignores_unrelated_files(self):
        # README/.tex/.pdf/etc shouldn't be scanned.
        self._write("README.txt", r"\usepackage{foo}" + "\n")
        self._write("doc/manual.tex", r"\usepackage{bar}" + "\n")
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertEqual(refs, {})

    def test_multiple_files_referencing_same_package_are_aggregated(self):
        self._write(
            "tex/latex/contrib/a/a.sty",
            r"\RequirePackage{shared}" + "\n",
        )
        self._write(
            "tex/latex/contrib/b/b.sty",
            r"\RequirePackage{shared}" + "\n",
        )
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertEqual(refs["shared"], {"a.sty", "b.sty"})

    def test_non_utf8_files_dont_crash(self):
        # CTAN packages occasionally ship Latin-1; the scanner must
        # not blow up on them. Write raw bytes that aren't valid UTF-8.
        path = self.root / "tex" / "latex" / "contrib" / "x" / "x.sty"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\\usepackage{ok}\n\xff\xfe garbage \n")
        refs = tpc._scan_ctan_dependencies(self.root)
        self.assertIn("ok", refs)


class ExtractMissingFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rules_latex_test_")
        self.log = Path(self.tmp.name) / "doc.log"

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_none_when_log_missing(self):
        self.assertIsNone(tpc._extract_missing_file(self.log))

    def test_extracts_with_backtick_quote_form(self):
        # `! LaTeX Error: File `foo.sty' not found.` — most common form.
        self.log.write_text(
            "Some preamble noise.\n"
            "! LaTeX Error: File `apa.bbx' not found.\n"
            "Trailing.\n"
        )
        self.assertEqual(tpc._extract_missing_file(self.log), "apa.bbx")

    def test_extracts_with_straight_quote_form(self):
        # Older tectonic builds occasionally emit straight quotes.
        self.log.write_text(
            "! LaTeX Error: File 'tcolorbox.sty' not found.\n"
        )
        self.assertEqual(
            tpc._extract_missing_file(self.log), "tcolorbox.sty"
        )

    def test_returns_first_match_when_multiple(self):
        # A failing build often re-emits the error multiple times as
        # \endgroup{} unwinds. We pick the first.
        self.log.write_text(
            "! LaTeX Error: File `first.sty' not found.\n"
            "! LaTeX Error: File `second.sty' not found.\n"
        )
        self.assertEqual(tpc._extract_missing_file(self.log), "first.sty")

    def test_returns_none_when_log_has_no_match(self):
        self.log.write_text("This log contains no error.\n")
        self.assertIsNone(tpc._extract_missing_file(self.log))


class FormatMissingFileHintTest(unittest.TestCase):
    def test_listed_case_warns_of_layout_issue(self):
        # Package is in the user's ctan_packages list — download
        # succeeded but tectonic still can't see it. Most likely a
        # TDS-layout problem in the archive.
        hint = tpc._format_missing_file_hint(
            missing="biblatex-apa.sty",
            ctan_packages=["biblatex-apa"],
            refs={},
        )
        self.assertIn("already listed", hint)
        self.assertIn("biblatex-apa", hint)

    def test_referenced_case_names_referring_file(self):
        # Missing package is referenced from a file in a fetched
        # ctan_package — the actionable next step is "add it to the
        # list", and we should name the witness file.
        hint = tpc._format_missing_file_hint(
            missing="apa7.sty",
            ctan_packages=["biblatex-apa"],
            refs={"apa7": {"apa.bbx"}},
        )
        self.assertIn("apa7", hint)
        self.assertIn("apa.bbx", hint)
        self.assertIn("ctan_packages", hint)

    def test_referenced_case_truncates_long_witness_list(self):
        # If many files reference the missing package, the hint
        # should sample a few rather than dump the whole list.
        refs = {"common": {f"pkg{i}.sty" for i in range(10)}}
        hint = tpc._format_missing_file_hint(
            missing="common.sty",
            ctan_packages=[],
            refs=refs,
        )
        self.assertIn("more", hint)

    def test_unknown_case_suggests_typo_or_missing_ctan(self):
        # Missing file isn't listed and isn't referenced by any
        # fetched package. Mention both possibilities.
        hint = tpc._format_missing_file_hint(
            missing="mystery.sty",
            ctan_packages=["biblatex-apa"],
            refs={},
        )
        self.assertIn("mystery", hint)
        self.assertIn("CTAN package", hint)
        self.assertIn("typo", hint)

    def test_strips_extension_for_package_name(self):
        # The package name (for the "add to ctan_packages" suggestion)
        # should be the file stem, not `foo.sty`.
        hint = tpc._format_missing_file_hint(
            missing="apa7.sty",
            ctan_packages=[],
            refs={"apa7": {"apa.bbx"}},
        )
        # The hint should mention `'apa7'` (without .sty) as the
        # name to add.
        self.assertIn("'apa7'", hint)


if __name__ == "__main__":
    unittest.main()
