"""Unit tests for the missing-file hint logic in tectonic_populate_cache.

Covers:
  * `_scan_package_dependencies` — which package-file references the
    scanner detects across .sty/.cls/.bbx/.cbx layouts.
  * `_extract_missing_file` — the LaTeX-error grep over a tectonic
    .log file.
  * `_format_missing_file_hint` — the three-case hint formatter
    (listed / referenced / unknown).
  * `_print_dep_summary` — the proactive dep-map report.

End-to-end coverage of the failure path (real subprocess, real
log-file, real ctan_dir) lives in test_populate_cache_e2e.py.
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


class ScanPackageDependenciesTest(unittest.TestCase):
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
        self.assertEqual(
            tpc._scan_package_dependencies(self.root / "does-not-exist"),
            set(),
        )

    def test_finds_RequirePackage(self):
        self._write("foo.sty", r"\RequirePackage{etoolbox}" + "\n")
        self.assertEqual(
            tpc._scan_package_dependencies(self.root), {"etoolbox"}
        )

    def test_finds_usepackage_with_options(self):
        self._write("foo.sty", r"\usepackage[utf8]{inputenc}" + "\n")
        self.assertIn(
            "inputenc", tpc._scan_package_dependencies(self.root)
        )

    def test_finds_LoadClass(self):
        self._write("bar.cls", r"\LoadClass[10pt]{article}" + "\n")
        self.assertIn("article", tpc._scan_package_dependencies(self.root))

    def test_handles_comma_separated_package_list(self):
        # \usepackage{a,b,c} is the multi-package form. We want all
        # three names so the hint can be precise about which one was
        # the culprit.
        self._write(
            "multi.sty",
            r"\usepackage{amsmath, amssymb,amsfonts}" + "\n",
        )
        self.assertEqual(
            tpc._scan_package_dependencies(self.root),
            {"amsmath", "amssymb", "amsfonts"},
        )

    def test_RequirePackageWithOptions_variant(self):
        self._write("x.sty", r"\RequirePackageWithOptions{geometry}" + "\n")
        self.assertIn("geometry", tpc._scan_package_dependencies(self.root))

    def test_biblatex_style_files_scanned(self):
        # APA-style citation styles live as .bbx/.cbx/.lbx files.
        # The scanner needs to read them — they're a major source of
        # transitive ctan_packages demand.
        self._write("apa.bbx", r"\RequirePackage{biblatex}" + "\n")
        self.assertIn(
            "biblatex", tpc._scan_package_dependencies(self.root)
        )

    def test_ignores_unrelated_files(self):
        # README / .tex / .pdf shouldn't be scanned.
        self._write("README.txt", r"\usepackage{foo}" + "\n")
        self._write("doc/manual.tex", r"\usepackage{bar}" + "\n")
        self.assertEqual(tpc._scan_package_dependencies(self.root), set())

    def test_walks_nested_directories(self):
        # Packages often ship multi-file trees (e.g. biblatex-apa has
        # apa.bbx, apa.cbx, american-apa.lbx in different subdirs).
        self._write("tex/latex/contrib/x/a.sty", r"\RequirePackage{p1}")
        self._write("tex/latex/contrib/x/b.sty", r"\RequirePackage{p2}")
        self._write("tex/latex/biblatex/cbx/x.cbx", r"\RequirePackage{p3}")
        self.assertEqual(
            tpc._scan_package_dependencies(self.root),
            {"p1", "p2", "p3"},
        )

    def test_non_utf8_files_dont_crash(self):
        # CTAN packages occasionally ship Latin-1; the scanner must
        # not blow up on them. Write raw bytes that aren't valid UTF-8.
        path = self.root / "x.sty"
        path.write_bytes(b"\\usepackage{ok}\n\xff\xfe garbage \n")
        self.assertIn("ok", tpc._scan_package_dependencies(self.root))


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
            package_deps={},
        )
        self.assertIn("already listed", hint)
        self.assertIn("biblatex-apa", hint)

    def test_referenced_case_names_referring_package(self):
        # Missing package is referenced from a file inside one of the
        # fetched ctan_packages — the actionable next step is "add
        # it to the list", and we should name the requiring package.
        hint = tpc._format_missing_file_hint(
            missing="apa7.sty",
            ctan_packages=["biblatex-apa"],
            package_deps={"biblatex-apa": {"apa7", "biblatex"}},
        )
        self.assertIn("apa7", hint)
        self.assertIn("biblatex-apa", hint)
        self.assertIn("ctan_packages", hint)

    def test_referenced_case_truncates_long_package_list(self):
        # If many packages reference the missing name, the hint
        # should sample a few rather than dump the whole list.
        deps = {f"pkg{i}": {"shared"} for i in range(10)}
        hint = tpc._format_missing_file_hint(
            missing="shared.sty",
            ctan_packages=list(deps.keys()),
            package_deps=deps,
        )
        self.assertIn("more", hint)

    def test_unknown_case_suggests_typo_or_missing_ctan(self):
        # Missing file isn't listed and isn't referenced by any
        # fetched package. Mention both possibilities.
        hint = tpc._format_missing_file_hint(
            missing="mystery.sty",
            ctan_packages=["biblatex-apa"],
            package_deps={"biblatex-apa": {"biblatex"}},
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
            package_deps={"biblatex-apa": {"apa7"}},
        )
        self.assertIn("'apa7'", hint)


class PrintDepSummaryTest(unittest.TestCase):
    def test_empty_map_emits_nothing(self):
        import io
        out = io.StringIO()
        tpc._print_dep_summary({}, out=out)
        self.assertEqual(out.getvalue(), "")

    def test_packages_with_deps_listed_alphabetically(self):
        import io
        out = io.StringIO()
        tpc._print_dep_summary(
            {
                "biblatex-apa": {"biblatex", "csquotes", "apa"},
                "tcolorbox": {"etoolbox", "pgf"},
            },
            out=out,
        )
        body = out.getvalue()
        # Packages listed in sorted order, dep names also sorted.
        self.assertIn(
            "biblatex-apa -> apa, biblatex, csquotes", body
        )
        self.assertIn("tcolorbox -> etoolbox, pgf", body)
        # biblatex-apa appears before tcolorbox in the output.
        self.assertLess(
            body.index("biblatex-apa"), body.index("tcolorbox")
        )

    def test_package_with_no_deps_is_noted(self):
        import io
        out = io.StringIO()
        tpc._print_dep_summary({"lipsum": set()}, out=out)
        self.assertIn("lipsum", out.getvalue())
        self.assertIn("no upstream", out.getvalue())


class ExtractBiblatexVersionMismatchTest(unittest.TestCase):
    """Detector for the modern-biblatex shadowing failure signature."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rules_latex_test_")
        self.log = Path(self.tmp.name) / "doc.log"

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_none_when_log_missing(self):
        self.assertIsNone(tpc._extract_biblatex_version_mismatch(self.log))

    def test_matches_bbx_error(self):
        # Real-world: this is exactly what biblatex-apa 9.x against
        # bundle's biblatex 3.17 produces.
        self.log.write_text(
            "note: Running TeX ...\n"
            "error: apa.bbx:258: Undefined control sequence\n"
            "error: halted on potentially-recoverable error as specified\n"
        )
        self.assertEqual(
            tpc._extract_biblatex_version_mismatch(self.log), "apa.bbx"
        )

    def test_matches_cbx_error(self):
        # The citation style file can be the one that breaks first
        # depending on which macro got the update first.
        self.log.write_text(
            "error: chicago.cbx:142: Undefined control sequence\n"
        )
        self.assertEqual(
            tpc._extract_biblatex_version_mismatch(self.log), "chicago.cbx"
        )

    def test_matches_lbx_error(self):
        self.log.write_text(
            "error: american-apa.lbx:88: Undefined control sequence\n"
        )
        self.assertEqual(
            tpc._extract_biblatex_version_mismatch(self.log),
            "american-apa.lbx",
        )

    def test_does_not_match_generic_undefined_control_sequence(self):
        # Bare "Undefined control sequence" from a .tex file (the
        # user typo'd a macro) shouldn't trigger this hint — we only
        # care about errors originating in extension style files.
        self.log.write_text(
            "main.tex:42: Undefined control sequence\n"
        )
        self.assertIsNone(tpc._extract_biblatex_version_mismatch(self.log))

    def test_does_not_match_sty_undefined_control_sequence(self):
        # A user-fetched .sty erroring shouldn't be misattributed to
        # the biblatex coupling.
        self.log.write_text(
            "error: foo.sty:1: Undefined control sequence\n"
        )
        self.assertIsNone(tpc._extract_biblatex_version_mismatch(self.log))


class FormatBiblatexVersionHintTest(unittest.TestCase):
    def test_includes_offending_file(self):
        msg = tpc._format_biblatex_version_hint(
            "apa.bbx", ctan_packages=[],
        )
        self.assertIn("apa.bbx", msg)

    def test_names_likely_seed_packages(self):
        # The hint should specifically call out biblatex-* entries in
        # ctan_packages, since those are the likely culprits.
        msg = tpc._format_biblatex_version_hint(
            "apa.bbx", ctan_packages=["biblatex-apa", "lipsum"],
        )
        self.assertIn("biblatex-apa", msg)
        # Non-biblatex entries shouldn't appear in the "likely culprits"
        # callout (they're unrelated).
        # We can't strictly assert lipsum is absent, but the seed
        # clause should not include it.
        self.assertNotIn("biblatex-apa, lipsum", msg)
        self.assertNotIn("lipsum, biblatex-apa", msg)

    def test_names_the_bundle_biblatex_version(self):
        # The bundle now ships biblatex 3.21; the hint should name that
        # version (no more modern_biblatex opt-in to suggest) and point at
        # the bundle-refresh path for styles needing something even newer.
        msg = tpc._format_biblatex_version_hint(
            "apa.bbx", ctan_packages=[],
        )
        self.assertIn("3.21", msg)
        self.assertNotIn("modern_biblatex", msg)

    def test_includes_docs_link(self):
        msg = tpc._format_biblatex_version_hint(
            "apa.bbx", ctan_packages=[],
        )
        self.assertIn(
            "nicklambourne.github.io/rules_latex/getting-started/"
            "bibliography",
            msg,
        )

    def test_handles_no_biblatex_seeds(self):
        # If the user's ctan_packages doesn't contain any biblatex-*
        # entries (rare — they might be using ctan_packages =
        # ["apa7"] directly, for instance), the hint should still
        # work, just without the "likely culprit" callout.
        msg = tpc._format_biblatex_version_hint(
            "apa.bbx", ctan_packages=["apa7"],
        )
        self.assertNotIn("likely culprit", msg)
        self.assertIn("3.21", msg)


if __name__ == "__main__":
    unittest.main()
