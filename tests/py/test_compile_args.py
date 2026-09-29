"""CLI and worker requests must parse the same Bazel response file."""

from __future__ import annotations

import importlib.util
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_TOOL = Path(__file__).resolve().parent.parent.parent / "tools" / "tectonic_compile.py"
spec = importlib.util.spec_from_file_location("tectonic_compile_test", _TOOL)
compile_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compile_tool)


class TestCompileArguments(unittest.TestCase):
    def test_structured_cache_layout_for_tarball_and_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(compile_tool._cache_layout(root), (root, None))
            (root / "cache").mkdir()
            (root / "ctan_pkgs").mkdir()
            self.assertEqual(
                compile_tool._cache_layout(root),
                (root / "cache", root / "ctan_pkgs"),
            )

    def test_structured_cache_directory_matches_tarball_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "doc.tex").write_text("document", encoding="utf-8")
            cache_root = root / "snapshot"
            (cache_root / "cache").mkdir(parents=True)
            (cache_root / "ctan_pkgs").mkdir()
            (cache_root / "cache" / "entry").write_text("cached")
            (cache_root / "ctan_pkgs" / "foo.sty").write_text("package")
            tarball = root / "snapshot.tar.gz"
            with tarfile.open(tarball, "w:gz") as archive:
                for path in cache_root.rglob("*"):
                    archive.add(path, arcname=path.relative_to(cache_root))

            seen = []

            def fake_tectonic(**kwargs):
                seen.append(kwargs)
                (kwargs["main_in_workdir"].parent / "doc.pdf").write_bytes(b"pdf")

            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                for mode, source in (("--cache-dir", cache_root),
                                     ("--cache-tarball", tarball)):
                    args = compile_tool.parse_args([
                        "--tectonic", "tectonic", "--main", "doc.tex",
                        "--src", "doc.tex", mode, str(source),
                        "--output", str(root / "out.pdf"),
                    ])
                    with patch.object(compile_tool, "run_tectonic", fake_tectonic):
                        self.assertEqual(compile_tool.run_one(args), 0)
                    self.assertEqual((root / "out.pdf").read_bytes(), b"pdf")
            finally:
                os.chdir(old_cwd)

            self.assertEqual(len(seen), 2)
            for call in seen:
                self.assertEqual(call["cache_dir"].name, "cache")
                self.assertEqual(call["ctan_dir"].name, "ctan_pkgs")

    def test_response_file_works_for_cli_and_worker(self):
        arguments = [
            "--tectonic", "tectonic", "--main", "pkg/main.tex",
            "--output", "out.pdf",
        ]
        with tempfile.TemporaryDirectory() as directory:
            params = Path(directory) / "compile.params"
            params.write_text("\n".join(arguments) + "\n", encoding="utf-8")
            with patch.object(sys, "argv", ["tectonic_compile.py", f"@{params}"]):
                cli = compile_tool.parse_args()
            worker = compile_tool.parse_args([f"@{params}"])

        for parsed in (cli, worker):
            self.assertEqual(parsed.tectonic, Path("tectonic"))
            self.assertEqual(parsed.main, Path("pkg/main.tex"))
            self.assertEqual(parsed.output, Path("out.pdf"))


if __name__ == "__main__":
    unittest.main()
