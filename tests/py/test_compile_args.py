"""CLI and worker requests must parse the same Bazel response file."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_TOOL = Path(__file__).resolve().parent.parent.parent / "tools" / "tectonic_compile.py"
spec = importlib.util.spec_from_file_location("tectonic_compile_test", _TOOL)
compile_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compile_tool)


class TestCompileArguments(unittest.TestCase):
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
