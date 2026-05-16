"""Unit tests for tools/staging.py.

Covers the pure-function logic of the main-rooted staging layout
introduced in rules_latex v0.3. The actual tectonic invocation is
exercised by example-target end-to-end builds in CI.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


_STAGING_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "tools"
    / "staging.py"
)


def _load_staging():
    spec = importlib.util.spec_from_file_location("staging", _STAGING_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["staging"] = module
    spec.loader.exec_module(module)
    return module


_S = _load_staging()


class TestNormaliseShortPath(unittest.TestCase):
    """`bazel-out/<config>/bin/` prefix stripping."""

    def test_strips_bazel_out_prefix(self):
        self.assertEqual(
            _S.normalise_short_path(Path("bazel-out/k8-fastbuild/bin/pkg/file.tex")),
            Path("pkg/file.tex"),
        )

    def test_strips_darwin_arm64_prefix(self):
        self.assertEqual(
            _S.normalise_short_path(Path("bazel-out/darwin_arm64-fastbuild/bin/pkg/sub/file.tex")),
            Path("pkg/sub/file.tex"),
        )

    def test_leaves_source_paths_alone(self):
        self.assertEqual(
            _S.normalise_short_path(Path("pkg/file.tex")),
            Path("pkg/file.tex"),
        )

    def test_leaves_too_short_paths_alone(self):
        # Edge case: a path that starts with bazel-out but has fewer than 3
        # components shouldn't be mistakenly stripped.
        self.assertEqual(
            _S.normalise_short_path(Path("bazel-out/file.tex")),
            Path("bazel-out/file.tex"),
        )

    def test_leaves_paths_with_other_prefix_alone(self):
        # bazel-out/external/foo would be a strange path but not a generated
        # file in the bin/ tree.
        self.assertEqual(
            _S.normalise_short_path(Path("bazel-out/external/foo/file.tex")),
            Path("bazel-out/external/foo/file.tex"),
        )


class TestComputeStagedPath(unittest.TestCase):
    """Mapping from src path + main package to staged-relative path."""

    def test_main_package_descendant_relativised(self):
        # A file in main's package is staged at the same path relative to
        # the package, rooted at the work dir.
        self.assertEqual(
            _S.compute_staged_path(
                Path("study/honours/thesis/thesis/sections/intro.tex"),
                Path("study/honours/thesis/thesis"),
            ),
            Path("sections/intro.tex"),
        )

    def test_cross_package_uses_workspace_path(self):
        # A cross-package src stages at its workspace-relative path,
        # preserving the package context.
        self.assertEqual(
            _S.compute_staged_path(
                Path("study/llb/lib/references/refs.bib"),
                Path("study/llb/1700/notes"),
            ),
            Path("study/llb/lib/references/refs.bib"),
        )

    def test_main_itself_relativised(self):
        # Main always lands at main.basename when it's the main of its own
        # package.
        self.assertEqual(
            _S.compute_staged_path(
                Path("pkg/main.tex"),
                Path("pkg"),
            ),
            Path("main.tex"),
        )

    def test_generated_file_normalised(self):
        # A bazel-out-prefixed src is treated as if it were a source at
        # its post-strip path.
        self.assertEqual(
            _S.compute_staged_path(
                Path("bazel-out/k8-fastbuild/bin/pkg/generated.tex"),
                Path("pkg"),
            ),
            Path("generated.tex"),
        )


class TestStageSources(unittest.TestCase):
    """End-to-end staging into a temp work directory.

    Mirrors how the tectonic action wrappers invoke `stage_sources`:
    paths are workspace-relative (Bazel passes execroot-relative
    paths), and the function is called with cwd at the workspace root.
    """

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        # Mirror a noodle-shaped layout:
        #   workspace/pkg/main.tex
        #   workspace/pkg/sections/intro.tex
        #   workspace/lib/refs.bib
        (self.workspace / "pkg" / "sections").mkdir(parents=True)
        (self.workspace / "lib").mkdir()
        for rel in [
            "pkg/main.tex",
            "pkg/sections/intro.tex",
            "lib/refs.bib",
        ]:
            (self.workspace / rel).write_text(rel)
        self.work_dir = Path(tempfile.mkdtemp())
        self._old_cwd = os.getcwd()
        os.chdir(self.workspace)

    def tearDown(self):
        os.chdir(self._old_cwd)
        shutil.rmtree(self.workspace)
        shutil.rmtree(self.work_dir)

    def test_main_lands_at_work_root(self):
        # Main alone, no other srcs.
        main = Path("pkg/main.tex")
        staged = _S.stage_sources(main, [main], [], self.work_dir)
        self.assertEqual(staged, self.work_dir / "main.tex")
        self.assertTrue((self.work_dir / "main.tex").is_file())
        self.assertEqual((self.work_dir / "main.tex").read_text(), "pkg/main.tex")

    def test_main_pkg_descendant_preserves_layout(self):
        main = Path("pkg/main.tex")
        srcs = [main, Path("pkg/sections/intro.tex")]
        staged = _S.stage_sources(main, srcs, [], self.work_dir)
        self.assertEqual(staged, self.work_dir / "main.tex")
        self.assertTrue((self.work_dir / "main.tex").is_file())
        self.assertTrue((self.work_dir / "sections" / "intro.tex").is_file())

    def test_cross_package_src_uses_workspace_path(self):
        # A src from a sibling package stages under its full
        # workspace-rooted path.
        main = Path("pkg/main.tex")
        srcs = [main, Path("lib/refs.bib")]
        _S.stage_sources(main, srcs, [], self.work_dir)
        self.assertTrue((self.work_dir / "main.tex").is_file())
        self.assertTrue((self.work_dir / "lib" / "refs.bib").is_file())
        self.assertEqual(
            (self.work_dir / "lib" / "refs.bib").read_text(),
            "lib/refs.bib",
        )

    def test_pkg_files_override_placement(self):
        main = Path("pkg/main.tex")
        srcs = [main]
        _S.stage_sources(
            main,
            srcs,
            [_S.PkgFile(src=Path("lib/refs.bib"), rel="refs.bib")],
            self.work_dir,
        )
        self.assertTrue((self.work_dir / "refs.bib").is_file())
        self.assertEqual(
            (self.work_dir / "refs.bib").read_text(),
            "lib/refs.bib",
        )

    def test_pkg_files_rejects_escaping_path(self):
        main = Path("pkg/main.tex")
        with self.assertRaises(_S.StagingError):
            _S.stage_sources(
                main,
                [main],
                [_S.PkgFile(src=main, rel="../escape.tex")],
                self.work_dir,
            )

    def test_pkg_files_rejects_absolute_path(self):
        main = Path("pkg/main.tex")
        with self.assertRaises(_S.StagingError):
            _S.stage_sources(
                main,
                [main],
                [_S.PkgFile(src=main, rel="/etc/passwd")],
                self.work_dir,
            )

    def test_pkg_files_override_wins_over_auto_staging(self):
        main = Path("pkg/main.tex")
        srcs = [main, Path("pkg/sections/intro.tex")]
        _S.stage_sources(
            main,
            srcs,
            [_S.PkgFile(src=Path("lib/refs.bib"), rel="sections/intro.tex")],
            self.work_dir,
        )
        # The override wins: sections/intro.tex contains the bib's content.
        self.assertEqual(
            (self.work_dir / "sections" / "intro.tex").read_text(),
            "lib/refs.bib",
        )

    def test_absolute_main_path_rejected(self):
        main_abs = self.workspace / "pkg" / "main.tex"
        with self.assertRaises(_S.StagingError):
            _S.stage_sources(main_abs, [main_abs], [], self.work_dir)

    def test_absolute_src_path_rejected(self):
        main = Path("pkg/main.tex")
        src_abs = self.workspace / "pkg" / "sections" / "intro.tex"
        with self.assertRaises(_S.StagingError):
            _S.stage_sources(main, [main, src_abs], [], self.work_dir)


if __name__ == "__main__":
    unittest.main()
